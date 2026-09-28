"""Historical analytics of real Delhi load (2018 onward) for the Analytics page. Cached: the history changes slowly."""
from __future__ import annotations

import time
import warnings
from datetime import datetime

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SldcLoad
from . import demand_model, grid_live

CACHE_SECONDS = 1800
DISCOMS = {"BRPL": "brpl_mw", "BYPL": "bypl_mw", "TPDDL": "ndpl_mw", "NDMC": "ndmc_mw", "MES": "mes_mw"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_cache: dict[str, tuple[float, dict]] = {}


def overview(db: Session) -> dict:
    hit = _cache.get("overview")
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    result = _overview(db)
    _cache["overview"] = (time.monotonic(), result)
    return result


def _overview(db: Session) -> dict:
    ds = demand_model.dataset(db)
    last = ds.last_load_index()
    load = ds.load[: last + 1]
    years = np.array([ds.time(i).year for i in range(0, last + 1, 24)]).repeat(24)[: last + 1]
    months = np.array([ds.time(i).month for i in range(0, last + 1, 24)]).repeat(24)[: last + 1]
    hours = ds.raw["hour"][: last + 1].astype(int)
    dow = ds.raw["dow"][: last + 1].astype(int)
    try:
        marks = demand_model.thresholds(db)
    except demand_model.ModelNotReady:
        marks = {"high_mw": float(np.nanpercentile(load[-365 * 24:], 90)), "critical_mw": float(np.nanpercentile(load[-365 * 24:], 97.5)), "low_mw": float(np.nanpercentile(load[-365 * 24:], 10))}
    last_time = ds.time(last)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        yearly = []
        for year in sorted(set(years.tolist())):
            selection = load[years == year]
            valid = selection[~np.isnan(selection)]
            if not len(valid):
                continue
            peak_index = int(np.flatnonzero(years == year)[0] + np.nanargmax(selection))
            yearly.append({
                "year": int(year), "partial": year == last_time.year, "coverage_pct": round(len(valid) / len(selection) * 100, 1),
                "mean_mw": round(float(valid.mean()), 1), "peak_mw": round(float(valid.max()), 1), "peak_at": ds.time(peak_index),
                "min_mw": round(float(valid.min()), 1),
                "hours_above_high": int((valid >= marks["high_mw"]).sum()), "hours_above_critical": int((valid >= marks["critical_mw"]).sum()),
                "energy_gwh": round(float(valid.sum()) / 1000 * len(selection) / len(valid), 0),
            })
        for previous, current in zip(yearly, yearly[1:]):
            current["mean_growth_pct"] = round((current["mean_mw"] / previous["mean_mw"] - 1) * 100, 1)
            current["peak_growth_pct"] = round((current["peak_mw"] / previous["peak_mw"] - 1) * 100, 1)

        # Same months last year vs this year, so a partial year compares fairly.
        this_year = [i for i in range(len(load)) if years[i] == last_time.year and not np.isnan(load[i])]
        comparable = None
        if this_year:
            upto = (last_time.month, last_time.day)
            def ytd(year: int) -> np.ndarray:
                mask = (years == year) & np.array([(ds.time(i).month, ds.time(i).day) <= upto for i in range(len(load))])
                values = load[mask]
                return values[~np.isnan(values)]
            now_values, before_values = ytd(last_time.year), ytd(last_time.year - 1)
            if len(now_values) and len(before_values):
                comparable = {
                    "through": last_time.strftime("%d %b"), "year": last_time.year,
                    "mean_mw": round(float(now_values.mean()), 1), "previous_mean_mw": round(float(before_values.mean()), 1),
                    "mean_growth_pct": round(float(now_values.mean() / before_values.mean() - 1) * 100, 1),
                    "peak_mw": round(float(now_values.max()), 1), "previous_peak_mw": round(float(before_values.max()), 1),
                    "hours_above_high": int((now_values >= marks["high_mw"]).sum()), "previous_hours_above_high": int((before_values >= marks["high_mw"]).sum()),
                }

        recent = slice(max(0, last + 1 - 365 * 24), last + 1)
        heat = []
        for month in range(1, 13):
            row = []
            for hour in range(24):
                values = load[recent][(months[recent] == month) & (hours[recent] == hour)]
                row.append(None if not np.any(~np.isnan(values)) else round(float(np.nanmean(values)), 0))
            heat.append({"month": MONTHS[month - 1], "values": row})

        profile_window = slice(max(0, last + 1 - 90 * 24), last + 1)
        holiday = ds.raw["is_holiday"][: last + 1].astype(bool)
        def profile(mask: np.ndarray) -> list[float | None]:
            return [None if not np.any(~np.isnan(load[profile_window][mask[profile_window] & (hours[profile_window] == h)])) else
                    round(float(np.nanmean(load[profile_window][mask[profile_window] & (hours[profile_window] == h)])), 0) for h in range(24)]
        profiles = {
            "Weekday": profile((dow < 5) & ~holiday), "Saturday": profile((dow == 5) & ~holiday),
            "Sunday": profile((dow == 6) & ~holiday), "Holiday": profile(holiday),
        }

    year_expr = func.strftime("%Y", SldcLoad.slot_at) if db.bind.dialect.name == "sqlite" else func.to_char(SldcLoad.slot_at, "YYYY")
    shares = []
    for year, *means in db.execute(select(year_expr, *[func.avg(getattr(SldcLoad, column)) for column in DISCOMS.values()]).group_by(year_expr).order_by(year_expr)):
        total = sum(value or 0 for value in means) or 1
        shares.append({"year": int(year), **{name: round((value or 0) / total * 100, 1) for name, value in zip(DISCOMS, means)},
                       "mw": {name: round(value or 0, 0) for name, value in zip(DISCOMS, means)}})

    peak_hour_counts = [0] * 24
    for d in range(max(0, last // 24 - 364), last // 24 + 1):
        day = load[d * 24:d * 24 + 24]
        if np.sum(~np.isnan(day)) >= 20:
            peak_hour_counts[int(np.nanargmax(day))] += 1

    return {
        "generated_at": grid_live.now_ist(), "data_until": last_time, "thresholds": {key: marks[key] for key in ("high_mw", "critical_mw", "low_mw")},
        "yearly": yearly, "year_to_date": comparable, "financial_year_peaks": grid_live.financial_year_peaks(db),
        "heatmap": heat, "day_profiles": profiles, "discom_shares": shares,
        "peak_hour_distribution": [{"hour": hour, "days": count} for hour, count in enumerate(peak_hour_counts)],
    }
