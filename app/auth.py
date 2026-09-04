import hashlib
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .paths import get_data_dir, IS_VERCEL

AUTH_PATH = get_data_dir() / "auth.json"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
PBKDF2_ITERATIONS = 260_000
AUTH_VERSION = 2
MIN_PASSWORD_LENGTH = 8

PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/status",
}

PASSWORD_CHANGE_ALLOWED = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
    "/api/auth/me",
    "/api/auth/change-password",
}

ADMIN_ONLY_PREFIXES = (
    "/api/backup",
    "/api/update/check",
    "/api/update/install",
    "/api/update/settings",
)

_LOGIN_WINDOW = 300
_LOGIN_MAX = 8
_login_attempts: dict[str, list[float]] = {}

MODULES = {
    "dashboard": "Dashboard",
    "attendance": "Control de Asistencia",
    "excel": "Importar Excel",
    "recursos": "Tardanzas RRHH",
    "offline": "Descarga Local",
    "employees": "Empleados",
    "sedes": "Sedes",
    "devices": "Relojes Biométricos",
    "schedules": "Horarios Laborales",
    "internet": "Conexión Remota",
    "accounts": "Usuarios del sistema",
}

ALL_MODULES = list(MODULES.keys())
DEFAULT_USER_MODULES = [
    "dashboard",
    "attendance",
    "employees",
    "offline",
]


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return digest.hex()


def _load_auth() -> dict:
    if not AUTH_PATH.exists():
        return {}
    try:
        return json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_auth(data: dict) -> None:
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(AUTH_PATH, 0o600)
    except OSError:
        pass


def validate_password(password: str) -> tuple[bool, str]:
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
    if not re.search(r"[A-Za-z]", password):
        return False, "La contraseña debe incluir al menos una letra"
    if not re.search(r"\d", password):
        return False, "La contraseña debe incluir al menos un número"
    if password.strip() != password:
        return False, "La contraseña no puede empezar ni terminar con espacios"
    return True, ""


def _normalize_username(username: str) -> str:
    return username.strip().lower()


def _new_user_record(
    username: str,
    password: str,
    *,
    role: str = "user",
    display_name: str | None = None,
    modules: list[str] | None = None,
    active: bool = True,
    must_change_password: bool = False,
) -> dict:
    salt = secrets.token_hex(16)
    mods = ALL_MODULES.copy() if role == "admin" else _sanitize_modules(modules or DEFAULT_USER_MODULES)
    return {
        "id": uuid.uuid4().hex,
        "username": _normalize_username(username),
        "display_name": (display_name or username).strip(),
        "role": "admin" if role == "admin" else "user",
        "active": active,
        "salt": salt,
        "password_hash": _hash_password(password, salt),
        "modules": mods,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "must_change_password": must_change_password,
    }


def _sanitize_modules(modules: list[str]) -> list[str]:
    clean = []
    for mod in modules:
        if mod in MODULES and mod != "accounts" and mod not in clean:
            clean.append(mod)
    return clean or DEFAULT_USER_MODULES.copy()


