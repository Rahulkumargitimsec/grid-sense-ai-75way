"""Collect Delhi SLDC real-time grid data and the official daily load profile.

Sources:
    https://delhisldc.org/api/*                       JSON API behind https://delhisldc.org/grid-watch/
        dynamic-data, discom-drawl, grid-loading, peak-demand   live values, no history
        load-webprofile-range?fromDate=DD/MM/YYYY&toDate=...   daily peak/min/avg; 404 for months SLDC lacks
    https://www.delhisldc.org/Redirect.aspx?Loc=0804  "Real Time Data" page: generation by plant, states drawl, import/export

SLDC keeps no history of the live values, so every poll is stored and history accrues from the first run:
    sldc_snapshot           one row per 5-minute slot (system totals)
    sldc_realtime_reading   one row per slot per DISCOM / substation / plant / state / feeder
    sldc_daily_summary      one row per day, backfillable from 2018 where SLDC has it

CLI:
    python -m app.services.sldc_realtime_collector poll
    python -m app.services.sldc_realtime_collector daily-backfill --start 2018-01-01
    python -m app.services.sldc_realtime_collector daily-sync
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import unescape

from ..database import Base, SessionLocal, engine
from ..models import SldcDailySummary, SldcRealtimeReading, SldcSnapshot
from .collection import upsert

log = logging.getLogger(__name__)

API = "https://delhisldc.org/api"
REALTIME_PAGE = "https://www.delhisldc.org/Redirect.aspx?Loc=0804"
HEADERS = {"User-Agent": "GridSenseAI-collector/1.0 (research; low-rate)", "Referer": "https://delhisldc.org/grid-watch/"}
IST = timezone(timedelta(hours=5, minutes=30))
SLOT_MINUTES = 5
TABLES = [SldcSnapshot.__table__, SldcRealtimeReading.__table__, SldcDailySummary.__table__]

_TABLE = r'<table[^>]*id="{id}"[^>]*>(.*?)</table>'
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


# ---- fetching -------------------------------------------------------------------------------------------------------

def _get(url: str, timeout: float = 30) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _api(path: str):
    return json.loads(_get(f"{API}/{path}"))


# ---- parsing helpers ------------------------------------------------------------------------------------------------

def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _utc_to_ist(stamp: str | None) -> datetime | None:
    """API stamps look like 2026-09-14T13:10:51.000Z (true UTC); store naive IST like the other SLDC tables."""
    if not stamp:
        return None
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(IST).replace(tzinfo=None)


def slot_of(moment: datetime) -> datetime:
    moment = moment.astimezone(IST).replace(tzinfo=None, second=0, microsecond=0)
    return moment.replace(minute=moment.minute - moment.minute % SLOT_MINUTES)


def table_rows(html: str, table_id: str) -> list[list[str]]:
    match = re.search(_TABLE.format(id=re.escape(table_id)), html, re.S | re.I)
    if not match:
        return []
    rows = ([unescape(_TAG.sub("", cell)).strip() for cell in _CELL.findall(row)] for row in _ROW.findall(match.group(1)))
    return [row for row in rows if row and any(row)]


def _data_rows(rows: list[list[str]], width: int) -> list[list[str]]:
    """Rows of the expected width whose second column is numeric (drops titles and header rows)."""
    return [row for row in rows if len(row) == width and _num(row[1]) is not None]


# ---- parsers --------------------------------------------------------------------------------------------------------

def parse_api_snapshot(dynamic: dict, peak: dict | None) -> dict:
    row = dynamic["data"][0]
    load, schedule, drawal = _num(row.get("DD_CURR_LOAD")), _num(row.get("DD_CURR_SCHEDULE")), _num(row.get("DD_DRAWAL"))
    snapshot = {
        "source_time": _utc_to_ist(row.get("DD_DATE")),
        "load_mw": load,
        "schedule_mw": schedule,
        "drawal_mw": drawal,
        "odud_mw": None if drawal is None or schedule is None else round(drawal - schedule, 3),
        "frequency_hz": _num(row.get("DD_FREQUENCY")),
        "peak_today_mw": _num(row.get("DD_PEAK_LOAD")),
        "peak_today_time": row.get("DD_PEAK_LOAD_TIME"),
        "min_today_mw": _num(row.get("DD_MIN_LOAD")),
        "min_today_time": row.get("DD_MIN_LOAD_TIME"),
    }
    if peak:
        snapshot["all_time_peak_mw"] = _num(peak.get("ALLTIMEPEAK"))
        stamp = " ".join(str(peak.get("ALLTIMEPEAKTIME", "")).split())  # "6/29/2026   15:17:09"
        try:
            snapshot["all_time_peak_at"] = datetime.strptime(stamp, "%m/%d/%Y %H:%M:%S")
        except ValueError:
            snapshot["all_time_peak_at"] = None
    return snapshot


def parse_api_discoms(payload: dict) -> list[dict]:
    return [
        {"category": "discom", "entity": row["DD_DISCOM"], "schedule_mw": _num(row.get("DD_SCHEDULE")), "actual_mw": _num(row.get("DD_DRAWL")), "deviation_mw": _num(row.get("DD_ODUD"))}
        for row in payload.get("data", []) if row.get("DD_DISCOM")
    ]


def parse_api_substations(payload: dict) -> list[dict]:
    return [
        {"category": "substation", "entity": row["DG_GRID"], "actual_mw": _num(row.get("DG_MW")), "mvar": _num(row.get("DG_MVAR")), "voltage_kv": _num(row.get("DG_VOLTAGE")), "status": row.get("DG_STTS")}
        for row in payload.get("data", []) if row.get("DG_GRID")
    ]


def parse_realtime_page(html: str) -> tuple[dict, list[dict]]:
    """Generation by plant, neighbouring states' drawl, Delhi import/export, plus page-level totals as an API fallback."""
    readings: list[dict] = []
    snapshot: dict = {}
    for name, schedule, _approved, actual, ui in _data_rows(table_rows(html, "Table2"), 5):
        if name.lower() == "total":
            snapshot["delhi_generation_mw"] = _num(actual)
        else:
            readings.append({"category": "delhi_genco", "entity": name, "schedule_mw": _num(schedule), "actual_mw": _num(actual), "deviation_mw": _num(ui)})
    for name, schedule, actual in _data_rows(table_rows(html, "ContentPlaceHolder3_dcsgeneration"), 3):
        s, a = _num(schedule), _num(actual)
        readings.append({"category": "central_genco", "entity": name, "schedule_mw": s, "actual_mw": a, "deviation_mw": None if s is None or a is None else a - s})
    for name, schedule, drawl, odud, load in _data_rows(table_rows(html, "Table3"), 5):
        readings.append({"category": "state", "entity": name, "schedule_mw": _num(schedule), "actual_mw": _num(drawl), "deviation_mw": _num(odud), "load_mw": _num(load)})
    for category, table_id in (("import", "ContentPlaceHolder3_DIMPORT"), ("export", "ContentPlaceHolder3_dEXPORT")):
        for name, mw, mvar in _data_rows(table_rows(html, table_id), 3):
            readings.append({"category": category, "entity": name, "actual_mw": _num(mw), "mvar": _num(mvar)})

    load_row = table_rows(html, "Table4")
    if len(load_row) >= 2 and len(load_row[1]) >= 3:
        snapshot.update(load_mw=_num(load_row[1][0]), schedule_mw=_num(load_row[1][1]), drawal_mw=_num(load_row[1][2]))
    freq_row = table_rows(html, "Table5")
    if len(freq_row) >= 2 and len(freq_row[1]) >= 2:
        snapshot.update(frequency_hz=_num(freq_row[1][0]), odud_mw=_num(freq_row[1][1]))
    return snapshot, readings


