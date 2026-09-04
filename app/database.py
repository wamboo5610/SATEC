import sqlite3
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from collections import defaultdict

from . import attendance_logic as al
from .paths import get_data_dir

DB_PATH = get_data_dir() / "attendance.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sedes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sede_id INTEGER,
                ip TEXT,
                port INTEGER DEFAULT 4370,
                password INTEGER DEFAULT 0,
                serial TEXT,
                mode TEXT DEFAULT 'direct',
                last_sync_at TEXT,
                last_sync_attempt_at TEXT,
                last_sync_ok INTEGER,
                last_sync_users INTEGER,
                last_sync_records_fetched INTEGER,
                last_sync_records_new INTEGER,
                last_sync_error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sede_id) REFERENCES sedes(id)
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT,
                timestamp TEXT NOT NULL,
                status INTEGER DEFAULT 0,
                verify_mode INTEGER DEFAULT 0,
                device_serial TEXT,
                source TEXT DEFAULT 'direct',
                punch_type TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, timestamp, device_serial)
            );
            CREATE TABLE IF NOT EXISTS users_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT,
                privilege TEXT,
                device_serial TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, device_serial)
            );
            CREATE TABLE IF NOT EXISTS adms_devices (
                serial TEXT PRIMARY KEY,
                sede_id INTEGER,
                alias TEXT,
                last_seen TEXT,
                options TEXT,
                online INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS work_schedules (
                sede_id INTEGER PRIMARY KEY,
                entry_time TEXT NOT NULL DEFAULT '08:00',
                exit_time TEXT NOT NULL DEFAULT '17:00',
                lunch_start TEXT NOT NULL DEFAULT '12:00',
                lunch_end TEXT NOT NULL DEFAULT '13:00',
                grace_minutes INTEGER NOT NULL DEFAULT 0,
                lunch_grace_minutes INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sede_id) REFERENCES sedes(id)
            );
            CREATE TABLE IF NOT EXISTS employee_schedules (
                user_id TEXT PRIMARY KEY,
                user_name TEXT,
                entry_time TEXT NOT NULL DEFAULT '08:00',
                exit_time TEXT NOT NULL DEFAULT '17:00',
                lunch_start TEXT NOT NULL DEFAULT '12:00',
                lunch_end TEXT NOT NULL DEFAULT '13:00',
                grace_minutes INTEGER NOT NULL DEFAULT 0,
                lunch_grace_minutes INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holiday_date TEXT NOT NULL,
                name TEXT NOT NULL,
                sede_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sede_id) REFERENCES sedes(id),
                UNIQUE(holiday_date, sede_id)
            );
            CREATE TABLE IF NOT EXISTS offline_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sede_id INTEGER,
                sede_name TEXT,
                device_name TEXT,
                device_ip TEXT,
                device_port INTEGER DEFAULT 4370,
                device_serial TEXT,
                users_count INTEGER DEFAULT 0,
                records_fetched INTEGER DEFAULT 0,
                records_new INTEGER DEFAULT 0,
                snapshot_file TEXT,
                notes TEXT,
                downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sede_id) REFERENCES sedes(id)
            );
            CREATE TABLE IF NOT EXISTS punch_remedies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                work_date TEXT NOT NULL,
                missing_slot TEXT NOT NULL,
                reason TEXT,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, work_date, missing_slot)
            );
        """)
        _migrate(conn)
        _ensure_indexes(conn)
        _recompute_punch_types(conn)
        _dedupe_attendance_storage(conn)


def _dedupe_attendance_storage(conn):
    version = conn.execute("SELECT value FROM meta WHERE key='attendance_dedupe_v1'").fetchone()
    if version and version[0] == "3":
        return
    rows = conn.execute(
        "SELECT id, user_id, user_name, timestamp, status, verify_mode, device_serial, source, punch_type "
        "FROM attendance ORDER BY id"
    ).fetchall()
    if not rows:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('attendance_dedupe_v1', '3') "
            "ON CONFLICT(key) DO UPDATE SET value='3'"
        )
        return
    records = [dict(r) for r in rows]
    keep_ids = {r["id"] for r in al.dedupe_attendance(records)}
    delete_ids = [r["id"] for r in records if r["id"] not in keep_ids]
    for i in range(0, len(delete_ids), 500):
        batch = delete_ids[i:i + 500]
        placeholders = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM attendance WHERE id IN ({placeholders})", batch)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('attendance_dedupe_v1', '3') "
        "ON CONFLICT(key) DO UPDATE SET value='3'"
    )


def delete_attendance_by_device_serial(device_serial: str) -> int:
    serial = str(device_serial or "").strip()
    if not serial:
        return 0
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM attendance WHERE device_serial=?", (serial,))
        return cur.rowcount


def delete_attendance_by_excel_sede(sede_id: int) -> int:
    serial = f"EXCEL-S{int(sede_id)}"
    return delete_attendance_by_device_serial(serial)


def _recompute_punch_types(conn):
    version = conn.execute("SELECT value FROM meta WHERE key='punch_logic_version'").fetchone()
    if version and version[0] == "3":
        return
    rows = conn.execute(
        "SELECT id, user_id, timestamp, status, verify_mode, punch_type FROM attendance ORDER BY user_id, timestamp"
    ).fetchall()
    if not rows:
        return
    records = [dict(r) for r in rows]
    inferred = al.infer_punch_types(records)
    updates = [
        (r["punch_type"], r["verify_mode"], r["id"])
        for r in inferred
        if r.get("punch_type")
    ]
    conn.executemany(
        "UPDATE attendance SET punch_type=?, verify_mode=? WHERE id=?",
        updates,
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('punch_logic_version', '3') "
        "ON CONFLICT(key) DO UPDATE SET value='3'"
    )


def _ensure_indexes(conn):
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_user_ts ON attendance(user_id, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attendance_serial ON attendance(device_serial)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_cache_serial ON users_cache(device_serial)")


def _migrate(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(devices)").fetchall()}
    if "sede_id" not in cols:
        conn.execute("ALTER TABLE devices ADD COLUMN sede_id INTEGER")
    for col, typedef in [
        ("last_sync_at", "TEXT"),
        ("last_sync_attempt_at", "TEXT"),
        ("last_sync_ok", "INTEGER"),
        ("last_sync_users", "INTEGER"),
        ("last_sync_records_fetched", "INTEGER"),
        ("last_sync_records_new", "INTEGER"),
        ("last_sync_error", "TEXT"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE devices ADD COLUMN {col} {typedef}")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(devices)").fetchall()}
    acols = {r[1] for r in conn.execute("PRAGMA table_info(adms_devices)").fetchall()}
    if "sede_id" not in acols:
        conn.execute("ALTER TABLE adms_devices ADD COLUMN sede_id INTEGER")
    if "alias" not in acols:
        conn.execute("ALTER TABLE adms_devices ADD COLUMN alias TEXT")

    if conn.execute("SELECT COUNT(*) FROM sedes").fetchone()[0] == 0:
        conn.execute("INSERT INTO sedes (name) VALUES ('Sede Principal')")
        sede_id = conn.execute("SELECT id FROM sedes WHERE name='Sede Principal'").fetchone()[0]
        conn.execute("UPDATE devices SET sede_id=? WHERE sede_id IS NULL", (sede_id,))
        conn.execute(
            "UPDATE devices SET serial='CKOU231160025' WHERE ip='192.168.0.7' AND (serial IS NULL OR serial='')"
        )

    for row in conn.execute("SELECT id FROM sedes").fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO work_schedules (sede_id) VALUES (?)""",
            (row["id"],),
        )

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "employee_schedules" not in tables:
        conn.execute("""
            CREATE TABLE employee_schedules (
                user_id TEXT PRIMARY KEY,
                user_name TEXT,
                entry_time TEXT NOT NULL DEFAULT '08:00',
                exit_time TEXT NOT NULL DEFAULT '17:00',
                lunch_start TEXT NOT NULL DEFAULT '12:00',
                lunch_end TEXT NOT NULL DEFAULT '13:00',
                grace_minutes INTEGER NOT NULL DEFAULT 0,
                lunch_grace_minutes INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    if not conn.execute("SELECT 1 FROM meta WHERE key='daily_grace_reset_v1'").fetchone():
        conn.execute("UPDATE work_schedules SET grace_minutes=0, lunch_grace_minutes=0")
        conn.execute("UPDATE employee_schedules SET grace_minutes=0, lunch_grace_minutes=0")
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('daily_grace_reset_v1', '1')"
        )
    wcols = {r[1] for r in conn.execute("PRAGMA table_info(work_schedules)").fetchall()}
    for col, default in [
        ("work_monday", 1), ("work_tuesday", 1), ("work_wednesday", 1),
        ("work_thursday", 1), ("work_friday", 1), ("work_saturday", 0), ("work_sunday", 0),
    ]:
        if col not in wcols:
            conn.execute(f"ALTER TABLE work_schedules ADD COLUMN {col} INTEGER NOT NULL DEFAULT {default}")

    if "holidays" not in tables:
        conn.execute("""
            CREATE TABLE holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holiday_date TEXT NOT NULL,
                name TEXT NOT NULL,
                sede_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sede_id) REFERENCES sedes(id),
                UNIQUE(holiday_date, sede_id)
            )
        """)

    if conn.execute("SELECT COUNT(*) FROM holidays").fetchone()[0] == 0:
        from .calendar_rules import PERU_HOLIDAYS_2026
        for d, name in PERU_HOLIDAYS_2026:
            conn.execute(
                "INSERT OR IGNORE INTO holidays (holiday_date, name, sede_id) VALUES (?, ?, NULL)",
                (d, name),
            )

    if "offline_downloads" not in tables:
        conn.execute("""
            CREATE TABLE offline_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sede_id INTEGER,
                sede_name TEXT,
                device_name TEXT,
                device_ip TEXT,
                device_port INTEGER DEFAULT 4370,
                device_serial TEXT,
                users_count INTEGER DEFAULT 0,
                records_fetched INTEGER DEFAULT 0,
                records_new INTEGER DEFAULT 0,
                snapshot_file TEXT,
                notes TEXT,
                downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sede_id) REFERENCES sedes(id)
            )
        """)

    if "punch_remedies" not in tables:
        conn.execute("""
            CREATE TABLE punch_remedies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                work_date TEXT NOT NULL,
                missing_slot TEXT NOT NULL,
                reason TEXT,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, work_date, missing_slot)
            )
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- Sedes ---
def get_sedes():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.*,
                   (SELECT COUNT(*) FROM devices d WHERE d.sede_id=s.id) as devices_count,
                   (SELECT COUNT(*) FROM adms_devices a WHERE a.sede_id=s.id) as adms_count
            FROM sedes s ORDER BY s.name
        """).fetchall()
        return [dict(r) for r in rows]


def save_sede(name):
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO sedes (name) VALUES (?)", (name.strip(),))
        sede_id = cur.lastrowid
        conn.execute("INSERT OR IGNORE INTO work_schedules (sede_id) VALUES (?)", (sede_id,))
        return sede_id


def delete_sede(sede_id):
    with get_conn() as conn:
        conn.execute("UPDATE devices SET sede_id=NULL WHERE sede_id=?", (sede_id,))
        conn.execute("UPDATE adms_devices SET sede_id=NULL WHERE sede_id=?", (sede_id,))
        conn.execute("DELETE FROM sedes WHERE id=?", (sede_id,))


# --- Devices ---
def save_device(name, ip, port=4370, password=0, serial=None, mode="direct", sede_id=None):
    with get_conn() as conn:
        if sede_id is None:
            row = conn.execute("SELECT id FROM sedes ORDER BY id LIMIT 1").fetchone()
            sede_id = row[0] if row else None
        cur = conn.execute(
            "INSERT INTO devices (name, sede_id, ip, port, password, serial, mode) VALUES (?,?,?,?,?,?,?)",
            (name, sede_id, ip, port, password, serial, mode),
        )
        return cur.lastrowid


def update_device(device_id, **kwargs):
    allowed = {
        "name", "sede_id", "ip", "port", "password", "serial",
        "last_sync_at", "last_sync_attempt_at", "last_sync_ok",
        "last_sync_users", "last_sync_records_fetched", "last_sync_records_new",
        "last_sync_error",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE devices SET {sets} WHERE id=?", (*fields.values(), device_id))


def record_device_sync(
    device_id,
    *,
    ok: bool,
    users: int | None = None,
    records_fetched: int | None = None,
    records_new: int | None = None,
    message: str | None = None,
):
    now = datetime.now().isoformat(timespec="seconds")
    if ok:
        update_device(
            device_id,
            last_sync_at=now,
            last_sync_attempt_at=now,
            last_sync_ok=1,
            last_sync_users=int(users or 0),
            last_sync_records_fetched=int(records_fetched or 0),
            last_sync_records_new=int(records_new or 0),
            last_sync_error="",
        )
        return
    update_device(
        device_id,
        last_sync_attempt_at=now,
        last_sync_ok=0,
        last_sync_error=(message or "Error al sincronizar")[:500],
    )


def get_device_local_counts(serial: str | None):
    if not serial:
        return {"local_users": 0, "local_records": 0}
    with get_conn() as conn:
        users = conn.execute(
            "SELECT COUNT(*) as c FROM users_cache WHERE device_serial=?",
            (serial,),
        ).fetchone()["c"]
        recs = conn.execute(
            "SELECT COUNT(*) as c FROM attendance WHERE device_serial=?",
            (serial,),
        ).fetchone()["c"]
        return {"local_users": users, "local_records": recs}


def get_devices():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT d.*, s.name as sede_name,
                   CASE WHEN d.serial IS NULL OR d.serial = '' THEN 0 ELSE (
                       SELECT COUNT(*) FROM users_cache u WHERE u.device_serial = d.serial
                   ) END as local_users,
                   CASE WHEN d.serial IS NULL OR d.serial = '' THEN 0 ELSE (
                       SELECT COUNT(*) FROM attendance a WHERE a.device_serial = d.serial
                   ) END as local_records
            FROM devices d
            LEFT JOIN sedes s ON d.sede_id = s.id
            ORDER BY s.name, d.name
        """).fetchall()
        return [dict(r) for r in rows]


