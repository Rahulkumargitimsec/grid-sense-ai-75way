"""Operational settings that the alert engine, recommender and demand forecast actually read.

Values live in the system_settings table (key -> text). Anything not saved there falls back to the default below, so a
fresh database behaves sensibly and "reset" is just deleting the row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SystemSetting


@dataclass(frozen=True)
class Spec:
    key: str
    group: str
    label: str
    kind: str  # "number" | "boolean"
    default: float | bool | None
    help: str
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    optional: bool = False  # blank means "automatic"


SPECS: tuple[Spec, ...] = (
    Spec("demand.high_mw", "Demand thresholds", "High demand mark", "number", None,
         "Hourly load above this is 'high'. Leave blank to use the model's automatic mark (top 10% of the last year's hours).", "MW", 1000, 15000, optional=True),
    Spec("demand.critical_mw", "Demand thresholds", "Critical demand mark", "number", None,
         "Load above this is 'critical'. Blank = top 2.5% of the last year's hours.", "MW", 1000, 15000, optional=True),
    Spec("demand.low_mw", "Demand thresholds", "Low demand mark", "number", None,
         "Load below this is 'low'. Blank = bottom 10% of the last year's hours.", "MW", 500, 10000, optional=True),

    Spec("alerts.forecast_days", "Alerts", "Days ahead to alert on", "number", 3, "Raise demand alerts for forecast days within this many days.", "days", 1, 7),
    Spec("alerts.low_demand_enabled", "Alerts", "Alert on low demand days", "boolean", True, "Warn when the forecast minimum falls below the low mark (surplus power, backing-down)."),
    Spec("alerts.frequency_low_hz", "Alerts", "Frequency lower limit", "number", 49.90, "IEGC band lower edge. Below it the grid is short of generation.", "Hz", 49.5, 50.0),
    Spec("alerts.frequency_high_hz", "Alerts", "Frequency upper limit", "number", 50.05, "IEGC band upper edge. Above it there is surplus generation.", "Hz", 50.0, 50.5),
    Spec("alerts.overdraw_mw", "Alerts", "Delhi overdrawal limit", "number", 250, "Alert when Delhi draws this much more than its schedule (costly deviation charges).", "MW", 25, 2000),
    Spec("alerts.discom_overdraw_mw", "Alerts", "DISCOM overdrawal limit", "number", 150, "Alert when a single DISCOM overdraws its schedule by this much.", "MW", 10, 1000),
    Spec("alerts.stale_data_minutes", "Alerts", "Stale SCADA data after", "number", 30, "Alert when no fresh SLDC capture has arrived for this long.", "min", 10, 720),

    Spec("recommendations.enabled", "Recommendations", "Generate recommendations automatically", "boolean", True, "Create peak, purchase, DISCOM and maintenance actions from the forecast every hour."),
    Spec("recommendations.power_price_inr_mwh", "Recommendations", "Peak power price", "number", 10000, "Price of marginal power at peak (exchange / DSM rate), used for savings estimates.", "₹/MWh", 0, 50000),
    Spec("recommendations.demand_response_pct", "Recommendations", "Demand response potential", "number", 3, "Share of peak load that can realistically be shifted or curtailed (commercial ACs, pumping, EV charging).", "% of load", 0, 20),
)
BY_KEY = {spec.key: spec for spec in SPECS}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _parse(spec: Spec, raw: str | None):
    if raw is None or raw.strip() == "":
        return spec.default
    if spec.kind == "boolean":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return float(raw)


def validate(key: str, raw: str) -> str:
    """Normalised text to store, or ValueError with a message fit for the user."""
    spec = BY_KEY.get(key)
    if spec is None:
        return raw  # free-form admin setting
    if raw.strip() == "":
        if spec.optional or spec.default is not None:
            return ""
        raise ValueError(f"{spec.label} needs a value.")
    if spec.kind == "boolean":
        if raw.strip().lower() not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            raise ValueError(f"{spec.label} must be true or false.")
        return "true" if raw.strip().lower() in ("1", "true", "yes", "on") else "false"
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{spec.label} must be a number.") from exc
    if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"{spec.label} must be between {spec.minimum:g} and {spec.maximum:g} {spec.unit}.".strip())
    return f"{value:g}"


def values(db: Session) -> dict:
    stored = {item.key: item.value for item in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(list(BY_KEY))))}
    result = {}
    for spec in SPECS:
        try:
            result[spec.key] = _parse(spec, stored.get(spec.key))
        except ValueError:
            result[spec.key] = spec.default
    return result


def describe(db: Session) -> list[dict]:
    stored = {item.key: item for item in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(list(BY_KEY))))}
    current = values(db)
    return [{
        "key": spec.key, "group": spec.group, "label": spec.label, "kind": spec.kind, "unit": spec.unit, "help": spec.help,
        "minimum": spec.minimum, "maximum": spec.maximum, "optional": spec.optional, "default": spec.default,
        "value": current[spec.key], "is_default": spec.key not in stored or stored[spec.key].value.strip() == "",
        "updated_by": stored[spec.key].updated_by if spec.key in stored else None,
        "updated_at": _utc(stored[spec.key].updated_at) if spec.key in stored else None,
    } for spec in SPECS]


def demand_thresholds(db: Session, automatic: dict) -> dict:
    """The model's percentile-based marks with any admin overrides applied."""
    current = values(db)
    result = dict(automatic)
    for name in ("high_mw", "critical_mw", "low_mw"):
        override = current[f"demand.{name}"]
        result[f"{name}_automatic"] = automatic[name]
        if override is not None:
            result[name] = float(override)
        result[f"{name}_overridden"] = override is not None
    return result
