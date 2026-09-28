"""Read models for the live grid view: what the website shows, assembled from the collected SLDC, weather and calendar data.

Freshness: the live endpoint serves stored data immediately and, when the newest capture is older than
LIVE_MAX_AGE_SECONDS, refreshes it from SLDC in the background (one refresh at a time, whatever the traffic).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SldcDailySummary, SldcLoad, SldcRealtimeReading, SldcSnapshot, WeatherHourly
from . import sldc_collector, sldc_realtime_collector

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
LIVE_MAX_AGE_SECONDS = 60
LOAD_MAX_AGE = timedelta(minutes=10)
SUBSTATION_CACHE_SECONDS = 60
DISCOM_NAMES = {
    "BRPL": "BSES Rajdhani", "BYPL": "BSES Yamuna", "NDPL": "Tata Power Delhi", "NDMC": "New Delhi Mun. Council",
    "MES": "Military Engineer Services", "RAILWAY": "Railways",
}
DISCOM_ORDER = list(DISCOM_NAMES)
READING_FIELDS = ("schedule_mw", "actual_mw", "deviation_mw", "mvar", "voltage_kv", "load_mw", "status")

_refresh_lock = threading.Lock()
_substation_cache: dict[str, tuple[float, list[dict]]] = {}
_fy_cache: dict[str, tuple[float, list[dict]]] = {}
FY_CACHE_SECONDS = 600


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


# ---- freshness ------------------------------------------------------------------------------------------------------

def latest_fetch_age_seconds(db: Session) -> float | None:
    fetched = db.scalar(select(func.max(SldcSnapshot.fetched_at)))
    if fetched is None:
        return None
    if fetched.tzinfo is None:  # SQLite drops the zone; values are written in UTC
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched).total_seconds()


def refresh_if_stale(db: Session) -> bool:
    """Returns True when a refresh ran. Non-blocking for concurrent callers: if one is running, skip."""
    age = latest_fetch_age_seconds(db)
    last_load = db.scalar(select(func.max(SldcLoad.slot_at)))
    load_stale = last_load is None or now_ist() - last_load > LOAD_MAX_AGE
    if age is not None and age < LIVE_MAX_AGE_SECONDS and not load_stale:
        return False
    if not _refresh_lock.acquire(blocking=False):
        return False
    try:
        if age is None or age >= LIVE_MAX_AGE_SECONDS:
            sldc_realtime_collector.poll()
        if load_stale:
            sldc_collector.sync()
        return True
    except Exception as exc:
        log.warning("live refresh failed: %s", exc)
        return False
    finally:
        _refresh_lock.release()


# ---- live snapshot --------------------------------------------------------------------------------------------------

def _financial_year(day: datetime) -> str:
    start = day.year if day.month >= 4 else day.year - 1
    return f"FY {start}–{str(start + 1)[2:]}"


def financial_year_peaks(db: Session) -> list[dict]:
    """Highest load per Indian financial year (Apr–Mar). SLDC's daily profile has the exact second; where it has gaps
    the 5-minute SCADA series fills in, so a year's peak is the larger of the two. Cached: history barely changes."""
    cached = _fy_cache.get("peaks")
    if cached and time.monotonic() - cached[0] < FY_CACHE_SECONDS:
        return cached[1]
    peaks: dict[str, dict] = {}

    def offer(fy: str, mw: float | None, at: datetime | None, precision: str) -> None:
        if mw is not None and (fy not in peaks or mw > peaks[fy]["peak_mw"]):
            peaks[fy] = {"financial_year": fy, "peak_mw": round(mw, 2), "peak_at": at, "precision": precision}

    for row in db.scalars(select(SldcDailySummary)):
        at = datetime.combine(row.day.date(), datetime.strptime(row.max_time, "%H:%M:%S").time()) if row.max_time else row.day
        offer(_financial_year(row.day), row.max_mw, at, "second")

    month_expr = func.strftime("%Y-%m", SldcLoad.slot_at) if db.bind.dialect.name == "sqlite" else func.to_char(SldcLoad.slot_at, "YYYY-MM")
    best_month: dict[str, tuple[float, str]] = {}
    for month, max_mw in db.execute(select(month_expr, func.max(SldcLoad.delhi_mw)).group_by(month_expr)):
        if max_mw is None:
            continue
        fy = _financial_year(datetime.strptime(month, "%Y-%m"))
        if fy not in best_month or max_mw > best_month[fy][0]:
            best_month[fy] = (max_mw, month)
    for fy, (max_mw, month) in best_month.items():
        if fy in peaks and peaks[fy]["peak_mw"] >= max_mw:
            continue
        at = db.scalar(select(SldcLoad.slot_at).where(SldcLoad.delhi_mw == max_mw, month_expr == month).limit(1))
        offer(fy, max_mw, at, "5-minute")

    result = sorted(peaks.values(), key=lambda row: row["financial_year"])
    _fy_cache["peaks"] = (time.monotonic(), result)
    return result