def _migrate_auth(data: dict) -> dict:
    if data.get("version", 1) >= AUTH_VERSION and data.get("users"):
        return data

    session_secret = data.get("session_secret") or secrets.token_hex(32)
    users: list[dict] = []

    if data.get("username") and data.get("password_hash"):
        users.append({
            "id": uuid.uuid4().hex,
            "username": _normalize_username(data["username"]),
            "display_name": "Administrador",
            "role": "admin",
            "active": True,
            "salt": data.get("salt", ""),
            "password_hash": data.get("password_hash", ""),
            "modules": ALL_MODULES.copy(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "must_change_password": _normalize_username(data["username"]) == DEFAULT_USERNAME,
        })
    else:
        users.append(_new_user_record(
            DEFAULT_USERNAME,
            DEFAULT_PASSWORD,
            role="admin",
            display_name="Administrador",
            must_change_password=True,
        ))

    migrated = {
        "version": AUTH_VERSION,
        "session_secret": session_secret,
        "users": users,
    }
    _save_auth(migrated)
    return migrated


def _apply_vercel_env_admin(data: dict) -> dict:
    """En Vercel, aplica usuario/clave desde variables de entorno del panel."""
    if not IS_VERCEL:
        return data
    password = os.environ.get("SISAT_ADMIN_PASSWORD", "").strip()
    if not password:
        return data
    ok, _ = validate_password(password)
    if not ok:
        return data
    username = _normalize_username(os.environ.get("SISAT_ADMIN_USER", DEFAULT_USERNAME))
    users = data.get("users", [])
    admin = next((u for u in users if u.get("role") == "admin"), None)
    salt = secrets.token_hex(16)
    if admin:
        admin["username"] = username
        admin["salt"] = salt
        admin["password_hash"] = _hash_password(password, salt)
        admin["must_change_password"] = False
        admin["active"] = True
    else:
        users.append(_new_user_record(
            username,
            password,
            role="admin",
            display_name="Administrador",
            must_change_password=False,
        ))
        data["users"] = users
    _save_auth(data)
    return data


def init_auth() -> None:
    data = _migrate_auth(_load_auth())
    if not data.get("session_secret"):
        data["session_secret"] = secrets.token_hex(32)
        _save_auth(data)
    _apply_vercel_env_admin(_load_auth())


def get_secret_key() -> str:
    env_key = os.environ.get("SESSION_SECRET", "").strip()
    if env_key:
        return env_key
    data = _load_auth()
    key = data.get("session_secret")
    if not key:
        key = secrets.token_hex(32)
        data["session_secret"] = key
        _save_auth(data)
    return key


def _find_user(username: str) -> dict | None:
    data = _load_auth()
    target = _normalize_username(username)
    for user in data.get("users", []):
        if user.get("username") == target:
            return user
    return None


def _find_user_by_id(user_id: str) -> dict | None:
    data = _load_auth()
    for user in data.get("users", []):
        if user.get("id") == user_id:
            return user
    return None


def _update_user_record(user_id: str, **fields) -> dict | None:
    data = _load_auth()
    updated = None
    for user in data.get("users", []):
        if user.get("id") != user_id:
            continue
        for key, value in fields.items():
            if value is not None:
                user[key] = value
        updated = user
        break
    if updated:
        _save_auth(data)
    return updated


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def login_allowed(key: str) -> tuple[bool, int]:
    now = time.time()
    stamps = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[key] = stamps
    if len(stamps) >= _LOGIN_MAX:
        oldest = min(stamps)
        wait = int(_LOGIN_WINDOW - (now - oldest))
        return False, max(wait, 1)
    return True, 0


def record_login_failure(key: str) -> None:
    _login_attempts.setdefault(key, []).append(time.time())


def clear_login_failures(key: str) -> None:
    _login_attempts.pop(key, None)


def _is_admin_only_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in ADMIN_ONLY_PREFIXES)


def is_authenticated(request) -> bool:
    if "session" not in request.scope:
        return False
    return bool(request.session.get("user_id"))


def verify_credentials(username: str, password: str) -> dict | None:
    user = _find_user(username)
    if not user or not user.get("active", True):
        return None
    salt = user.get("salt", "")
    stored = user.get("password_hash", "")
    if not secrets.compare_digest(_hash_password(password, salt), stored):
        return None
    return user


def login_user(request, user: dict) -> None:
    request.session["user_id"] = user["id"]
    request.session["user"] = user["username"]
    request.session["role"] = user.get("role", "user")
    request.session["modules"] = (
        ALL_MODULES if user.get("role") == "admin" else _sanitize_modules(user.get("modules", []))
    )
    request.session["must_change_password"] = bool(user.get("must_change_password"))


def logout_user(request) -> None:
    request.session.clear()


def get_current_user(request) -> str | None:
    return request.session.get("user")


def get_current_user_record(request) -> dict | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return _find_user_by_id(user_id)


def is_admin(request) -> bool:
    return request.session.get("role") == "admin"


def get_user_modules(request) -> list[str]:
    if is_admin(request):
        return ALL_MODULES.copy()
    return _sanitize_modules(request.session.get("modules", []))


def has_module(request, module: str) -> bool:
    if is_admin(request):
        return True
    return module in get_user_modules(request)


def path_module(path: str) -> str | None:
    if path.startswith("/api/auth/accounts"):
        return "accounts"
    if path.startswith("/api/auth/"):
        return None
    if path.startswith("/api/server-info"):
        return None
    if path.startswith("/api/dashboard") or path.startswith("/api/stats"):
        return "dashboard"
    if path.startswith("/api/attendance") or path.startswith("/api/reports"):
        return "attendance"
    if path.startswith("/api/excel"):
        return "excel"
    if path.startswith("/api/recursos"):
        return "recursos"
    if path.startswith("/api/offline"):
        return "offline"
    if path.startswith("/api/users") or path.startswith("/api/employee-schedules"):
        return "employees"
    if path.startswith("/api/sedes"):
        return "sedes"
    if path.startswith("/api/devices"):
        return "devices"
    if path.startswith("/api/backup") or path.startswith("/api/update") or path.startswith("/api/system"):
        return None
    if path.startswith("/api/schedules") or path.startswith("/api/holidays"):
        return "schedules"
    if path.startswith("/api/adms"):
        return "internet"
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not is_public_path(path):
            if not is_authenticated(request):
                return JSONResponse({"detail": "No autorizado"}, status_code=401)
            if request.session.get("must_change_password") and path not in PASSWORD_CHANGE_ALLOWED:
                return JSONResponse(
                    {"detail": "Debe cambiar la contraseña antes de continuar"},
                    status_code=403,
                )
            if _is_admin_only_path(path) and not is_admin(request):
                return JSONResponse({"detail": "Solo administradores"}, status_code=403)
            module = path_module(path)
            if module == "accounts" and not is_admin(request):
                return JSONResponse({"detail": "Solo administradores"}, status_code=403)
            if module and not has_module(request, module):
                return JSONResponse({"detail": "No tienes permiso para este módulo"}, status_code=403)
        return await call_next(request)


