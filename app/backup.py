"""Exportar y restaurar la base de datos local."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from . import database as db
from .auth import AUTH_PATH
from .database import DB_PATH
from .paths import get_data_dir
from .version import APP_NAME, APP_VERSION, AUTHOR

MAX_RESTORE_BYTES = 80 * 1024 * 1024


def _checkpoint() -> None:
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def backup_info() -> dict:
    size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    mtime = None
    if DB_PATH.exists():
        mtime = datetime.fromtimestamp(DB_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    stats = {}
    try:
        stats = db.attendance_stats()
    except Exception:
        stats = {}
    return {
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "size_bytes": size,
        "size_label": _size_label(size),
        "modified_at": mtime,
        "auth_exists": AUTH_PATH.exists(),
        "stats": stats,
        "version": APP_VERSION,
    }


def _size_label(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def build_backup_zip() -> tuple[io.BytesIO, str]:
    _checkpoint()
    data_dir = get_data_dir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if DB_PATH.exists():
            zf.write(DB_PATH, "attendance.db")
        for extra in ("attendance.db-wal", "attendance.db-shm"):
            extra_path = DB_PATH.parent / extra
            if extra_path.exists() and extra_path.stat().st_size:
                zf.write(extra_path, extra)
        if AUTH_PATH.exists():
            zf.write(AUTH_PATH, "auth.json")
        meta = {
            "exported_at": datetime.now().isoformat(),
            "version": APP_VERSION,
            "app": APP_NAME,
            "author": AUTHOR,
            "kind": "satec-database",
        }
        zf.writestr("backup_meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
    buf.seek(0)
    fname = f"satec_base_datos_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    return buf, fname


def export_to_path(path: str | Path) -> dict:
    dest = Path(path).expanduser()
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    buf, fname = build_backup_zip()
    dest.write_bytes(buf.getvalue())
    return {"ok": True, "path": str(dest), "filename": dest.name or fname, "size_bytes": dest.stat().st_size}


def restore_from_bytes(content: bytes) -> dict:
    if not content:
        raise ValueError("El archivo está vacío")
    if len(content) > MAX_RESTORE_BYTES:
        raise ValueError("Archivo demasiado grande (máx. 80 MB)")
    data_dir = get_data_dir()
    tmp_dir = data_dir / "_restore_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    try:
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise ValueError("Archivo ZIP inválido") from exc
        with zf:
            names = zf.namelist()
            db_name = next((n for n in names if n.replace("\\", "/").endswith("attendance.db") and not n.endswith("/")), None)
            if not db_name:
                raise ValueError("El ZIP no contiene attendance.db")
            zf.extractall(tmp_dir)
        db_src = tmp_dir / Path(db_name).name
        if not db_src.exists():
            matches = list(tmp_dir.rglob("attendance.db"))
            if not matches:
                raise ValueError("No se encontró attendance.db en el ZIP")
            db_src = matches[0]
        try:
            test_conn = sqlite3.connect(str(db_src))
            test_conn.execute("SELECT 1 FROM sqlite_master")
            test_conn.close()
        except sqlite3.Error as exc:
            raise ValueError(f"Base de datos inválida: {exc}") from exc
        _checkpoint()
        shutil.copy2(db_src, DB_PATH)
        auth_src = db_src.parent / "auth.json"
        if not auth_src.exists():
            found_auth = list(tmp_dir.rglob("auth.json"))
            auth_src = found_auth[0] if found_auth else None
        if auth_src and auth_src.exists():
            shutil.copy2(auth_src, AUTH_PATH)
        db.init_db()
        return {"ok": True, "message": "Base de datos restaurada correctamente"}
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def restore_from_path(path: str | Path) -> dict:
    src = Path(path).expanduser()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError("No se encontró el archivo de respaldo")
    if src.suffix.lower() != ".zip":
        raise ValueError("El respaldo debe ser un archivo .zip")
    return restore_from_bytes(src.read_bytes())
