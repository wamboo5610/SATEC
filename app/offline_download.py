"""Descarga local desde reloj ZKTeco sin internet (laptop por cable de red)."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from . import database as db
from . import reports as rep
from . import zk_device as zk
from .paths import get_offline_downloads_dir


def _users_csv_bytes(users: list[dict]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Nombre", "Privilegio", "Reloj (serial)", "Sede"])
    for u in users:
        writer.writerow([
            u.get("user_id"),
            u.get("name") or "",
            u.get("privilege") or "",
            u.get("device_serial") or "",
            u.get("sede_name") or "",
        ])
    return output.getvalue().encode("utf-8-sig")


def _attendance_csv_bytes(rows: list[dict]) -> bytes:
    headers = ["ID", "Nombre", "Fecha/Hora", "Tipo", "Verificación", "Sede", "Reloj", "Origen"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([
            r.get("user_id"),
            r.get("user_name") or "",
            r.get("timestamp"),
            r.get("punch_type", ""),
            r.get("verify_label", ""),
            r.get("sede_name", ""),
            r.get("device_name", ""),
            r.get("source") or "",
        ])
    return output.getvalue().encode("utf-8-sig")


def _ensure_device(sede_id: int, ip: str, port: int, password: int, serial: str, device_name: str | None) -> int:
    existing = db.get_device_by_serial(serial) if serial else None
    if not existing:
        existing = db.get_device_by_ip(ip)
    label = (device_name or "").strip() or f"Reloj {ip}"
    if existing:
        db.update_device(
            existing["id"],
            name=label,
            sede_id=sede_id,
            ip=ip,
            port=port,
            password=password,
            serial=serial or existing.get("serial"),
        )
        return existing["id"]
    return db.save_device(label, ip, port, password, serial=serial, sede_id=sede_id)


def _build_snapshot(
    *,
    sede_name: str,
    device_name: str,
    ip: str,
    port: int,
    serial: str,
    users: list[dict],
    records: list[dict],
    users_count: int,
    records_fetched: int,
    records_new: int,
    notes: str | None,
) -> str:
    folder = get_offline_downloads_dir()
    safe_sede = "".join(c if c.isalnum() or c in "-_" else "_" for c in sede_name)[:30]
    safe_serial = (serial or "sin_serial")[:20]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"descarga_{safe_sede}_{safe_serial}_{stamp}.zip"
    path = folder / filename

    device_map = db.get_device_serial_map()
    enriched = rep.enrich_rows(records, device_map)
    for u in users:
        dev = device_map.get(serial, {})
        u["sede_name"] = dev.get("sede_name", sede_name)

    meta = {
        "app": "SISAT",
        "version": "2.0.0",
        "downloaded_at": datetime.now().isoformat(),
        "sede_name": sede_name,
        "device_name": device_name,
        "device_ip": ip,
        "device_port": port,
        "device_serial": serial,
        "users_count": users_count,
        "records_fetched": records_fetched,
        "records_new": records_new,
        "notes": notes or "",
    }

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        zf.writestr("usuarios.csv", _users_csv_bytes(users))
        zf.writestr("asistencia.csv", _attendance_csv_bytes(enriched))

    return filename


def download_and_store(
    ip: str,
    port: int = 4370,
    password: int = 0,
    sede_id: int | None = None,
    device_name: str | None = None,
    notes: str | None = None,
) -> dict:
    sedes = {s["id"]: s["name"] for s in db.get_sedes()}
    if sede_id not in sedes:
        raise ValueError("Sede no encontrada. Crea la sede antes de descargar.")
    sede_name = sedes[sede_id]

    conn = None
    try:
        _, conn = zk.connect_device(ip, port, password, timeout=30)
        conn.disable_device()
        info = zk.get_device_info(conn)
        serial = info.get("serial") or "UNKNOWN"
        users = zk.fetch_users(conn)
        records = zk.fetch_attendance(conn, serial)
        name_map = {u["user_id"]: u["name"] for u in users}
        for r in records:
            r["user_name"] = name_map.get(r["user_id"])
        conn.enable_device()
    except Exception as e:
        raise RuntimeError(str(e)) from e
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass

    device_id = _ensure_device(sede_id, ip, port, password, serial, device_name)
    db.upsert_users(users, serial)
    records_new = db.insert_attendance(records)

    label = (device_name or "").strip() or f"Reloj {ip}"
    snapshot_file = _build_snapshot(
        sede_name=sede_name,
        device_name=label,
        ip=ip,
        port=port,
        serial=serial,
        users=users,
        records=records,
        users_count=len(users),
        records_fetched=len(records),
        records_new=records_new,
        notes=notes,
    )

    download_id = db.save_offline_download(
        sede_id=sede_id,
        sede_name=sede_name,
        device_name=label,
        device_ip=ip,
        device_port=port,
        device_serial=serial,
        users_count=len(users),
        records_fetched=len(records),
        records_new=records_new,
        snapshot_file=snapshot_file,
        notes=notes,
    )

    return {
        "ok": True,
        "download_id": download_id,
        "device_id": device_id,
        "sede_id": sede_id,
        "sede_name": sede_name,
        "device_name": label,
        "device_serial": serial,
        "users": len(users),
        "records_fetched": len(records),
        "records_new": records_new,
        "snapshot_file": snapshot_file,
        "snapshot_path": str(get_offline_downloads_dir() / snapshot_file),
        "device_info": info,
        "message": (
            f"Descarga guardada: {records_new} registros nuevos de {len(records)} totales. "
            f"Los datos quedaron en la base local y en DESCARGAS_LOCALES/{snapshot_file}"
        ),
    }


def resolve_snapshot_path(filename: str) -> Path:
    folder = get_offline_downloads_dir().resolve()
    path = (folder / filename).resolve()
    if not str(path).startswith(str(folder)):
        raise ValueError("Ruta de archivo inválida")
    if not path.exists():
        raise FileNotFoundError("Archivo de descarga no encontrado")
    return path