"""Lógica de marcaciones para Sistema de Control de Asistencia."""

from collections import defaultdict
from datetime import datetime, timedelta

from .calendar_rules import classify_day, merge_work_days

SOURCE_PRIORITY = {
    "direct": 0,
    "adms": 1,
    "offline": 1,
    "excel": 2,
}


def record_source_rank(rec: dict) -> tuple:
    """Menor valor = fuente preferida (reloj físico sobre Excel)."""
    src = (rec.get("source") or "direct").lower()
    priority = SOURCE_PRIORITY.get(src, 3)
    serial = str(rec.get("device_serial") or "")
    if serial.startswith("EXCEL-") and not serial.startswith("EXCEL-S"):
        excel_tier = 2
    elif serial.startswith("EXCEL-"):
        excel_tier = 1
    else:
        excel_tier = 0
    has_name = 0 if rec.get("user_name") else 1
    return (priority, excel_tier, has_name, serial, rec.get("id") or 0)


def dedupe_attendance_records(rows: list[dict]) -> list[dict]:
    """Una marcación por empleado y hora, aunque venga de varios relojes/Excel."""
    best: dict[tuple[str, str], dict] = {}

    for r in rows:
        uid = str(r.get("user_id", ""))
        ts = str(r.get("timestamp", ""))
        if not uid or not ts:
            continue
        key = (uid, ts)
        prev = best.get(key)
        if not prev or record_source_rank(r) < record_source_rank(prev):
            best[key] = r

    return sorted(best.values(), key=lambda x: (x.get("timestamp", ""), x.get("user_id", "")))


def dedupe_attendance_by_day(rows: list[dict]) -> list[dict]:
    """Una sola fuente por empleado y día (evita Excel + reloj físico el mismo día)."""
    by_day: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        uid = str(r.get("user_id", ""))
        day = extract_date(r.get("timestamp", ""))
        if uid and day:
            by_day[(uid, day)].append(r)

    result: list[dict] = []
    for _key, day_rows in by_day.items():
        by_serial: dict[str, list[dict]] = defaultdict(list)
        for r in day_rows:
            by_serial[str(r.get("device_serial") or "")].append(r)
        if len(by_serial) <= 1:
            result.extend(day_rows)
            continue
        winner = min(by_serial.keys(), key=lambda serial: record_source_rank(by_serial[serial][0]))
        result.extend(by_serial[winner])
    return sorted(result, key=lambda x: (x.get("timestamp", ""), x.get("user_id", "")))


def dedupe_attendance(rows: list[dict]) -> list[dict]:
    return dedupe_attendance_by_day(dedupe_attendance_records(rows))


MONTHLY_TOLERANCE_MINUTES = 20

DEFAULT_SCHEDULE = {
    "entry_time": "08:00",
    "exit_time": "17:00",
    "lunch_start": "12:00",
    "lunch_end": "14:30",
    "grace_minutes": 0,
    "lunch_grace_minutes": 0,
}

# Ventanas horarias para clasificar las 4 marcaciones del día (hora local del reloj).
DEFAULT_PUNCH_WINDOWS = {
    "entrada": ("05:00", "09:00"),           # Entrada mañana
    "salida_almuerzo": ("12:00", "14:00"),   # Salida mañana / almuerzo
    "entrada_almuerzo": ("14:01", "15:00"),  # Entrada tarde
    "salida": ("17:00", "23:59"),            # Salida tarde
}

PUNCH_WINDOW_SCHEDULE_KEYS = {
    "entrada": ("punch_entrada_start", "punch_entrada_end"),
    "salida_almuerzo": ("punch_salida_am_start", "punch_salida_am_end"),
    "entrada_almuerzo": ("punch_entrada_pm_start", "punch_entrada_pm_end"),
    "salida": ("punch_salida_pm_start", "punch_salida_pm_end"),
}

