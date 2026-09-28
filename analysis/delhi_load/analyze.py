"""Delhi power load analysis: drivers, festival/holiday effects, zone peak risk, day explanations.

Dataset: https://www.kaggle.com/datasets/pratikyuvrajchougule/delhi-datset (MIT, synthetic).
Run:     python3 analysis/delhi_load/analyze.py
Output:  analysis/delhi_load/output/report.json and report.html, plus the copy the API serves at
         backend/app/data/delhi_load_report.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score, roc_auc_score

HERE = Path(__file__).parent
DATA = HERE / "data" / "delhi_power_load_dataset1.csv"
OUT = HERE / "output"
API_COPY = HERE.parents[1] / "backend" / "app" / "data" / "delhi_load_report.json"

LOAD_MIN, LOAD_MAX = 2000, 8300  # the dataset clamps load to this range
PEAK_MW = 6000  # roughly the top 9% of hours
TEST_FROM = "2023-11-01"  # time-based split: train Jan-Oct, test Nov-Dec
FEATURES = ["temp", "hum", "wind", "rain", "pub", "week", "fest", "zone", "high", "hour", "month", "dow"]
ZONES = {"Low": 0, "Medium": 1, "High": 2}
FESTIVAL_NAMES = {
    "2023-01-26": "Republic Day",
    "2023-03-08": "Holi",
    "2023-04-22": "Eid al-Fitr",
    "2023-08-15": "Independence Day",
    "2023-10-24": "Dussehra",
    "2023-11-12": "Diwali",
    "2023-12-25": "Christmas",
}
# Factor groups for "why was this day high/low": each is reset to a typical value.
FACTORS = {
    "Rain": ["rain"],
    "Temperature": ["temp"],
    "Wind": ["wind"],
    "Humidity": ["hum"],
    # Festival/holiday/weekend uplifts don't stack in this data, so they're attributed together.
    "Day type (festival / holiday / weekend)": ["fest", "pub", "week"],
    "Development zone mix": ["zone"],
}


def load() -> pd.DataFrame:
    df = pd.read_csv(DATA)
    df.columns = ["date", "time", "temp", "hum", "wind", "rain", "pub", "week", "fest",
                  "dev", "low", "med", "high", "load"]
    dt = pd.to_datetime(df["date"] + " " + df["time"])
    df["hour"], df["month"], df["dow"] = dt.dt.hour, dt.dt.month, dt.dt.dayofweek
    df["zone"] = df["dev"].map(ZONES)
    return df


def data_quality(df: pd.DataFrame) -> dict:
    jan_night = df[(df.month == 1) & (df.hour.between(0, 5))]
    return {
        "rows": len(df),
        "days": int(df.date.nunique()),
        "start": df.date.min(),
        "end": df.date.max(),
        "floor_share": round(float((df.load == 2000).mean()), 3),
        "cap_share": round(float((df.load == 8300).mean()), 3),
        "temp_hour_to_hour_autocorr": round(float(df.temp.autocorr()), 2),
        "jan_night_temp_max": round(float(jan_night.temp.max()), 1),
        "public_holiday_days": int(df[df.pub == 1].date.nunique()),
        "festival_days": int(df[df.fest == 1].date.nunique()),
        "weekend_days": int(df[df.week == 1].date.nunique()),
        "avg_load_mw": round(float(df.load.mean())),
    }


def train(df: pd.DataFrame):
    train_mask = df.date < TEST_FROM
    X, y = df[FEATURES], df["load"]
    cat = [FEATURES.index("zone")]
    reg = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, categorical_features=cat, random_state=0)
    reg.fit(X[train_mask], y[train_mask])
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, categorical_features=cat, random_state=0)
    clf.fit(X[train_mask], y[train_mask] >= PEAK_MW)

    Xt, yt = X[~train_mask], y[~train_mask]
    pred = predict_mw(reg, Xt)
    hour_profile = df[train_mask].groupby("hour")["load"].mean()
    baseline = hour_profile.loc[df.loc[~train_mask, "hour"]].to_numpy()
    imp = permutation_importance(reg, Xt, yt, n_repeats=5, random_state=0, scoring="r2")
    metrics = {
        "test_period": f"{TEST_FROM} to {df.date.max()}",
        "mae_mw": round(float(mean_absolute_error(yt, pred)), 0),
        "r2": round(float(r2_score(yt, pred)), 3),
        "baseline_mae_mw": round(float(mean_absolute_error(yt, baseline)), 0),
        "peak_auc": round(float(roc_auc_score(yt >= PEAK_MW, clf.predict_proba(Xt)[:, 1])), 3),
    }
    labels = {"temp": "Temperature", "hum": "Humidity", "wind": "Wind speed", "rain": "Rain",
              "pub": "Public holiday", "week": "Weekend", "fest": "Festival", "zone": "Development zone",
              "high": "High-dev area %", "hour": "Hour of day", "month": "Month", "dow": "Day of week"}
    importance = sorted(
        ({"feature": labels[f], "importance": round(float(m), 3)} for f, m in zip(FEATURES, imp.importances_mean)),
        key=lambda r: -r["importance"],
    )
    return reg, clf, metrics, importance


def predict_mw(model, X) -> np.ndarray:
    return np.clip(model.predict(X), LOAD_MIN, LOAD_MAX)


def controlled_effect(model, df: pd.DataFrame, col: str, on, off) -> float:
    """Average change in predicted load when only `col` changes, all else held at the real values."""
    a, b = df[FEATURES].copy(), df[FEATURES].copy()
    a[col], b[col] = on, off
    return float(np.mean(predict_mw(model, a) - predict_mw(model, b)))


def effects(reg, df: pd.DataFrame) -> dict:
    raw = lambda col: float(df[df[col] == 1].load.mean() - df[df[col] == 0].load.mean())
    event_rows = []
    for col, name in [("fest", "Festival"), ("pub", "Public holiday"), ("week", "Weekend")]:
        event_rows.append({
            "event": name,
            "raw_uplift_mw": round(raw(col)),
            "controlled_uplift_mw": round(controlled_effect(reg, df, col, 1, 0)),
        })
    weather = [
        {"factor": "Heavy rain (15 mm) vs dry", "effect_mw": round(controlled_effect(reg, df, "rain", 15.0, 0.0))},
        {"factor": "Hot (42°C) vs mild (22°C)", "effect_mw": round(controlled_effect(reg, df, "temp", 42.0, 22.0))},
        {"factor": "Windy (18 km/h) vs calm (2 km/h)", "effect_mw": round(controlled_effect(reg, df, "wind", 18.0, 2.0))},
        {"factor": "Humid (85%) vs dry air (35%)", "effect_mw": round(controlled_effect(reg, df, "hum", 85.0, 35.0))},
        {"factor": "High vs Low development zone", "effect_mw": round(controlled_effect(reg, df, "zone", 2, 0))},
    ]
    bins = {"rain": [-0.1, 0, 2, 5, 10, 15, 20], "temp": [5, 15, 25, 30, 35, 40, 45]}
    curves = {}
    for col, edges in bins.items():
        g = df.groupby(pd.cut(df[col], edges), observed=True)["load"].mean()
        curves[col] = [{"bin": str(k), "load_mw": round(v)} for k, v in g.items()]
    hourly = df.groupby(["hour", "dev"])["load"].mean().unstack()
    curves["hour_by_zone"] = [
        {"hour": int(h), **{z: round(float(hourly.loc[h, z])) for z in ZONES}} for h in hourly.index
    ]
    monthly = df.groupby("month").agg(load=("load", "mean"), temp=("temp", "mean"))
    curves["monthly"] = [{"month": int(m), "load_mw": round(r.load), "temp_c": round(r.temp, 1)} for m, r in monthly.iterrows()]
    return {"events": event_rows, "weather_and_zone": weather, "curves": curves}


def zone_outlook(clf, df: pd.DataFrame) -> dict:
    rows = []
    for zone, code in ZONES.items():
        z = df[df.zone == code]
        probs = clf.predict_proba(z[FEATURES])[:, 1]
        rows.append({
            "zone": zone,
            "avg_load_mw": round(float(z.load.mean())),
            "peak_hour_share": round(float((z.load >= PEAK_MW).mean()), 3),
            "mean_peak_probability": round(float(probs.mean()), 3),
        })
    share = df.groupby("date")[["low", "med", "high"]].first()
    zone_mean = df.groupby("dev")["load"].mean()
    def mix_load(r):
        return (r.low * zone_mean["Low"] + r.med * zone_mean["Medium"] + r.high * zone_mean["High"]) / 100
    start, end = share.iloc[0], share.iloc[-1]
    return {
        "zones": rows,
        "area_mix": {
            "start": {"date": share.index[0], "low": round(start.low, 1), "medium": round(start.med, 1), "high": round(start.high, 1)},
            "end": {"date": share.index[-1], "low": round(end.low, 1), "medium": round(end.med, 1), "high": round(end.high, 1)},
            "mix_weighted_load_start_mw": round(mix_load(start)),
            "mix_weighted_load_end_mw": round(mix_load(end)),
        },
    }


def scenarios(reg, clf, df: pd.DataFrame) -> list[dict]:
    """What-if grid: which zone/day/weather/time combinations are most likely to need more power."""
    day_types = {
        "Normal weekday": dict(pub=0, week=0, fest=0, dow=2),
        "Weekend": dict(pub=0, week=1, fest=0, dow=5),
        "Public holiday": dict(pub=1, week=0, fest=0, dow=2),
        "Festival (e.g. Diwali)": dict(pub=0, week=0, fest=1, dow=6),
    }
    weather = {
        "Hot & dry": dict(temp=42.0, hum=40.0, wind=3.0, rain=0.0),
        "Mild & rainy": dict(temp=26.0, hum=80.0, wind=12.0, rain=14.0),
    }
    times = {"3 AM": 3, "10 AM": 10, "3 PM": 15, "11 PM": 23}
    high_now = float(df.high.iloc[-1])
    rows, records = [], []
    for zone, code in ZONES.items():
        for dname, d in day_types.items():
            for wname, w in weather.items():
                for tname, h in times.items():
                    records.append(dict(zone=code, hour=h, month=6, high=high_now, **d, **w))
                    rows.append({"zone": zone, "day_type": dname, "weather": wname, "time": tname})
    X = pd.DataFrame(records)[FEATURES]
    for row, mw, p in zip(rows, predict_mw(reg, X), clf.predict_proba(X)[:, 1]):
        row["predicted_mw"] = round(float(mw))
        row["peak_probability"] = round(float(p), 3)
    return rows


def explain_days(reg, df: pd.DataFrame) -> list[dict]:
    typical = {"rain": float(df.rain.median()), "temp": float(df.temp.median()), "wind": float(df.wind.median()),
               "hum": float(df.hum.median()), "fest": 0, "pub": 0, "week": 0, "zone": 1}
    daily = df.groupby("date")["load"].mean()
    year_avg = float(df.load.mean())
    picks = {d: "Highest-load day" for d in daily.nlargest(4).index}
    picks.update({d: "Lowest-load day" for d in daily.drop("2023-12-31").nsmallest(4).index})  # 31 Dec has 1 row
    picks.update({d: f"Festival: {n}" for d, n in FESTIVAL_NAMES.items()})

    out = []
    for date, day in df.groupby("date"):
        tag = picks.get(date, "")
        X = day[FEATURES]
        base = float(predict_mw(reg, X).mean())
        drivers = []
        for name, cols in FACTORS.items():
            if all((day[c] == typical[c]).all() for c in cols):
                continue
            cf = X.copy()
            for c in cols:
                cf[c] = typical[c]
            delta = base - float(predict_mw(reg, cf).mean())
            if abs(delta) >= 40:
                drivers.append({"factor": name, "effect_mw": round(delta)})
        drivers.sort(key=lambda r: -abs(r["effect_mw"]))
        out.append({
            "date": date,
            "tag": tag,
            "festival_name": FESTIVAL_NAMES.get(date),
            "weekday": pd.Timestamp(date).day_name(),
            "actual_avg_mw": round(float(day.load.mean())),
            "vs_year_avg_mw": round(float(day.load.mean()) - year_avg),
            "peak_mw": round(float(day.load.max())),
            "rain_mm": round(float(day.rain.mean()), 1),
            "temp_c": round(float(day.temp.mean()), 1),
            "flags": [n for c, n in [("fest", "festival"), ("pub", "public holiday"), ("week", "weekend")] if day[c].iloc[0]],
            "drivers": drivers[:5],
        })
    return out


def main() -> None:
    df = load()
    reg, clf, metrics, importance = train(df)
    # Refit on the full year for the explanations and what-if scenarios.
    full_reg = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05,
                                             categorical_features=[FEATURES.index("zone")], random_state=0).fit(df[FEATURES], df.load)
    full_clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                              categorical_features=[FEATURES.index("zone")], random_state=0).fit(df[FEATURES], df.load >= PEAK_MW)
    report = {
        "peak_threshold_mw": PEAK_MW,
        "data_quality": data_quality(df),
        "model": metrics,
        "importance": importance,
        "effects": effects(full_reg, df),
        "zone_outlook": zone_outlook(full_clf, df),
        "scenarios": scenarios(full_reg, full_clf, df),
        "days": explain_days(full_reg, df),
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    API_COPY.parent.mkdir(exist_ok=True)
    API_COPY.write_text(json.dumps(report, separators=(",", ":")))
    template = (HERE / "report_template.html").read_text()
    (OUT / "report.html").write_text(template.replace("/*REPORT_JSON*/null", json.dumps(report)))
    print(json.dumps({k: report[k] for k in ("data_quality", "model", "importance")}, indent=2))
    print(json.dumps(report["effects"]["events"] + report["effects"]["weather_and_zone"], indent=2))
    print(json.dumps(report["zone_outlook"], indent=2))


if __name__ == "__main__":
    main()
