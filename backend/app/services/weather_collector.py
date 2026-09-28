"""Collect hourly Delhi weather into the weather_hourly table.

Sources (both free, no key), queried in UTC and stored as naive IST like sldc_load:
    nasa_power  https://power.larc.nasa.gov  history from 1981, lags ~2 days  (backfill)
    open_meteo  https://api.open-meteo.com   last 92 days + 7-day forecast    (sync)
Units: temperature °C, relative humidity %, precipitation mm over the hour, wind speed m/s at 10 m.

CLI:
    python -m app.services.weather_collector backfill --start 2018-01-01 [--end 2026-09-12]
    python -m app.services.weather_collector sync
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from ..database import Base, SessionLocal, engine
from ..models import WeatherHourly
from .collection import upsert

log = logging.getLogger(__name__)

LATITUDE, LONGITUDE = 28.6139, 77.2090  # New Delhi
IST_OFFSET = timedelta(hours=5, minutes=30)
USER_AGENT = "GridSenseAI-collector/1.0"
FIELDS = ("temperature_c", "humidity_pct", "precipitation_mm", "wind_speed_ms")
NASA_PARAMS = dict(zip(("T2M", "RH2M", "PRECTOTCORR", "WS10M"), FIELDS))
OPEN_METEO_PARAMS = dict(zip(("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m"), FIELDS))
NASA_FILL = -999.0


def _get_json(url: str, params: dict, timeout: float = 120) -> dict:
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def parse_nasa_power(payload: dict) -> list[dict]:
    parameters = payload["properties"]["parameter"]
    fill = payload.get("header", {}).get("fill_value", NASA_FILL)
    rows: dict[str, dict] = {}
    for name, field in NASA_PARAMS.items():
        for stamp, value in parameters.get(name, {}).items():
            rows.setdefault(stamp, {})[field] = None if value == fill else value
    result = []
    for stamp, values in sorted(rows.items()):
        if all(values.get(field) is None for field in FIELDS):
            continue  # hours NASA hasn't processed yet
        slot = datetime.strptime(stamp, "%Y%m%d%H") + IST_OFFSET
        result.append({"source": "nasa_power", "slot_at": slot, "is_forecast": False, **{field: values.get(field) for field in FIELDS}})
    return result


def parse_open_meteo(payload: dict, now_utc: datetime) -> list[dict]:
    hourly = payload["hourly"]
    result = []
    for index, stamp in enumerate(hourly["time"]):
        utc = datetime.fromisoformat(stamp)
        values = {field: hourly[name][index] for name, field in OPEN_METEO_PARAMS.items()}
        if all(value is None for value in values.values()):
            continue
        result.append({"source": "open_meteo", "slot_at": utc + IST_OFFSET, "is_forecast": utc > now_utc, **values})
    return result


def fetch_nasa_power(start: date, end: date) -> list[dict]:
    payload = _get_json("https://power.larc.nasa.gov/api/temporal/hourly/point", {
        "parameters": ",".join(NASA_PARAMS), "community": "RE", "latitude": LATITUDE, "longitude": LONGITUDE,
        "start": f"{start:%Y%m%d}", "end": f"{end:%Y%m%d}", "format": "JSON", "time-standard": "UTC",
    })
    return parse_nasa_power(payload)


def fetch_open_meteo(past_days: int = 92, forecast_days: int = 7) -> list[dict]:
    payload = _get_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": LATITUDE, "longitude": LONGITUDE, "hourly": ",".join(OPEN_METEO_PARAMS),
        "wind_speed_unit": "ms", "timezone": "GMT", "past_days": past_days, "forecast_days": forecast_days,
    })
    return parse_open_meteo(payload, datetime.now(timezone.utc).replace(tzinfo=None))


def backfill(start: date, end: date, delay: float = 2.0) -> dict:
    """NASA POWER in yearly chunks; reruns simply overwrite."""
    Base.metadata.create_all(bind=engine, tables=[WeatherHourly.__table__])
    stats = {"chunks": 0, "rows": 0, "failed": []}
    with SessionLocal() as db:
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(date(chunk_start.year, 12, 31), end)
            try:
                count = upsert(db, WeatherHourly, fetch_nasa_power(chunk_start, chunk_end), ["source", "slot_at"])
                stats["rows"] += count
                stats["chunks"] += 1
                log.info("nasa_power %s..%s: %d rows", chunk_start, chunk_end, count)
            except Exception as exc:
                db.rollback()
                stats["failed"].append(f"{chunk_start}..{chunk_end}")
                log.warning("nasa_power %s..%s failed: %s", chunk_start, chunk_end, exc)
            chunk_start = chunk_end + timedelta(days=1)
            time.sleep(delay)
    return stats


def sync() -> int:
    """Open-Meteo recent + forecast (hourly refresh), then the last week of NASA POWER as it gets published."""
    today = date.today()
    with SessionLocal() as db:
        count = upsert(db, WeatherHourly, fetch_open_meteo(), ["source", "slot_at"])
        try:
            count += upsert(db, WeatherHourly, fetch_nasa_power(today - timedelta(days=7), today), ["source", "slot_at"])
        except Exception as exc:
            db.rollback()
            log.warning("nasa_power recent fetch failed: %s", exc)
        return count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    fill = sub.add_parser("backfill")
    fill.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    fill.add_argument("--end", type=date.fromisoformat, default=None, help="default: today")
    sub.add_parser("sync")
    args = parser.parse_args()
    if args.command == "backfill":
        print(backfill(args.start, args.end or date.today()))
    else:
        Base.metadata.create_all(bind=engine, tables=[WeatherHourly.__table__])
        print({"rows": sync()})


if __name__ == "__main__":
    main()