def _load_at(db: Session, slot: datetime) -> float | None:
    return db.scalar(select(SldcLoad.delhi_mw).where(SldcLoad.slot_at <= slot, SldcLoad.slot_at > slot - timedelta(minutes=15)).order_by(SldcLoad.slot_at.desc()).limit(1))


def live_state(db: Session) -> dict:
    snapshot = db.scalar(select(SldcSnapshot).order_by(SldcSnapshot.captured_at.desc()).limit(1))
    captured = db.scalar(select(func.max(SldcRealtimeReading.captured_at)))
    readings: dict[str, list[dict]] = {}
    if captured is not None:
        for row in db.scalars(select(SldcRealtimeReading).where(SldcRealtimeReading.captured_at == captured)):
            readings.setdefault(row.category, []).append({"entity": row.entity, **{field: getattr(row, field) for field in READING_FIELDS}})

    snap = None
    comparison = None
    if snapshot is not None:
        snap = {column.name: getattr(snapshot, column.name) for column in snapshot.__table__.columns if column.name != "fetched_at"}
        reference = snapshot.source_time or snapshot.captured_at
        yesterday = _load_at(db, reference - timedelta(days=1))
        if yesterday and snapshot.load_mw:
            comparison = {"yesterday_mw": round(yesterday, 2), "change_pct": round((snapshot.load_mw / yesterday - 1) * 100, 2), "yesterday_at": reference - timedelta(days=1)}
        if snapshot.peak_today_mw is None:  # page-only capture: derive today's extremes from the SCADA series
            today = reference.replace(hour=0, minute=0, second=0, microsecond=0)
            snap["peak_today_mw"], snap["min_today_mw"] = db.execute(select(func.max(SldcLoad.delhi_mw), func.min(SldcLoad.delhi_mw)).where(SldcLoad.slot_at >= today)).one()

    discoms = sorted(readings.get("discom", []), key=lambda row: DISCOM_ORDER.index(row["entity"]) if row["entity"] in DISCOM_ORDER else 99)
    total = sum(row["actual_mw"] or 0 for row in discoms) or None
    for row in discoms:
        row["name"] = DISCOM_NAMES.get(row["entity"], row["entity"])
        row["share_pct"] = round((row["actual_mw"] or 0) / total * 100, 1) if total else None

    fy = financial_year_peaks(db)
    all_time = None
    if snap and snap.get("all_time_peak_mw"):
        all_time = {"peak_mw": snap["all_time_peak_mw"], "peak_at": snap["all_time_peak_at"]}
    elif fy:
        best = max(fy, key=lambda row: row["peak_mw"])
        all_time = {"peak_mw": best["peak_mw"], "peak_at": best["peak_at"]}

    return {
        "server_time": now_ist(),
        "snapshot": snap,
        "vs_yesterday": comparison,
        "readings_captured_at": captured,
        "discoms": discoms,
        "substations": sorted(readings.get("substation", []), key=lambda row: row["entity"].lower()),
        "delhi_generation": sorted(readings.get("delhi_genco", []), key=lambda row: -(row["actual_mw"] or 0)),
        "central_generation": sorted(readings.get("central_genco", []), key=lambda row: -(row["actual_mw"] or 0)),
        "states": sorted(readings.get("state", []), key=lambda row: -(row["load_mw"] or 0)),
        "imports": sorted(readings.get("import", []), key=lambda row: row["entity"]),
        "exports": sorted(readings.get("export", []), key=lambda row: row["entity"]),
        "financial_year_peaks": fy,
        "all_time_peak": all_time,
    }


