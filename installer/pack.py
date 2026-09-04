"""Empaqueta el instalador ZIP de SATEC — WAMBOO TIC."""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.version import APP_NAME, APP_VERSION, AUTHOR  # noqa: E402

DIST = ROOT / "dist"
SKIP_DIRS = {
    "venv",
    "__pycache__",
    "dist",
    "recursos",
    ".git",
    "webview",
    "EXT",
    "DESCARGAS_LOCALES",
    "REPORTES",
    "data",
    "web",
    "runtime",
}
COPY_DIRS = ("app", "desktop", "assets", "installer", "herramientas")
COPY_FILES = (
    "main.py",
    "run.py",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "INICIAR.bat",
    "INSTALAR.bat",
)


def _copy_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in SKIP_DIRS or item.name.endswith(".pyc"):
            continue
        target = dest / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            shutil.copy2(item, target)


def build() -> Path:
    name = f"{APP_NAME}-{APP_VERSION}-Instalador-{AUTHOR.replace(' ', '-')}"
    out_dir = DIST / name
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    payload = out_dir / "payload"
    payload.mkdir(parents=True)

    for folder in COPY_DIRS:
        src = ROOT / folder
        if src.is_dir():
            _copy_tree(src, payload / folder)
    for name_file in COPY_FILES:
        src = ROOT / name_file
        if src.exists():
            shutil.copy2(src, payload / name_file)
    (payload / "data").mkdir(exist_ok=True)
    (payload / "data" / ".gitkeep").write_text("", encoding="utf-8")

    (out_dir / "INSTALAR.bat").write_text(
        "\r\n".join(
            [
                "@echo off",
                "chcp 65001 >nul",
                "cd /d \"%~dp0\"",
                "title Instalar SATEC — WAMBOO TIC",
                "echo.",
                "echo  SATEC  Sistema de Asistencia Tecnico",
                "echo  Instalador para PC  |  WAMBOO TIC",
                "echo  Si ya habia SATEC, se conserva la base de datos.",
                "echo.",
                "powershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0payload\\installer\\instalar.ps1\" -Mode Setup",
                "if errorlevel 1 (",
                "  echo.",
                "  echo La instalacion no termino. Revise internet y vuelva a intentar.",
                "  pause",
                "  exit /b 1",
                ")",
                "pause",
            ]
        )
        + "\r\n",
        encoding="utf-8",
    )
    (out_dir / "LEAME.txt").write_text(
        f"SATEC {APP_VERSION} — {AUTHOR}\n\n"
        "INSTALAR EN CUALQUIER PC\n"
        "1. Descomprima este ZIP.\n"
        "2. Doble clic en INSTALAR.bat\n"
        "3. Si la PC no tiene Python, el instalador descarga una copia portatil.\n"
        "4. Se crea el acceso directo 'SATEC WAMBOO TIC' en el escritorio.\n"
        "5. Acceso inicial: admin / admin123  (cambielo al entrar).\n\n"
        "Reinstalar o actualizar NO borra la base de datos.\n"
        "Las nuevas versiones se avisan dentro de SATEC (Sistema).\n",
        encoding="utf-8",
    )

    zip_path = DIST / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in out_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(DIST))
    return zip_path, out_dir


def build_exe(out_dir: Path) -> Path:
    import PyInstaller.__main__

    exe_name = f"{APP_NAME}-{APP_VERSION}-Instalador-{AUTHOR.replace(' ', '-')}"
    icon = ROOT / "assets" / "icon.ico"
    payload = out_dir / "payload"
    work = DIST / "_pyi_work"
    spec = DIST / "_pyi_spec"
    args = [
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        f"--name={exe_name}",
        f"--distpath={DIST}",
        f"--workpath={work}",
        f"--specpath={spec}",
        "--add-data",
        f"{payload}{os.pathsep}payload",
    ]
    if icon.exists():
        args.append(f"--icon={icon}")
    args.append(str(ROOT / "installer" / "exe_setup.py"))
    PyInstaller.__main__.run(args)
    exe = DIST / f"{exe_name}.exe"
    if not exe.exists():
        raise FileNotFoundError("No se generó el .exe del instalador")
    short = DIST / f"{APP_NAME}-Instalador.exe"
    shutil.copy2(exe, short)
    return exe


if __name__ == "__main__":
    zip_path, folder = build()
    print(zip_path)
    try:
        exe = build_exe(folder)
        print(exe)
    except Exception as exc:
        print("ZIP listo. El EXE no se pudo generar:", exc)
        raise