def get_device(device_id):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT d.*, s.name as sede_name FROM devices d
            LEFT JOIN sedes s ON d.sede_id=s.id WHERE d.id=?
        """, (device_id,)).fetchone()
        if not row:
            return None
        device = dict(row)
    device.update(get_device_local_counts(device.get("serial")))
    return device


def get_device_by_serial(serial):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM devices WHERE serial=?", (serial,)).fetchone()
        return dict(row) if row else None


def ensure_excel_device(sede_id, sede_name, device_serial):
    name = f"Excel — {sede_name}"
    existing = get_device_by_serial(device_serial)
    if existing:
        update_device(existing["id"], name=name, sede_id=sede_id)
        return existing["id"]
    return save_device(
        name=name,
        ip="importado",
        serial=device_serial,
        mode="excel",
        sede_id=sede_id,
    )


def delete_device(device_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM devices WHERE id=?", (device_id,))


def get_device_serial_map():
    """Map serial -> {device_name, sede_name, sede_id, device_id}"""
    mapping = {}
    with get_conn() as conn:
        for r in conn.execute("""
            SELECT d.id, d.name as device_name, d.serial, d.sede_id, s.name as sede_name
            FROM devices d LEFT JOIN sedes s ON d.sede_id=s.id WHERE d.serial IS NOT NULL
        """).fetchall():
            mapping[r["serial"]] = dict(r)
        for r in conn.execute("""
            SELECT a.serial, a.alias, a.sede_id, s.name as sede_name
            FROM adms_devices a LEFT JOIN sedes s ON a.sede_id=s.id
        """).fetchall():
            mapping[r["serial"]] = {
                "device_name": r["alias"] or r["serial"],
                "sede_name": r["sede_name"] or "Sin sede",
                "sede_id": r["sede_id"],
                "device_id": None,
            }
    return mapping


def get_serials_for_filters(sede_id=None, device_id=None, device_serial=None, source_mode: str = "auto"):
    if device_serial:
        return [device_serial]
    source_mode = (source_mode or "auto").lower()
    if source_mode == "excel":
        if sede_id:
            return [f"EXCEL-S{int(sede_id)}"]
        with get_conn() as conn:
            return [f"EXCEL-S{r['id']}" for r in conn.execute("SELECT id FROM sedes ORDER BY id").fetchall()]
    serials = []
    with get_conn() as conn:
        if device_id:
            row = conn.execute("SELECT serial FROM devices WHERE id=?", (device_id,)).fetchone()
            if row and row["serial"]:
                serial = row["serial"]
                if source_mode == "device" and str(serial).startswith("EXCEL-"):
                    return []
                return [serial]
        if sede_id:
            for r in conn.execute("SELECT serial FROM devices WHERE sede_id=? AND serial IS NOT NULL", (sede_id,)):
                serials.append(r["serial"])
            for r in conn.execute("SELECT serial FROM adms_devices WHERE sede_id=?", (sede_id,)):
                serials.append(r["serial"])
    serials = list(set(serials))
    if source_mode == "device":
        serials = [s for s in serials if not str(s).startswith("EXCEL-")]
    return serials


def attendance_exists(conn, user_id, timestamp) -> bool:
    row = conn.execute(
        "SELECT 1 FROM attendance WHERE user_id=? AND timestamp=? LIMIT 1",
        (str(user_id), timestamp),
    ).fetchone()
    return row is not None


def insert_attendance(records):
    if not records:
        return 0
    with get_conn() as conn:
        user_ids = list({str(r.get("user_id") or "") for r in records if r.get("user_id")})
        existing = set()
        for i in range(0, len(user_ids), 400):
            batch = user_ids[i:i + 400]
            placeholders = ",".join("?" * len(batch))
            for row in conn.execute(
                f"SELECT user_id, timestamp FROM attendance WHERE user_id IN ({placeholders})",
                batch,
            ):
                existing.add((str(row["user_id"]), str(row["timestamp"])))
        rows = []
        seen = set()
        for r in records:
            uid = str(r.get("user_id") or "")
            ts = str(r.get("timestamp") or "")
            if not uid or not ts:
                continue
            key = (uid, ts)
            if key in existing or key in seen:
                continue
            seen.add(key)
            rows.append((
                uid,
                r.get("user_name"),
                ts,
                r.get("status", 0),
                r.get("verify_mode", 0),
                r.get("device_serial"),
                r.get("source", "direct"),
                r.get("punch_type"),
            ))
        if rows:
            conn.executemany(
                """INSERT OR IGNORE INTO attendance
                   (user_id, user_name, timestamp, status, verify_mode, device_serial, source, punch_type)
                   VALUES (?,?,?,?,?,?,?,?)""",
                rows,
            )
        return len(rows)


def backfill_attendance_names(name_map: dict) -> int:
    if not name_map:
        return 0
    updated = 0
    with get_conn() as conn:
        for uid, name in name_map.items():
            label = (name or "").strip()
            if not label:
                continue
            cur = conn.execute(
                "UPDATE attendance SET user_name=? WHERE user_id=? AND IFNULL(user_name,'') != ?",
                (label, str(uid), label),
            )
            updated += cur.rowcount or 0
    return updated


def get_attendance(date_from=None, date_to=None, user_id=None, device_serial=None,
                   sede_id=None, device_id=None, limit=50000, source_mode: str = "auto"):
    serials = get_serials_for_filters(sede_id, device_id, device_serial, source_mode)
    q = "SELECT * FROM attendance WHERE 1=1"
    params = []
    if date_from:
        q += " AND timestamp >= ?"
        params.append(date_from)
    if date_to:
        q += " AND timestamp <= ?"
        params.append(date_to + " 23:59:59")
    if user_id:
        q += " AND user_id = ?"
        params.append(user_id)
    if serials:
        placeholders = ",".join("?" * len(serials))
        q += f" AND device_serial IN ({placeholders})"
        params.extend(serials)
    elif sede_id or device_id:
        return []
    q += " ORDER BY user_id, timestamp ASC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    mode = (source_mode or "auto").lower()
    if mode in ("device", "excel"):
        return al.dedupe_attendance_records(rows)
    return al.dedupe_attendance(rows)


def get_report_summary(
    date_from=None, date_to=None, sede_id=None, device_id=None, user_id=None, source_mode: str = "auto"
):
    rows = get_attendance(
        date_from, date_to, user_id, sede_id=sede_id, device_id=device_id, limit=100000, source_mode=source_mode
    )
    device_map = get_device_serial_map()
    rows = al.infer_punch_types([
        {**al.enrich_record(r), **{
            "sede_name": device_map.get(r.get("device_serial") or "", {}).get("sede_name", "Sin sede"),
            "device_name": device_map.get(r.get("device_serial") or "", {}).get("device_name", "—"),
        }}
        for r in rows
    ])
    by_person = defaultdict(lambda: {"count": 0, "name": "", "sede": "", "device": ""})
    by_sede = defaultdict(lambda: {"count": 0, "persons": set()})
    for r in rows:
        sede = r.get("sede_name", "Sin sede")
        uid = r["user_id"]
        by_person[uid]["count"] += 1
        by_person[uid]["name"] = r.get("user_name") or by_person[uid]["name"]
        by_person[uid]["sede"] = sede
        by_person[uid]["device"] = r.get("device_name", "—")
        by_sede[sede]["count"] += 1
        by_sede[sede]["persons"].add(uid)
    return {
        "total_records": len(rows),
        "total_persons": len(by_person),
        "by_sede": [{"sede": k, "records": v["count"], "persons": len(v["persons"])} for k, v in sorted(by_sede.items())],
        "by_person": sorted([
            {"user_id": k, "name": v["name"], "sede": v["sede"], "device": v["device"], "records": v["count"]}
            for k, v in by_person.items()
        ], key=lambda x: x["name"] or x["user_id"]),
    }


def upsert_users(users, device_serial=None):
    if not users:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        (str(u.get("user_id") or ""), u.get("name"), u.get("privilege"), device_serial, now)
        for u in users
        if u.get("user_id") not in (None, "")
    ]
    if not rows:
        return 0
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO users_cache (user_id, name, privilege, device_serial, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id, device_serial) DO UPDATE SET
               name=excluded.name, privilege=excluded.privilege, updated_at=excluded.updated_at""",
            rows,
        )
    return len(rows)