# ---- load curve & peak vs weather -----------------------------------------------------------------------------------

def daily_curve(db: Session, day: date) -> dict:
    start = datetime.combine(day, datetime.min.time())
    rows = db.scalars(select(SldcLoad).where(SldcLoad.slot_at >= start, SldcLoad.slot_at < start + timedelta(days=1)).order_by(SldcLoad.slot_at)).all()
    entities = {"Delhi": "delhi_mw", "BRPL": "brpl_mw", "BYPL": "bypl_mw", "TPDDL": "ndpl_mw", "MES": "mes_mw", "NDMC": "ndmc_mw"}
    return {
        "date": day.isoformat(),
        "times": [row.slot_at.strftime("%H:%M") for row in rows],
        "series": {name: [getattr(row, column) for row in rows] for name, column in entities.items()},
    }


def peak_vs_weather(db: Session, days: int) -> list[dict]:
    end = now_ist().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=days)
    summary = {row.day.date(): row for row in db.scalars(select(SldcDailySummary).where(SldcDailySummary.day >= start, SldcDailySummary.day < end))}

    day_expr = func.date(SldcLoad.slot_at)
    scada = {date.fromisoformat(str(day)[:10]): (peak, samples) for day, peak, samples in db.execute(
        select(day_expr, func.max(SldcLoad.delhi_mw), func.count()).where(SldcLoad.slot_at >= start, SldcLoad.slot_at < end).group_by(day_expr))}

    weather: dict[date, dict] = {}
    rows = db.execute(select(WeatherHourly.source, WeatherHourly.slot_at, WeatherHourly.temperature_c, WeatherHourly.humidity_pct, WeatherHourly.precipitation_mm)
                      .where(WeatherHourly.slot_at >= start, WeatherHourly.slot_at < end)).all()
    chosen: dict[datetime, tuple] = {}
    for source, slot, temperature, humidity, rain in rows:
        if slot not in chosen or source == "nasa_power":
            chosen[slot] = (temperature, humidity, rain)
    for slot, (temperature, humidity, rain) in chosen.items():
        bucket = weather.setdefault(slot.date(), {"temps": [], "hums": [], "rain": 0.0})
        if temperature is not None:
            bucket["temps"].append(temperature)
        if humidity is not None:
            bucket["hums"].append(humidity)
        bucket["rain"] += rain or 0

    result = []
    day = start.date()
    while day < end.date():
        official = summary.get(day)
        peak, _samples = scada.get(day, (None, 0))
        w = weather.get(day)
        result.append({
            "date": day.isoformat(),
            "peak_mw": round(official.max_mw if official and official.max_mw else peak, 2) if (official and official.max_mw) or peak else None,
            "peak_time": official.max_time if official else None,
            "temp_max_c": round(max(w["temps"]), 1) if w and w["temps"] else None,
            "temp_mean_c": round(sum(w["temps"]) / len(w["temps"]), 1) if w and w["temps"] else None,
            "humidity_mean_pct": round(sum(w["hums"]) / len(w["hums"]), 1) if w and w["hums"] else None,
            "rain_mm": round(w["rain"], 1) if w else None,
        })
        day += timedelta(days=1)
    return result


# ---- transformer detail (live, on demand) ---------------------------------------------------------------------------

def substation_transformers(name: str) -> list[dict]:
    cached = _substation_cache.get(name)
    if cached and time.monotonic() - cached[0] < SUBSTATION_CACHE_SECONDS:
        return cached[1]
    payload = json.loads(sldc_realtime_collector._get(f"{sldc_realtime_collector.API}/grid-data?substation={urllib.parse.quote(name)}"))
    rows = [
        {"transformer": row.get("GD_SUBSTATION"), "mw": row.get("GD_MW"), "mvar": row.get("GD_MVAR"), "voltage_kv": row.get("GD_VOLTAGE"),
         "measured_at": sldc_realtime_collector._utc_to_ist(row.get("GD_DATE"))}
        for row in payload.get("data", [])
    ]
    _substation_cache[name] = (time.monotonic(), rows)
    return rows
