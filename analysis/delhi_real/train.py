"""Day-ahead hourly Delhi load model trained on the real collected data.

Inputs (filled by the backend collectors, see backend/app/services/*_collector.py):
    sldc_load         5-minute SCADA load, Delhi SLDC
    weather_hourly    NASA POWER history, Open-Meteo recent/forecast
    calendar_context  gazetted holidays + restricted-holiday festivals
Run:     python3 analysis/delhi_real/train.py [--db backend/gridsense.db] [--test-days 365]
Output:  analysis/delhi_real/output/report.json, plus the copy the API serves at
         backend/app/data/delhi_real_report.json

"Day-ahead" means every feature is known by midnight before the forecast day: load lags are >= 24 h and weather is
the observed value, standing in for a next-day weather forecast (so live accuracy will be somewhat worse).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "output"
API_COPY = ROOT / "backend" / "app" / "data" / "delhi_real_report.json"

MIN_SAMPLES_PER_HOUR = 9  # of 12 five-minute slots; fewer means a SCADA gap, not a real hourly mean
MIN_VALID_MW = 500  # SCADA occasionally reports near-zero glitches
LOCKDOWN = ("2020-03-25", "2020-05-31")  # demand collapsed; kept out of training and scoring
FEATURES = {
    "hour": "Hour of day", "dow": "Day of week", "doy": "Day of year", "is_weekend": "Weekend",
    "is_holiday": "Gazetted holiday", "is_festival": "Festival (restricted holiday)",
    "temperature_c": "Temperature", "humidity_pct": "Humidity", "precipitation_mm": "Rain", "wind_speed_ms": "Wind",
    "temp_mean_24h": "Temperature, 24 h mean", "heat_index": "Temperature × humidity",
    "lag_24h": "Load same hour yesterday", "lag_168h": "Load same hour last week", "prev_day_mean": "Yesterday's mean load",
    "prev_day_peak": "Yesterday's peak load",
}


def load_frames(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with sqlite3.connect(db_path) as con:
        load = pd.read_sql("SELECT slot_at, delhi_mw FROM sldc_load", con, parse_dates=["slot_at"])
        weather = pd.read_sql("SELECT source, slot_at, temperature_c, humidity_pct, precipitation_mm, wind_speed_ms FROM weather_hourly", con, parse_dates=["slot_at"])
        calendar = pd.read_sql("SELECT calendar_date, is_weekend, is_holiday, festival_name FROM calendar_context", con)
    return load, weather, calendar


def build_hourly(load: pd.DataFrame, weather: pd.DataFrame, calendar: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    load = load[load.delhi_mw >= MIN_VALID_MW]
    grouped = load.set_index("slot_at").delhi_mw.resample("h")
    hourly = pd.DataFrame({"load_mw": grouped.mean(), "samples": grouped.count()})
    hourly.loc[hourly.samples < MIN_SAMPLES_PER_HOUR, "load_mw"] = np.nan
    hourly = hourly.asfreq("h")

    # Both weather sources stamp whole UTC hours, i.e. HH:30 IST; floor onto the load hour. Prefer NASA POWER.
    weather = weather.assign(slot_at=weather.slot_at.dt.floor("h"), rank=(weather.source != "nasa_power").astype(int))
    weather = weather.sort_values("rank").drop_duplicates("slot_at").set_index("slot_at").drop(columns=["source", "rank"])
    df = hourly.join(weather, how="left")

    calendar["date"] = pd.to_datetime(calendar.calendar_date.str[:10])
    names = calendar.festival_name.fillna("")
    calendar["is_festival"] = (names != "") & ~calendar.is_holiday.astype(bool)
    df["date"] = df.index.normalize()
    df = df.reset_index().merge(calendar[["date", "is_weekend", "is_holiday", "is_festival", "festival_name"]], on="date", how="left").set_index("slot_at")

    df["hour"], df["dow"], df["doy"] = df.index.hour, df.index.dayofweek, df.index.dayofyear
    for column in ("is_weekend", "is_holiday", "is_festival"):
        df[column] = df[column].fillna(False).astype(int)
    df["temp_mean_24h"] = df.temperature_c.rolling(24, min_periods=12).mean()
    df["heat_index"] = df.temperature_c * df.humidity_pct / 100
    df["lag_24h"] = df.load_mw.shift(24)
    df["lag_168h"] = df.load_mw.shift(168)
    daily = df.load_mw.resample("D").agg(["mean", "max"]).shift(1)
    df["prev_day_mean"] = daily["mean"].reindex(df.date).to_numpy()
    df["prev_day_peak"] = daily["max"].reindex(df.date).to_numpy()
    df["lockdown"] = (df.index >= LOCKDOWN[0]) & (df.index < pd.Timestamp(LOCKDOWN[1]) + pd.Timedelta(days=1))

    quality = {
        "load_slots": int(len(load)),
        "first_hour": str(df.index.min()),
        "last_hour": str(df.load_mw.last_valid_index()),
        "hours_total": int(len(df)),
        "hours_with_load": int(df.load_mw.notna().sum()),
        "hours_missing_load": int(df.load_mw.isna().sum()),
        "hours_missing_weather": int(df.temperature_c.isna().sum()),
        "days_with_gaps": int((df.load_mw.isna().groupby(df.date).sum() > 0).sum()),
    }
    return df, quality


def train(df: pd.DataFrame, test_days: int):
    usable = df[df.load_mw.notna() & ~df.lockdown]
    test_from = usable.index.max().normalize() - pd.Timedelta(days=test_days - 1)
    train_rows, test_rows = usable[usable.index < test_from], usable[usable.index >= test_from]
    features = list(FEATURES)
    model = HistGradientBoostingRegressor(max_iter=600, learning_rate=0.05, max_leaf_nodes=63, l2_regularization=1.0, random_state=0)
    model.fit(train_rows[features], train_rows.load_mw)

    predicted = model.predict(test_rows[features])
    actual = test_rows.load_mw.to_numpy()

    def score(pred) -> dict:
        mask = ~np.isnan(pred)
        return {
            "mae_mw": round(float(mean_absolute_error(actual[mask], pred[mask])), 1),
            "mape_pct": round(float(np.mean(np.abs(actual[mask] - pred[mask]) / actual[mask]) * 100), 2),
        }

    daily = test_rows.assign(pred=predicted).groupby("date").agg(actual_peak=("load_mw", "max"), pred_peak=("pred", "max"))
    metrics = {
        "train_period": f"{train_rows.index.min():%Y-%m-%d} to {train_rows.index.max():%Y-%m-%d}",
        "test_period": f"{test_from:%Y-%m-%d} to {test_rows.index.max():%Y-%m-%d}",
        "train_hours": int(len(train_rows)),
        "test_hours": int(len(test_rows)),
        "model": score(predicted),
        "baseline_same_hour_last_week": score(test_rows.lag_168h.to_numpy()),
        "baseline_same_hour_yesterday": score(test_rows.lag_24h.to_numpy()),
        "daily_peak_mae_mw": round(float((daily.actual_peak - daily.pred_peak).abs().mean()), 1),
    }
    sample = test_rows.sample(min(len(test_rows), 4000), random_state=0)
    imp = permutation_importance(model, sample[features], sample.load_mw, n_repeats=3, random_state=0, scoring="neg_mean_absolute_error")
    importance = sorted(
        ({"feature": FEATURES[name], "mae_increase_mw": round(float(value), 1)} for name, value in zip(features, imp.importances_mean)),
        key=lambda row: -row["mae_increase_mw"],
    )
    return model, test_rows.assign(pred=predicted), metrics, importance


def controlled_effect(model, rows: pd.DataFrame, **changes) -> float:
    """Mean change in predicted load when only the named inputs change, everything else at real values."""
    features = list(FEATURES)
    on, off = rows[features].copy(), rows[features].copy()
    for column, (a, b) in changes.items():
        on[column], off[column] = a, b
    if "temperature_c" in changes:  # keep derived weather features consistent with the edited temperature
        for frame, key in ((on, 0), (off, 1)):
            frame["temp_mean_24h"] = changes["temperature_c"][key]
            frame["heat_index"] = changes["temperature_c"][key] * frame.humidity_pct / 100
    return round(float(np.mean(model.predict(on) - model.predict(off))), 1)


def summarise(df: pd.DataFrame, model, scored: pd.DataFrame) -> dict:
    valid = df[df.load_mw.notna()]
    yearly = valid.groupby(valid.index.year).load_mw.agg(["mean", "max", "idxmax"])
    peaks = [{"year": int(year), "mean_mw": round(row["mean"]), "peak_mw": round(row["max"]), "peak_at": str(row["idxmax"])} for year, row in yearly.iterrows()]
    summer = scored[scored.index.month.isin([5, 6, 7])]
    effects = [
        {"factor": "Gazetted holiday vs working day", "effect_mw": controlled_effect(model, scored, is_holiday=(1, 0))},
        {"factor": "Festival (restricted holiday) vs ordinary day", "effect_mw": controlled_effect(model, scored, is_festival=(1, 0))},
        {"factor": "Weekend vs weekday", "effect_mw": controlled_effect(model, scored, is_weekend=(1, 0))},
        {"factor": "Summer: 44°C vs 36°C", "effect_mw": controlled_effect(model, summer if len(summer) else scored, temperature_c=(44.0, 36.0))},
        {"factor": "Rain 10 mm/h vs dry", "effect_mw": controlled_effect(model, scored, precipitation_mm=(10.0, 0.0))},
    ]
    backtest = scored.tail(14 * 24)
    return {
        "yearly": peaks,
        "effects": effects,
        "backtest_last_14_days": [
            {"hour": str(index), "actual_mw": round(row.load_mw, 1), "predicted_mw": round(float(row.pred), 1), "temperature_c": None if pd.isna(row.temperature_c) else round(row.temperature_c, 1)}
            for index, row in backtest.iterrows()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "backend" / "gridsense.db")
    parser.add_argument("--test-days", type=int, default=365)
    args = parser.parse_args()

    df, quality = build_hourly(*load_frames(args.db))
    model, scored, metrics, importance = train(df, args.test_days)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "load": "Delhi SLDC SCADA, https://www.delhisldc.org/Loaddata.aspx",
            "weather": "NASA POWER hourly (history), Open-Meteo (recent)",
            "calendar": "python-holidays, India / Delhi (gazetted + restricted lists)",
        },
        "data_quality": quality,
        "model": metrics,
        "importance": importance,
        **summarise(df, model, scored),
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    API_COPY.write_text(json.dumps(report, separators=(",", ":")))
    print(json.dumps({key: report[key] for key in ("data_quality", "model", "importance", "yearly", "effects")}, indent=2))


if __name__ == "__main__":
    main()
