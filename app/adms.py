import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from . import database as db
from .attendance_logic import STATUS_MAP, enrich_record, infer_punch_types, resolve_verify_mode

command_queues: dict[str, list[str]] = {}
command_id_counter = 0


def get_serial_from_request(request) -> str:
    sn = request.query_params.get("SN") or request.headers.get("SN", "")
    if not sn and request.url.path:
        parts = request.url.path.strip("/").split("/")
        if len(parts) > 1:
            sn = parts[-1]
    return sn.strip()


def parse_attendance_body(body: str, serial: str) -> list[dict]:
    records = []
    for line in body.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("USER") or "USERINFO" in line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            parts = re.split(r"\s+", line)
        if len(parts) < 2:
            continue
        user_id = parts[0].strip()
        ts_raw = parts[1].strip()
        status = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else 0
        verify = int(parts[3]) if len(parts) > 3 and parts[3].strip().isdigit() else 15
        try:
            if ts_raw.isdigit():
                ts = datetime.fromtimestamp(int(ts_raw)).strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            ts = ts_raw
        records.append(enrich_record({
            "user_id": user_id,
            "timestamp": ts,
            "status": status,
            "verify_mode": resolve_verify_mode(status, verify),
            "device_serial": serial,
            "source": "adms",
        }))
    return infer_punch_types(records)


def parse_registry_body(body: str) -> dict:
    info = {}
    for part in body.replace("\n", ",").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            info[k.strip().lstrip("~")] = v.strip()
    return info


def queue_command(serial: str, command: str) -> int:
    global command_id_counter
    command_id_counter += 1
    command_queues.setdefault(serial, []).append(f"C:{command_id_counter}:{command}")
    return command_id_counter


def drain_commands(serial: str) -> str:
    cmds = command_queues.pop(serial, [])
    return "\n".join(cmds) + ("\n" if cmds else "OK")


def handle_cdata_get(serial: str) -> str:
    db.upsert_adms_device(serial)
    return "OK"


def handle_cdata_post(body: str, serial: str, table: str = "") -> tuple[str, int]:
    db.upsert_adms_device(serial)
    inserted = 0
    if "ATTLOG" in table.upper() or "\t" in body:
        records = parse_attendance_body(body, serial)
        if records:
            inserted = db.insert_attendance(records)
    if "OPERLOG" not in table.upper() and "USER" in body.upper():
        pass
    return "OK", inserted


def handle_registry(body: str, serial: str) -> str:
    info = parse_registry_body(body) if body else {}
    import json
    db.upsert_adms_device(serial, json.dumps(info) if info else None)
    return "OK"


def handle_getrequest(serial: str) -> str:
    db.upsert_adms_device(serial)
    return drain_commands(serial) or "OK"


def handle_devicecmd(body: str) -> str:
    return "OK"