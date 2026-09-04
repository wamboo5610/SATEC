"""Rutas de datos: local en PC, /tmp en Vercel (sistema de archivos de solo lectura)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_VERCEL = os.environ.get("VERCEL") == "1"


def is_desktop() -> bool:
    return os.environ.get("SISAT_DESKTOP") == "1"


def assets_dir() -> Path:
    return ROOT / "assets"


def icon_path() -> Path:
    ico = assets_dir() / "icon.ico"
    png = assets_dir() / "icon.png"
    return ico if ico.exists() else png


def login_bg_path() -> Path:
    return assets_dir() / "login-bg.jpg"


def get_listen_port() -> int:
    raw = os.environ.get("SISAT_PORT", "8000").strip()
    try:
        port = int(raw)
    except ValueError:
        return 8000
    if 1 <= port <= 65535:
        return port
    return 8000


def _seed_file(src: Path, dest: Path) -> None:
    if src.exists() and not dest.exists():
        shutil.copy2(src, dest)


def get_data_dir() -> Path:
    if IS_VERCEL:
        data_dir = Path("/tmp/sisat-data")
        data_dir.mkdir(parents=True, exist_ok=True)
        _seed_file(ROOT / "data" / "attendance.db", data_dir / "attendance.db")
        _seed_file(ROOT / "data" / "auth.json", data_dir / "auth.json")
        return data_dir
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_offline_downloads_dir() -> Path:
    if IS_VERCEL:
        folder = Path("/tmp/sisat-descargas")
    else:
        folder = ROOT / "DESCARGAS_LOCALES"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get_reportes_dir() -> Path:
    if IS_VERCEL:
        reportes = Path("/tmp/sisat-reportes")
        reportes.mkdir(parents=True, exist_ok=True)
        bundled = ROOT / "REPORTES"
        if bundled.exists():
            for item in bundled.glob("*.xlsx"):
                _seed_file(item, reportes / item.name)
        return reportes
    reportes = ROOT / "REPORTES"
    reportes.mkdir(parents=True, exist_ok=True)
    return reportes