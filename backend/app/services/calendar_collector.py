"""Fill calendar_context with Delhi weekends, gazetted holidays and restricted-holiday festivals.

Dates come from the `holidays` package (India, subdivision DL), which follows the central government lists:
the "public" category is the gazetted list (is_holiday=True); "optional" is the restricted list, kept as
festival_name with is_holiday=False because offices stay open but household load still shifts (Chhath, Karwa Chauth...).
Lunar dates are computed, so future years are available before the official notification — re-run when it lands.

CLI:
    python -m app.services.calendar_collector [--start-year 2018] [--end-year 2027]
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

import holidays

from ..database import Base, SessionLocal, engine
from ..models import CalendarContext
from .collection import upsert

IST = timezone(timedelta(hours=5, minutes=30))
FIRST_YEAR = 2018


def build_days(start_year: int, end_year: int) -> list[dict]:
    years = range(start_year, end_year + 1)
    gazetted = holidays.India(subdiv="DL", years=years, categories=("public",))
    restricted = holidays.India(subdiv="DL", years=years, categories=("optional",))
    rows = []
    day, last = date(start_year, 1, 1), date(end_year, 12, 31)
    while day <= last:
        names = [name for name in (gazetted.get(day), restricted.get(day)) if name]
        rows.append({
            "calendar_date": datetime(day.year, day.month, day.day, tzinfo=IST),
            "is_weekend": day.weekday() >= 5,
            "is_holiday": day in gazetted,
            "festival_name": "; ".join(names)[:120] or None,
        })
        day += timedelta(days=1)
    return rows


def sync(start_year: int = FIRST_YEAR, end_year: int | None = None) -> int:
    end_year = end_year or date.today().year + 1
    Base.metadata.create_all(bind=engine, tables=[CalendarContext.__table__])
    with SessionLocal() as db:
        return upsert(db, CalendarContext, build_days(start_year, end_year), ["calendar_date"], stamp=None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=FIRST_YEAR)
    parser.add_argument("--end-year", type=int, default=None, help="default: next year")
    args = parser.parse_args()
    print({"days": sync(args.start_year, args.end_year)})


if __name__ == "__main__":
    main()