def parse_daily_profile(payload: list) -> list[dict]:
    rows = []
    for row in payload:
        if str(row.get("ENTITY", "")).lower() != "delhi":
            continue
        rows.append({
            "day": datetime.strptime(row["FORDATE"], "%d/%m/%Y"),
            "max_mw": _num(row.get("MAXVALUE")), "max_time": row.get("MAXVALTIME"),
            "min_mw": _num(row.get("MINVALUE")), "min_time": row.get("MINVALTIME"),
            "avg_mw": _num(row.get("AVGVALUE")), "samples": row.get("COUNTER"),
        })
    return rows


# ---- collection -----------------------------------------------------------------------------------------------------

def _fill_keys(readings: list[dict]) -> list[dict]:
    """Every row in one INSERT must carry the same columns."""
    columns = ("schedule_mw", "actual_mw", "deviation_mw", "mvar", "voltage_kv", "load_mw", "status")
    return [{column: reading.get(column) for column in columns} | {"category": reading["category"], "entity": reading["entity"][:80]} for reading in readings]


def poll(now: datetime | None = None) -> int:
    """One real-time capture. Each source is optional: whatever answers gets stored."""
    captured_at = slot_of(now or datetime.now(timezone.utc))
    snapshot: dict = {}
    readings: list[dict] = []
    errors = []

    try:
        page_snapshot, page_readings = parse_realtime_page(_get(REALTIME_PAGE))
        snapshot.update(page_snapshot)
        readings += page_readings
    except Exception as exc:
        errors.append(f"realtime page: {exc}")
    try:
        peak = None
        try:
            peak = _api("peak-demand")
        except Exception as exc:
            errors.append(f"peak-demand: {exc}")
        snapshot.update(parse_api_snapshot(_api("dynamic-data"), peak))  # API values are more precise than the page's
    except Exception as exc:
        errors.append(f"dynamic-data: {exc}")
    for path, parser in (("discom-drawl", parse_api_discoms), ("grid-loading", parse_api_substations)):
        try:
            readings += parser(_api(path))
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    if errors:
        log.warning("SLDC realtime partial capture at %s: %s", captured_at, "; ".join(errors))
    if not snapshot and not readings:
        raise RuntimeError("no SLDC realtime source responded")

    columns = [column.name for column in SldcSnapshot.__table__.columns if column.name not in ("captured_at", "fetched_at")]
    with SessionLocal() as db:
        count = 0
        if snapshot:
            count += upsert(db, SldcSnapshot, [{"captured_at": captured_at, **{column: snapshot.get(column) for column in columns}}], ["captured_at"])
        rows = [{"captured_at": captured_at, **reading} for reading in _fill_keys(readings)]
        count += upsert(db, SldcRealtimeReading, rows, ["captured_at", "category", "entity"])
    return count


