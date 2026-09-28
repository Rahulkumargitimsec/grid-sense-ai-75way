"""Delhi demand model: when will load be high or low, and why. Also measures holiday and weather effects.

One gradient-boosted model predicts hourly Delhi load up to 7 days ahead. Every input is known a week in advance:
    weather     temperature, humidity, rain, wind (+ 24 h mean temperature and 24 h rain) from weather_hourly
                (history: NASA POWER actuals; future: Open-Meteo forecast)
    calendar    hour, day of week, day of year, gazetted holiday, festival (calendar_context)
    level       Delhi's demand level over days d-14..d-8: daily mean, same-hour mean, peak (sldc_load), plus a year
                counter so the trees use the latest year's behaviour (air-conditioning growth) instead of averaging it away

"High" and "low" are relative to Delhi's own recent history: an hour is high above the 90th percentile of hourly load
over the last 365 days, critical above the 97.5th, and low below the 10th. The probability of an hour being high comes from the model's residual
spread on a full held-out year.

"Why": each prediction is split into 8 reasons with exact group Shapley values (all 256 on/off combinations) against a
"normal" reference: typical weather for that month and hour, an ordinary weekday with no holiday, and last year's
demand level. The reasons sum exactly to prediction minus normal.

CLI:
    python -m app.services.demand_model train
    python -m app.services.demand_model forecast
    python -m app.services.demand_model explain 2024-06-19
    python -m app.services.demand_model insights
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import threading
import time
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CalendarContext, SldcLoad, WeatherHourly
from . import app_settings

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
ARTIFACT = Path(__file__).resolve().parents[1] / "data" / "models" / "demand_model.joblib"
START = datetime(2018, 1, 1)
HORIZON_DAYS = 7
MIN_SAMPLES_PER_HOUR = 9  # of 12 five-minute slots
MIN_VALID_MW = 500
LOCKDOWN = (datetime(2020, 3, 25), datetime(2020, 6, 1))
TEST_DAYS = 365
HIGH_PERCENTILE, CRITICAL_PERCENTILE, LOW_PERCENTILE = 90.0, 97.5, 10.0
DATASET_TTL_SECONDS = 600
RETRAIN_AFTER = timedelta(hours=20)

FEATURES = [
    "hour", "dow", "doy", "is_weekend", "is_holiday", "is_festival",
    "temperature_c", "humidity_pct", "precipitation_mm", "wind_speed_ms", "temp_mean_24h", "rain_24h",
    "heat_index", "cooling_degrees", "level_mean", "level_hour", "level_peak", "year",
]
RAW_INPUTS = [name for name in FEATURES if name not in ("heat_index", "cooling_degrees")]
GROUPS = {  # reason -> raw inputs it controls
    "heat": ["temperature_c", "temp_mean_24h"],
    "humidity": ["humidity_pct"],
    "rain": ["precipitation_mm", "rain_24h"],
    "wind": ["wind_speed_ms"],
    "calendar": ["is_holiday", "is_festival"],
    "weekday": ["dow", "is_weekend"],
    "level": ["level_mean", "level_hour", "level_peak"],
    "trend": ["year"],
}
GROUP_LABELS = {
    "heat": "Heat", "humidity": "Humidity", "rain": "Rain", "wind": "Wind",
    "calendar": "Holiday / festival", "weekday": "Day of week", "level": "Recent demand level", "trend": "Year-on-year growth",
    "time_of_day": "Time of day", "season": "Season",
}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ModelNotReady(RuntimeError):
    pass


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


# ---- dataset ----------------------------------------------------------------------------------------------------------

@dataclass
class Dataset:
    start: datetime
    load: np.ndarray                    # hourly mean Delhi load (MW), NaN where SCADA has a gap
    raw: dict[str, np.ndarray]          # RAW_INPUTS, one value per hour
    weather_forecast: np.ndarray        # True where the weather is a forecast rather than an observation
    daily_mean: np.ndarray
    daily_peak: np.ndarray
    names: dict[date, str]              # holiday / festival names
    built_at: float = field(default_factory=time.monotonic)
    gazetted: set[date] = field(default_factory=set)  # every gazetted holiday in the calendar, past and future

    @property
    def hours(self) -> int:
        return len(self.load)

    def time(self, index: int) -> datetime:
        return self.start + timedelta(hours=int(index))

    def index(self, moment: datetime) -> int:
        return int((moment - self.start).total_seconds() // 3600)

    def last_load_index(self) -> int:
        valid = np.flatnonzero(~np.isnan(self.load))
        return int(valid[-1]) if len(valid) else -1


def _nan_windows(values: np.ndarray, lag_from: int, width: int) -> np.ndarray:
    """For each day d, the `width` values ending `lag_from` days earlier: values[d-lag_from-width+1 .. d-lag_from]."""
    pad_shape = (lag_from + width - 1,) + values.shape[1:]
    padded = np.concatenate([np.full(pad_shape, np.nan), values])
    return np.lib.stride_tricks.sliding_window_view(padded, width, axis=0)[: len(values)]


def _ist_date(value: datetime) -> date:
    return (value.astimezone(IST) if value.tzinfo else value).date()


def build_dataset(db: Session, until: datetime | None = None) -> Dataset:
    end = (until or now_ist()).replace(minute=0, second=0, microsecond=0) + timedelta(days=HORIZON_DAYS + 1)
    days = (end.date() - START.date()).days + 1
    hours = days * 24
    sqlite = db.bind.dialect.name == "sqlite"

    load = np.full(hours, np.nan)
    bucket = func.strftime("%Y-%m-%d %H", SldcLoad.slot_at) if sqlite else func.to_char(SldcLoad.slot_at, "YYYY-MM-DD HH24")
    for key, mean, count in db.execute(select(bucket, func.avg(SldcLoad.delhi_mw), func.count()).where(SldcLoad.delhi_mw >= MIN_VALID_MW, SldcLoad.slot_at >= START).group_by(bucket)):
        index = int((datetime.strptime(key, "%Y-%m-%d %H") - START).total_seconds() // 3600)
        if 0 <= index < hours and count >= MIN_SAMPLES_PER_HOUR:
            load[index] = mean

    weather = {name: np.full(hours, np.nan) for name in ("temperature_c", "humidity_pct", "precipitation_mm", "wind_speed_ms")}
    forecast = np.zeros(hours, dtype=bool)
    rows = db.execute(select(WeatherHourly.source, WeatherHourly.slot_at, WeatherHourly.temperature_c, WeatherHourly.humidity_pct, WeatherHourly.precipitation_mm, WeatherHourly.wind_speed_ms, WeatherHourly.is_forecast)
                      .where(WeatherHourly.slot_at >= START)).all()
    # Open-Meteo first so NASA POWER observations overwrite it wherever both exist.
    for source, slot, temperature, humidity, rain, wind, is_forecast in sorted(rows, key=lambda row: row[0] == "nasa_power"):
        index = int((slot.replace(minute=0, second=0, microsecond=0) - START).total_seconds() // 3600)
        if 0 <= index < hours:
            for name, value in zip(weather, (temperature, humidity, rain, wind)):
                if value is not None:
                    weather[name][index] = value
            forecast[index] = bool(is_forecast) and source != "nasa_power"

    def rolling(values: np.ndarray, width: int, mean: bool) -> np.ndarray:
        present = ~np.isnan(values)
        total = np.concatenate([[0.0], np.cumsum(np.where(present, values, 0.0))])
        count = np.concatenate([[0], np.cumsum(present)])
        window_total = total[width:] - total[:-width]
        window_count = count[width:] - count[:-width]
        head = np.full(width - 1, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            result = window_total / window_count if mean else window_total
        result = np.where(window_count >= width // 2, result, np.nan)
        return np.concatenate([head, result])

    holiday_day = np.zeros(days)
    festival_day = np.zeros(days)
    names: dict[date, str] = {}
    gazetted: set[date] = set()
    for calendar_date, is_holiday, name in db.execute(select(CalendarContext.calendar_date, CalendarContext.is_holiday, CalendarContext.festival_name)):
        day = _ist_date(calendar_date)
        d = (day - START.date()).days
        if name:
            names[day] = name  # kept beyond the dataset window so insights can show each holiday's next date
        if is_holiday:
            gazetted.add(day)
        if 0 <= d < days:
            holiday_day[d] = 1.0 if is_holiday else 0.0
            festival_day[d] = 1.0 if (name and not is_holiday) else 0.0

    day_numbers = np.arange(days)
    dow_day = (START.weekday() + day_numbers) % 7
    doy_day = np.array([(START.date() + timedelta(days=int(d))).timetuple().tm_yday for d in day_numbers])

    load2d = load.reshape(days, 24)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        valid_hours = (~np.isnan(load2d)).sum(axis=1)
        daily_mean = np.where(valid_hours >= 16, np.nanmean(load2d, axis=1), np.nan)
        daily_peak = np.where(valid_hours >= 16, np.nanmax(np.where(np.isnan(load2d), -np.inf, load2d), axis=1), np.nan)

        def level(values: np.ndarray, reduce, axis: int) -> np.ndarray:
            """Days d-14..d-8; when SCADA outages leave fewer than 4 usable days there, widen to d-21..d-8."""
            week, fortnight = _nan_windows(values, 8, 7), _nan_windows(values, 8, 14)
            week_ok, fortnight_ok = (~np.isnan(week)).sum(axis=axis) >= 4, (~np.isnan(fortnight)).sum(axis=axis) >= 4
            return np.where(week_ok, reduce(week, axis=axis), np.where(fortnight_ok, reduce(fortnight, axis=axis), np.nan))

        level_mean = level(daily_mean, np.nanmean, 1)
        level_peak = level(daily_peak, np.nanmax, 1)
        level_hour = level(load2d, np.nanmean, 2)  # (days, 24)

    raw = {
        "hour": np.tile(np.arange(24), days).astype(float),
        "dow": np.repeat(dow_day, 24).astype(float),
        "doy": np.repeat(doy_day, 24).astype(float),
        "is_weekend": np.repeat((dow_day >= 5).astype(float), 24),
        "is_holiday": np.repeat(holiday_day, 24),
        "is_festival": np.repeat(festival_day, 24),
        **weather,
        "temp_mean_24h": rolling(weather["temperature_c"], 24, mean=True),
        "rain_24h": rolling(weather["precipitation_mm"], 24, mean=False),
        "level_mean": np.repeat(level_mean, 24),
        "level_hour": level_hour.reshape(-1),
        "level_peak": np.repeat(level_peak, 24),
        "year": np.arange(hours) / 8760.0,
    }
    return Dataset(START, load, raw, forecast, daily_mean, daily_peak, names, gazetted=gazetted)


def make_matrix(raw: dict[str, np.ndarray]) -> np.ndarray:
    temperature, humidity = raw["temperature_c"], raw["humidity_pct"]
    derived = {"heat_index": temperature * humidity / 100.0, "cooling_degrees": np.clip(temperature - 24.0, 0, None)}
    return np.column_stack([raw[name] if name in raw else derived[name] for name in FEATURES])


def _take(raw: dict[str, np.ndarray], index: np.ndarray) -> dict[str, np.ndarray]:
    return {name: values[index] for name, values in raw.items()}


# ---- artifact -----------------------------------------------------------------------------------------------------------

_artifact_lock = threading.Lock()
_artifact: tuple[float, dict] | None = None
_dataset_lock = threading.Lock()
_dataset: Dataset | None = None
_cache: dict[str, tuple[float, object]] = {}


def load_artifact() -> dict:
    global _artifact
    if not ARTIFACT.exists():
        raise ModelNotReady("The demand model has not been trained yet. Run: python -m app.services.demand_model train")
    mtime = ARTIFACT.stat().st_mtime
    with _artifact_lock:
        if _artifact is None or _artifact[0] != mtime:
            _artifact = (mtime, joblib.load(ARTIFACT))
            _cache.clear()
        return _artifact[1]


def dataset(db: Session) -> Dataset:
    global _dataset
    with _dataset_lock:
        if _dataset is None or time.monotonic() - _dataset.built_at > DATASET_TTL_SECONDS:
            _dataset = build_dataset(db)
            _cache.clear()
        return _dataset


def _cached(key: str, ttl: float, compute):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = compute()
    _cache[key] = (time.monotonic(), value)
    return value


# ---- training -----------------------------------------------------------------------------------------------------------

def _new_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(max_iter=700, learning_rate=0.05, max_leaf_nodes=63, min_samples_leaf=40, l2_regularization=1.0, random_state=0)


def _typical_tables(ds: Dataset, upto: int) -> dict[str, np.ndarray]:
    """Median weather per (month, hour) from observed history: the 'normal' used by explanations."""
    months = np.array([ds.time(i).month for i in range(0, upto, 24)])
    month_per_hour = np.repeat(months, 24)[:upto]
    tables = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for name in ("temperature_c", "humidity_pct", "wind_speed_ms", "temp_mean_24h"):
            values = ds.raw[name][:upto]
            table = np.full((12, 24), np.nan)
            for month in range(1, 13):
                for hour in range(24):
                    selection = values[(month_per_hour == month) & (ds.raw["hour"][:upto] == hour)]
                    table[month - 1, hour] = np.nanmedian(selection) if selection.size else np.nan
            tables[name] = table
    return tables


def _probability_high(predicted: np.ndarray, threshold: float, sigma: np.ndarray) -> np.ndarray:
    z = (threshold - predicted) / np.maximum(sigma, 1.0)
    return 0.5 * np.vectorize(math.erfc)(z / math.sqrt(2))


def _probability_low(predicted: np.ndarray, threshold: float, sigma: np.ndarray) -> np.ndarray:
    return 1.0 - _probability_high(predicted, threshold, sigma)


def train(db: Session) -> dict:
    ds = build_dataset(db)
    last = ds.last_load_index()
    if last < 24 * 400:
        raise ModelNotReady("At least 400 days of SLDC load are needed to train the demand model.")
    X = make_matrix(ds.raw)
    y = ds.load
    times = np.arange(ds.hours)
    lockdown = (times >= ds.index(LOCKDOWN[0])) & (times < ds.index(LOCKDOWN[1]))
    usable = ~np.isnan(y) & ~np.isnan(ds.raw["level_mean"]) & ~lockdown & (times <= last)
    test_start = (last // 24 - TEST_DAYS + 1) * 24
    train_mask, test_mask = usable & (times < test_start), usable & (times >= test_start)

    model = _new_model().fit(X[train_mask], y[train_mask])
    predicted = model.predict(X[test_mask])
    actual = y[test_mask]
    residual = actual - predicted
    test_hours = ds.raw["hour"][test_mask].astype(int)
    sigma_by_hour = np.array([np.std(residual[test_hours == h]) if np.any(test_hours == h) else np.std(residual) for h in range(24)])

    # Thresholds as an operator would have known them when the test year began, so the backtest is honest.
    history = y[max(0, test_start - 365 * 24):test_start]
    backtest_high = float(np.nanpercentile(history, HIGH_PERCENTILE))
    backtest_low = float(np.nanpercentile(history, LOW_PERCENTILE))
    actual_low = actual <= backtest_low
    predicted_low = _probability_low(predicted, backtest_low, sigma_by_hour[test_hours]) >= 0.5
    low_true_positive = int(np.sum(actual_low & predicted_low))
    actual_high = actual >= backtest_high
    predicted_high = _probability_high(predicted, backtest_high, sigma_by_hour[test_hours]) >= 0.5
    true_positive = int(np.sum(actual_high & predicted_high))

    test_index = np.flatnonzero(test_mask)
    test_days = test_index // 24
    peak_errors, min_errors, peak_hour_gaps, peak_hour_hits, high_days, caught_days = [], [], [], 0, 0, 0
    for day in np.unique(test_days):
        in_day = test_days == day
        if in_day.sum() < 18:
            continue
        actual_peak, predicted_peak = actual[in_day].max(), predicted[in_day].max()
        peak_errors.append(abs(actual_peak - predicted_peak))
        min_errors.append(abs(actual[in_day].min() - predicted[in_day].min()))
        hours_in_day = test_hours[in_day]
        peak_hour_gaps.append(abs(int(hours_in_day[np.argmax(actual[in_day])]) - int(hours_in_day[np.argmax(predicted[in_day])])))
        peak_hour_hits += int(peak_hour_gaps[-1] <= 1)
        if actual_peak >= backtest_high:
            high_days += 1
            hours_of_day = test_hours[in_day]
            caught_days += int(_probability_high(np.array([predicted_peak]), backtest_high, np.array([sigma_by_hour[hours_of_day[np.argmax(predicted[in_day])]]]))[0] >= 0.2)

    level_hour_baseline = ds.raw["level_hour"][test_mask]
    baseline_ok = ~np.isnan(level_hour_baseline)
    metrics = {
        "train_period": f"{ds.time(np.flatnonzero(train_mask)[0]):%Y-%m-%d} to {ds.time(np.flatnonzero(train_mask)[-1]):%Y-%m-%d}",
        "test_period": f"{ds.time(test_index[0]):%Y-%m-%d} to {ds.time(test_index[-1]):%Y-%m-%d}",
        "train_hours": int(train_mask.sum()),
        "test_hours": int(test_mask.sum()),
        "mae_mw": round(float(np.mean(np.abs(residual))), 1),
        "mape_pct": round(float(np.mean(np.abs(residual) / actual) * 100), 2),
        "rmse_mw": round(float(np.sqrt(np.mean(residual ** 2))), 1),
        "r2": round(float(1 - np.sum(residual ** 2) / np.sum((actual - actual.mean()) ** 2)), 3),
        "peak_timing_error_hours": round(float(np.mean(peak_hour_gaps)), 2),
        "baseline_recent_profile_mae_mw": round(float(np.mean(np.abs(actual[baseline_ok] - level_hour_baseline[baseline_ok]))), 1),
        "daily_peak_mae_mw": round(float(np.mean(peak_errors)), 1),
        "daily_min_mae_mw": round(float(np.mean(min_errors)), 1),
        "peak_hour_within_1h_pct": round(peak_hour_hits / max(1, len(peak_errors)) * 100, 1),
        "low_threshold_backtest_mw": round(backtest_low, 1),
        "low_hour_precision": round(low_true_positive / max(1, int(predicted_low.sum())), 3),
        "low_hour_recall": round(low_true_positive / max(1, int(actual_low.sum())), 3),
        "high_threshold_backtest_mw": round(backtest_high, 1),
        "high_hour_precision": round(true_positive / max(1, int(predicted_high.sum())), 3),
        "high_hour_recall": round(true_positive / max(1, int(actual_high.sum())), 3),
        "high_days_in_test": high_days,
        "high_days_flagged_watch_or_above": caught_days,
    }

    importance = _group_importance(model, ds, np.flatnonzero(test_mask))

    final = _new_model().fit(X[usable], y[usable])  # production model sees the test year too
    recent = y[max(0, last - 365 * 24 + 1):last + 1]
    artifact = {
        "model": final,
        "features": FEATURES,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_until": ds.time(last).isoformat(),
        "thresholds": {"high_mw": round(float(np.nanpercentile(recent, HIGH_PERCENTILE)), 1), "critical_mw": round(float(np.nanpercentile(recent, CRITICAL_PERCENTILE)), 1),
                       "low_mw": round(float(np.nanpercentile(recent, LOW_PERCENTILE)), 1),
                       "high_percentile": HIGH_PERCENTILE, "critical_percentile": CRITICAL_PERCENTILE, "low_percentile": LOW_PERCENTILE},
        "sigma_by_hour": sigma_by_hour,
        "typical": _typical_tables(ds, last + 1),
        "metrics": metrics,
        "importance": importance,
    }
    artifact["insights"] = compute_insights(final, ds, artifact, usable, last)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    temporary = ARTIFACT.with_suffix(".tmp")
    joblib.dump(artifact, temporary, compress=3)
    temporary.replace(ARTIFACT)
    _cache.clear()
    return {key: artifact[key] for key in ("trained_at", "data_until", "thresholds", "metrics", "importance")}


# ---- holiday & weather insights (computed at training time) ---------------------------------------------------------------

SEASONS = {"Summer (Apr–Jun)": (4, 5, 6), "Monsoon (Jul–Sep)": (7, 8, 9), "Post-monsoon (Oct–Nov)": (10, 11), "Winter (Dec–Mar)": (12, 1, 2, 3)}


def holiday_key(name: str) -> str:
    """The first name of a calendar entry, without the lunar-date "(estimated)" marker, so years line up."""
    return name.split(";")[0].replace("(estimated)", "").strip()


def compute_insights(model, ds: Dataset, artifact: dict, usable: np.ndarray, last: int) -> dict:
    return {"holidays": _holiday_effects(model, ds, artifact, usable, last), "weather": _weather_response(model, ds, usable, last)}


def _holiday_effects(model, ds: Dataset, artifact: dict, usable: np.ndarray, last: int) -> list[dict]:
    """For every named holiday / festival: how much the day itself moved demand, all else as it really was.
    The model's calendar inputs are switched off and on for each hour of each occurrence."""
    occurrences: dict[str, list[int]] = {}
    for day, name in ds.names.items():
        d = (day - START.date()).days
        if 0 <= d and d * 24 + 24 <= last + 1:
            occurrences.setdefault(holiday_key(name), []).append(d)
    rows = []
    for name, day_numbers in occurrences.items():
        index = np.concatenate([np.arange(d * 24, d * 24 + 24) for d in day_numbers])
        index = index[usable[index]]
        if len(index) < 36:
            continue
        actual = _take(ds.raw, index)
        without = dict(actual, is_holiday=np.zeros(len(index)), is_festival=np.zeros(len(index)))
        with_day, without_day = model.predict(make_matrix(actual)), model.predict(make_matrix(without))
        effect = with_day - without_day
        evening = np.isin(actual["hour"], (19, 20, 21, 22))
        afternoon = np.isin(actual["hour"], (13, 14, 15, 16))
        gazetted = bool(actual["is_holiday"].max())
        future = sorted(day for day, n in ds.names.items() if holiday_key(n) == name and day > ds.time(last).date())
        rows.append({
            "name": name, "gazetted": gazetted, "occurrences": len({i // 24 for i in index}),
            "effect_mw": round(float(effect.mean()), 1), "effect_pct": round(float(effect.sum() / without_day.sum() * 100), 2),
            "afternoon_effect_mw": round(float(effect[afternoon].mean()), 1) if afternoon.any() else None,
            "evening_effect_mw": round(float(effect[evening].mean()), 1) if evening.any() else None,
            "last_seen": ds.time(index[-1]).date().isoformat(), "next_date": future[0].isoformat() if future else None,
        })
    return sorted(rows, key=lambda row: row["effect_mw"])


def _weather_response(model, ds: Dataset, usable: np.ndarray, last: int) -> dict:
    """Partial dependence on the latest year: set one weather input for every hour of a season and average the prediction."""
    rng = np.random.default_rng(1)
    recent = np.flatnonzero(usable[: last + 1])
    recent = recent[recent > last - 365 * 24]
    months = np.array([ds.time(i).month for i in recent])
    temperatures = list(range(4, 48, 2))
    result = {"temperature": [], "humidity": [], "rain": []}
    for season, season_months in SEASONS.items():
        rows = recent[np.isin(months, season_months)]
        if not len(rows):
            continue
        rows = rng.choice(rows, size=min(2500, len(rows)), replace=False)
        raw = _take(ds.raw, rows)
        observed = raw["temperature_c"]
        low, high = np.nanpercentile(observed, 2), np.nanpercentile(observed, 98)
        curve = []
        for value in temperatures:
            if value < low - 2 or value > high + 2:
                continue
            shifted = dict(raw, temperature_c=np.full(len(rows), float(value)), temp_mean_24h=raw["temp_mean_24h"] + (value - observed))
            curve.append({"x": value, "load_mw": round(float(model.predict(make_matrix(shifted)).mean()), 1)})
        slope = None
        if len(curve) >= 3:
            xs, ys = np.array([c["x"] for c in curve]), np.array([c["load_mw"] for c in curve])
            middle = (xs >= np.nanpercentile(observed, 25)) & (xs <= np.nanpercentile(observed, 75))
            if middle.sum() >= 2:
                slope = round(float(np.polyfit(xs[middle], ys[middle], 1)[0]), 1)
        result["temperature"].append({"season": season, "typical_c": round(float(np.nanmedian(observed)), 1), "mw_per_degree": slope, "curve": curve})
        if season_months[0] in (4, 7):
            result["humidity"].append({"season": season, "curve": [
                {"x": value, "load_mw": round(float(model.predict(make_matrix(dict(raw, humidity_pct=np.full(len(rows), float(value))))).mean()), 1)}
                for value in range(20, 101, 10)]})
            result["rain"].append({"season": season, "curve": [
                {"x": value, "load_mw": round(float(model.predict(make_matrix(dict(raw, precipitation_mm=np.full(len(rows), value / 6.0), rain_24h=np.full(len(rows), float(value))))).mean()), 1)}
                for value in (0, 2, 5, 10, 20, 40, 60)]})
    return result


def _group_importance(model, ds: Dataset, index: np.ndarray, sample: int = 4000) -> list[dict]:
    rng = np.random.default_rng(0)
    rows = rng.choice(index, size=min(sample, len(index)), replace=False)
    raw = _take(ds.raw, rows)
    actual = ds.load[rows]
    base_error = np.mean(np.abs(actual - model.predict(make_matrix(raw))))
    groups = {**GROUPS, "time_of_day": ["hour"], "season": ["doy"]}
    result = []
    for group, columns in groups.items():
        shuffled = dict(raw)
        order = rng.permutation(len(rows))
        for column in columns:
            shuffled[column] = raw[column][order]
        error = np.mean(np.abs(actual - model.predict(make_matrix(shuffled))))
        result.append({"group": group, "label": GROUP_LABELS[group], "mae_increase_mw": round(float(error - base_error), 1)})
    return sorted(result, key=lambda row: -row["mae_increase_mw"])


# ---- explanation --------------------------------------------------------------------------------------------------------

def normal_inputs(ds: Dataset, artifact: dict, index: np.ndarray) -> dict[str, np.ndarray]:
    """The 'normal' reference for each hour: typical weather for its month and hour, a dry ordinary Wednesday, and the
    demand level of the same week last year (so growth shows up as a reason)."""
    raw = _take(ds.raw, index)
    months = np.array([ds.time(i).month - 1 for i in index])
    hours = raw["hour"].astype(int)
    typical = artifact["typical"]
    last_year = index - 364 * 24
    normal = dict(raw)
    for name in ("temperature_c", "humidity_pct", "wind_speed_ms", "temp_mean_24h"):
        normal[name] = typical[name][months, hours]
    normal["precipitation_mm"] = np.zeros(len(index))
    normal["rain_24h"] = np.zeros(len(index))
    normal["is_holiday"] = np.zeros(len(index))
    normal["is_festival"] = np.zeros(len(index))
    normal["dow"] = np.full(len(index), 2.0)
    normal["is_weekend"] = np.zeros(len(index))
    for name in GROUPS["level"] + GROUPS["trend"]:
        previous = np.where(last_year >= 0, ds.raw[name][np.clip(last_year, 0, None)], np.nan)
        normal[name] = np.where(np.isnan(previous), raw[name], previous)
    return normal


def group_shapley(model, actual: dict[str, np.ndarray], normal: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Exact Shapley values over the reason groups (2^8 on/off combinations). Returns (normal prediction, full prediction, contributions)."""
    groups = list(GROUPS)
    n = len(groups)
    rows = len(next(iter(actual.values())))
    masks = range(1 << n)
    batch = {name: [] for name in actual}
    for mask in masks:
        on = {column for g, group in enumerate(groups) if mask >> g & 1 for column in GROUPS[group]}
        for name in actual:
            batch[name].append(actual[name] if name in on else normal[name])
    values = model.predict(make_matrix({name: np.concatenate(parts) for name, parts in batch.items()})).reshape(1 << n, rows)
    weights = [math.factorial(k) * math.factorial(n - k - 1) / math.factorial(n) for k in range(n)]
    contributions = {}
    for g, group in enumerate(groups):
        total = np.zeros(rows)
        for mask in masks:
            if mask >> g & 1:
                continue
            total += weights[bin(mask).count("1")] * (values[mask | (1 << g)] - values[mask])
        contributions[group] = total
    return values[0], values[-1], contributions


def _reason_detail(group: str, effect: float, row: dict, normal_row: dict, when: datetime, tense: str, names: dict[date, str]) -> str:
    month = MONTHS[when.month - 1]
    if group == "heat":
        # The model reacts to the last 24 h as much as to the hour itself (a hot night keeps ACs running), so show both.
        hour_diff = row["temperature_c"] - normal_row["temperature_c"]
        day_diff = row["temp_mean_24h"] - normal_row["temp_mean_24h"]
        hour_text = f"{row['temperature_c']:.1f}°C at {when:%H}:00 ({hour_diff:+.1f}°C vs a normal {month})"
        day_text = f"a 24 h average of {row['temp_mean_24h']:.1f}°C ({day_diff:+.1f}°C vs normal)"
        return f"{day_text}, {hour_text}" if abs(day_diff) > abs(hour_diff) else f"{hour_text}, {day_text}"
    if group == "humidity":
        diff = row["humidity_pct"] - normal_row["humidity_pct"]
        return f"{row['humidity_pct']:.0f}% humidity ({diff:+.0f} points vs normal)"
    if group == "rain":
        if row["rain_24h"] >= 1:
            return f"{row['rain_24h']:.0f} mm of rain in the previous 24 h cooled the city"
        return "dry weather, no rain to cool the city"
    if group == "wind":
        return f"wind {row['wind_speed_ms']:.1f} m/s vs {normal_row['wind_speed_ms']:.1f} m/s normally"
    if group == "calendar":
        name = names.get(when.date())
        if row["is_holiday"]:
            return f"{name or 'gazetted holiday'}: offices and markets closed"
        if row["is_festival"]:
            return f"{name}"
        return "an ordinary working day"
    if group == "weekday":
        return f"it {tense} a {DAYS[int(row['dow'])]}"
    if group == "level":
        if normal_row["level_mean"] and not math.isnan(normal_row["level_mean"]):
            pct = (row["level_mean"] / normal_row["level_mean"] - 1) * 100
            return f"demand in the weeks before ran {pct:+.1f}% vs the same weeks last year"
        return "recent demand level"
    if group == "trend":
        return "a year of growth in Delhi's electricity use (more ACs and connections)" if effect >= 0 else "this year's pattern for this hour runs below last year's"
    return ""


def explain_rows(model, artifact: dict, ds: Dataset, index: np.ndarray, tense: str = "was") -> list[dict]:
    actual = _take(ds.raw, index)
    normal = normal_inputs(ds, artifact, index)
    base, full, contributions = group_shapley(model, actual, normal)
    result = []
    for k, i in enumerate(index):
        when = ds.time(i)
        row = {name: float(values[k]) for name, values in actual.items()}
        normal_row = {name: float(values[k]) for name, values in normal.items()}
        drivers = sorted(
            ({"group": g, "label": GROUP_LABELS[g], "effect_mw": round(float(contributions[g][k]), 1), "detail": _reason_detail(g, float(contributions[g][k]), row, normal_row, when, tense, ds.names)}
             for g in GROUPS),
            key=lambda item: -abs(item["effect_mw"]),
        )
        result.append({"at": when, "normal_mw": round(float(base[k]), 1), "predicted_mw": round(float(full[k]), 1), "drivers": drivers,
                       "weather": {"temperature_c": _round(row["temperature_c"]), "humidity_pct": _round(row["humidity_pct"]), "rain_24h_mm": _round(row["rain_24h"]), "wind_speed_ms": _round(row["wind_speed_ms"])}})
    return result


def _round(value: float, digits: int = 1):
    return None if value is None or math.isnan(value) else round(value, digits)


def reason_sentence(explained: dict, headline: str, direction: str = "up") -> str:
    """direction="up" explains a high value (drivers pushing demand up); "down" explains a low one."""
    sign = 1 if direction == "up" else -1
    main = [d for d in explained["drivers"] if sign * d["effect_mw"] >= 40]
    against = [d for d in explained["drivers"] if sign * d["effect_mw"] <= -40]
    parts = [f"{d['detail']} ({d['effect_mw']:+.0f} MW)" for d in main[:3]]
    text = f"{headline} " + ("mainly because " + "; ".join(parts) + "." if parts else "with no single factor far from normal.")
    if against:
        d = against[0]
        text += f" {'Held back' if direction == 'up' else 'Kept from falling further'} by {d['detail']} ({d['effect_mw']:+.0f} MW)."
    return text


def risk_level(peak_mw: float, probability: float, thresholds: dict) -> str:
    if peak_mw >= thresholds["critical_mw"]:
        return "critical"
    if probability >= 0.5:
        return "high"
    if probability >= 0.2:
        return "watch"
    return "normal"


def _windows(ks: list[int], flags: np.ndarray) -> list[list[int]]:
    windows, run = [], None
    for k in ks:
        if flags[k]:
            run = [k, k] if run is None else [run[0], k]
        elif run:
            windows.append(run)
            run = None
    if run:
        windows.append(run)
    return windows


# ---- public read models -------------------------------------------------------------------------------------------------

def model_info() -> dict:
    artifact = load_artifact()
    return {key: artifact[key] for key in ("trained_at", "data_until", "thresholds", "metrics", "importance")}


def insights() -> dict:
    artifact = load_artifact()
    return {"trained_at": artifact["trained_at"], "thresholds": artifact["thresholds"], **artifact["insights"]}


def thresholds(db: Session) -> dict:
    return app_settings.demand_thresholds(db, load_artifact()["thresholds"])


def forecast(db: Session) -> dict:
    artifact = load_artifact()
    thresholds = app_settings.demand_thresholds(db, artifact["thresholds"])
    key = f"forecast:{thresholds['high_mw']}:{thresholds['critical_mw']}:{thresholds['low_mw']}"
    return _cached(key, 600, lambda: _forecast(db, artifact, thresholds))


def _forecast(db: Session, artifact: dict, thresholds: dict) -> dict:
    ds = dataset(db)
    model, sigma = artifact["model"], artifact["sigma_by_hour"]
    now = now_ist().replace(minute=0, second=0, microsecond=0)
    first = ds.index(now.replace(hour=0))
    last = ds.index(now.replace(hour=0) + timedelta(days=HORIZON_DAYS))
    index = np.arange(first, last)
    index = index[~np.isnan(ds.raw["temperature_c"][index]) & ~np.isnan(ds.raw["level_mean"][index])]
    base = {"generated_at": now_ist(), "trained_at": artifact["trained_at"], "thresholds": thresholds, "metrics": artifact["metrics"]}
    if not len(index):
        return {**base, "hours": [], "days": []}

    predicted = model.predict(make_matrix(_take(ds.raw, index)))
    hours_of_day = ds.raw["hour"][index].astype(int)
    p_high = _probability_high(predicted, thresholds["high_mw"], sigma[hours_of_day])
    p_low = _probability_low(predicted, thresholds["low_mw"], sigma[hours_of_day])
    band = 1.2816 * sigma[hours_of_day]

    hourly = [{
        "at": ds.time(i), "predicted_mw": round(float(p), 1), "low_band_mw": round(float(p - b), 1), "high_band_mw": round(float(p + b), 1),
        "actual_mw": _round(float(ds.load[i])), "probability_high": round(float(qh), 3), "probability_low": round(float(ql), 3),
        "is_high": bool(qh >= 0.5), "is_low": bool(ql >= 0.5),
        "temperature_c": _round(float(ds.raw["temperature_c"][i])), "humidity_pct": _round(float(ds.raw["humidity_pct"][i])),
        "rain_mm": _round(float(ds.raw["precipitation_mm"][i])), "weather_is_forecast": bool(ds.weather_forecast[i]),
    } for i, p, qh, ql, b in zip(index, predicted, p_high, p_low, band)]

    by_day: dict[date, list[int]] = {}
    for k, i in enumerate(index):
        by_day.setdefault(ds.time(i).date(), []).append(k)
    peaks = [ks[int(np.argmax(predicted[ks]))] for ks in by_day.values()]
    minima = [ks[int(np.argmin(predicted[ks]))] for ks in by_day.values()]
    explained = explain_rows(model, artifact, ds, index[peaks + minima], tense="is expected to be")
    peak_why, min_why = explained[: len(peaks)], explained[len(peaks):]

    days = []
    for (day, ks), peak_k, min_k, why_peak, why_min in zip(by_day.items(), peaks, minima, peak_why, min_why):
        level = risk_level(float(predicted[peak_k]), float(p_high[peak_k]), thresholds)
        headline = {"critical": "Critical demand expected", "high": "High demand expected", "watch": "Demand may turn high", "normal": "Peak expected to stay below the high mark"}[level]
        low_level = "low" if p_low[min_k] >= 0.5 else "watch" if p_low[min_k] >= 0.2 else "normal"
        low_headline = {"low": "Very low demand expected", "watch": "Demand may drop low", "normal": "Minimum expected to stay above the low mark"}[low_level]
        days.append({
            "date": day.isoformat(), "weekday": DAYS[day.weekday()], "holiday": ds.names.get(day), "hours_covered": len(ks),
            "peak_mw": round(float(predicted[peak_k]), 1), "peak_at": ds.time(index[peak_k]), "probability_high": round(float(p_high[peak_k]), 3), "risk": level,
            "high_windows": [{"from": ds.time(index[a]), "to": ds.time(index[b]) + timedelta(hours=1)} for a, b in _windows(ks, p_high >= 0.5)],
            "normal_at_peak_mw": why_peak["normal_mw"], "drivers": why_peak["drivers"], "weather_at_peak": why_peak["weather"],
            "reason": reason_sentence(why_peak, f"{headline} at {ds.time(index[peak_k]):%H}:00 ({predicted[peak_k]:,.0f} MW vs {why_peak['normal_mw']:,.0f} MW normal),"),
            "min_mw": round(float(predicted[min_k]), 1), "min_at": ds.time(index[min_k]), "probability_low": round(float(p_low[min_k]), 3), "low_level": low_level,
            "low_windows": [{"from": ds.time(index[a]), "to": ds.time(index[b]) + timedelta(hours=1)} for a, b in _windows(ks, p_low >= 0.5)],
            "normal_at_min_mw": why_min["normal_mw"], "min_drivers": why_min["drivers"], "weather_at_min": why_min["weather"],
            "min_reason": reason_sentence(why_min, f"{low_headline} at {ds.time(index[min_k]):%H}:00 ({predicted[min_k]:,.0f} MW vs {why_min['normal_mw']:,.0f} MW normal),", direction="down"),
        })
    return {**base, "hours": hourly, "days": days}


def explain_day(db: Session, day: date) -> dict | None:
    artifact = load_artifact()
    ds = dataset(db)
    first = ds.index(datetime.combine(day, datetime.min.time()))
    if first < 0 or first + 24 > ds.hours:
        return None
    index = np.arange(first, first + 24)
    usable = index[~np.isnan(ds.raw["temperature_c"][index]) & ~np.isnan(ds.raw["level_mean"][index])]
    if not len(usable):
        return None
    model, thresholds = artifact["model"], app_settings.demand_thresholds(db, artifact["thresholds"])
    predicted = model.predict(make_matrix(_take(ds.raw, usable)))
    normal = model.predict(make_matrix(normal_inputs(ds, artifact, usable)))
    actual = ds.load[usable]
    has_actual = int(np.sum(~np.isnan(actual))) >= 12
    series = actual if has_actual else predicted
    peak_k, min_k = (int(np.nanargmax(series)), int(np.nanargmin(series)))
    tense = "was" if has_actual else "is expected to be"
    why_peak, why_min = explain_rows(model, artifact, ds, usable[[peak_k, min_k]], tense=tense)
    peak_mw, min_mw = float(series[peak_k]), float(series[min_k])
    level = "critical" if peak_mw >= thresholds["critical_mw"] else "high" if peak_mw >= thresholds["high_mw"] else "normal"
    low_level = "low" if min_mw <= thresholds["low_mw"] else "normal"

    past = slice(max(0, first // 24 - 365), first // 24 + 1)
    year_peaks = ds.daily_peak[past][~np.isnan(ds.daily_peak[past])]
    verb = "was" if has_actual else "is expected to be"
    peak_headline = f"Demand {verb} {'critically high' if level == 'critical' else 'high' if level == 'high' else 'below the high mark'} at its {ds.time(usable[peak_k]):%H}:00 peak ({peak_mw:,.0f} MW vs {why_peak['normal_mw']:,.0f} MW normal),"
    min_headline = f"Demand {verb} {'low' if low_level == 'low' else 'above the low mark'} at its {ds.time(usable[min_k]):%H}:00 minimum ({min_mw:,.0f} MW vs {why_min['normal_mw']:,.0f} MW normal),"
    return {
        "date": day.isoformat(), "weekday": DAYS[day.weekday()], "holiday": ds.names.get(day), "has_actual": has_actual, "thresholds": thresholds,
        "peak_mw": round(peak_mw, 1), "peak_at": ds.time(usable[peak_k]), "level": level,
        "percentile_in_past_year": round(float(np.mean(year_peaks <= peak_mw) * 100), 1) if has_actual and len(year_peaks) else None,
        "normal_at_peak_mw": why_peak["normal_mw"], "model_at_peak_mw": why_peak["predicted_mw"],
        "unexplained_at_peak_mw": round(peak_mw - why_peak["predicted_mw"], 1) if has_actual else None,
        "drivers": why_peak["drivers"], "weather_at_peak": why_peak["weather"], "reason": reason_sentence(why_peak, peak_headline),
        "min_mw": round(min_mw, 1), "min_at": ds.time(usable[min_k]), "low_level": low_level,
        "normal_at_min_mw": why_min["normal_mw"], "model_at_min_mw": why_min["predicted_mw"],
        "unexplained_at_min_mw": round(min_mw - why_min["predicted_mw"], 1) if has_actual else None,
        "min_drivers": why_min["drivers"], "weather_at_min": why_min["weather"], "min_reason": reason_sentence(why_min, min_headline, direction="down"),
        "hours": [{"at": ds.time(i), "actual_mw": _round(float(a)), "predicted_mw": round(float(p), 1), "normal_mw": round(float(n), 1)} for i, a, p, n in zip(usable, actual, predicted, normal)],
    }


def ranked_days(db: Session, kind: str = "high", days: int = 365, limit: int = 20) -> list[dict]:
    """The highest-peak (kind="high") or lowest-minimum (kind="low") days of the recent past, each with its top reasons."""
    artifact = load_artifact()
    return _cached(f"{kind}:{days}:{limit}", 600, lambda: _ranked_days(db, artifact, kind, days, limit))


def _ranked_days(db: Session, artifact: dict, kind: str, days: int, limit: int) -> list[dict]:
    ds = dataset(db)
    last_day = ds.last_load_index() // 24
    load2d = ds.load[: (last_day + 1) * 24].reshape(-1, 24)
    candidates = [d for d in range(max(0, last_day - days + 1), last_day + 1) if not np.isnan(ds.daily_peak[d])]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        value = {d: (np.nanmax(load2d[d]) if kind == "high" else np.nanmin(load2d[d])) for d in candidates}
    chosen = sorted(candidates, key=lambda d: -value[d] if kind == "high" else value[d])[:limit]
    index = np.array([d * 24 + int(np.nanargmax(load2d[d]) if kind == "high" else np.nanargmin(load2d[d])) for d in chosen], dtype=int)
    keep = ~np.isnan(ds.raw["temperature_c"][index]) & ~np.isnan(ds.raw["level_mean"][index]) if len(index) else np.array([], dtype=bool)
    chosen, index = [d for d, k in zip(chosen, keep) if k], index[keep]
    if not len(index):
        return []
    sign = 1 if kind == "high" else -1
    rows = []
    for i, why in zip(index, explain_rows(artifact["model"], artifact, ds, index)):
        day = ds.time(i).date()
        rows.append({"date": day.isoformat(), "weekday": DAYS[day.weekday()], "holiday": ds.names.get(day), "load_mw": round(float(ds.load[i]), 1), "at": ds.time(i),
                     "normal_mw": why["normal_mw"], "top_reasons": [d for d in why["drivers"] if sign * d["effect_mw"] > 0][:2], "weather": why["weather"]})
    return rows


def recent_hourly_load(db: Session, days: int = 14) -> list[tuple[datetime, float]]:
    """Complete hourly means of Delhi load over the last `days` days of SLDC data (IST, oldest first)."""
    latest = db.scalar(select(func.max(SldcLoad.slot_at)))
    if latest is None:
        return []
    sqlite = db.bind.dialect.name == "sqlite"
    bucket = func.strftime("%Y-%m-%d %H", SldcLoad.slot_at) if sqlite else func.to_char(SldcLoad.slot_at, "YYYY-MM-DD HH24")
    rows = db.execute(select(bucket, func.avg(SldcLoad.delhi_mw), func.count())
                      .where(SldcLoad.slot_at >= latest - timedelta(days=days), SldcLoad.delhi_mw >= MIN_VALID_MW)
                      .group_by(bucket).order_by(bucket)).all()
    return [(datetime.strptime(key, "%Y-%m-%d %H"), round(float(mean), 2)) for key, mean, count in rows if count >= MIN_SAMPLES_PER_HOUR]


def explain_hour(db: Session, at: datetime) -> dict | None:
    """Reasons for one (usually upcoming) hour, against the same 'normal' reference as the day explanations."""
    artifact = load_artifact()
    ds = dataset(db)
    index = ds.index(at.replace(minute=0, second=0, microsecond=0))
    if not 0 <= index < ds.hours or np.isnan(ds.raw["temperature_c"][index]) or np.isnan(ds.raw["level_mean"][index]):
        return None
    tense = "was" if not np.isnan(ds.load[index]) else "is expected to be"
    return explain_rows(artifact["model"], artifact, ds, np.array([index]), tense=tense)[0]


def festival_days(db: Session, year: int) -> list[dict]:
    """Every named holiday / festival in a year with what it did to Delhi's demand.

    past      real SLDC load, and the model's estimate of the same day without the festival (calendar inputs switched off,
              weather and demand level as they really were): the difference is the festival's own effect
    forecast  within the weather-forecast horizon: the same comparison on predicted load
    upcoming  beyond the forecast: the effect this festival had on average in past years
    """
    artifact = load_artifact()
    return _cached(f"festivals:{year}", 600, lambda: _festival_days(db, artifact, year))


def _festival_days(db: Session, artifact: dict, year: int) -> list[dict]:
    ds = dataset(db)
    model = artifact["model"]
    typical = {row["name"]: row for row in artifact["insights"]["holidays"]}
    last_actual_day = ds.time(ds.last_load_index()).date()
    rows = []
    for day, name in sorted((d, n) for d, n in ds.names.items() if d.year == year):
        first = ds.index(datetime.combine(day, datetime.min.time()))
        primary = holiday_key(name)
        base = {"date": day.isoformat(), "weekday": DAYS[day.weekday()], "name": name, "primary_name": primary, "gazetted": day in ds.gazetted,
                "typical_effect_mw": typical[primary]["effect_mw"] if primary in typical else None,
                "typical_effect_pct": typical[primary]["effect_pct"] if primary in typical else None}
        index = np.arange(first, first + 24) if 0 <= first and first + 24 <= ds.hours else np.array([], dtype=int)
        usable = index[~np.isnan(ds.raw["temperature_c"][index]) & ~np.isnan(ds.raw["level_mean"][index])] if len(index) else index
        if len(usable) < 12:
            rows.append({**base, "status": "upcoming" if day > last_actual_day else "no_data"})
            continue
        actual_inputs = _take(ds.raw, usable)
        without = dict(actual_inputs, is_holiday=np.zeros(len(usable)), is_festival=np.zeros(len(usable)))
        with_day, without_day = model.predict(make_matrix(actual_inputs)), model.predict(make_matrix(without))
        effect = with_day - without_day
        actual = ds.load[usable]
        has_actual = int(np.sum(~np.isnan(actual))) >= 18
        evening = np.isin(actual_inputs["hour"], (19, 20, 21, 22))
        series = actual if has_actual else with_day
        peak_k = int(np.nanargmax(series))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rows.append({
                **base, "status": "past" if day <= last_actual_day else "forecast", "has_actual": has_actual,
                "mean_mw": round(float(np.nanmean(series)), 1), "peak_mw": round(float(series[peak_k]), 1), "peak_at": ds.time(usable[peak_k]),
                "without_festival_mean_mw": round(float(np.mean(without_day)), 1),
                "festival_effect_mw": round(float(effect.mean()), 1), "festival_effect_pct": round(float(effect.sum() / without_day.sum() * 100), 2),
                "evening_effect_mw": round(float(effect[evening].mean()), 1) if evening.any() else None,
                "vs_without_mw": round(float(np.nanmean(series) - np.mean(without_day)), 1),
                "temperature_max_c": _round(float(np.nanmax(actual_inputs["temperature_c"]))),
            })
    return rows


def high_days(db: Session, days: int = 365, limit: int = 20) -> list[dict]:
    return ranked_days(db, "high", days, limit)


# ---- scheduled job: retrain daily, keep the forecast warm (alerts: services/alert_engine.py) ---------------------------

def scheduled_job(db: Session) -> int:
    trained_recently = ARTIFACT.exists() and datetime.now(timezone.utc) - datetime.fromtimestamp(ARTIFACT.stat().st_mtime, timezone.utc) < RETRAIN_AFTER
    if not trained_recently:
        train(db)
    global _dataset
    _dataset = None  # pick up the newest load and weather forecast
    _cache.clear()
    return len(forecast(db)["hours"])


def main() -> None:
    from ..database import Base, SessionLocal, engine

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("train")
    sub.add_parser("forecast")
    explain = sub.add_parser("explain")
    explain.add_argument("day", type=date.fromisoformat)
    for name in ("high-days", "low-days"):
        ranked = sub.add_parser(name)
        ranked.add_argument("--days", type=int, default=365)
    sub.add_parser("insights")
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if args.command == "train":
            result = train(db)
        elif args.command == "forecast":
            result = forecast(db)
            result = {"thresholds": result["thresholds"], "days": [{k: d[k] for k in ("date", "risk", "peak_mw", "peak_at", "probability_high", "high_windows", "reason", "low_level", "min_mw", "min_at", "min_reason")} for d in result["days"]]}
        elif args.command == "explain":
            result = explain_day(db, args.day)
            result = result and {k: v for k, v in result.items() if k != "hours"}
        elif args.command == "insights":
            result = insights()
        else:
            result = ranked_days(db, "high" if args.command == "high-days" else "low", args.days)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
