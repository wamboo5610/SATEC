import csv
import io
import json
import shutil
import socket
import sqlite3
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openpyxl import Workbook
from starlette.middleware.sessions import SessionMiddleware

from . import database as db
from . import attendance_logic as al
from . import zk_device as zk
from . import adms
from . import reports as rep
from . import auth
from . import excel_import as xls
from . import recursos_tardiness as recursos
from . import offline_download as offdl
from . import backup as bak
from . import updater as upd
from .paths import get_data_dir, IS_VERCEL, is_desktop, get_listen_port, assets_dir
from .database import DB_PATH
from .auth import AUTH_PATH
from .version import APP_NAME, APP_TITLE, APP_VERSION, AUTHOR


@asynccontextmanager
async def lifespan(application: FastAPI):
    db.init_db()
    auth.init_auth()
    yield


app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    lifespan=lifespan,
)
app.add_middleware(auth.AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=auth.get_secret_key(), same_site="lax")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC = Path(__file__).parent / "static"


class DeviceCreate(BaseModel):
    name: str
    ip: str
    port: int = 4370
    password: int = 0
    sede_id: int | None = None


class SedeCreate(BaseModel):
    name: str


class DeviceUpdate(BaseModel):
    name: str | None = None
    sede_id: int | None = None


class AdmsUpdate(BaseModel):
    sede_id: int | None = None
    alias: str | None = None


class DeviceTest(BaseModel):
    ip: str
    port: int = 4370
    password: int = 0


class SyncRequest(BaseModel):
    device_id: int