def fetch_daily(start: date, end: date) -> list[dict]:
    try:
        payload = _api(f"load-webprofile-range?fromDate={start:%d/%m/%Y}&toDate={end:%d/%m/%Y}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # SLDC answers 404 when it has no profile for the range
            return []
        raise
    return parse_daily_profile(payload)


def daily_backfill(start: date, end: date, delay: float = 1.0) -> dict:
    Base.metadata.create_all(bind=engine, tables=TABLES)
    stats = {"months": 0, "empty_months": [], "rows": 0, "failed": []}
    month = date(start.year, start.month, 1)
    with SessionLocal() as db:
        while month <= end:
            next_month = (month + timedelta(days=32)).replace(day=1)
            chunk_start, chunk_end = max(month, start), min(next_month - timedelta(days=1), end)
            try:
                rows = fetch_daily(chunk_start, chunk_end)
                stats["rows"] += upsert(db, SldcDailySummary, rows, ["day"])
                stats["months"] += 1
                if not rows:
                    stats["empty_months"].append(f"{month:%Y-%m}")
                log.info("daily profile %s: %d days", f"{month:%Y-%m}", len(rows))
            except Exception as exc:
                db.rollback()
                stats["failed"].append(f"{month:%Y-%m}")
                log.warning("daily profile %s failed: %s", f"{month:%Y-%m}", exc)
            month = next_month
            time.sleep(delay)
    return stats


def daily_sync() -> int:
    today = datetime.now(IST).date()
    with SessionLocal() as db:
        return upsert(db, SldcDailySummary, fetch_daily(today - timedelta(days=7), today - timedelta(days=1)), ["day"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("poll")
    fill = sub.add_parser("daily-backfill")
    fill.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    fill.add_argument("--end", type=date.fromisoformat, default=None, help="default: yesterday (IST)")
    sub.add_parser("daily-sync")
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine, tables=TABLES)
    if args.command == "poll":
        print({"rows": poll()})
    elif args.command == "daily-backfill":
        print(daily_backfill(args.start, args.end or datetime.now(IST).date() - timedelta(days=1)))
    else:
        print({"rows": daily_sync()})


if __name__ == "__main__":
    main()
