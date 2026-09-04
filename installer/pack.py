"""Empaqueta el instalador ZIP de SISAT — WAMBOO TIC."""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.version import APP_VERSION, AUTHOR  # noqa: E402

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
}
SKIP_FILES = {".pyc", ".pyo"}
COPY_DIRS = ("app", "desktop", "assets", "installer")
COPY_FILES = (
    "main.py",
    "run.py",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "INICIAR.bat",
    "INICIAR_CONSOLA.bat",
    "INICIAR_SERVIDOR.bat",
    "INSTALAR.bat",
    "DESINSTALAR.bat",
    "CREAR_INSTALADOR.bat",
    "PUBLICAR_GITHUB.bat",
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
    name = f"SISAT-{APP_VERSION}-Instalador-{AUTHOR.replace(' ', '-')}"
    out_dir = DIST / name
    if DIST.exists():
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

    shutil.copy2(ROOT / "installer" / "setup.py", out_dir / "setup.py")
    (out_dir / "SETUP.bat").write_text(
        "\r\n".join(
            [
                "@echo off",
                "chcp 65001 >nul",
                "cd /d \"%~dp0\"",
                "title Instalar SISAT — WAMBOO TIC",
                "where python >nul 2>nul",
                "if errorlevel 1 (",
                "  echo No se encontro Python 3.12 o superior.",
                "  echo Descarguelo en https://www.python.org/downloads/",
                "  echo Marque Add python.exe to PATH.",
                "  pause",
                "  exit /b 1",
                ")",
                "python setup.py",
                "if errorlevel 1 pause",
            ]
        )
        + "\r\n",
        encoding="utf-8",
    )
    (out_dir / "LEAME.txt").write_text(
        f"SISAT {APP_VERSION} — {AUTHOR}\n\n"
        "1. Instale Python 3.12 o superior (con PATH).\n"
        "2. Ejecute SETUP.bat\n"
        "3. Acceso inicial: admin / admin123\n"
        "4. Cambie la contraseña al entrar.\n",
        encoding="utf-8",
    )

    zip_path = DIST / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in out_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(DIST))
    return zip_path


if __name__ == "__main__":
    path = build()
    print(path)
