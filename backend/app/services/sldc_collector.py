"""Collect Delhi SLDC 5-minute SCADA load into the sldc_load table.

Source: https://www.delhisldc.org/Loaddata.aspx?mode=DD/MM/YYYY (one HTML table per day, from 2018 onward;
columns DELHI, BRPL, BYPL, NDPL, NDMC, MES in MW, timestamps in IST).

CLI:
    python -m app.services.sldc_collector backfill --start 2018-01-01 [--end 2026-09-13] [--delay 1.0]
    python -m app.services.sldc_collector sync      # re-fetch yesterday and today
The API process also runs `sync` on a schedule when DATA_SYNC_ENABLED=true (see services/collection.py).
"""
from __future__ import annotations

import argparse
import logging
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import unescape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import Base, SessionLocal, engine
from ..models import SldcLoad
from .collection import upsert

log = logging.getLogger(__name__)

URL = "https://www.delhisldc.org/Loaddata.aspx?mode={day:%d/%m/%Y}"
USER_AGENT = "GridSenseAI-collector/1.0 (research; low-rate)"
IST = timezone(timedelta(hours=5, minutes=30))
COLUMNS = {"DELHI": "delhi_mw", "BRPL": "brpl_mw", "BYPL": "bypl_mw", "NDPL": "ndpl_mw", "NDMC": "ndmc_mw", "MES": "mes_mw"}
FIRST_DAY = date(2018, 1, 1)

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_SLOT = re.compile(r"^\d{1,2}:\d{2}$")


def today_ist() -> date:
    return datetime.now(IST).date()


def fetch_html(day: date, timeout: float = 30) -> str:
    request = urllib.request.Request(URL.format(day=day), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_day(html: str, day: date) -> list[dict]:
    """Rows for one day. The header row names the columns, so a reordered or trimmed table still parses."""
    header: list[str] | None = None
    rows: list[dict] = []
    for raw_row in _ROW.findall(html):
        cells = [unescape(_TAG.sub("", cell)).strip() for cell in _CELL.findall(raw_row)]
        if not cells:
            continue
        if header is None:
            if "DELHI" in (cell.upper() for cell in cells):
                header = [cell.upper() for cell in cells]
            continue
        if len(cells) != len(header) or not _SLOT.match(cells[0]):
            continue
        hour, minute = map(int, cells[0].split(":"))
        if hour > 23:  # some days publish a 24:00 slot; it duplicates the next day's 00:00
            continue
        row = {"slot_at": datetime(day.year, day.month, day.day, hour, minute)}
        for name, value in zip(header[1:], cells[1:]):
            if name in COLUMNS:
                row[COLUMNS[name]] = _number(value)
        if row.get("delhi_mw") is not None:
            rows.append(row)
    return rows


def collect_day(db: Session, day: date) -> int:
    return upsert(db, SldcLoad, parse_day(fetch_html(day), day), ["slot_at"])


def backfill(start: date, end: date, delay: float = 1.0, skip_complete: bool = True) -> dict:
    """Fetch every day in [start, end]. Days already holding a full 288 slots are skipped so reruns resume."""
    Base.metadata.create_all(bind=engine, tables=[SldcLoad.__table__])
    stats = {"days": 0, "skipped": 0, "rows": 0, "failed": []}
    with SessionLocal() as db:
        complete = _complete_days(db, start, end) if skip_complete else set()
        day = start
        while day <= end:
            if day in complete:
                stats["skipped"] += 1
            else:
                try:
                    count = collect_day(db, day)
                    stats["rows"] += count
                    stats["days"] += 1
                    log.info("%s: %d rows", day, count)
                except Exception as exc:  # network hiccups shouldn't stop a multi-year backfill
                    db.rollback()
                    stats["failed"].append(day.isoformat())
                    log.warning("%s failed: %s", day, exc)
                time.sleep(delay)
            day += timedelta(days=1)
    return stats


def sync() -> int:
    """Refresh yesterday (late revisions) and today (new slots)."""
    today = today_ist()
    with SessionLocal() as db:
        return sum(collect_day(db, day) for day in (today - timedelta(days=1), today))


def _complete_days(db: Session, start: date, end: date) -> set[date]:
    day_expr = func.date(SldcLoad.slot_at)
    query = (
        select(day_expr, func.count())
        .where(SldcLoad.slot_at >= datetime.combine(start, datetime.min.time()))
        .where(SldcLoad.slot_at < datetime.combine(end + timedelta(days=1), datetime.min.time()))
        .group_by(day_expr)
    )
    return {date.fromisoformat(str(day)[:10]) for day, count in db.execute(query) if count >= 288}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    fill = sub.add_parser("backfill")
    fill.add_argument("--start", type=date.fromisoformat, default=FIRST_DAY)
    fill.add_argument("--end", type=date.fromisoformat, default=None, help="default: yesterday (IST)")
    fill.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    fill.add_argument("--refetch", action="store_true", help="also re-download days that are already complete")
    sub.add_parser("sync")
    args = parser.parse_args()
    if args.command == "backfill":
        end = args.end or today_ist() - timedelta(days=1)
        print(backfill(args.start, end, delay=args.delay, skip_complete=not args.refetch))
    else:
        Base.metadata.create_all(bind=engine, tables=[SldcLoad.__table__])
        print({"rows": sync()})


if __name__ == "__main__":
    main()