def get_users(device_serial=None, sede_id=None):
    with get_conn() as conn:
        if sede_id:
            serials = get_serials_for_filters(sede_id=sede_id)
            if not serials:
                return []
            ph = ",".join("?" * len(serials))
            rows = conn.execute(f"SELECT * FROM users_cache WHERE device_serial IN ({ph}) ORDER BY name", serials).fetchall()
        elif device_serial:
            rows = conn.execute("SELECT * FROM users_cache WHERE device_serial=?", (device_serial,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM users_cache ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def upsert_adms_device(serial, options=None, sede_id=None, alias=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO adms_devices (serial, sede_id, alias, last_seen, options, online)
               VALUES (?, ?, ?, ?, ?, 1)
               ON CONFLICT(serial) DO UPDATE SET
               last_seen=?, options=COALESCE(?, options), online=1,
               sede_id=COALESCE(excluded.sede_id, adms_devices.sede_id),
               alias=COALESCE(excluded.alias, adms_devices.alias)""",
            (serial, sede_id, alias, datetime.now().isoformat(), options,
             datetime.now().isoformat(), options),
        )


def update_adms_device(serial, sede_id=None, alias=None):
    with get_conn() as conn:
        if sede_id is not None:
            conn.execute("UPDATE adms_devices SET sede_id=? WHERE serial=?", (sede_id, serial))
        if alias:
            conn.execute("UPDATE adms_devices SET alias=? WHERE serial=?", (alias, serial))


def get_adms_devices():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.*, s.name as sede_name FROM adms_devices a
            LEFT JOIN sedes s ON a.sede_id=s.id ORDER BY a.last_seen DESC
        """).fetchall()
        return [dict(r) for r in rows]


def attendance_stats():
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM attendance").fetchone()["c"]
        today = conn.execute(
            "SELECT COUNT(*) as c FROM attendance WHERE date(timestamp)=date('now','localtime')"
        ).fetchone()["c"]
        users = conn.execute("SELECT COUNT(DISTINCT user_id) as c FROM attendance").fetchone()["c"]
        sedes = conn.execute("SELECT COUNT(*) as c FROM sedes").fetchone()["c"]
        registered = conn.execute("SELECT COUNT(*) as c FROM users_cache").fetchone()["c"]
        devices = conn.execute("SELECT COUNT(*) as c FROM devices").fetchone()["c"]
        return {
            "total": total, "today": today, "unique_users": users,
            "sedes": sedes, "registered_employees": registered, "devices": devices,
        }


def _enrich_with_device_map(rows, device_map):
    enriched = []
    for r in rows:
        row = al.enrich_record(dict(r))
        dev = device_map.get(r.get("device_serial") or "", {})
        row["sede_name"] = dev.get("sede_name", "Sin sede")
        row["sede_id"] = dev.get("sede_id")
        row["device_name"] = dev.get("device_name", r.get("device_serial") or "—")
        enriched.append(row)
    return al.infer_punch_types(enriched)


def get_sede_name_to_id():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM sedes").fetchall()
        return {r["name"]: r["id"] for r in rows}


def get_work_schedules():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ws.*, s.name as sede_name
            FROM work_schedules ws
            JOIN sedes s ON s.id = ws.sede_id
            ORDER BY s.name
        """).fetchall()
        return [dict(r) for r in rows]


def get_schedules_by_sede_id():
    return {s["sede_id"]: s for s in get_work_schedules()}


def get_work_schedule(sede_id: int):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT ws.*, s.name as sede_name
            FROM work_schedules ws
            JOIN sedes s ON s.id = ws.sede_id
            WHERE ws.sede_id = ?
        """, (sede_id,)).fetchone()
        return dict(row) if row else None


def get_holidays(sede_id=None):
    with get_conn() as conn:
        if sede_id is None:
            rows = conn.execute("""
                SELECT h.*, s.name as sede_name
                FROM holidays h
                LEFT JOIN sedes s ON s.id = h.sede_id
                ORDER BY h.holiday_date, h.name
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT h.*, s.name as sede_name
                FROM holidays h
                LEFT JOIN sedes s ON s.id = h.sede_id
                WHERE h.sede_id IS NULL OR h.sede_id = ?
                ORDER BY h.holiday_date, h.name
            """, (sede_id,)).fetchall()
        return [dict(r) for r in rows]


def get_holiday_dates_lookup():
    global_dates: set[str] = set()
    by_sede: dict[int, set[str]] = {}
    with get_conn() as conn:
        for r in conn.execute("SELECT holiday_date, sede_id FROM holidays").fetchall():
            if r["sede_id"] is None:
                global_dates.add(r["holiday_date"])
            else:
                by_sede.setdefault(r["sede_id"], set()).add(r["holiday_date"])
    return global_dates, by_sede


def holidays_for_sede(sede_id, global_dates, by_sede):
    dates = set(global_dates)
    if sede_id is not None:
        dates |= by_sede.get(sede_id, set())
    return dates


def save_holiday(holiday_date: str, name: str, sede_id=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO holidays (holiday_date, name, sede_id) VALUES (?, ?, ?)",
            (holiday_date.strip(), name.strip(), sede_id),
        )
        return cur.lastrowid


def delete_holiday(holiday_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM holidays WHERE id=?", (holiday_id,))


def seed_peru_holidays_2026():
    from .calendar_rules import PERU_HOLIDAYS_2026
    added = 0
    with get_conn() as conn:
        for d, name in PERU_HOLIDAYS_2026:
            cur = conn.execute(
                "INSERT OR IGNORE INTO holidays (holiday_date, name, sede_id) VALUES (?, ?, NULL)",
                (d, name),
            )
            added += cur.rowcount
    return added


def save_work_schedule(sede_id, **kwargs):
    allowed = {
        "entry_time", "exit_time", "lunch_start", "lunch_end",
        "grace_minutes", "lunch_grace_minutes",
        "work_monday", "work_tuesday", "work_wednesday", "work_thursday",
        "work_friday", "work_saturday", "work_sunday",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO work_schedules (sede_id) VALUES (?)",
            (sede_id,),
        )
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE work_schedules SET {sets} WHERE sede_id=?",
            (*fields.values(), sede_id),
        )


def get_employee_schedules():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM employee_schedules ORDER BY COALESCE(user_name, user_id), user_id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_schedules_by_user_id():
    return {s["user_id"]: s for s in get_employee_schedules()}


def get_employee_schedule(user_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM employee_schedules WHERE user_id=?",
            (str(user_id),),
        ).fetchone()
        return dict(row) if row else None


def save_employee_schedule(user_id, user_name=None, **kwargs):
    allowed = {
        "entry_time", "exit_time", "lunch_start", "lunch_end",
        "grace_minutes", "lunch_grace_minutes", "notes",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    fields["updated_at"] = datetime.now().isoformat()
    if user_name is not None:
        fields["user_name"] = user_name
    uid = str(user_id)
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT user_id FROM employee_schedules WHERE user_id=?",
            (uid,),
        ).fetchone()
        if existing:
            if not fields:
                return
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE employee_schedules SET {sets} WHERE user_id=?",
                (*fields.values(), uid),
            )
            return
        defaults = dict(al.DEFAULT_SCHEDULE)
        defaults.update(fields)
        conn.execute(
            """INSERT INTO employee_schedules
               (user_id, user_name, entry_time, exit_time, lunch_start, lunch_end,
                grace_minutes, lunch_grace_minutes, notes, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                uid,
                user_name or "",
                defaults["entry_time"],
                defaults["exit_time"],
                defaults["lunch_start"],
                defaults["lunch_end"],
                defaults["grace_minutes"],
                defaults["lunch_grace_minutes"],
                defaults.get("notes"),
                defaults["updated_at"],
            ),
        )


def delete_employee_schedule(user_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM employee_schedules WHERE user_id=?", (str(user_id),))


# --- Subsanaciones de marcaciones (RRHH) ---
def get_punch_remedies(date_from=None, date_to=None, user_id=None):
    clauses, params = [], []
    if date_from:
        clauses.append("work_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("work_date <= ?")
        params.append(date_to)
    if user_id:
        clauses.append("user_id = ?")
        params.append(str(user_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM punch_remedies {where} ORDER BY work_date DESC, user_id",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def get_punch_remedies_lookup(date_from=None, date_to=None, user_id=None):
    lookup: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in get_punch_remedies(date_from, date_to, user_id):
        lookup[(str(row["user_id"]), str(row["work_date"]))].append(row)
    return dict(lookup)


def save_punch_remedy(user_id: str, work_date: str, missing_slot: str, reason: str | None = None, created_by: str | None = None):
    slot = str(missing_slot).strip()
    if slot not in al.VALID_PUNCH_SLOTS:
        raise ValueError(f"Marcación inválida: {missing_slot}")
    uid = str(user_id).strip()
    if not uid or not work_date:
        raise ValueError("Empleado y fecha son obligatorios")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO punch_remedies (user_id, work_date, missing_slot, reason, created_by)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, work_date, missing_slot) DO UPDATE SET
                 reason=excluded.reason,
                 created_by=excluded.created_by,
                 created_at=CURRENT_TIMESTAMP""",
            (uid, work_date, slot, (reason or "").strip() or None, created_by),
        )
        row = conn.execute(
            "SELECT * FROM punch_remedies WHERE user_id=? AND work_date=? AND missing_slot=?",
            (uid, work_date, slot),
        ).fetchone()
        return dict(row) if row else None


