"""Actualización del sistema desde GitHub."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .paths import ROOT, get_data_dir
from .version import APP_VERSION, GITHUB_BRANCH, GITHUB_OWNER, GITHUB_REPO, GITHUB_URL

SETTINGS_NAME = "update.json"
STAGE_DIRNAME = "_update_stage"
VERSION_RE = re.compile(r'APP_VERSION\s*=\s*["\']([\d.]+)["\']')
ALLOWED_DIRS = ("app", "desktop", "assets", "installer", "herramientas")
ALLOWED_FILES = (
    "main.py",
    "run.py",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "INICIAR.bat",
    "INSTALAR.bat",
)
SKIP_NAMES = {
    "__pycache__",
    "venv",
    "data",
    "dist",
    "recursos",
    ".git",
    "webview",
    "web",
    "runtime",
    "EXT",
}


def settings_path() -> Path:
    return get_data_dir() / SETTINGS_NAME


CACHE_KEYS = (
    "last_check_at",
    "last_remote_version",
    "last_update_available",
    "last_message",
    "dismissed_version",
)


def load_settings() -> dict:
    data = {
        "repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
        "branch": GITHUB_BRANCH,
        "github_token": (os.environ.get("SATEC_GITHUB_TOKEN") or os.environ.get("SISAT_GITHUB_TOKEN") or "").strip(),
    }
    path = settings_path()
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                if stored.get("repo"):
                    data["repo"] = str(stored["repo"]).strip().strip("/")
                if stored.get("branch"):
                    data["branch"] = str(stored["branch"]).strip()
                if stored.get("github_token"):
                    data["github_token"] = str(stored["github_token"]).strip()
                for key in CACHE_KEYS:
                    if key in stored:
                        data[key] = stored[key]
        except (json.JSONDecodeError, OSError):
            pass
    return data


def save_settings(*, github_token: str | None = None, repo: str | None = None, branch: str | None = None) -> dict:
    data = load_settings()
    if github_token is not None:
        data["github_token"] = github_token.strip()
    if repo is not None:
        data["repo"] = repo.strip().strip("/")
    if branch is not None:
        data["branch"] = branch.strip() or GITHUB_BRANCH
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return public_settings(data)


def public_settings(data: dict | None = None) -> dict:
    data = data or load_settings()
    token = data.get("github_token") or ""
    return {
        "current_version": APP_VERSION,
        "has_token": bool(token),
    }


def parse_version(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value or "")
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_newer(remote: str, local: str) -> bool:
    r, l = parse_version(remote), parse_version(local)
    n = max(len(r), len(l))
    r += (0,) * (n - len(r))
    l += (0,) * (n - len(l))
    return r > l


def _headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SATEC-WAMBOOTIC-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in (401, 403, 404):
            raise RuntimeError("No hay una versión nueva disponible por ahora.") from exc
        raise RuntimeError("No se pudo consultar actualizaciones.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("No se pudo consultar actualizaciones.") from exc


def _http_bytes(url: str, token: str) -> bytes:
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise RuntimeError("No se pudo descargar la actualización.") from exc
        raise RuntimeError("No se pudo descargar la actualización.") from exc


def _version_from_text(text: str) -> str | None:
    match = VERSION_RE.search(text)
    return match.group(1) if match else None


def check_for_update() -> dict:
    settings = load_settings()
    repo = settings["repo"]
    branch = settings["branch"]
    token = settings["github_token"]
    result = {
        **public_settings(settings),
        "update_available": False,
        "remote_version": None,
        "source": None,
        "notes": "",
        "download_url": None,
        "message": "Estás en la última versión.",
    }
    release = None
    try:
        release = _http_json(f"https://api.github.com/repos/{repo}/releases/latest", token)
        if release.get("message") == "Not Found":
            release = None
    except RuntimeError:
        release = None

    remote_version = None
    notes = ""
    download_url = None
    source = None
    if release and not release.get("draft"):
        tag = str(release.get("tag_name") or "").lstrip("vV")
        remote_version = tag or None
        notes = (release.get("body") or "").strip()
        download_url = release.get("zipball_url")
        source = "release"
        assets = release.get("assets") or []
        for asset in assets:
            name = (asset.get("name") or "").lower()
            if name.endswith(".zip") and "source" not in name:
                download_url = asset.get("browser_download_url") or download_url
                break

    if not remote_version:
        payload = _http_json(
            f"https://api.github.com/repos/{repo}/contents/app/version.py?ref={branch}",
            token,
        )
        import base64

        content = payload.get("content") or ""
        text = base64.b64decode(content.replace("\n", "")).decode("utf-8", errors="replace") if payload.get("encoding") == "base64" else content
        remote_version = _version_from_text(text)
        download_url = f"https://api.github.com/repos/{repo}/zipball/{branch}"
        source = "branch"
        notes = f"Último código en la rama {branch}."

    if not remote_version:
        raise RuntimeError("No se encontró APP_VERSION en GitHub. Sube app/version.py al repositorio.")

    result["remote_version"] = remote_version
    result["source"] = source
    result["notes"] = notes
    result["download_url"] = download_url
    if is_newer(remote_version, APP_VERSION):
        result["update_available"] = True
        result["message"] = f"Hay una versión nueva: {remote_version} (tú tienes {APP_VERSION})."
    elif remote_version == APP_VERSION:
        result["message"] = f"Ya estás en la versión {APP_VERSION}."
    else:
        result["message"] = f"Tu versión local ({APP_VERSION}) es más reciente que GitHub ({remote_version})."
    return result


def _store_notice(info: dict) -> None:
    data = load_settings()
    data["last_check_at"] = time.time()
    data["last_remote_version"] = info.get("remote_version")
    data["last_update_available"] = bool(info.get("update_available"))
    data["last_message"] = info.get("message") or ""
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def notice_for_ui(*, force: bool = False, max_age_hours: float = 6) -> dict:
    """Consulta GitHub (con caché) para avisar si hay versión nueva. Nunca lanza error a la UI."""
    settings = load_settings()
    last = float(settings.get("last_check_at") or 0)
    age = time.time() - last
    if not force and last and age < max_age_hours * 3600 and settings.get("last_remote_version"):
        available = bool(settings.get("last_update_available"))
        remote = settings.get("last_remote_version")
        dismissed = settings.get("dismissed_version")
        return {
            **public_settings(settings),
            "update_available": available and dismissed != remote,
            "remote_version": remote,
            "message": settings.get("last_message") or "",
            "cached": True,
        }
    try:
        info = check_for_update()
        _store_notice(info)
        dismissed = load_settings().get("dismissed_version")
        if info.get("update_available") and dismissed == info.get("remote_version"):
            info = {**info, "update_available": False, "dismissed": True}
        return {**info, "cached": False}
    except Exception as exc:
        return {
            **public_settings(settings),
            "update_available": False,
            "remote_version": settings.get("last_remote_version"),
            "message": str(exc),
            "offline": True,
            "cached": True,
        }


def dismiss_notice(version: str | None = None) -> dict:
    data = load_settings()
    data["dismissed_version"] = (version or data.get("last_remote_version") or "").strip()
    settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return public_settings(data)


def _copy_allowed(src_root: Path, dest: Path) -> list[str]:
    copied: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for folder in ALLOWED_DIRS:
        origin = src_root / folder
        if not origin.is_dir():
            continue
        target = dest / folder
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            origin,
            target,
            ignore=shutil.ignore_patterns(*SKIP_NAMES, "*.pyc"),
        )
        copied.append(folder + "/")
    for name in ALLOWED_FILES:
        origin = src_root / name
        if origin.is_file():
            shutil.copy2(origin, dest / name)
            copied.append(name)
    return copied


def _find_payload_root(extracted: Path) -> Path:
    entries = [p for p in extracted.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(entries) == 1 and (entries[0] / "app").exists():
        return entries[0]
    if (extracted / "app").exists():
        return extracted
    for path in extracted.rglob("app"):
        if path.is_dir() and (path / "version.py").exists():
            return path.parent
    raise RuntimeError("El ZIP de GitHub no tiene la carpeta app/")


def download_and_stage(download_url: str | None = None) -> dict:
    info = check_for_update()
    url = download_url or info.get("download_url")
    if not url:
        raise RuntimeError("No hay URL de descarga")
    if not info.get("update_available"):
        return {**info, "staged": False}
    settings = load_settings()
    raw = _http_bytes(url, settings["github_token"])
    data_dir = get_data_dir()
    stage = data_dir / STAGE_DIRNAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix="satec-upd-"))
    try:
        zip_path = tmp / "update.zip"
        zip_path.write_bytes(raw)
        extract_dir = tmp / "unpack"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        payload = _find_payload_root(extract_dir)
        copied = _copy_allowed(payload, stage)
        (stage / "update_meta.json").write_text(
            json.dumps(
                {
                    "remote_version": info.get("remote_version"),
                    "downloaded_at": time.time(),
                    "copied": copied,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return {**info, "staged": True, "copied": copied, "stage": str(stage)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _write_apply_script(stage: Path, pid: int) -> Path:
    bat = get_data_dir() / "apply_update.bat"
    root = ROOT
    venv_py = root / "venv" / "Scripts" / "python.exe"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f"set ROOT={root}",
        f"set STAGE={stage}",
        f"set PID={pid}",
        "echo Aplicando actualizacion SATEC...",
        "timeout /t 2 /nobreak >nul",
        "taskkill /PID %PID% /F >nul 2>&1",
        "timeout /t 2 /nobreak >nul",
        'if exist "%STAGE%\\app" xcopy /E /Y /I "%STAGE%\\app" "%ROOT%\\app\\" >nul',
        'if exist "%STAGE%\\desktop" xcopy /E /Y /I "%STAGE%\\desktop" "%ROOT%\\desktop\\" >nul',
        'if exist "%STAGE%\\assets" xcopy /E /Y /I "%STAGE%\\assets" "%ROOT%\\assets\\" >nul',
        'if exist "%STAGE%\\installer" xcopy /E /Y /I "%STAGE%\\installer" "%ROOT%\\installer\\" >nul',
        'if exist "%STAGE%\\herramientas" xcopy /E /Y /I "%STAGE%\\herramientas" "%ROOT%\\herramientas\\" >nul',
        'if exist "%STAGE%\\main.py" copy /Y "%STAGE%\\main.py" "%ROOT%\\main.py" >nul',
        'if exist "%STAGE%\\run.py" copy /Y "%STAGE%\\run.py" "%ROOT%\\run.py" >nul',
        'if exist "%STAGE%\\requirements.txt" copy /Y "%STAGE%\\requirements.txt" "%ROOT%\\requirements.txt" >nul',
        'if exist "%STAGE%\\pyproject.toml" copy /Y "%STAGE%\\pyproject.toml" "%ROOT%\\pyproject.toml" >nul',
        'if exist "%STAGE%\\README.md" copy /Y "%STAGE%\\README.md" "%ROOT%\\README.md" >nul',
        'if exist "%STAGE%\\INICIAR.bat" copy /Y "%STAGE%\\INICIAR.bat" "%ROOT%\\INICIAR.bat" >nul',
        'if exist "%STAGE%\\INSTALAR.bat" copy /Y "%STAGE%\\INSTALAR.bat" "%ROOT%\\INSTALAR.bat" >nul',
        f'if exist "{venv_py}" "{venv_py}" -m pip install -r "%ROOT%\\requirements.txt" -q',
        'if exist "%ROOT%\\runtime\\python.exe" "%ROOT%\\runtime\\python.exe" -m pip install -r "%ROOT%\\requirements.txt" -q',
        'rmdir /s /q "%STAGE%" >nul 2>&1',
        'start "" "%ROOT%\\INICIAR.bat"',
        "exit",
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return bat


def apply_and_restart() -> dict:
    info = download_and_stage()
    if not info.get("staged"):
        return info
    stage = Path(info["stage"])
    bat = _write_apply_script(stage, os.getpid())
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        f'start "" "{bat}"',
        cwd=str(ROOT),
        shell=True,
        close_fds=True,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        **info,
        "restarting": True,
        "message": f"Descargada la versión {info.get('remote_version')}. SATEC se reiniciará para aplicarla. La base de datos no se toca.",
    }