class AttendanceFilter(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    user_id: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AccountCreate(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    modules: list[str] = []
    role: str = "user"
    active: bool = True


class AccountUpdate(BaseModel):
    display_name: str | None = None
    modules: list[str] | None = None
    active: bool | None = None
    role: str | None = None


class AccountResetPassword(BaseModel):
    new_password: str


class WorkScheduleUpdate(BaseModel):
    entry_time: str = "08:00"
    exit_time: str = "17:00"
    lunch_start: str = "12:00"
    lunch_end: str = "13:00"
    grace_minutes: int = 0
    lunch_grace_minutes: int = 0
    work_monday: int | None = None
    work_tuesday: int | None = None
    work_wednesday: int | None = None
    work_thursday: int | None = None
    work_friday: int | None = None
    work_saturday: int | None = None
    work_sunday: int | None = None


class HolidayCreate(BaseModel):
    holiday_date: str
    name: str
    sede_id: int | None = None


class PunchRemedyCreate(BaseModel):
    user_id: str
    work_date: str
    missing_slot: str
    reason: str | None = None


class ExcelTardinessLocalRequest(BaseModel):
    filename: str
    sede_id: int | None = None
    sede_name: str | None = None


class ExcelImportLocalRequest(BaseModel):
    filename: str
    sede_id: int
    sede_name: str | None = None
    replace_existing: bool = False


class RecursosTardinessRequest(BaseModel):
    lista_dir: str | None = None
    lista_filename: str | None = None
    reportes_dir: str | None = None
    report_files: list[str] | None = None
    sede_id: int | None = None
    sede_name: str | None = None


class OfflineDownloadRequest(BaseModel):
    ip: str
    port: int = 4370
    password: int = 0
    sede_id: int
    device_name: str | None = None
    notes: str | None = None


class PathRequest(BaseModel):
    path: str


class UpdateSettingsRequest(BaseModel):
    github_token: str | None = None
    repo: str | None = None
    branch: str | None = None


class EmployeeScheduleUpdate(BaseModel):
    user_name: str | None = None
    entry_time: str = "08:00"
    exit_time: str = "17:00"
    lunch_start: str = "12:00"
    lunch_end: str = "13:00"
    grace_minutes: int = 0
    lunch_grace_minutes: int = 0
    notes: str | None = None


@app.post("/api/auth/login")
def login(data: LoginRequest, request: Request):
    user = auth.verify_credentials(data.username.strip(), data.password)
    if not user:
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    auth.login_user(request, user)
    profile = auth.user_public_view(user)
    return {
        "ok": True,
        "username": profile["username"],
        "display_name": profile["display_name"],
        "role": profile["role"],
        "modules": profile["modules"],
        "must_change_password": profile["must_change_password"],
    }


@app.post("/api/auth/logout")
def logout(request: Request):
    auth.logout_user(request)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    record = auth.get_current_user_record(request)
    if not record:
        raise HTTPException(401, "No autorizado")
    profile = auth.user_public_view(record)
    return {
        "username": profile["username"],
        "display_name": profile["display_name"],
        "role": profile["role"],
        "modules": auth.get_user_modules(request),
        "is_admin": auth.is_admin(request),
        "must_change_password": profile["must_change_password"],
        "module_labels": auth.MODULES,
    }


@app.get("/api/auth/status")
def auth_status(request: Request):
    user = auth.get_current_user(request)
    return {
        "authenticated": bool(user),
        "username": user,
        "must_change_password": bool(request.session.get("must_change_password")),
    }


@app.get("/api/auth/modules")
def auth_modules():
    return {"modules": auth.MODULES}


@app.post("/api/auth/change-password")
def change_password(data: ChangePasswordRequest, request: Request):
    if not auth.is_authenticated(request):
        raise HTTPException(401, "No autorizado")
    ok, message = auth.change_password(request, data.current_password, data.new_password)
    if not ok:
        raise HTTPException(400, message)
    return {"message": message}


@app.get("/api/auth/accounts")
def list_accounts(request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores")
    return auth.list_accounts()


@app.post("/api/auth/accounts")
def create_account(data: AccountCreate, request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores")
    account, message = auth.create_account(
        data.username,
        data.password,
        display_name=data.display_name,
        modules=data.modules,
        role=data.role,
        active=data.active,
    )
    if not account:
        raise HTTPException(400, message)
    return {"message": message, "account": account}


@app.patch("/api/auth/accounts/{user_id}")
def update_account(user_id: str, data: AccountUpdate, request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores")
    account, message = auth.update_account(
        user_id,
        display_name=data.display_name,
        modules=data.modules,
        active=data.active,
        role=data.role,
    )
    if not account:
        raise HTTPException(400, message)
    return {"message": message, "account": account}


@app.delete("/api/auth/accounts/{user_id}")
def delete_account(user_id: str, request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores")
    current = request.session.get("user_id")
    ok, message = auth.delete_account(user_id, current)
    if not ok:
        raise HTTPException(400, message)
    return {"message": message}


@app.post("/api/auth/accounts/{user_id}/reset-password")
def reset_account_password(user_id: str, data: AccountResetPassword, request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores")
    ok, message = auth.reset_account_password(user_id, data.new_password)
    if not ok:
        raise HTTPException(400, message)
    return {"message": message}


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.get("/api/server-info")
def server_info(request: Request):
    host = request.headers.get("host", "localhost:8000")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    local_ip = get_local_ip()
    hostname = host.split(":")[0]
    is_local = hostname in ("127.0.0.1", "localhost", local_ip)
    port = get_listen_port()
    return {
        "local_ip": local_ip,
        "adms_url": f"{scheme}://{host}/iclock/",
        "panel_url": f"{scheme}://{host}/",
        "lan_adms_url": f"http://{local_ip}:{port}/iclock/",
        "needs_public_url": is_local,
        "is_vercel": IS_VERCEL,
        "is_desktop": is_desktop(),
        "direct_connect_available": not IS_VERCEL,
        "port": port,
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "author": AUTHOR,
    }


@app.get("/api/stats")
def stats():
    return db.attendance_stats()


@app.get("/api/dashboard")
def dashboard():
    return db.get_dashboard_data()


@app.get("/api/attendance/daily")
def daily_attendance(
    date: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
):
    return db.get_daily_attendance(date, sede_id, device_id)


@app.get("/api/schedules")
def list_schedules():
    return db.get_work_schedules()


@app.get("/api/schedules/{sede_id}")
def get_schedule(sede_id: int):
    schedule = db.get_work_schedule(sede_id)
    if not schedule:
        raise HTTPException(404, "Horario no encontrado")
    return schedule


@app.put("/api/schedules/{sede_id}")
def update_schedule(sede_id: int, data: WorkScheduleUpdate):
    sedes = {s["id"] for s in db.get_sedes()}
    if sede_id not in sedes:
        raise HTTPException(404, "Sede no encontrada")
    db.save_work_schedule(sede_id, **data.model_dump(exclude_none=True))
    return {"message": "Horario guardado", "schedule": db.get_work_schedule(sede_id)}


@app.get("/api/holidays")
def list_holidays(sede_id: int | None = None):
    return db.get_holidays(sede_id)


@app.post("/api/holidays")
def create_holiday(data: HolidayCreate):
    if not data.holiday_date or not data.name.strip():
        raise HTTPException(400, "Fecha y nombre son obligatorios")
    if data.sede_id is not None:
        sedes = {s["id"] for s in db.get_sedes()}
        if data.sede_id not in sedes:
            raise HTTPException(404, "Sede no encontrada")
    holiday_id = db.save_holiday(data.holiday_date, data.name, data.sede_id)
    return {"message": "Feriado guardado", "id": holiday_id}


@app.delete("/api/holidays/{holiday_id}")
def remove_holiday(holiday_id: int):
    db.delete_holiday(holiday_id)
    return {"message": "Feriado eliminado"}


@app.post("/api/holidays/seed-peru-2026")
def seed_peru_holidays():
    added = db.seed_peru_holidays_2026()
    return {"message": "Feriados Perú 2026 cargados", "added": added, "holidays": db.get_holidays()}


@app.get("/api/employee-schedules")
def list_employee_schedules_page():
    return db.get_employee_schedules()


@app.get("/api/employee-schedules/{user_id}")
def get_employee_schedule_page(user_id: str):
    schedule = db.get_employee_schedule(user_id)
    if not schedule:
        raise HTTPException(404, "Horario personalizado no configurado")
    return schedule


@app.put("/api/employee-schedules/{user_id}")
def update_employee_schedule_page(user_id: str, data: EmployeeScheduleUpdate):
    db.save_employee_schedule(user_id, **data.model_dump())
    saved = db.get_employee_schedule(user_id)
    return {"message": "Horario personalizado guardado", "schedule": saved}


@app.delete("/api/employee-schedules/{user_id}")
def remove_employee_schedule_page(user_id: str):
    db.delete_employee_schedule(user_id)
    return {"message": "Horario personalizado eliminado. Se usará el horario de la sede."}


def _schedule_for_excel(sede_id: int | None) -> tuple[dict | None, str]:
    if sede_id:
        schedule = db.get_work_schedule(sede_id)
        if not schedule:
            raise HTTPException(404, "Horario no encontrado para la sede")
        sedes = {s["id"]: s["name"] for s in db.get_sedes()}
        return schedule, sedes.get(sede_id, "Sede")
    return None, "Excel importado"


def _excel_tardiness_meta(report: dict, sede_name: str) -> dict:
    meta = report.get("meta", {})
    return {
        "date_from": meta.get("date_from", "—"),
        "date_to": meta.get("date_to", "—"),
        "sede_name": sede_name or meta.get("sede_name", "Excel"),
        "device_name": "Importación Excel",
    }


@app.get("/api/excel/files")
def excel_report_files():
    return xls.list_report_files()


@app.post("/api/excel/tardiness")
async def excel_tardiness_upload(
    file: UploadFile = File(...),
    sede_id: int | None = Form(None),
    sede_name: str | None = Form(None),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Sube un archivo Excel (.xlsx)")
    content = await file.read()
    if not content:
        raise HTTPException(400, "El archivo está vacío")
    schedule, default_sede = _schedule_for_excel(sede_id)
    label = (sede_name or default_sede).strip() or default_sede
    try:
        return xls.calculate_tardiness_from_excel(
            content, sede_name=label, schedule=schedule, sede_id=sede_id
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al procesar Excel: {e}")


@app.post("/api/excel/tardiness/local")
def excel_tardiness_local(data: ExcelTardinessLocalRequest):
    schedule, default_sede = _schedule_for_excel(data.sede_id)
    label = (data.sede_name or default_sede).strip() or default_sede
    try:
        content = xls.read_local_report(data.filename)
        return xls.calculate_tardiness_from_excel(
            content, sede_name=label, schedule=schedule, sede_id=data.sede_id
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al procesar Excel: {e}")


@app.post("/api/excel/tardiness/export")
async def excel_tardiness_export(
    file: UploadFile = File(...),
    sede_id: int | None = Form(None),
    sede_name: str | None = Form(None),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Sube un archivo Excel (.xlsx)")
    content = await file.read()
    schedule, default_sede = _schedule_for_excel(sede_id)
    label = (sede_name or default_sede).strip() or default_sede
    try:
        report = xls.calculate_tardiness_from_excel(
            content, sede_name=label, schedule=schedule, sede_id=sede_id
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al procesar Excel: {e}")
    meta = _excel_tardiness_meta(report, label)
    buf = rep.build_excel_tardiness(report, meta)
    fname = f"tardanzas_excel_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/excel/import")
async def excel_import_upload(
    file: UploadFile = File(...),
    sede_id: int = Form(...),
    sede_name: str | None = Form(None),
    replace_existing: bool = Form(False),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Sube un archivo Excel (.xlsx)")
    content = await file.read()
    if not content:
        raise HTTPException(400, "El archivo está vacío")
    try:
        return xls.import_excel_to_db(
            content, sede_id=sede_id, sede_name=sede_name, filename=file.filename,
            replace_existing=replace_existing,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al importar Excel: {e}")


@app.post("/api/excel/import/local")
def excel_import_local(data: ExcelImportLocalRequest):
    try:
        content = xls.read_local_report(data.filename)
        return xls.import_excel_to_db(
            content,
            sede_id=data.sede_id,
            sede_name=data.sede_name,
            filename=data.filename,
            replace_existing=data.replace_existing,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al importar Excel: {e}")


@app.post("/api/excel/import-and-tardiness")
async def excel_import_and_tardiness(
    file: UploadFile = File(...),
    sede_id: int = Form(...),
    sede_name: str | None = Form(None),
    replace_existing: bool = Form(False),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Sube un archivo Excel (.xlsx)")
    content = await file.read()
    if not content:
        raise HTTPException(400, "El archivo está vacío")
    schedule, default_sede = _schedule_for_excel(sede_id)
    label = (sede_name or default_sede).strip() or default_sede
    try:
        imported = xls.import_excel_to_db(
            content, sede_id=sede_id, sede_name=label, filename=file.filename,
            replace_existing=replace_existing,
        )
        tardiness = xls.calculate_tardiness_from_excel(
            content, sede_name=label, schedule=schedule, sede_id=sede_id
        )
        return {"import": imported, "tardiness": tardiness}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al procesar Excel: {e}")


@app.post("/api/excel/import-and-tardiness/local")
def excel_import_and_tardiness_local(data: ExcelImportLocalRequest):
    schedule, default_sede = _schedule_for_excel(data.sede_id)
    label = (data.sede_name or default_sede).strip() or default_sede
    try:
        content = xls.read_local_report(data.filename)
        imported = xls.import_excel_to_db(
            content,
            sede_id=data.sede_id,
            sede_name=label,
            filename=data.filename,
            replace_existing=data.replace_existing,
        )
        tardiness = xls.calculate_tardiness_from_excel(
            content, sede_name=label, schedule=schedule, sede_id=data.sede_id
        )
        return {"import": imported, "tardiness": tardiness}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al procesar Excel: {e}")


@app.post("/api/excel/tardiness/local/export")
def excel_tardiness_local_export(data: ExcelTardinessLocalRequest):
    schedule, default_sede = _schedule_for_excel(data.sede_id)
    label = (data.sede_name or default_sede).strip() or default_sede
    try:
        content = xls.read_local_report(data.filename)
        report = xls.calculate_tardiness_from_excel(
            content, sede_name=label, schedule=schedule, sede_id=data.sede_id
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al procesar Excel: {e}")
    meta = _excel_tardiness_meta(report, label)
    buf = rep.build_excel_tardiness(report, meta)
    fname = f"tardanzas_{Path(data.filename).stem}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/recursos/status")
def recursos_status(
    lista_dir: str | None = None,
    reportes_dir: str | None = None,
):
    lista_path = Path(lista_dir) if lista_dir else None
    reportes_path = Path(reportes_dir) if reportes_dir else None
    return recursos.get_recursos_status(lista_path, reportes_path)


@app.get("/api/recursos/folders")
def recursos_folders():
    return recursos.get_folder_presets()


@app.post("/api/recursos/browse-folder")
def recursos_browse_folder():
    if IS_VERCEL:
        raise HTTPException(400, "Examinar carpetas no está disponible en Vercel")
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as e:
        raise HTTPException(500, f"No se pudo abrir el selector de carpetas: {e}")
    root = tk.Tk()
    root.withdraw()
    try:
        root.wm_attributes("-topmost", 1)
    except Exception:
        pass
    folder = filedialog.askdirectory(title="Seleccionar carpeta")
    root.destroy()
    if not folder:
        return {"path": None, "cancelled": True}
    return {"path": folder, "cancelled": False}


@app.post("/api/recursos/tardiness")
def recursos_tardiness_calculate(data: RecursosTardinessRequest):
    lista_dir = Path(data.lista_dir) if data.lista_dir else None
    reportes_dir = Path(data.reportes_dir) if data.reportes_dir else None
    schedule, default_sede = _schedule_for_excel(data.sede_id) if data.sede_id else (None, None)
    label = (data.sede_name or default_sede or "").strip() or None
    try:
        return recursos.calculate_recursos_tardiness(
            lista_dir=lista_dir,
            lista_filename=data.lista_filename,
            reportes_dir=reportes_dir,
            report_files=data.report_files,
            sede_id=data.sede_id,
            schedule=schedule,
            default_sede_name=label,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al calcular tardanzas: {e}")


@app.post("/api/recursos/tardiness/upload-lista")
async def recursos_tardiness_upload_lista(
    lista_file: UploadFile = File(...),
    lista_dir: str | None = Form(None),
    reportes_dir: str | None = Form(None),
    report_files: str | None = Form(None),
    sede_id: int | None = Form(None),
    sede_name: str | None = Form(None),
):
    if not lista_file.filename or not lista_file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Sube el listado de personas en Excel (.xlsx)")
    content = await lista_file.read()
    if not content:
        raise HTTPException(400, "El archivo de lista está vacío")
    files = [f.strip() for f in (report_files or "").split(",") if f.strip()] or None
    schedule, default_sede = _schedule_for_excel(sede_id) if sede_id else (None, None)
    label = (sede_name or default_sede or "").strip() or None
    try:
        return recursos.calculate_recursos_tardiness(
            lista_bytes=content,
            lista_dir=Path(lista_dir) if lista_dir else None,
            reportes_dir=Path(reportes_dir) if reportes_dir else None,
            report_files=files,
            sede_id=sede_id,
            schedule=schedule,
            default_sede_name=label,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al calcular tardanzas: {e}")


@app.post("/api/recursos/tardiness/export")
def recursos_tardiness_export(data: RecursosTardinessRequest):
    lista_dir = Path(data.lista_dir) if data.lista_dir else None
    reportes_dir = Path(data.reportes_dir) if data.reportes_dir else None
    schedule, default_sede = _schedule_for_excel(data.sede_id) if data.sede_id else (None, None)
    label = (data.sede_name or default_sede or "").strip() or None
    try:
        report = recursos.calculate_recursos_tardiness(
            lista_dir=lista_dir,
            lista_filename=data.lista_filename,
            reportes_dir=reportes_dir,
            report_files=data.report_files,
            sede_id=data.sede_id,
            schedule=schedule,
            default_sede_name=label,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al calcular tardanzas: {e}")
    meta = _excel_tardiness_meta(report, label)
    meta["lista_file"] = report.get("meta", {}).get("lista_file")
    meta["lista_persons"] = report.get("meta", {}).get("lista_persons")
    meta["matched_persons"] = report.get("meta", {}).get("matched_persons")
    buf = rep.build_excel_tardiness(report, meta)
    fname = f"tardanzas_rrhh_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/reports/tardiness")
def tardiness_report(
    date_from: str | None = None,
    date_to: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
    user_id: str | None = None,
    source_mode: str = "auto",
):
    return db.get_tardiness_report(date_from, date_to, sede_id, device_id, user_id, source_mode)


def _tardiness_export_data(date_from, date_to, user_id, sede_id, device_id, source_mode="auto"):
    return db.get_tardiness_report(date_from, date_to, sede_id, device_id, user_id, source_mode)


@app.get("/api/reports/tardiness/export/xlsx")
def export_tardiness_xlsx(
    date_from: str | None = None,
    date_to: str | None = None,
    user_id: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
):
    data = _tardiness_export_data(date_from, date_to, user_id, sede_id, device_id)
    meta = _export_meta(date_from, date_to, sede_id, device_id)
    buf = rep.build_excel_tardiness(data, meta)
    fname = f"tardanzas_por_empleado_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/reports/tardiness/export/csv")
def export_tardiness_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    user_id: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
    format: str = "person",
):
    data = _tardiness_export_data(date_from, date_to, user_id, sede_id, device_id)
    if format == "detail":
        headers, rows = rep.tardiness_detail_csv_rows(data)
        fname = f"tardanzas_detalle_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    else:
        headers, rows = rep.tardiness_person_csv_rows(data)
        fname = f"tardanzas_por_empleado_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/reports/tardiness/monthly")
def monthly_tardiness_report(
    year: int | None = None,
    month: int | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
    user_id: str | None = None,
):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    try:
        return db.get_monthly_tardiness_report(y, m, sede_id, device_id, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/reports/tardiness/monthly/export/xlsx")
def export_monthly_tardiness_xlsx(
    year: int | None = None,
    month: int | None = None,
    user_id: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    try:
        data = db.get_monthly_tardiness_report(y, m, sede_id, device_id, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    meta = _export_meta(data["date_from"], data["date_to"], sede_id, device_id)
    meta["month_label"] = data.get("month_label", "")
    buf = rep.build_excel_monthly_tardiness(data, meta)
    fname = f"tardanzas_mensual_{y}_{m:02d}_{datetime.now().strftime('%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/reports/tardiness/monthly/export/csv")
def export_monthly_tardiness_csv(
    year: int | None = None,
    month: int | None = None,
    user_id: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    try:
        data = db.get_monthly_tardiness_report(y, m, sede_id, device_id, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    headers, rows = rep.monthly_tardiness_csv_rows(data)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    output.seek(0)
    fname = f"tardanzas_mensual_{y}_{m:02d}_{datetime.now().strftime('%H%M')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/reports/punch-observations")
def punch_observations_report(
    date_from: str | None = None,
    date_to: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
    user_id: str | None = None,
    source_mode: str = "auto",
):
    return db.get_punch_observations_report(date_from, date_to, sede_id, device_id, user_id, source_mode)


@app.get("/api/punch-remedies")
def list_punch_remedies(
    date_from: str | None = None,
    date_to: str | None = None,
    user_id: str | None = None,
):
    return db.get_punch_remedies(date_from, date_to, user_id)


@app.post("/api/punch-remedies")
def create_punch_remedy(data: PunchRemedyCreate, request: Request):
    if not data.user_id.strip() or not data.work_date:
        raise HTTPException(400, "Empleado y fecha son obligatorios")
    if data.missing_slot not in al.VALID_PUNCH_SLOTS:
        raise HTTPException(400, "Marcación faltante inválida")
    created_by = request.session.get("username")
    try:
        remedy = db.save_punch_remedy(
            data.user_id, data.work_date, data.missing_slot, data.reason, created_by
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": "Marcación subsanada", "remedy": remedy}


@app.delete("/api/punch-remedies/{remedy_id}")
def remove_punch_remedy(remedy_id: int):
    db.delete_punch_remedy(remedy_id)
    return {"message": "Subsanación eliminada"}


@app.get("/api/sedes")
def list_sedes():
    return db.get_sedes()


@app.post("/api/sedes")
def add_sede(data: SedeCreate):
    sid = db.save_sede(data.name)
    return {"id": sid, "message": "Sede creada"}


@app.delete("/api/sedes/{sede_id}")
def remove_sede(sede_id: int):
    db.delete_sede(sede_id)
    return {"message": "Sede eliminada"}


@app.get("/api/devices")
def list_devices():
    return db.get_devices()


@app.post("/api/devices")
def add_device(data: DeviceCreate):
    did = db.save_device(data.name, data.ip, data.port, data.password, sede_id=data.sede_id)
    return {"id": did, "message": "Dispositivo guardado"}


@app.patch("/api/devices/{device_id}")
def patch_device(device_id: int, data: DeviceUpdate):
    db.update_device(device_id, **data.model_dump(exclude_none=True))
    return {"message": "Actualizado"}


@app.delete("/api/devices/{device_id}")
def remove_device(device_id: int):
    db.delete_device(device_id)
    return {"message": "Eliminado"}


@app.post("/api/devices/test")
def test_device(data: DeviceTest):
    return zk.test_connection(data.ip, data.port, data.password)


@app.get("/api/devices/{device_id}/info")
def device_info(device_id: int):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo no encontrado")
    result = zk.test_connection(device["ip"], device["port"], device["password"])
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return result["info"]


def _pull_device_live(device_id: int) -> dict:
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo no encontrado")
    conn = None
    try:
        _, conn = zk.connect_device(
            device["ip"], device["port"], device["password"], timeout=20
        )
        zk.safe_disable(conn)
        info = zk.get_device_info(conn)
        serial = zk.resolve_serial(info, device.get("serial"))
        if serial:
            db.update_device(device_id, serial=serial)
        users = zk.fetch_users(conn)
        db.upsert_users(users, serial)
        records = zk.fetch_attendance(conn, serial)
        name_map = {u["user_id"]: u["name"] for u in users if u.get("user_id")}
        for r in records:
            r["user_name"] = name_map.get(r["user_id"]) or r.get("user_name")
        inserted = db.insert_attendance(records)
        names_updated = db.backfill_attendance_names(name_map)
        db.record_device_sync(
            device_id,
            ok=True,
            users=len(users),
            records_fetched=len(records),
            records_new=inserted,
        )
        device = db.get_device(device_id)
        return {
            "device": device,
            "serial": serial,
            "users": users,
            "records": records,
            "info": info,
            "records_new": inserted,
            "records_fetched": len(records),
            "users_count": len(users),
            "names_updated": names_updated,
            "local_users": device.get("local_users", len(users)),
            "local_records": device.get("local_records", 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        err = zk.friendly_error(device.get("ip") or "", e)
        try:
            db.record_device_sync(device_id, ok=False, message=err)
        except Exception:
            pass
        raise HTTPException(400, f"Error al sincronizar el reloj: {err}")
    finally:
        if conn:
            zk.safe_enable(conn)
            try:
                conn.disconnect()
            except Exception:
                pass


def _sync_payload(result: dict) -> dict:
    device = result.get("device") or {}
    return {
        "ok": True,
        "users": result["users_count"],
        "records_fetched": result["records_fetched"],
        "records_new": result["records_new"],
        "names_updated": result.get("names_updated", 0),
        "local_users": result.get("local_users", device.get("local_users", 0)),
        "local_records": result.get("local_records", device.get("local_records", 0)),
        "last_sync_at": device.get("last_sync_at"),
        "serial": result.get("serial") or device.get("serial"),
        "device_name": device.get("name"),
        "device_info": result["info"],
        "message": (
            f"{result['users_count']} empleados sincronizados. "
            f"{result['records_new']} marcaciones nuevas de {result['records_fetched']} leídas. "
            f"Guardado en la base local."
        ),
    }


@app.post("/api/devices/sync-all")
def sync_all_devices():
    results = []
    for device in db.get_devices():
        if (device.get("mode") or "direct") == "excel":
            continue
        ip = (device.get("ip") or "").strip()
        if not ip or ip.lower() == "importado":
            continue
        try:
            pulled = _pull_device_live(device["id"])
            item = _sync_payload(pulled)
            item["id"] = device["id"]
            item["name"] = device.get("name")
            results.append(item)
        except HTTPException as e:
            results.append({
                "id": device["id"],
                "name": device.get("name"),
                "ok": False,
                "error": e.detail,
            })
        except Exception as e:
            results.append({
                "id": device["id"],
                "name": device.get("name"),
                "ok": False,
                "error": str(e),
            })
    synced = sum(1 for r in results if r.get("ok"))
    failed = len(results) - synced
    return {
        "ok": failed == 0,
        "synced": synced,
        "failed": failed,
        "records_new": sum(int(r.get("records_new") or 0) for r in results if r.get("ok")),
        "users": sum(int(r.get("users") or 0) for r in results if r.get("ok")),
        "results": results,
    }


@app.post("/api/devices/{device_id}/sync")
def sync_from_device(device_id: int):
    return _sync_payload(_pull_device_live(device_id))


@app.post("/api/devices/{device_id}/sync-time")
def sync_time(device_id: int):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo no encontrado")
    conn = None
    try:
        _, conn = zk.connect_device(device["ip"], device["port"], device["password"])
        zk.sync_time(conn)
        return {"message": "Hora sincronizada", "time": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


@app.get("/api/users")
def list_users(device_serial: str | None = None, sede_id: int | None = None):
    users = db.get_users(device_serial, sede_id)
    device_map = db.get_device_serial_map()
    enriched = []
    for u in users:
        row = dict(u)
        dev = device_map.get(row.get("device_serial") or "", {})
        row["sede_name"] = dev.get("sede_name", "Sin sede")
        row["sede_id"] = dev.get("sede_id")
        row["device_name"] = dev.get("device_name", row.get("device_serial") or "—")
        enriched.append(row)
    return enriched


@app.post("/api/offline/download")
def offline_download_store(data: OfflineDownloadRequest):
    if IS_VERCEL:
        raise HTTPException(
            400,
            "La descarga local solo funciona en tu PC (INICIAR.bat). "
            "Conecta el cable de red al reloj y abre http://127.0.0.1:8000",
        )
    ip = data.ip.strip()
    if not ip:
        raise HTTPException(400, "Ingresa la IP del reloj")
    try:
        return offdl.download_and_store(
            ip=ip,
            port=data.port,
            password=data.password,
            sede_id=data.sede_id,
            device_name=data.device_name,
            notes=data.notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/offline/downloads")
def list_offline_downloads():
    return db.get_offline_downloads()


@app.get("/api/offline/downloads/{download_id}/export")
def export_offline_download(download_id: int):
    row = db.get_offline_download(download_id)
    if not row or not row.get("snapshot_file"):
        raise HTTPException(404, "Descarga no encontrada")
    try:
        path = offdl.resolve_snapshot_path(row["snapshot_file"])
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(
        path,
        media_type="application/zip",
        filename=row["snapshot_file"],
    )


@app.delete("/api/offline/downloads/{download_id}")
def remove_offline_download(download_id: int, purge_attendance: bool = True):
    row = db.get_offline_download(download_id)
    if not row:
        raise HTTPException(404, "Descarga no encontrada")
    if row.get("snapshot_file"):
        try:
            offdl.resolve_snapshot_path(row["snapshot_file"]).unlink(missing_ok=True)
        except (FileNotFoundError, ValueError):
            pass
    result = db.delete_offline_download(download_id, purge_attendance=purge_attendance)
    removed = result.get("attendance_removed", 0)
    msg = "Descarga eliminada"
    if purge_attendance and removed:
        msg += f" — {removed} marcaciones del reloj {result.get('device_serial') or ''} borradas de la base"
    elif purge_attendance:
        msg += " — no había marcaciones de ese reloj en la base"
    return {"message": msg, **result}


@app.delete("/api/attendance/source/excel")
def purge_excel_attendance(sede_id: int):
    if sede_id < 1:
        raise HTTPException(400, "Sede inválida")
    removed = db.delete_attendance_by_excel_sede(sede_id)
    return {
        "message": f"Marcaciones Excel de la sede eliminadas ({removed} registros)",
        "attendance_removed": removed,
        "device_serial": f"EXCEL-S{sede_id}",
    }


@app.get("/api/attendance")
def list_attendance(
    date_from: str | None = None,
    date_to: str | None = None,
    user_id: str | None = None,
    device_serial: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
    limit: int = 50000,
    source_mode: str = "auto",
):
    rows = db.get_attendance(
        date_from, date_to, user_id, device_serial, sede_id, device_id, limit, source_mode
    )
    device_map = db.get_device_serial_map()
    return rep.enrich_rows(rows, device_map)


@app.get("/api/reports/summary")
def report_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    user_id: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
    source_mode: str = "auto",
):
    return db.get_report_summary(date_from, date_to, sede_id, device_id, user_id, source_mode)


@app.get("/api/adms/devices")
def adms_devices():
    return db.get_adms_devices()


@app.patch("/api/adms/devices/{serial}")
def patch_adms_device(serial: str, data: AdmsUpdate):
    db.update_adms_device(serial, data.sede_id, data.alias)
    return {"message": "Actualizado"}


@app.post("/api/adms/{serial}/command")
def adms_command(serial: str, body: dict):
    cmd = body.get("command", "INFO")
    cid = adms.queue_command(serial, cmd)
    return {"queued": True, "command_id": cid}


def _fetch_export_rows(date_from, date_to, user_id, sede_id, device_id):
    rows = db.get_attendance(date_from, date_to, user_id, sede_id=sede_id, device_id=device_id)
    device_map = db.get_device_serial_map()
    return rep.enrich_rows(rows, device_map)


def _export_meta(date_from, date_to, sede_id, device_id):
    meta = {"date_from": date_from or "Todos", "date_to": date_to or "Todos"}
    if sede_id:
        sedes = {s["id"]: s["name"] for s in db.get_sedes()}
        meta["sede_name"] = sedes.get(sede_id, "—")
    else:
        meta["sede_name"] = "Todas"
    if device_id:
        dev = db.get_device(device_id)
        meta["device_name"] = dev["name"] if dev else "—"
    else:
        meta["device_name"] = "Todos"
    return meta


def records_to_export(rows):
    headers = ["ID", "Nombre", "Fecha/Hora", "Tipo", "Verificación", "Sede", "Reloj", "Origen"]
    data = []
    for r in rows:
        data.append([
            r.get("user_id"),
            r.get("user_name") or "",
            r.get("timestamp"),
            r.get("punch_type", ""),
            r.get("verify_label", ""),
            r.get("sede_name", ""),
            r.get("device_name", ""),
            r.get("source") or "",
        ])
    return headers, data


@app.get("/api/attendance/export/csv")
def export_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    user_id: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
):
    rows = _fetch_export_rows(date_from, date_to, user_id, sede_id, device_id)
    headers, data = records_to_export(rows)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(data)
    output.seek(0)
    fname = f"asistencia_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/attendance/export/xlsx")
def export_xlsx(
    date_from: str | None = None,
    date_to: str | None = None,
    user_id: str | None = None,
    sede_id: int | None = None,
    device_id: int | None = None,
    format: str = "person",
):
    rows = _fetch_export_rows(date_from, date_to, user_id, sede_id, device_id)
    meta = _export_meta(date_from, date_to, sede_id, device_id)
    if format == "sede":
        buf = rep.build_excel_by_sede(rows, meta)
        fname = f"asistencia_por_sede_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    elif format == "flat":
        headers, data = records_to_export(rows)
        wb = Workbook()
        ws = wb.active
        ws.title = "Asistencia"
        ws.append(headers)
        for row in data:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        fname = f"asistencia_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    else:
        buf = rep.build_excel_by_person(rows, meta)
        fname = f"asistencia_por_persona_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _device_serial(device_id: int) -> str | None:
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo no encontrado")
    return device.get("serial")


def _dedupe_users(rows):
    by_id = {}
    for u in rows:
        cur = by_id.get(u["user_id"])
        if not cur or (u.get("name") and not cur.get("name")):
            by_id[u["user_id"]] = u
    return sorted(by_id.values(), key=lambda x: (x.get("name") or x["user_id"]).lower())


def _users_csv_bytes(users):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Nombre", "Privilegio", "Reloj (serial)", "Actualizado"])
    for u in users:
        writer.writerow([
            u.get("user_id"),
            u.get("name") or "",
            u.get("privilege") or "",
            u.get("device_serial") or "",
            u.get("updated_at") or "",
        ])
    return output.getvalue().encode("utf-8-sig")


def _attendance_csv_bytes(rows):
    headers, data = records_to_export(rows)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(data)
    return output.getvalue().encode("utf-8-sig")


@app.get("/api/users/export/csv")
def export_users_csv(
    device_id: int | None = None,
    device_serial: str | None = None,
    sede_id: int | None = None,
):
    if device_id:
        device_serial = _device_serial(device_id)
    users = _dedupe_users(db.get_users(device_serial, sede_id))
    fname = f"empleados_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([_users_csv_bytes(users)]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/devices/{device_id}/download/info")
def download_device_info(device_id: int, live: bool = Query(True)):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo no encontrado")
    payload = {
        "exported_at": datetime.now().isoformat(),
        "device": {
            "id": device["id"],
            "name": device["name"],
            "ip": device["ip"],
            "port": device["port"],
            "serial": device.get("serial"),
            "sede_id": device.get("sede_id"),
            "sede_name": device.get("sede_name"),
        },
        "live_info": None,
    }
    if live:
        result = zk.test_connection(device["ip"], device["port"], device["password"])
        if result.get("ok"):
            payload["live_info"] = result.get("info")
        else:
            payload["connection_error"] = result.get("error")
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    safe = (device["name"] or "reloj").replace(" ", "_")[:30]
    fname = f"reloj_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/devices/{device_id}/download/users")
def download_device_users(device_id: int, live: bool = Query(True)):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo no encontrado")
    if live:
        result = _pull_device_live(device_id)
        device = result["device"]
        users = _dedupe_users(result["users"])
    else:
        serial = device.get("serial")
        users = _dedupe_users(db.get_users(serial)) if serial else []
    safe = (device["name"] or "reloj").replace(" ", "_")[:30]
    fname = f"usuarios_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([_users_csv_bytes(users)]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/devices/{device_id}/download/attendance")
def download_device_attendance(device_id: int, live: bool = Query(True)):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo no encontrado")
    if live:
        result = _pull_device_live(device_id)
        device = result["device"]
        device_map = db.get_device_serial_map()
        rows = rep.enrich_rows(result["records"], device_map)
    else:
        rows = _fetch_export_rows(None, None, None, None, device_id)
    safe = (device["name"] or "reloj").replace(" ", "_")[:30]
    fname = f"asistencia_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([_attendance_csv_bytes(rows)]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/devices/{device_id}/download/all")
def download_device_all(device_id: int, sync: bool = Query(True)):
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo no encontrado")
    live_info = None
    sync_summary = None
    if sync:
        result = _pull_device_live(device_id)
        device = result["device"]
        live_info = result["info"]
        sync_summary = {
            "users": result["users_count"],
            "records_fetched": result["records_fetched"],
            "records_new": result["records_new"],
        }
        users = _dedupe_users(result["users"])
        device_map = db.get_device_serial_map()
        rows = rep.enrich_rows(result["records"], device_map)
    else:
        serial = device.get("serial")
        users = _dedupe_users(db.get_users(serial)) if serial else []
        rows = _fetch_export_rows(None, None, None, None, device_id)
    buf = io.BytesIO()
    safe = (device["name"] or "reloj").replace(" ", "_")[:30]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info_payload = {
            "exported_at": datetime.now().isoformat(),
            "device": device,
            "live_info": live_info,
            "sync_summary": sync_summary,
        }
        zf.writestr("info_reloj.json", json.dumps(info_payload, ensure_ascii=False, indent=2))
        zf.writestr("usuarios.csv", _users_csv_bytes(users))
        zf.writestr("asistencia.csv", _attendance_csv_bytes(rows))
    buf.seek(0)
    fname = f"datos_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/system/status")
def system_status(request: Request):
    info = bak.backup_info()
    info["update"] = upd.public_settings()
    info["is_admin"] = auth.is_admin(request)
    info["is_desktop"] = is_desktop()
    return info


@app.get("/api/backup/info")
def backup_info_endpoint():
    return bak.backup_info()


@app.get("/api/backup/export")
def export_backup():
    buf, fname = bak.build_backup_zip()
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/backup/export-to")
def export_backup_to(data: PathRequest):
    path = (data.path or "").strip()
    if not path:
        raise HTTPException(400, "Elige dónde guardar el archivo")
    try:
        return bak.export_to_path(path)
    except Exception as e:
        raise HTTPException(400, f"No se pudo exportar: {e}")


@app.post("/api/backup/restore")
async def restore_backup(request: Request, file: UploadFile = File(...)):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores pueden restaurar la base")
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Sube un archivo .zip de respaldo")
    content = await file.read()
    try:
        return bak.restore_from_bytes(content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al restaurar: {e}")


@app.post("/api/backup/restore-from")
def restore_backup_from(data: PathRequest, request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores pueden restaurar la base")
    path = (data.path or "").strip()
    if not path:
        raise HTTPException(400, "Elige el archivo ZIP de respaldo")
    try:
        return bak.restore_from_path(path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al restaurar: {e}")


@app.get("/api/update/status")
def update_status():
    return upd.public_settings()


@app.post("/api/update/settings")
def update_settings(data: UpdateSettingsRequest, request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores")
    return upd.save_settings(
        github_token=data.github_token,
        repo=data.repo,
        branch=data.branch,
    )


@app.post("/api/update/check")
def update_check(request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores")
    try:
        return upd.check_for_update()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"No se pudo consultar GitHub: {e}")


@app.post("/api/update/install")
def update_install(request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "Solo administradores")
    if IS_VERCEL:
        raise HTTPException(400, "Las actualizaciones de escritorio no aplican en Vercel")
    try:
        return upd.apply_and_restart()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"No se pudo actualizar: {e}")


# --- ADMS Protocol (dispositivo se conecta por internet) ---

@app.api_route("/iclock/cdata", methods=["GET", "POST"])
async def iclock_cdata(request: Request):
    serial = adms.get_serial_from_request(request)
    table = request.query_params.get("table", "")
    if request.method == "GET":
        return PlainTextResponse(adms.handle_cdata_get(serial))
    body = (await request.body()).decode("utf-8", errors="ignore")
    resp, inserted = adms.handle_cdata_post(body, serial, table)
    return PlainTextResponse(resp)


@app.api_route("/iclock/registry", methods=["GET", "POST"])
async def iclock_registry(request: Request):
    serial = adms.get_serial_from_request(request)
    body = (await request.body()).decode("utf-8", errors="ignore") if request.method == "POST" else ""
    return PlainTextResponse(adms.handle_registry(body, serial))


@app.get("/iclock/getrequest")
async def iclock_getrequest(request: Request):
    serial = adms.get_serial_from_request(request)
    return PlainTextResponse(adms.handle_getrequest(serial))


@app.post("/iclock/devicecmd")
async def iclock_devicecmd(request: Request):
    body = (await request.body()).decode("utf-8", errors="ignore")
    return PlainTextResponse(adms.handle_devicecmd(body))


@app.get("/login")
async def login_page(request: Request):
    if auth.is_authenticated(request):
        return RedirectResponse("/")
    return FileResponse(STATIC / "login.html")


@app.get("/")
async def index(request: Request):
    if not auth.is_authenticated(request):
        return RedirectResponse("/login")
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
ASSETS = assets_dir()
if ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")