def user_public_view(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": user.get("role", "user"),
        "active": user.get("active", True),
        "modules": ALL_MODULES if user.get("role") == "admin" else _sanitize_modules(user.get("modules", [])),
        "created_at": user.get("created_at"),
        "must_change_password": bool(user.get("must_change_password")),
    }


def list_accounts() -> list[dict]:
    data = _load_auth()
    return [user_public_view(u) for u in data.get("users", [])]


def create_account(
    username: str,
    password: str,
    *,
    display_name: str | None = None,
    modules: list[str] | None = None,
    role: str = "user",
    active: bool = True,
) -> tuple[dict | None, str]:
    username = _normalize_username(username)
    if not username or len(username) < 3:
        return None, "El usuario debe tener al menos 3 caracteres"
    if not re.fullmatch(r"[a-z0-9._-]+", username):
        return None, "Usuario inválido (usa letras, números, punto, guión)"
    ok, msg = validate_password(password)
    if not ok:
        return None, msg
    data = _load_auth()
    if any(u.get("username") == username for u in data.get("users", [])):
        return None, "Ese nombre de usuario ya existe"
    user = _new_user_record(
        username,
        password,
        role="admin" if role == "admin" else "user",
        display_name=display_name,
        modules=modules,
        active=active,
        must_change_password=True,
    )
    data.setdefault("users", []).append(user)
    _save_auth(data)
    return user_public_view(user), "Usuario creado"


def update_account(
    user_id: str,
    *,
    display_name: str | None = None,
    modules: list[str] | None = None,
    active: bool | None = None,
    role: str | None = None,
) -> tuple[dict | None, str]:
    user = _find_user_by_id(user_id)
    if not user:
        return None, "Usuario no encontrado"
    updates: dict = {}
    if display_name is not None:
        updates["display_name"] = display_name.strip() or user["username"]
    if active is not None:
        updates["active"] = active
    if role is not None and role in ("admin", "user"):
        updates["role"] = role
        if role == "admin":
            updates["modules"] = ALL_MODULES.copy()
    if modules is not None and (role or user.get("role")) != "admin":
        updates["modules"] = _sanitize_modules(modules)
    if not updates:
        return user_public_view(user), "Sin cambios"
    if updates.get("active") is False:
        admins = [u for u in _load_auth().get("users", []) if u.get("role") == "admin" and u.get("active", True)]
        if user.get("role") == "admin" and len(admins) <= 1:
            return None, "Debe haber al menos un administrador activo"
    updated = _update_user_record(user_id, **updates)
    return user_public_view(updated), "Usuario actualizado"


def delete_account(user_id: str, current_user_id: str) -> tuple[bool, str]:
    if user_id == current_user_id:
        return False, "No puedes eliminar tu propia cuenta"
    user = _find_user_by_id(user_id)
    if not user:
        return False, "Usuario no encontrado"
    data = _load_auth()
    if user.get("role") == "admin":
        active_admins = [
            u for u in data.get("users", [])
            if u.get("role") == "admin" and u.get("active", True) and u.get("id") != user_id
        ]
        if not active_admins:
            return False, "No puedes eliminar el último administrador"
    data["users"] = [u for u in data.get("users", []) if u.get("id") != user_id]
    _save_auth(data)
    return True, "Usuario eliminado"


def reset_account_password(user_id: str, new_password: str) -> tuple[bool, str]:
    ok, msg = validate_password(new_password)
    if not ok:
        return False, msg
    salt = secrets.token_hex(16)
    updated = _update_user_record(
        user_id,
        salt=salt,
        password_hash=_hash_password(new_password, salt),
        must_change_password=True,
    )
    if not updated:
        return False, "Usuario no encontrado"
    return True, "Contraseña restablecida. El usuario deberá cambiarla al ingresar."


def change_password(request, current_password: str, new_password: str) -> tuple[bool, str]:
    user = get_current_user_record(request)
    if not user:
        return False, "Sesión no válida"
    salt = user.get("salt", "")
    stored = user.get("password_hash", "")
    if not secrets.compare_digest(_hash_password(current_password, salt), stored):
        return False, "Contraseña actual incorrecta"
    ok, msg = validate_password(new_password)
    if not ok:
        return False, msg
    new_salt = secrets.token_hex(16)
    _update_user_record(
        user["id"],
        salt=new_salt,
        password_hash=_hash_password(new_password, new_salt),
        must_change_password=False,
    )
    request.session["must_change_password"] = False
    return True, "Contraseña actualizada correctamente"