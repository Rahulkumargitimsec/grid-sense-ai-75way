"""Read-only access to the Delhi load study produced by analysis/delhi_load/analyze.py.

The heavy lifting (model training, counterfactual attribution) runs offline so the API
needs no ML dependencies; this module only loads the precomputed report and phrases it.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REPORT_PATH = Path(__file__).resolve().parents[1] / "data" / "delhi_load_report.json"
DAY_TYPE_FACTOR = "Day type (festival / holiday / weekend)"


@lru_cache(maxsize=1)
def load_report() -> dict:
    return json.loads(REPORT_PATH.read_text())


def summary() -> dict:
    report = load_report()
    return {key: report[key] for key in ("peak_threshold_mw", "data_quality", "model", "importance", "effects", "zone_outlook")}


def scenarios(zone: str | None = None, day_type: str | None = None, weather: str | None = None) -> list[dict]:
    rows = load_report()["scenarios"]
    filters = {"zone": zone, "day_type": day_type, "weather": weather}
    return [row for row in rows if all(value is None or row[key] == value for key, value in filters.items())]


def _reason(day: dict, driver: dict) -> str:
    factor, effect = driver["factor"], driver["effect_mw"]
    if factor == DAY_TYPE_FACTOR:
        return f"it was a {' and '.join(day['flags'])}" if day["flags"] else "it was a regular working day"
    if factor == "Rain":
        return f"rain was lighter than usual ({day['rain_mm']} mm/h average)" if effect > 0 else f"rain was heavier than usual ({day['rain_mm']} mm/h average)"
    if factor == "Temperature":
        return f"it was hotter than usual ({day['temp_c']}°C average)" if effect > 0 else f"it was cooler than usual ({day['temp_c']}°C average)"
    if factor == "Wind":
        return "the wind was calm" if effect > 0 else "it was windy"
    if factor == "Humidity":
        return "it was humid" if effect > 0 else "the air was dry"
    return "more hours came from High-development zones" if effect > 0 else "more hours came from Low-development zones"


def explain(day: dict) -> str:
    gap = day["vs_year_avg_mw"]
    direction = "above" if gap >= 0 else "below"
    aligned = [d for d in day["drivers"] if (d["effect_mw"] > 0) == (gap >= 0)][:2]
    if not aligned:
        return f"Load was {abs(gap):,} MW {direction} the 2023 average, with no single factor clearly responsible."
    parts = [f"{_reason(day, d)} ({d['effect_mw']:+,} MW)" for d in aligned]
    return f"Load ran {abs(gap):,} MW {direction} the 2023 average, mainly because {', and '.join(parts)}."


def _with_explanation(day: dict) -> dict:
    return {**day, "explanation": explain(day)}


def days(notable_only: bool = False) -> list[dict]:
    return [_with_explanation(day) for day in load_report()["days"] if day["tag"] or not notable_only]


def day(date: str) -> dict | None:
    match = next((d for d in load_report()["days"] if d["date"] == date), None)
    return _with_explanation(match) if match else None