EXPECTED_PUNCH_COUNT = 4
EXPECTED_PUNCH_SLOTS = (
    ("Entrada", "entrada"),
    ("Salida almuerzo", "salida_almuerzo"),
    ("Entrada almuerzo", "entrada_almuerzo"),
    ("Salida", "salida"),
)
SLOT_KEY_TO_LABEL = {key: label for label, key in EXPECTED_PUNCH_SLOTS}
VALID_PUNCH_SLOTS = frozenset(SLOT_KEY_TO_LABEL)

STATUS_MAP = {
    0: "Entrada",
    1: "Salida",
    2: "Salida descanso",
    3: "Entrada descanso",
    4: "Entrada extra",
    5: "Salida extra",
}

VERIFY_MAP = {
    0: "Contraseña",
    1: "Huella",
    2: "Tarjeta",
    4: "Tarjeta",
    15: "Rostro",
    25: "Palma",
}

# En relojes uFace algunos firmwares guardan el tipo de verificación en status.
VERIFY_STATUS_CODES = {1, 2, 15, 25}


def extract_date(timestamp: str) -> str:
    if not timestamp:
        return ""
    return str(timestamp)[:10]


def resolve_verify_mode(status: int, punch: int = 0) -> int:
    if punch:
        return punch
    if status in VERIFY_STATUS_CODES:
        return status
    return 0


def resolve_standard_punch_type(status: int) -> str | None:
    # Solo conservar tipos especiales (descanso / horas extra).
    # Los códigos 0/1 del reloj uFace suelen no distinguir entrada y salida.
    if status in (2, 3, 4, 5):
        return STATUS_MAP[status]
    return None


def needs_sequence_inference(status: int) -> bool:
    return status not in (2, 3, 4, 5)


def parse_hhmm_to_minutes(time_str: str) -> int:
    parts = str(time_str or "00:00").strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return hour * 60 + minute


def minutes_to_hhmm(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def resolve_afternoon_entry_deadline(schedule: dict | None = None) -> str:
    """Hora esperada de entrada tarde (p. ej. 14:30) para calcular tardanza."""
    sched = normalize_schedule(schedule)
    return sched["lunch_end"]


def apply_monthly_tolerance(
    gross_minutes: int, tolerance: int = MONTHLY_TOLERANCE_MINUTES
) -> tuple[int, int]:
    """Resta la tolerancia mensual del total bruto de tardanza por empleado."""
    gross = max(0, int(gross_minutes or 0))
    tol = max(0, int(tolerance or 0))
    applied = min(tol, gross) if gross else 0
    return max(0, gross - tol), applied


def get_punch_windows(schedule: dict | None = None) -> dict[str, tuple[int, int]]:
    """Devuelve ventanas {slot: (inicio_min, fin_min)} para clasificar marcaciones."""
    sched = normalize_schedule(schedule) if schedule else {}
    windows: dict[str, tuple[int, int]] = {}
    for slot, (default_start, default_end) in DEFAULT_PUNCH_WINDOWS.items():
        start_key, end_key = PUNCH_WINDOW_SCHEDULE_KEYS[slot]
        start = parse_hhmm_to_minutes(sched.get(start_key, default_start))
        end = parse_hhmm_to_minutes(sched.get(end_key, default_end))
        windows[slot] = (start, end)
    return windows


def classify_punch_slot_by_time(timestamp: str, schedule: dict | None = None) -> str | None:
    """Clasifica una marcación según la hora del día y las ventanas configuradas."""
    dt = parse_timestamp(timestamp)
    if not dt:
        return None
    minutes = dt.hour * 60 + dt.minute
    for slot, (start, end) in get_punch_windows(schedule).items():
        if start <= minutes <= end:
            return slot
    return None


def slot_to_punch_type(slot: str | None) -> str | None:
    if slot in ("entrada", "entrada_almuerzo"):
        return "Entrada"
    if slot in ("salida_almuerzo", "salida"):
        return "Salida"
    return None


def slot_to_display_label(slot: str | None) -> str | None:
    return SLOT_KEY_TO_LABEL.get(slot or "")


def infer_punch_types(rows: list[dict], schedule: dict | None = None) -> list[dict]:
    """Clasifica marcaciones por ventana horaria; si no encaja, alterna Entrada/Salida."""
    if not rows:
        return rows

    result = [dict(r) for r in rows]
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)

    for i, r in enumerate(result):
        status = int(r.get("status") or 0)
        standard = resolve_standard_punch_type(status)
        r["verify_mode"] = resolve_verify_mode(status, int(r.get("verify_mode") or 0))
        r["verify_label"] = VERIFY_MAP.get(r["verify_mode"], "Biométrico")

        if standard and not needs_sequence_inference(status):
            r["punch_type"] = standard
            if status in (2, 3):
                r["punch_slot"] = "salida_almuerzo" if status == 2 else "entrada_almuerzo"
            continue

        slot = classify_punch_slot_by_time(r.get("timestamp", ""), schedule)
        punch_type = slot_to_punch_type(slot)
        if slot and punch_type:
            r["punch_slot"] = slot
            r["punch_type"] = punch_type
            continue

        groups[(str(r.get("user_id", "")), extract_date(r.get("timestamp", "")))].append(i)

    for indices in groups.values():
        indices.sort(key=lambda idx: result[idx].get("timestamp", ""))
        for n, idx in enumerate(indices):
            r = result[idx]
            r["punch_type"] = "Entrada" if n % 2 == 0 else "Salida"

    return result


def enrich_record(r: dict) -> dict:
    row = dict(r)
    status = int(row.get("status") or 0)
    punch = int(row.get("verify_mode") or 0)
    row["verify_mode"] = resolve_verify_mode(status, punch)
    row["verify_label"] = VERIFY_MAP.get(row["verify_mode"], "Biométrico")
    standard = resolve_standard_punch_type(status)
    if standard and not needs_sequence_inference(status):
        row["punch_type"] = row.get("punch_type") or standard
    if not row.get("punch_slot"):
        slot = classify_punch_slot_by_time(row.get("timestamp", ""))
        if slot:
            row["punch_slot"] = slot
            row["punch_type"] = row.get("punch_type") or slot_to_punch_type(slot)
    row["punch_slot_label"] = slot_to_display_label(row.get("punch_slot"))
    return row


def parse_timestamp(ts: str) -> datetime | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts[:19] if len(ts) > 16 else ts, fmt)
        except ValueError:
            continue
    return None


def time_on_date(date_str: str, time_str: str) -> datetime:
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")


def minutes_late(actual: datetime, expected: datetime) -> int:
    delta = (actual - expected).total_seconds() / 60
    return max(0, int(delta))


def normalize_schedule(schedule: dict | None) -> dict:
    base = dict(DEFAULT_SCHEDULE)
    base.update(merge_work_days(schedule))
    if schedule:
        base.update({k: schedule[k] for k in base if k in schedule and schedule[k] is not None})
    return base


def is_laborable_day(date_str: str, work_day_schedule: dict | None, holiday_dates: set[str] | None) -> dict:
    return classify_day(date_str, work_day_schedule, holiday_dates)


def resolve_expected_slots(marcaciones: list[dict], schedule: dict | None = None) -> dict[str, str | None]:
    """Asigna las 4 marcaciones del día según ventanas horarias."""
    slots: dict[str, str | None] = {key: None for _label, key in EXPECTED_PUNCH_SLOTS}
    for m in sorted(marcaciones, key=lambda x: x.get("timestamp", "")):
        ts = m.get("timestamp", "")
        if not ts:
            continue
        slot = m.get("slot") or m.get("punch_slot") or classify_punch_slot_by_time(ts, schedule)
        if slot in slots and not slots[slot]:
            slots[slot] = ts
    return slots


def analyze_punch_completeness(
    marcaciones: list[dict],
    *,
    dia_laborable: bool,
    entrada: str | None = None,
    salida_almuerzo: str | None = None,
    entrada_almuerzo: str | None = None,
    salida: str | None = None,
    subsanados: set[str] | None = None,
    subsanaciones: list[dict] | None = None,
) -> dict:
    """Evalúa si el empleado tiene las 4 marcaciones diarias esperadas."""
    total = len(marcaciones)
    base = {
        "total_marcaciones": total,
        "marcaciones_esperadas": EXPECTED_PUNCH_COUNT if dia_laborable else 0,
        "marcaciones_faltantes": [],
        "marcaciones_faltantes_slots": [],
        "marcaciones_extras": 0,
        "estado_marcaciones": "No aplica",
        "observacion_marcaciones": "",
        "subsanaciones": list(subsanaciones or []),
        "puede_subsanar": False,
    }
    if not dia_laborable:
        return base

    slots = {
        "entrada": entrada,
        "salida_almuerzo": salida_almuerzo,
        "entrada_almuerzo": entrada_almuerzo,
        "salida": salida,
    }
    missing_keys = [key for _label, key in EXPECTED_PUNCH_SLOTS if not slots.get(key)]
    subsanados = subsanados or set()
    pending_keys = [key for key in missing_keys if key not in subsanados]
    pending = [SLOT_KEY_TO_LABEL[key] for key in pending_keys]
    covered = [SLOT_KEY_TO_LABEL[key] for key in missing_keys if key in subsanados]
    extras = max(0, total - EXPECTED_PUNCH_COUNT)
    base["marcaciones_faltantes"] = pending
    base["marcaciones_faltantes_slots"] = pending_keys
    base["marcaciones_extras"] = extras

    if total == 0:
        base["estado_marcaciones"] = "Sin marcaciones"
        base["observacion_marcaciones"] = "Sin marcaciones — RRHH debe verificar papeleta o ausencia"
        return base

    if not pending_keys and not extras:
        if covered:
            base["estado_marcaciones"] = "Completo subsanado"
            reasons = "; ".join(
                f"{SLOT_KEY_TO_LABEL.get(r.get('missing_slot', ''), r.get('missing_slot', ''))}"
                f"{': ' + r['reason'] if r.get('reason') else ''}"
                for r in (subsanaciones or [])
            )
            base["observacion_marcaciones"] = f"Subsanado por RRHH — {reasons}" if reasons else "Subsanado por RRHH"
        else:
            base["estado_marcaciones"] = "Completo"
            base["observacion_marcaciones"] = ""
        return base

    if pending_keys and extras:
        base["estado_marcaciones"] = "Incompleto con exceso"
        base["observacion_marcaciones"] = (
            f"Faltan {len(pending_keys)} marcación(es): {', '.join(pending)}; "
            f"tiene {extras} marcación(es) de más — revisar papeleta y duplicados"
        )
        return base

    if pending_keys:
        if total == 3:
            faltantes_txt = ", ".join(pending)
            base["estado_marcaciones"] = "Tres marcaciones"
            base["puede_subsanar"] = True
            if len(pending_keys) == 1:
                base["observacion_marcaciones"] = (
                    f"Tiene 3 de 4 marcaciones — falta {pending[0]} — "
                    "RRHH puede subsanar con papeleta o justificación"
                )
            else:
                base["observacion_marcaciones"] = (
                    f"Tiene 3 marcaciones — no encajan en todos los horarios: faltan {faltantes_txt}. "
                    "RRHH puede subsanar una marcación a la vez con papeleta"
                )
            return base
        base["estado_marcaciones"] = "Incompleto"
        base["puede_subsanar"] = len(pending_keys) == 1
        base["observacion_marcaciones"] = (
            f"Faltan {len(pending_keys)} marcación(es): {', '.join(pending)} — "
            + (
                "RRHH puede subsanar la marcación faltante con papeleta"
                if base["puede_subsanar"]
                else "RRHH debe verificar papeleta o justificación"
            )
        )
        return base

    if extras:
        base["estado_marcaciones"] = "Exceso"
        base["observacion_marcaciones"] = (
            f"Tiene {total} marcaciones ({extras} de más) — revisar duplicados o salidas extras"
        )
        return base

    base["estado_marcaciones"] = "Completo"
    base["observacion_marcaciones"] = ""
    return base


def resolve_schedule(
    user_id: str,
    sede_id: int | None = None,
    schedules_by_sede_id: dict | None = None,
    schedules_by_user_id: dict | None = None,
) -> tuple[dict | None, str]:
    schedules_by_user_id = schedules_by_user_id or {}
    schedules_by_sede_id = schedules_by_sede_id or {}
    uid = str(user_id)
    if uid in schedules_by_user_id:
        return schedules_by_user_id[uid], "personalizado"
    if sede_id is not None and sede_id in schedules_by_sede_id:
        return schedules_by_sede_id[sede_id], "sede"
    return None, "sede"


def _attach_punch_analysis(
    result: dict,
    marcaciones: list[dict],
    *,
    dia_laborable: bool,
    remedies_lookup: dict | None = None,
    schedule: dict | None = None,
) -> dict:
    uid = str(result.get("user_id", ""))
    date = extract_date(result.get("entrada") or "")
    if not date and marcaciones:
        date = extract_date(marcaciones[0].get("timestamp", ""))

    subsanaciones: list[dict] = []
    subsanados: set[str] = set()
    if remedies_lookup and uid and date:
        subsanaciones = list(remedies_lookup.get((uid, date), []))
        subsanados = {r["missing_slot"] for r in subsanaciones if r.get("missing_slot")}

    slots = resolve_expected_slots(marcaciones, schedule)
    punch = analyze_punch_completeness(
        marcaciones,
        dia_laborable=dia_laborable,
        subsanados=subsanados,
        subsanaciones=subsanaciones,
        **slots,
    )
    result.update(punch)
    return result


def enrich_with_schedule(
    person: dict,
    schedule: dict | None,
    *,
    work_day_schedule: dict | None = None,
    holiday_dates: set[str] | None = None,
    remedies_lookup: dict | None = None,
) -> dict:
    """Calcula tardanza de entrada y regreso de almuerzo según horario de la sede."""
    result = dict(person)
    sched = normalize_schedule(schedule)
    work_sched = work_day_schedule or schedule
    marcaciones = result.pop("marcaciones", [])
    date = extract_date(result.get("entrada") or "")
    if not date and marcaciones:
        date = extract_date(marcaciones[0].get("timestamp", ""))

    empty = {
        "hora_esperada": sched["entry_time"],
        "hora_almuerzo_inicio": sched["lunch_start"],
        "hora_almuerzo_fin": sched["lunch_end"],
        "tardanza_minutos": 0,
        "tardanza_almuerzo_minutos": 0,
        "tardanza_total_minutos": 0,
        "salida_almuerzo": None,
        "entrada_almuerzo": None,
        "estado_asistencia": "Sin entrada",
        "tipo_dia": "sin_fecha",
        "dia_laborable": False,
    }

    day_info = is_laborable_day(date, work_sched, holiday_dates)
    result["tipo_dia"] = day_info["tipo_dia"]
    result["dia_laborable"] = day_info["laborable"]

    if date and not day_info["laborable"]:
        non_work = dict(empty)
        non_work["estado_asistencia"] = day_info["etiqueta"]
        if result.get("entrada"):
            non_work["estado_asistencia"] = f"{day_info['etiqueta']} (con marcación)"
        result.update(non_work)
        return _attach_punch_analysis(
            result, marcaciones, dia_laborable=False, remedies_lookup=remedies_lookup, schedule=sched
        )

    slot_times = resolve_expected_slots(marcaciones, sched)
    if slot_times.get("entrada"):
        result["entrada"] = slot_times["entrada"]
    if slot_times.get("salida"):
        result["salida"] = slot_times["salida"]

    if not result.get("entrada") or not date:
        result.update(empty)
        result["tipo_dia"] = day_info.get("tipo_dia", "sin_fecha")
        result["dia_laborable"] = day_info.get("laborable", False)
        return _attach_punch_analysis(
            result,
            marcaciones,
            dia_laborable=result["dia_laborable"],
            remedies_lookup=remedies_lookup,
            schedule=sched,
        )

    entrada_dt = parse_timestamp(result["entrada"])
    if not entrada_dt:
        result.update(empty)
        result["estado_asistencia"] = "Sin entrada"
        return _attach_punch_analysis(
            result, marcaciones, dia_laborable=True, remedies_lookup=remedies_lookup, schedule=sched
        )

    entry_expected = time_on_date(date, sched["entry_time"]) + timedelta(minutes=int(sched["grace_minutes"]))
    tardanza_entrada = minutes_late(entrada_dt, entry_expected)

    salida_almuerzo = slot_times.get("salida_almuerzo")
    entrada_almuerzo = slot_times.get("entrada_almuerzo")
    tardanza_almuerzo = 0

    afternoon_deadline = resolve_afternoon_entry_deadline(sched)
    if entrada_almuerzo:
        entrada_alm_dt = parse_timestamp(entrada_almuerzo)
        lunch_expected = time_on_date(date, afternoon_deadline) + timedelta(
            minutes=int(sched["lunch_grace_minutes"])
        )
        if entrada_alm_dt:
            tardanza_almuerzo = minutes_late(entrada_alm_dt, lunch_expected)

    total = tardanza_entrada + tardanza_almuerzo
    if tardanza_entrada and tardanza_almuerzo:
        estado = "Tardanza entrada y almuerzo"
    elif tardanza_entrada:
        estado = "Tardanza entrada"
    elif tardanza_almuerzo:
        estado = "Tardanza almuerzo"
    else:
        estado = "Puntual"

    result.update({
        "hora_esperada": sched["entry_time"],
        "hora_almuerzo_inicio": sched["lunch_start"],
        "hora_almuerzo_fin": afternoon_deadline,
        "tardanza_minutos": tardanza_entrada,
        "tardanza_almuerzo_minutos": tardanza_almuerzo,
        "tardanza_total_minutos": total,
        "salida_almuerzo": salida_almuerzo,
        "entrada_almuerzo": entrada_almuerzo,
        "estado_asistencia": estado,
        "tipo_dia": "laborable",
        "dia_laborable": True,
    })
    return _attach_punch_analysis(
        result, marcaciones, dia_laborable=True, remedies_lookup=remedies_lookup, schedule=sched
    )


def build_daily_summary(
    rows: list[dict],
    schedules_by_sede_id: dict | None = None,
    sede_name_to_id: dict | None = None,
    schedules_by_user_id: dict | None = None,
    global_holidays: set[str] | None = None,
    holidays_by_sede: dict | None = None,
    remedies_lookup: dict | None = None,
) -> list[dict]:
    """Resumen diario con entrada, salida, almuerzo y minutos de tardanza."""
    inferred = infer_punch_types(rows)
    by_person: dict[tuple[str, str], dict] = {}
    schedules_by_sede_id = schedules_by_sede_id or {}
    schedules_by_user_id = schedules_by_user_id or {}
    sede_name_to_id = sede_name_to_id or {}
    global_holidays = global_holidays or set()
    holidays_by_sede = holidays_by_sede or {}

    for r in inferred:
        uid = str(r.get("user_id", ""))
        day = extract_date(r.get("timestamp", ""))
        if not uid or not day:
            continue
        key = (uid, day)
        if key not in by_person:
            by_person[key] = {
                "user_id": uid,
                "work_date": day,
                "user_name": r.get("user_name") or "",
                "sede_name": r.get("sede_name") or "—",
                "sede_id": r.get("sede_id"),
                "device_name": r.get("device_name") or "—",
                "total": 0,
                "entrada": None,
                "salida": None,
                "marcaciones": [],
            }
        p = by_person[key]
        p["total"] += 1
        if r.get("user_name"):
            p["user_name"] = r["user_name"]
        if r.get("sede_name"):
            p["sede_name"] = r["sede_name"]
        if r.get("sede_id"):
            p["sede_id"] = r["sede_id"]
        ts = r.get("timestamp", "")
        tipo = r.get("punch_type", "")
        slot = r.get("punch_slot") or classify_punch_slot_by_time(ts)
        if slot == "entrada" and (p["entrada"] is None or ts < p["entrada"]):
            p["entrada"] = ts
        elif slot == "salida" and (p["salida"] is None or ts > p["salida"]):
            p["salida"] = ts
        elif tipo == "Entrada" and (p["entrada"] is None or ts < p["entrada"]):
            p["entrada"] = ts
        elif tipo == "Salida" and (p["salida"] is None or ts > p["salida"]):
            p["salida"] = ts
        p["marcaciones"].append({"timestamp": ts, "tipo": tipo, "slot": slot})

    summary = []
    for person in sorted(by_person.values(), key=lambda x: (x.get("work_date", ""), x.get("user_name") or x["user_id"])):
        sede_id = person.get("sede_id") or sede_name_to_id.get(person.get("sede_name"))
        schedule, horario_tipo = resolve_schedule(
            person["user_id"], sede_id, schedules_by_sede_id, schedules_by_user_id
        )
        sede_schedule = schedules_by_sede_id.get(sede_id) if sede_id else None
        holiday_dates = set(global_holidays)
        if sede_id is not None:
            holiday_dates |= holidays_by_sede.get(sede_id, set())
        row = enrich_with_schedule(
            person,
            schedule,
            work_day_schedule=sede_schedule or schedule,
            holiday_dates=holiday_dates,
            remedies_lookup=remedies_lookup,
        )
        row["horario_aplicado"] = horario_tipo
        summary.append(row)
    return summary


def build_tardiness_report(
    rows: list[dict],
    schedules_by_sede_id: dict | None = None,
    sede_name_to_id: dict | None = None,
    schedules_by_user_id: dict | None = None,
    global_holidays: set[str] | None = None,
    holidays_by_sede: dict | None = None,
    remedies_lookup: dict | None = None,
) -> list[dict]:
    """Agrupa marcaciones por empleado y día, calculando tardanzas del período."""
    inferred = infer_punch_types(rows)
    by_day: dict[tuple[str, str], list[dict]] = defaultdict(list)
    sede_name_to_id = sede_name_to_id or {}

    for r in inferred:
        uid = str(r.get("user_id", ""))
        day = extract_date(r.get("timestamp", ""))
        if uid and day:
            by_day[(uid, day)].append(r)

    report = []
    for (uid, day), day_rows in sorted(by_day.items(), key=lambda x: (x[0][1], x[0][0])):
        sample = day_rows[0]
        sede_name = sample.get("sede_name") or "—"
        mini_summary = build_daily_summary(
            day_rows,
            schedules_by_sede_id,
            sede_name_to_id,
            schedules_by_user_id,
            global_holidays,
            holidays_by_sede,
            remedies_lookup,
        )
        person = next((p for p in mini_summary if p["user_id"] == uid), None)
        if not person:
            continue
        report.append({
            "date": day,
            "user_id": uid,
            "user_name": person.get("user_name") or "",
            "sede_name": sede_name,
            "entrada": person.get("entrada"),
            "salida": person.get("salida"),
            "salida_almuerzo": person.get("salida_almuerzo"),
            "entrada_almuerzo": person.get("entrada_almuerzo"),
            "tardanza_minutos": person.get("tardanza_minutos", 0),
            "tardanza_almuerzo_minutos": person.get("tardanza_almuerzo_minutos", 0),
            "tardanza_total_minutos": person.get("tardanza_total_minutos", 0),
            "estado_asistencia": person.get("estado_asistencia", ""),
            "hora_esperada": person.get("hora_esperada"),
            "hora_almuerzo_fin": person.get("hora_almuerzo_fin"),
            "horario_aplicado": person.get("horario_aplicado", "sede"),
            "tipo_dia": person.get("tipo_dia"),
            "dia_laborable": person.get("dia_laborable", True),
            "total_marcaciones": person.get("total_marcaciones", 0),
            "marcaciones_esperadas": person.get("marcaciones_esperadas", 0),
            "marcaciones_faltantes": person.get("marcaciones_faltantes", []),
            "marcaciones_faltantes_slots": person.get("marcaciones_faltantes_slots", []),
            "marcaciones_extras": person.get("marcaciones_extras", 0),
            "estado_marcaciones": person.get("estado_marcaciones", ""),
            "observacion_marcaciones": person.get("observacion_marcaciones", ""),
            "subsanaciones": person.get("subsanaciones", []),
            "puede_subsanar": person.get("puede_subsanar", False),
        })
    return report


def aggregate_tardiness_report(report: list[dict]) -> dict:
    """Agrega estadísticas de tardanza; excluye feriados y días no laborables."""
    laborable = [r for r in report if r.get("dia_laborable", True)]

    total_late_days = sum(1 for r in laborable if r.get("tardanza_total_minutos", 0) > 0)

    by_person: dict[str, dict] = defaultdict(lambda: {
        "name": "", "sede": "", "entries": 0,
        "late_days": 0, "late_minutes": 0,
        "late_minutes_entrada": 0, "late_minutes_almuerzo": 0,
        "late_days_entrada": 0, "late_days_almuerzo": 0,
        "punctual_days": 0,
        "non_work_days": 0,
    })

    for r in report:
        uid = r["user_id"]
        p = by_person[uid]
        p["name"] = r.get("user_name") or p["name"]
        p["sede"] = r.get("sede_name") or p["sede"]
        if not r.get("dia_laborable", True):
            p["non_work_days"] += 1
            continue
        p["entries"] += 1
        t_ent = r.get("tardanza_minutos", 0) or 0
        t_alm = r.get("tardanza_almuerzo_minutos", 0) or 0
        t_total = r.get("tardanza_total_minutos", 0) or 0
        if t_ent > 0:
            p["late_days_entrada"] += 1
            p["late_minutes_entrada"] += t_ent
        if t_alm > 0:
            p["late_days_almuerzo"] += 1
            p["late_minutes_almuerzo"] += t_alm
        if t_total > 0:
            p["late_days"] += 1
            p["late_minutes"] += t_total
        else:
            p["punctual_days"] += 1

    by_person_list = []
    total_late_minutes_gross = 0
    total_late_minutes_net = 0
    total_tolerance_applied = 0
    for k, v in by_person.items():
        gross = v["late_minutes"]
        net, applied = apply_monthly_tolerance(gross)
        total_late_minutes_gross += gross
        total_late_minutes_net += net
        total_tolerance_applied += applied
        avg = round(net / v["late_days"], 1) if v["late_days"] else 0
        by_person_list.append({
            "user_id": k,
            "name": v["name"],
            "sede": v["sede"],
            "days_with_attendance": v["entries"],
            "punctual_days": v["punctual_days"],
            "late_days": v["late_days"],
            "late_days_entrada": v["late_days_entrada"],
            "late_days_almuerzo": v["late_days_almuerzo"],
            "late_minutes_gross": gross,
            "late_minutes": net,
            "late_minutes_entrada": v["late_minutes_entrada"],
            "late_minutes_almuerzo": v["late_minutes_almuerzo"],
            "monthly_tolerance_applied": applied,
            "avg_late_minutes": avg,
            "non_work_days": v["non_work_days"],
        })
    by_person_list.sort(key=lambda x: (-x["late_minutes"], x["name"] or x["user_id"]))

    return {
        "total_days": len(laborable),
        "late_days": total_late_days,
        "total_late_minutes_gross": total_late_minutes_gross,
        "total_tolerance_applied": total_tolerance_applied,
        "total_late_minutes": total_late_minutes_net,
        "monthly_tolerance_minutes": MONTHLY_TOLERANCE_MINUTES,
        "total_persons": len(by_person_list),
        "persons_with_late": sum(1 for p in by_person_list if p["late_minutes"] > 0),
        "non_work_days": len(report) - len(laborable),
        "by_person": by_person_list,
        "details": report,
    }