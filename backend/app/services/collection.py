"""Shared pieces for the data collectors: idempotent upserts and the in-process sync scheduler.

With DATA_SYNC_ENABLED=true the API process keeps every source fresh:
    SLDC load        every SLDC_SYNC_MINUTES          (default 5)
    SLDC real-time   every SLDC_REALTIME_MINUTES      (default 5)
    SLDC daily peak  every 6 hours
    demand model     hourly: fresh forecast; retrains once a day
    alerts           every ALERTS_MINUTES (default 5): services/alert_engine.py
    recommendations  hourly: services/recommender.py
    weather          every WEATHER_SYNC_MINUTES       (default 60)
    calendar         once a day
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
_BATCH = 2000  # keeps statements under SQLite's bound-parameter limit


def upsert(db: Session, model: type, rows: list[dict], keys: list[str], stamp: str | None = "fetched_at") -> int:
    """Insert rows, overwriting non-key columns when a row with the same keys already exists."""
    if not rows:
        return 0
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    now = datetime.now(timezone.utc)
    for offset in range(0, len(rows), _BATCH):
        batch = [{**row, stamp: now} if stamp else row for row in rows[offset:offset + _BATCH]]
        stmt = insert(model).values(batch)
        updates = {column: stmt.excluded[column] for column in batch[0] if column not in keys}
        db.execute(stmt.on_conflict_do_update(index_elements=keys, set_=updates) if updates else stmt.on_conflict_do_nothing(index_elements=keys))
    db.commit()
    return len(rows)


def sync_enabled() -> bool:
    return os.getenv("DATA_SYNC_ENABLED", "false").lower() == "true"


def jobs() -> list[tuple[str, float, Callable[[], int]]]:
    from . import calendar_collector, sldc_collector, sldc_realtime_collector, weather_collector

    def demand() -> int:
        from ..database import SessionLocal
        from . import demand_model

        with SessionLocal() as db:
            return demand_model.scheduled_job(db)

    def alerts() -> int:
        from ..database import SessionLocal
        from . import alert_engine

        with SessionLocal() as db:
            result = alert_engine.run(db)
            return result["raised"] + result["updated"] + result["resolved"]

    def recommendations() -> int:
        from ..database import SessionLocal
        from . import recommender

        with SessionLocal() as db:
            result = recommender.generate(db)
            return result["created"] + result["refreshed"]

    return [
        ("sldc", float(os.getenv("SLDC_SYNC_MINUTES", "5")), sldc_collector.sync),
        ("sldc_realtime", float(os.getenv("SLDC_REALTIME_MINUTES", "5")), sldc_realtime_collector.poll),
        ("sldc_daily", 6 * 60, sldc_realtime_collector.daily_sync),
        ("weather", float(os.getenv("WEATHER_SYNC_MINUTES", "60")), weather_collector.sync),
        ("calendar", 24 * 60, calendar_collector.sync),
        ("demand_model", 60, demand),
        ("alerts", float(os.getenv("ALERTS_MINUTES", "5")), alerts),
        ("recommendations", 60, recommendations),
    ]


async def run_scheduler(job_list: list[tuple[str, float, Callable[[], int]]] | None = None, tick_seconds: float = 30) -> None:
    job_list = job_list if job_list is not None else jobs()
    next_run = {name: 0.0 for name, _, _ in job_list}
    while True:
        for name, minutes, job in job_list:
            if time.monotonic() < next_run[name]:
                continue
            try:
                log.info("%s sync stored %d rows", name, await asyncio.to_thread(job))
            except Exception as exc:  # one failing source must not stop the others
                log.warning("%s sync failed: %s", name, exc)
            next_run[name] = time.monotonic() + minutes * 60
        await asyncio.sleep(tick_seconds)


def main() -> None:
    """Run the scheduler as its own worker process: python -m app.services.collection"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from ..database import Base, engine

    Base.metadata.create_all(bind=engine)
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
