import ipaddress
from datetime import datetime

try:
    from zk import ZK, const
    ZK_AVAILABLE = True
except ImportError:
    ZK = None
    const = None
    ZK_AVAILABLE = False

from .paths import IS_VERCEL
from .attendance_logic import (
    STATUS_MAP,
    VERIFY_MAP,
    enrich_record,
    infer_punch_types,
    resolve_verify_mode,
)


def _is_private_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.strip()).is_private
    except ValueError:
        return False


def _connection_hint(ip: str) -> str | None:
    if IS_VERCEL and _is_private_ip(ip):
        return (
            "Estás en Vercel (nube). No puede alcanzar IPs locales como 192.168.x.x. "
            "Para probar el reloj en tu red: ejecuta INICIAR.bat en tu PC y abre "
            "http://127.0.0.1:8000. Para relojes remotos usa Conexión Remota (ADMS) "
            "con la URL https://sisat.vercel.app/iclock/"
        )
    if IS_VERCEL:
        return (
            "La conexión directa al puerto 4370 solo funciona en la misma red local. "
            "Desde Vercel usa Conexión Remota (ADMS)."
        )
    return None


def connect_device(ip, port=4370, password=0, timeout=10):
    if not ZK_AVAILABLE:
        raise RuntimeError(
            "Módulo de reloj biométrico no disponible: falta instalar pyzk. "
            "Cierra el servidor (Ctrl+C), ejecuta INICIAR.bat de nuevo "
            "(instala dependencias automáticamente) y recarga la página (F5)."
        )
    hint = _connection_hint(ip)
    if hint:
        raise RuntimeError(hint)
    zk = ZK(ip, port=port, timeout=timeout, password=password, force_udp=False, ommit_ping=True)
    conn = zk.connect()
    return zk, conn


def safe_disable(conn) -> bool:
    try:
        conn.disable_device()
        return True
    except Exception:
        return False


def safe_enable(conn) -> bool:
    try:
        conn.enable_device()
        return True
    except Exception:
        return False


def resolve_serial(info: dict | None, fallback=None) -> str | None:
    serial = (info or {}).get("serial")
    if serial and str(serial).strip() and str(serial).strip().upper() not in {"N/A", "NONE", "UNKNOWN"}:
        return str(serial).strip()
    fallback = str(fallback).strip() if fallback else ""
    return fallback or None


def get_device_info(conn):
    info = {}
    try:
        info["firmware"] = conn.get_firmware_version()
    except Exception:
        info["firmware"] = "N/A"
    try:
        info["serial"] = conn.get_serialnumber()
    except Exception:
        info["serial"] = "N/A"
    try:
        info["platform"] = conn.get_platform()
    except Exception:
        info["platform"] = "N/A"
    try:
        info["device_name"] = conn.get_device_name()
    except Exception:
        info["device_name"] = "Reloj biométrico"
    try:
        info["face_version"] = conn.get_face_version()
    except Exception:
        info["face_version"] = "N/A"
    try:
        info["mac"] = conn.get_mac()
    except Exception:
        info["mac"] = "N/A"
    try:
        info["time"] = str(conn.get_time())
    except Exception:
        info["time"] = "N/A"
    try:
        conn.read_sizes()
        info["users_count"] = conn.users
        info["records_count"] = conn.records
        info["users_capacity"] = conn.users_cap
    except Exception:
        pass
    return info


def fetch_users(conn):
    users = []
    for u in conn.get_users():
        privilege = "Admin" if u.privilege == const.USER_ADMIN else "Usuario"
        users.append({
            "user_id": str(u.user_id),
            "uid": u.uid,
            "name": u.name or f"Usuario {u.user_id}",
            "privilege": privilege,
        })
    return users


def fetch_attendance(conn, device_serial=None):
    records = []
    for att in conn.get_attendance():
        ts = att.timestamp
        if isinstance(ts, datetime):
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_str = str(ts)
        status = getattr(att, "status", 0) or 0
        verify = getattr(att, "punch", 0) or getattr(att, "verify_type", 0) or 0
        records.append(enrich_record({
            "user_id": str(att.user_id),
            "timestamp": ts_str,
            "status": status,
            "verify_mode": resolve_verify_mode(status, verify),
            "device_serial": device_serial,
            "source": "direct",
        }))
    return infer_punch_types(records)


def sync_time(conn):
    conn.set_time(datetime.now())


def _friendly_error(ip: str, exc: Exception) -> str:
    msg = str(exc).strip()
    lower = msg.lower()
    if "can't reach device (ping" in lower:
        return (
            f"No responde al ping ({ip}). Verifica IP y que el reloj esté encendido. "
            "Si el ping está bloqueado por el firewall, la conexión TCP se intentará igual. "
            "Confirma que tu PC y el reloj están en la misma red."
        )
    if "timed out" in lower or "timeout" in lower:
        hint = (
            f"Tiempo de espera agotado al conectar con {ip}:{4370}. "
            "Revisa IP, puerto (4370), firewall y que ambos estén en la misma red."
        )
        if ip.strip().startswith("190.168."):
            hint += (
                " ¿Quisiste decir 192.168.? "
                f"Prueba con 192.168.{ip.strip().split('.', 2)[-1]}"
            )
        return hint
    if "connection refused" in lower or "10061" in lower:
        return f"Conexión rechazada en {ip}. Verifica el puerto (4370) y la contraseña del reloj."
    return msg


def friendly_error(ip: str, exc: Exception) -> str:
    return _friendly_error(ip, exc)


def test_connection(ip, port=4370, password=0, timeout=8):
    zk = None
    conn = None
    try:
        zk, conn = connect_device(ip, port, password, timeout)
        info = get_device_info(conn)
        conn.enable_device()
        return {"ok": True, "info": info}
    except Exception as e:
        return {"ok": False, "error": _friendly_error(ip, e)}
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass