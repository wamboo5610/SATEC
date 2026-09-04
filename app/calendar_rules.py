"""Días laborables y feriados para el cálculo de tardanzas."""

from __future__ import annotations

from datetime import datetime

WORK_DAY_KEYS = (
    "work_monday",
    "work_tuesday",
    "work_wednesday",
    "work_thursday",
    "work_friday",
    "work_saturday",
    "work_sunday",
)

DEFAULT_WORK_DAYS = {
    "work_monday": 1,
    "work_tuesday": 1,
    "work_wednesday": 1,
    "work_thursday": 1,
    "work_friday": 1,
    "work_saturday": 0,
    "work_sunday": 0,
}

# Feriados nacionales Perú 2026 (referencia — se pueden editar en el panel)
PERU_HOLIDAYS_2026 = [
    ("2026-01-01", "Año Nuevo"),
    ("2026-04-02", "Jueves Santo"),
    ("2026-04-03", "Viernes Santo"),
    ("2026-05-01", "Día del Trabajo"),
    ("2026-06-29", "San Pedro y San Pablo"),
    ("2026-07-28", "Fiestas Patrias"),
    ("2026-07-29", "Fiestas Patrias"),
    ("2026-08-30", "Santa Rosa de Lima"),
    ("2026-10-08", "Combate de Angamos"),
    ("2026-11-01", "Todos los Santos"),
    ("2026-12-08", "Inmaculada Concepción"),
    ("2026-12-25", "Navidad"),
]


def merge_work_days(schedule: dict | None) -> dict:
    merged = dict(DEFAULT_WORK_DAYS)
    if schedule:
        for key in WORK_DAY_KEYS:
            if key in schedule and schedule[key] is not None:
                merged[key] = 1 if schedule[key] else 0
    return merged


def classify_day(date_str: str, schedule: dict | None, holiday_dates: set[str] | None = None) -> dict:
    """Clasifica un día: laborable, fin de semana o feriado."""
    holiday_dates = holiday_dates or set()
    if not date_str:
        return {"laborable": False, "tipo_dia": "sin_fecha", "etiqueta": "Sin fecha"}

    if date_str in holiday_dates:
        return {"laborable": False, "tipo_dia": "feriado", "etiqueta": "Feriado"}

    try:
        weekday = datetime.strptime(date_str[:10], "%Y-%m-%d").weekday()
    except ValueError:
        return {"laborable": False, "tipo_dia": "invalido", "etiqueta": "Fecha inválida"}

    work_days = merge_work_days(schedule)
    active = bool(work_days.get(WORK_DAY_KEYS[weekday], 0))
    if active:
        return {"laborable": True, "tipo_dia": "laborable", "etiqueta": "Día laborable"}

    if weekday >= 5:
        return {"laborable": False, "tipo_dia": "fin_de_semana", "etiqueta": "Fin de semana"}
    return {"laborable": False, "tipo_dia": "no_laborable", "etiqueta": "No laborable"}


def work_days_label(schedule: dict | None) -> str:
    work_days = merge_work_days(schedule)
    names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    active = [names[i] for i, key in enumerate(WORK_DAY_KEYS) if work_days.get(key)]
    return ", ".join(active) if active else "Ninguno"