# Instala SATEC en esta PC. Conserva data\ (bases de datos).
param(
    [ValidateSet("InPlace", "Setup")]
    [string]$Mode = "InPlace"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
$Author = "WAMBOO TIC"
$AppName = "SATEC"
$PyEmbedUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

function Write-Step($text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Green
}

function Find-SystemPython {
    $candidates = @()
    foreach ($cmd in @("py", "python", "python3")) {
        $item = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($item) { $candidates += $item.Source }
    }
    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $exe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch {}
        try {
            $exe = & py -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch {}
    }
    foreach ($exe in $candidates) {
        try {
            $ver = & $exe -c "import sys; print(sys.version_info[:2] >= (3, 12))" 2>$null
            if ($ver -match "True") { return $exe }
        } catch {}
    }
    return $null
}

function Configure-EmbedRuntime([string]$RuntimeDir) {
    $dest = Split-Path $RuntimeDir -Parent
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    $pth = Get-ChildItem $RuntimeDir -Filter "python*._pth" | Select-Object -First 1
    if ($pth) {
        $pthLines = @(
            "python312.zip",
            ".",
            "..",
            $dest,
            "Lib",
            "Lib\site-packages",
            "import site"
        )
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllLines($pth.FullName, $pthLines, $utf8)
    }
    $siteCustomize = @"
import sys
from pathlib import Path
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
"@
    Set-Content -Path (Join-Path $RuntimeDir "sitecustomize.py") -Value $siteCustomize -Encoding UTF8
    $sp = Join-Path $RuntimeDir "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $sp | Out-Null
    Set-Content -Path (Join-Path $sp "satec.pth") -Value $dest -Encoding UTF8
}

function Install-EmbeddablePython([string]$RuntimeDir) {
    Write-Step "Descargando Python portatil (no hace falta instalarlo en Windows)..."
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    $zip = Join-Path $env:TEMP "satec-python-embed.zip"
    Invoke-WebRequest -Uri $PyEmbedUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $RuntimeDir -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Configure-EmbedRuntime $RuntimeDir
    $getPip = Join-Path $RuntimeDir "get-pip.py"
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPip -UseBasicParsing
    $py = Join-Path $RuntimeDir "python.exe"
    & $py $getPip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar pip en Python portatil." }
    & $py -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar setuptools." }
    Configure-EmbedRuntime $RuntimeDir
    return $py
}

function Get-Python([string]$Dest) {
    $venvPy = Join-Path $Dest "venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    $runtimePy = Join-Path $Dest "runtime\python.exe"
    if (Test-Path $runtimePy) {
        Configure-EmbedRuntime (Join-Path $Dest "runtime")
        return $runtimePy
    }
    $system = Find-SystemPython
    if ($system) {
        Write-Step "Creando entorno virtual con Python del sistema..."
        & $system -m venv (Join-Path $Dest "venv")
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear venv." }
        return (Join-Path $Dest "venv\Scripts\python.exe")
    }
    return (Install-EmbeddablePython (Join-Path $Dest "runtime"))
}

function Copy-Payload([string]$Payload, [string]$Dest) {
    Write-Step "Copiando SATEC a $Dest (se conserva la base de datos)..."
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $excludeDirs = @("data", "venv", "runtime", "__pycache__", "dist", "webview")
    Get-ChildItem $Payload -Force | ForEach-Object {
        if ($excludeDirs -contains $_.Name) { return }
        $target = Join-Path $Dest $_.Name
        if ($_.PSIsContainer) {
            robocopy $_.FullName $target /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
        } else {
            Copy-Item $_.FullName $target -Force
        }
    }
    $dataDest = Join-Path $Dest "data"
    New-Item -ItemType Directory -Force -Path $dataDest | Out-Null
}

function Install-Deps([string]$Python, [string]$Dest) {
    Write-Step "Instalando componentes de SATEC..."
    & $Python -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar setuptools. Revise la conexion a internet." }
    & $Python -m pip install -r (Join-Path $Dest "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de dependencias. Revise la conexion a internet." }
}

function New-Shortcut([string]$Target, [string]$Link, [string]$WorkDir, [string]$Icon, [string]$Description) {
    $folder = Split-Path $Link -Parent
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $w = New-Object -ComObject WScript.Shell
    $s = $w.CreateShortcut($Link)
    $s.TargetPath = $Target
    $s.WorkingDirectory = $WorkDir
    if (Test-Path $Icon) { $s.IconLocation = $Icon }
    $s.Description = $Description
    $s.Save()
}

function Install-Shortcuts([string]$Dest) {
    Write-Step "Creando accesos directos..."
    $iniciar = Join-Path $Dest "INICIAR.bat"
    $icon = Join-Path $Dest "assets\icon.ico"
    $desktop = [Environment]::GetFolderPath("Desktop")
    New-Shortcut $iniciar (Join-Path $desktop "$AppName $Author.lnk") $Dest $icon "$AppName - $Author"
    $start = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$Author"
    New-Shortcut $iniciar (Join-Path $start "$AppName.lnk") $Dest $icon "$AppName - $Author"
}

function Test-Engine([string]$Python, [string]$Dest) {
    Write-Step "Comprobando motor y base de datos..."
    $runtimeDir = Join-Path $Dest "runtime"
    if (Test-Path (Join-Path $runtimeDir "python.exe")) {
        Configure-EmbedRuntime $runtimeDir
    }
    $code = @"
import sys
sys.path.insert(0, r'''$Dest''')
from app import database, auth
from app.version import APP_NAME, APP_VERSION
database.init_db(); auth.init_auth()
print('OK', APP_NAME, APP_VERSION)
"@
    & $Python -c $code
    if ($LASTEXITCODE -ne 0) { throw "El motor no arranco." }
}

Write-Host "====================================================="
Write-Host " $AppName  Sistema de Asistencia Tecnico"
Write-Host " Instalador para PC  |  $Author"
Write-Host "====================================================="
Write-Host ""
Write-Host "La base de datos existente NO se borra."

$Dest = $Root
if ($Mode -eq "Setup") {
    if ((Split-Path $Root -Leaf) -eq "payload") {
        $Payload = $Root
    } elseif (Test-Path (Join-Path $Root "payload")) {
        $Payload = Join-Path $Root "payload"
    } else {
        $Payload = $Root
    }
    $Dest = Join-Path $env:LOCALAPPDATA "WAMBOOTIC\SATEC"
    Copy-Payload $Payload $Dest
}

$python = Get-Python $Dest
Install-Deps $python $Dest
Test-Engine $python $Dest
Install-Shortcuts $Dest

Write-Host ""
Write-Host "Instalacion lista." -ForegroundColor Green
Write-Host "Carpeta: $Dest"
Write-Host "Abra el acceso directo '$AppName $Author' en el escritorio."
Write-Host "Usuario inicial: admin   contrasena: admin123"
Write-Host "Cambie la contrasena al entrar."