def delete_punch_remedy(remedy_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM punch_remedies WHERE id=?", (remedy_id,))


def get_tardiness_report(
    date_from=None, date_to=None, sede_id=None, device_id=None, user_id=None, source_mode: str = "auto"
):
    global_dates, by_sede = get_holiday_dates_lookup()
    rows = get_attendance(
        date_from, date_to, user_id, sede_id=sede_id, device_id=device_id, limit=100000, source_mode=source_mode
    )
    device_map = get_device_serial_map()
    enriched = _enrich_with_device_map(rows, device_map)
    schedules = get_schedules_by_sede_id()
    emp_schedules = get_schedules_by_user_id()
    sede_map = get_sede_name_to_id()
    remedies = get_punch_remedies_lookup(date_from, date_to, user_id)
    report = al.build_tardiness_report(
        enriched, schedules, sede_map, emp_schedules, global_dates, by_sede, remedies
    )
    return al.aggregate_tardiness_report(report)


MONTH_NAMES_ES = (
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


def get_monthly_tardiness_report(year: int, month: int, sede_id=None, device_id=None, user_id=None):
    from calendar import monthrange

    if month < 1 or month > 12:
        raise ValueError("Mes inválido")
    last_day = monthrange(year, month)[1]
    date_from = f"{year:04d}-{month:02d}-01"
    date_to = f"{year:04d}-{month:02d}-{last_day:02d}"
    data = get_tardiness_report(date_from, date_to, sede_id, device_id, user_id)
    data["year"] = year
    data["month"] = month
    data["month_label"] = f"{MONTH_NAMES_ES[month]} {year}"
    data["date_from"] = date_from
    data["date_to"] = date_to
    return data


def get_punch_observations_report(
    date_from=None, date_to=None, sede_id=None, device_id=None, user_id=None, source_mode: str = "auto"
):
    data = get_tardiness_report(date_from, date_to, sede_id, device_id, user_id, source_mode)
    ok_states = ("Completo", "Completo subsanado", "No aplica", "")
    issues = [
        d for d in data.get("details", [])
        if d.get("dia_laborable")
        and d.get("estado_marcaciones") not in ok_states
    ]
    return {
        "total_issues": len(issues),
        "three_punch_days": sum(1 for d in issues if d.get("estado_marcaciones") == "Tres marcaciones"),
        "incomplete_days": sum(
            1 for d in issues
            if "Incompleto" in (d.get("estado_marcaciones") or "")
            and d.get("estado_marcaciones") != "Tres marcaciones"
        ),
        "excess_days": sum(1 for d in issues if "Exceso" in (d.get("estado_marcaciones") or "")),
        "no_punch_days": sum(1 for d in issues if d.get("estado_marcaciones") == "Sin marcaciones"),
        "subsanado_days": sum(
            1 for d in data.get("details", [])
            if d.get("dia_laborable") and d.get("estado_marcaciones") == "Completo subsanado"
        ),
        "details": sorted(issues, key=lambda x: (x.get("date", ""), x.get("user_name") or x.get("user_id", ""))),
    }


def get_daily_attendance(date=None, sede_id=None, device_id=None):
    target = date or datetime.now().strftime("%Y-%m-%d")
    global_dates, by_sede = get_holiday_dates_lookup()
    rows = get_attendance(date_from=target, date_to=target, sede_id=sede_id, device_id=device_id, limit=100000)
    device_map = get_device_serial_map()
    enriched = _enrich_with_device_map(rows, device_map)
    schedules = get_schedules_by_sede_id()
    emp_schedules = get_schedules_by_user_id()
    sede_map = get_sede_name_to_id()
    remedies = get_punch_remedies_lookup(target, target)
    summary = al.build_daily_summary(
        enriched, schedules, sede_map, emp_schedules, global_dates, by_sede, remedies
    )
    laborable = [p for p in summary if p.get("dia_laborable", True)]
    late_count = sum(1 for p in laborable if p.get("tardanza_total_minutos", 0) > 0)
    late_minutes = sum(p.get("tardanza_total_minutos", 0) for p in laborable)
    return {
        "date": target,
        "summary": summary,
        "total_persons": len({r["user_id"] for r in enriched}),
        "total_punches": len(enriched),
        "late_persons": late_count,
        "late_minutes": late_minutes,
    }


def get_dashboard_data():
    device_map = get_device_serial_map()
    today = datetime.now().strftime("%Y-%m-%d")

    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM attendance").fetchone()["c"]
        registered = conn.execute("SELECT COUNT(*) as c FROM users_cache").fetchone()["c"]
        devices = conn.execute("SELECT COUNT(*) as c FROM devices").fetchone()["c"]
        adms_devices = conn.execute("SELECT COUNT(*) as c FROM adms_devices").fetchone()["c"]
        sedes_count = conn.execute("SELECT COUNT(*) as c FROM sedes").fetchone()["c"]

        week_rows = conn.execute("""
            SELECT date(timestamp) as d, COUNT(*) as c
            FROM attendance WHERE date(timestamp) >= date('now','localtime','-6 days')
            GROUP BY date(timestamp) ORDER BY d
        """).fetchall()

        recent = conn.execute("""
            SELECT user_id, user_name, timestamp, status, verify_mode, device_serial, punch_type
            FROM attendance ORDER BY timestamp DESC LIMIT 12
        """).fetchall()

    today_rows = get_attendance(date_from=today, date_to=today, limit=100000)
    today_enriched = _enrich_with_device_map(today_rows, device_map)

    today_punches = len(today_enriched)
    today_persons = len({r["user_id"] for r in today_enriched})
    today_entries = sum(1 for r in today_enriched if r.get("punch_type") == "Entrada")
    today_exits = sum(1 for r in today_enriched if r.get("punch_type") == "Salida")

    global_dates, by_sede = get_holiday_dates_lookup()
    schedules = get_schedules_by_sede_id()
    emp_schedules = get_schedules_by_user_id()
    sede_map = get_sede_name_to_id()
    remedies = get_punch_remedies_lookup(today, today)
    today_summary = al.build_daily_summary(
        today_enriched, schedules, sede_map, emp_schedules, global_dates, by_sede, remedies
    )
    today_laborable = [p for p in today_summary if p.get("dia_laborable", True)]
    today_late_persons = sum(1 for p in today_laborable if p.get("tardanza_total_minutos", 0) > 0)
    today_late_minutes = sum(p.get("tardanza_total_minutos", 0) for p in today_laborable)

    sede_data = defaultdict(lambda: {"punches": 0, "persons": set(), "entries": 0, "exits": 0})
    for r in today_enriched:
        sede = r.get("sede_name", "Sin sede")
        sede_data[sede]["punches"] += 1
        sede_data[sede]["persons"].add(r["user_id"])
        if r.get("punch_type") == "Entrada":
            sede_data[sede]["entries"] += 1
        elif r.get("punch_type") == "Salida":
            sede_data[sede]["exits"] += 1

    by_sede_today = [
        {
            "sede": k,
            "punches": v["punches"],
            "persons": len(v["persons"]),
            "entries": v["entries"],
            "exits": v["exits"],
        }
        for k, v in sorted(sede_data.items(), key=lambda x: -x[1]["punches"])
    ]

    recent_enriched = _enrich_with_device_map([dict(r) for r in recent], device_map)

    return {
        "total_records": total,
        "today_punches": today_punches,
        "today_persons": today_persons,
        "today_entries": today_entries,
        "today_exits": today_exits,
        "today_late_persons": today_late_persons,
        "today_late_minutes": today_late_minutes,
        "registered_employees": registered,
        "devices": devices + adms_devices,
        "sedes": sedes_count,
        "by_sede_today": by_sede_today,
        "week_trend": [{"date": r["d"], "count": r["c"]} for r in week_rows],
        "recent": [
            {
                "user_id": r["user_id"],
                "user_name": r["user_name"] or "",
                "timestamp": r["timestamp"],
                "punch_type": r.get("punch_type", "Marcación"),
                "verify_label": r.get("verify_label", "Biométrico"),
                "sede_name": r.get("sede_name", "—"),
            }
            for r in recent_enriched
        ],
    }


def save_offline_download(**kwargs):
    allowed = {
        "sede_id", "sede_name", "device_name", "device_ip", "device_port",
        "device_serial", "users_count", "records_fetched", "records_new",
        "snapshot_file", "notes",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    cols = ", ".join(fields)
    placeholders = ", ".join("?" * len(fields))
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO offline_downloads ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return cur.lastrowid


def get_offline_downloads():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT o.*, s.name as sede_label
            FROM offline_downloads o
            LEFT JOIN sedes s ON s.id = o.sede_id
            ORDER BY o.downloaded_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_offline_download(download_id: int):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT o.*, s.name as sede_label
            FROM offline_downloads o
            LEFT JOIN sedes s ON s.id = o.sede_id
            WHERE o.id = ?
        """, (download_id,)).fetchone()
        return dict(row) if row else None


def delete_offline_download(download_id: int, *, purge_attendance: bool = True) -> dict:
    row = get_offline_download(download_id)
    if not row:
        return {"deleted": 0, "attendance_removed": 0}
    attendance_removed = 0
    if purge_attendance and row.get("device_serial"):
        attendance_removed = delete_attendance_by_device_serial(row["device_serial"])
    with get_conn() as conn:
        conn.execute("DELETE FROM offline_downloads WHERE id=?", (download_id,))
    return {"deleted": 1, "attendance_removed": attendance_removed, "device_serial": row.get("device_serial")}


def get_device_by_ip(ip: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM devices WHERE ip=? ORDER BY id DESC LIMIT 1", (ip,)).fetchone()
        return dict(row) if row else None