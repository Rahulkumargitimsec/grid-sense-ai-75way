"""Alert engine: turns the collected grid data and the demand forecast into operator alerts.

Runs every few minutes from the collection scheduler. Each check yields findings with a stable dedupe key:
    demand_forecast  high / critical day in the next N days (demand model)        resolves if the risk goes away
    low_demand       forecast minimum below the low mark                             resolves if the risk goes away
    live_load        Delhi load above the high / critical mark right now             resolves when load drops back
    frequency        grid frequency outside the IEGC band                            resolves when back in band
    overdrawal       Delhi or a DISCOM drawing far above schedule                    resolves when back near schedule
    voltage          220 / 400 kV buses (good telemetry) outside IEGC operating limits resolves when back inside
    peak_record      today set a new financial-year or all-time peak                 stays until acknowledged
    data_quality     stale SCADA capture, missing weather forecast, old model        resolves when fresh again
Thresholds come from services/app_settings.py. A live alert that clears and comes back within 30 minutes is reopened
rather than duplicated, so a frequency hovering at the band edge doesn't flood the list.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Alert, SldcDailySummary, SldcLoad, SldcRealtimeReading, SldcSnapshot, WeatherHourly
from . import app_settings, demand_model

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
REOPEN_WITHIN = timedelta(minutes=30)
# IEGC operating voltage limits (kV): 400 kV buses 380–420, 220 kV buses 198–245.
VOLTAGE_LIMITS = {400: (380.0, 420.0), 220: (198.0, 245.0)}
LEGACY_DEMAND_TITLE = __import__("re").compile(r"^(High|Critical) demand expected on \w+ \d{4}-\d{2}-\d{2}$")
DISCOM_LABEL = {"NDPL": "TPDDL"}


@dataclass
class Finding:
    key: str
    category: str
    severity: str
    title: str
    message: str
    link: str


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def _mw(value: float) -> str:
    return f"{value:,.0f} MW"


# ---- checks -------------------------------------------------------------------------------------------------------------

def check_forecast(db: Session, settings: dict, now: datetime) -> list[Finding]:
    try:
        outlook = demand_model.forecast(db)
    except demand_model.ModelNotReady:
        return []
    findings = []
    last_day = (now + timedelta(days=int(settings["alerts.forecast_days"]))).date()
    for day in outlook["days"]:
        date = datetime.fromisoformat(day["date"]).date()
        if date > last_day:
            continue
        label = f"{day['weekday']} {date:%d %b}"
        if day["risk"] in ("high", "critical") and day["peak_at"] > now:
            windows = ", ".join(f"{w['from']:%H:%M}–{w['to']:%H:%M}" for w in day["high_windows"]) or f"around {day['peak_at']:%H:00}"
            findings.append(Finding(
                f"demand:{date}", "demand_forecast", day["risk"],
                f"{'Critical' if day['risk'] == 'critical' else 'High'} demand expected on {label}",
                f"{day['reason']} High between {windows}; chance of high demand {day['probability_high']:.0%}.", "/peak-prediction",
            ))
        if settings["alerts.low_demand_enabled"] and day["low_level"] == "low" and day["min_at"] > now:
            findings.append(Finding(
                f"low:{date}", "low_demand", "medium", f"Low demand expected on {label}",
                f"{day['min_reason']} Consider trimming purchases or backing down generation.", "/peak-prediction",
            ))
    return findings


def _latest_snapshot(db: Session) -> SldcSnapshot | None:
    return db.scalar(select(SldcSnapshot).order_by(SldcSnapshot.captured_at.desc()).limit(1))


def check_live(db: Session, settings: dict, now: datetime) -> list[Finding]:
    snapshot = _latest_snapshot(db)
    if snapshot is None or now - snapshot.captured_at > timedelta(minutes=float(settings["alerts.stale_data_minutes"])):
        return []  # stale data is reported by check_data_quality; don't alert on old values
    findings = []
    stamp = f"{(snapshot.source_time or snapshot.captured_at):%H:%M}"

    try:
        marks = demand_model.thresholds(db)
    except demand_model.ModelNotReady:
        marks = None
    if marks and snapshot.load_mw:
        if snapshot.load_mw >= marks["critical_mw"]:
            findings.append(Finding(f"live_load:{snapshot.captured_at.date()}", "live_load", "critical", "Delhi load above the critical mark",
                                    f"Delhi load is {_mw(snapshot.load_mw)} at {stamp}, above the critical mark of {_mw(marks['critical_mw'])}. Today's peak so far: {_mw(snapshot.peak_today_mw or snapshot.load_mw)}.", "/live-grid"))
        elif snapshot.load_mw >= marks["high_mw"]:
            findings.append(Finding(f"live_load:{snapshot.captured_at.date()}", "live_load", "high", "Delhi load above the high mark",
                                    f"Delhi load is {_mw(snapshot.load_mw)} at {stamp}, above the high mark of {_mw(marks['high_mw'])}.", "/live-grid"))

    if snapshot.frequency_hz:
        low, high = float(settings["alerts.frequency_low_hz"]), float(settings["alerts.frequency_high_hz"])
        if snapshot.frequency_hz < low:
            findings.append(Finding("frequency:low", "frequency", "critical" if snapshot.frequency_hz < low - 0.1 else "high", "Grid frequency below the IEGC band",
                                    f"Frequency is {snapshot.frequency_hz:.2f} Hz at {stamp}, below {low:.2f} Hz: generation is short of demand. Avoid overdrawal now.", "/live-grid"))
        elif snapshot.frequency_hz > high:
            findings.append(Finding("frequency:high", "frequency", "medium", "Grid frequency above the IEGC band",
                                    f"Frequency is {snapshot.frequency_hz:.2f} Hz at {stamp}, above {high:.2f} Hz: surplus power on the grid; underdrawal is being paid less.", "/live-grid"))

    if snapshot.odud_mw is not None and snapshot.odud_mw > float(settings["alerts.overdraw_mw"]):
        findings.append(Finding("overdraw:delhi", "overdrawal", "high", "Delhi overdrawing its schedule",
                                f"Delhi drawal is {_mw(snapshot.drawal_mw or 0)} against a schedule of {_mw(snapshot.schedule_mw or 0)} at {stamp}: {_mw(snapshot.odud_mw)} over.", "/live-grid"))

    captured = db.scalar(select(func.max(SldcRealtimeReading.captured_at)))
    if captured is not None and now - captured <= timedelta(minutes=float(settings["alerts.stale_data_minutes"])):
        readings = db.scalars(select(SldcRealtimeReading).where(SldcRealtimeReading.captured_at == captured)).all()
        limit = float(settings["alerts.discom_overdraw_mw"])
        for row in readings:
            if row.category == "discom" and (row.deviation_mw or 0) > limit:
                name = DISCOM_LABEL.get(row.entity, row.entity)
                findings.append(Finding(f"overdraw:{row.entity}", "overdrawal", "medium", f"{name} overdrawing its schedule",
                                        f"{name} draws {_mw(row.actual_mw or 0)} against a schedule of {_mw(row.schedule_mw or 0)} ({_mw(row.deviation_mw)} over) at {captured:%H:%M}.", "/live-grid"))
        off = []
        for row in readings:
            if row.category != "substation" or row.status != 1 or not row.voltage_kv:
                continue
            level = 400 if row.voltage_kv > 300 else 220
            low_kv, high_kv = VOLTAGE_LIMITS[level]
            if not low_kv <= row.voltage_kv <= high_kv:
                gap = (low_kv - row.voltage_kv) if row.voltage_kv < low_kv else (row.voltage_kv - high_kv)
                off.append((gap / level, f"{row.entity} {row.voltage_kv:.0f} kV on a {level} kV bus (limit {low_kv:.0f}–{high_kv:.0f})"))
        if off:
            off.sort(reverse=True)
            findings.append(Finding("voltage", "voltage", "high" if off[0][0] > 0.03 else "medium",
                                    f"{len(off)} substation{'s' if len(off) > 1 else ''} outside grid-code voltage limits",
                                    "Buses with good telemetry outside IEGC limits: " + "; ".join(text for _, text in off[:8]) + ("…" if len(off) > 8 else "."), "/live-grid"))
    return findings


def check_records(db: Session, now: datetime) -> list[Finding]:
    snapshot = _latest_snapshot(db)
    if snapshot is None or not snapshot.peak_today_mw or snapshot.captured_at.date() != now.date():
        return []
    today = now.date()
    findings = []
    if snapshot.all_time_peak_at and snapshot.all_time_peak_at.date() == today:
        findings.append(Finding(f"record:alltime:{today}", "peak_record", "critical", "New all-time peak demand for Delhi",
                                f"Delhi reached {_mw(snapshot.all_time_peak_mw)} at {snapshot.all_time_peak_at:%H:%M:%S} today, its highest ever.", "/live-grid"))
        return findings
    fy_start = datetime(today.year if today.month >= 4 else today.year - 1, 4, 1)
    previous = db.scalar(select(func.max(SldcDailySummary.max_mw)).where(SldcDailySummary.day >= fy_start, SldcDailySummary.day < datetime.combine(today, datetime.min.time())))
    scada = db.scalar(select(func.max(SldcLoad.delhi_mw)).where(SldcLoad.slot_at >= fy_start, SldcLoad.slot_at < datetime.combine(today, datetime.min.time())))
    best = max(value for value in (previous, scada, 0.0) if value is not None)
    if best and snapshot.peak_today_mw > best:
        findings.append(Finding(f"record:fy:{today}", "peak_record", "high", "New peak for this financial year",
                                f"Today's peak of {_mw(snapshot.peak_today_mw)} at {snapshot.peak_today_time} beats this financial year's previous high of {_mw(best)}.", "/live-grid"))
    return findings


def check_data_quality(db: Session, settings: dict, now: datetime) -> list[Finding]:
    findings = []
    stale = timedelta(minutes=float(settings["alerts.stale_data_minutes"]))
    snapshot = _latest_snapshot(db)
    if snapshot is None or now - snapshot.captured_at > stale:
        age = "no capture yet" if snapshot is None else f"last capture {snapshot.captured_at:%d %b %H:%M}"
        findings.append(Finding("stale:realtime", "data_quality", "high", "Live SCADA data is stale",
                                f"No fresh Delhi SLDC real-time data ({age}). Check that the collection worker is running and delhisldc.org is reachable.", "/datasets"))
    last_load = db.scalar(select(func.max(SldcLoad.slot_at)))
    # SLDC publishes the 5-minute series about an hour late, so allow for that before calling it stale.
    if last_load is None or now - last_load > stale + timedelta(minutes=90):
        findings.append(Finding("stale:load", "data_quality", "medium", "5-minute load history is behind",
                                f"The latest stored load slot is {'missing' if last_load is None else f'{last_load:%d %b %H:%M}'}; forecasts use older demand levels until it catches up.", "/datasets"))
    horizon = db.scalar(select(func.max(WeatherHourly.slot_at)).where(WeatherHourly.source == "open_meteo"))
    if horizon is None or horizon < now + timedelta(hours=24):
        findings.append(Finding("stale:weather", "data_quality", "medium", "Weather forecast is missing or short",
                                "Open-Meteo forecast covers less than the next 24 hours, so demand forecasts for the coming days are incomplete.", "/datasets"))
    if demand_model.ARTIFACT.exists():
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(demand_model.ARTIFACT.stat().st_mtime, timezone.utc)
        if age > timedelta(hours=48):
            findings.append(Finding("model:stale", "data_quality", "low", "Demand model not retrained recently",
                                    f"GridSense AI was last trained {age.days} day(s) ago; it normally retrains daily.", "/model-training"))
    else:
        findings.append(Finding("model:missing", "data_quality", "medium", "Demand model not trained",
                                "GridSense AI has not been trained yet, so demand forecasts and forecast alerts are unavailable.", "/model-training"))
    suspect = 0
    captured = db.scalar(select(func.max(SldcRealtimeReading.captured_at)))
    if captured is not None:
        suspect = db.scalar(select(func.count()).select_from(SldcRealtimeReading).where(SldcRealtimeReading.captured_at == captured, SldcRealtimeReading.category == "substation", SldcRealtimeReading.status != 1)) or 0
    if suspect >= 5:
        findings.append(Finding("telemetry:rtu", "data_quality", "low", f"{suspect} substations report suspect telemetry",
                                f"{suspect} substation RTUs are flagged suspect by SLDC, so their MW and voltage readings may be wrong.", "/live-grid"))
    return findings


def evaluate(db: Session, now: datetime | None = None) -> list[Finding]:
    now = now or now_ist()
    settings = app_settings.values(db)
    findings: list[Finding] = []
    for name, check in (("forecast", lambda: check_forecast(db, settings, now)), ("live", lambda: check_live(db, settings, now)),
                        ("records", lambda: check_records(db, now)), ("data_quality", lambda: check_data_quality(db, settings, now))):
        try:
            findings.extend(check())
        except Exception as exc:  # one broken check must not silence the others
            log.warning("alert check %s failed: %s", name, exc)
    return findings


# ---- persistence --------------------------------------------------------------------------------------------------------

PERSISTENT_CATEGORIES = {"peak_record"}  # historical facts: never auto-resolved


def run(db: Session, now: datetime | None = None) -> dict:
    now = now or now_ist()
    findings = {finding.key: finding for finding in evaluate(db, now)}
    utc_now = datetime.now(timezone.utc)
    raised = updated = resolved = reopened = 0

    # Forecast alerts raised before this engine (title "High demand expected on Wednesday 2026-09-16") are superseded.
    for legacy in db.scalars(select(Alert).where(Alert.dedupe_key.is_(None), Alert.resolved_at.is_(None))):
        if LEGACY_DEMAND_TITLE.match(legacy.title):
            legacy.resolved_at = utc_now
            resolved += 1
    active = {alert.dedupe_key: alert for alert in db.scalars(select(Alert).where(Alert.dedupe_key.is_not(None), Alert.resolved_at.is_(None)))}
    for key, finding in findings.items():
        alert = active.get(key)
        if alert is None:
            recent = db.scalar(select(Alert).where(Alert.dedupe_key == key, Alert.resolved_at.is_not(None)).order_by(Alert.resolved_at.desc()).limit(1))
            resolved_at = recent.resolved_at.replace(tzinfo=timezone.utc) if recent is not None and recent.resolved_at and recent.resolved_at.tzinfo is None else (recent.resolved_at if recent is not None else None)
            if recent is not None and resolved_at and utc_now - resolved_at < REOPEN_WITHIN:
                alert, recent.resolved_at = recent, None
                reopened += 1
            else:
                # Adopt an older alert with the same title (raised before the engine existed) instead of duplicating it.
                alert = db.scalar(select(Alert).where(Alert.title == finding.title, Alert.dedupe_key.is_(None), Alert.acknowledged.is_(False)).limit(1))
                if alert is None:
                    alert = Alert(title=finding.title, severity=finding.severity, message=finding.message, category=finding.category, dedupe_key=key, link=finding.link)
                    db.add(alert)
                    raised += 1
                    continue
                alert.dedupe_key = key
        changed = alert.message != finding.message or alert.severity != finding.severity or alert.title != finding.title
        if SEVERITY_RANK[finding.severity] > SEVERITY_RANK.get(alert.severity, 0):
            alert.acknowledged = False  # escalation needs a fresh look even if the milder alert was acknowledged
        alert.title, alert.severity, alert.message, alert.category, alert.link = finding.title, finding.severity, finding.message, finding.category, finding.link
        updated += int(changed)

    for key, alert in active.items():
        if key in findings or alert.category in PERSISTENT_CATEGORIES:
            continue
        alert.resolved_at = utc_now
        resolved += 1
    db.commit()
    return {"raised": raised, "updated": updated, "resolved": resolved, "reopened": reopened, "active_conditions": len(findings)}
