from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import mean, median
from typing import Iterable


MODEL_NAMES = ("persistence", "moving_average", "trend", "weighted_ensemble")
DEFAULT_WINDOW = 3

# This series is intentionally stable so an empty development database still has
# useful, repeatable API responses. It is never mixed with imported observations.
FALLBACK_SERIES: tuple[float, ...] = (
    98.0,
    101.0,
    99.0,
    104.0,
    108.0,
    106.0,
    111.0,
    115.0,
    112.0,
    118.0,
    121.0,
    119.0,
)


@dataclass(frozen=True)
class ForecastPoint:
    forecast_for: datetime
    demand_mw: float
    confidence: float


@dataclass(frozen=True)
class ForecastResult:
    model_name: str
    points: tuple[ForecastPoint, ...]
    metrics: dict[str, float]
    data_points: int
    last_observed_at: datetime
    last_observed_demand_mw: float
    used_fallback: bool
    cadence_minutes: int


def _normalise_model(model_name: str) -> str:
    model = model_name.strip().lower().replace("-", "_").replace(" ", "_")
    if model not in MODEL_NAMES:
        raise ValueError(f"Unsupported model: {model_name}")
    return model


def _slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    x_mean = (len(values) - 1) / 2
    y_mean = mean(values)
    denominator = sum((index - x_mean) ** 2 for index in range(len(values)))
    if denominator == 0:
        return 0.0
    return sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values)) / denominator


def _prediction(values: list[float], model_name: str, window: int = DEFAULT_WINDOW) -> float:
    model = _normalise_model(model_name)
    if not values:
        return FALLBACK_SERIES[-1]
    last = values[-1]
    average = mean(values[-window:])
    trend = max(0.0, last + _slope(values[-max(window, 2):]))
    if model == "persistence":
        result = last
    elif model == "moving_average":
        result = average
    elif model == "trend":
        result = trend
    else:
        result = (0.20 * last) + (0.30 * average) + (0.20 * trend) + (0.30 * ((last + average) / 2))
    return round(max(0.0, result), 3)


def _cadence(timestamps: list[datetime]) -> timedelta:
    intervals = [
        (right - left).total_seconds()
        for left, right in zip(timestamps, timestamps[1:])
        if (right - left).total_seconds() > 0
    ]
    # Hourly data is the safest deterministic default for a sparse dataset.
    seconds = median(intervals) if intervals else 3600
    return timedelta(seconds=max(60, int(seconds)))


def _metrics(actual: list[float], predicted: list[float], timestamps: list[datetime]) -> dict[str, float]:
    if not actual:
        return {
            "mae": 0.0,
            "rmse": 0.0,
            "mape": 0.0,
            "r2": 0.0,
            "peak_timing_error_hours": 0.0,
            "peak_timing_error": 0.0,
            "peak_magnitude_error_mw": 0.0,
            "peak_magnitude_error": 0.0,
        }
    errors = [prediction - observed for observed, prediction in zip(actual, predicted)]
    mae = mean(abs(error) for error in errors)
    rmse = sqrt(mean(error * error for error in errors))
    non_zero = [abs(observed) for observed in actual if observed != 0]
    mape = (mean(abs(error) / abs(observed) for observed, error in zip(actual, errors) if observed != 0) * 100) if non_zero else 0.0
    actual_mean = mean(actual)
    total_sum = sum((observed - actual_mean) ** 2 for observed in actual)
    r2 = 1.0 - (sum(error * error for error in errors) / total_sum) if total_sum else (1.0 if mae == 0 else 0.0)
    actual_peak = max(range(len(actual)), key=actual.__getitem__)
    predicted_peak = max(range(len(predicted)), key=predicted.__getitem__)
    timing_error = abs((timestamps[predicted_peak] - timestamps[actual_peak]).total_seconds()) / 3600
    return {
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "mape": round(mape, 3),
        "r2": round(r2, 3),
        "peak_timing_error_hours": round(timing_error, 3),
        "peak_timing_error": round(timing_error, 3),
        "peak_magnitude_error_mw": round(abs(max(predicted) - max(actual)), 3),
        "peak_magnitude_error": round(abs(max(predicted) - max(actual)), 3),
    }


def _observations(rows: Iterable[tuple[datetime, float]]) -> tuple[list[datetime], list[float], bool]:
    ordered = sorted(((timestamp, float(value)) for timestamp, value in rows if value is not None), key=lambda row: row[0])
    if ordered:
        return [row[0] for row in ordered], [row[1] for row in ordered], False
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    timestamps = [base + timedelta(hours=index) for index in range(len(FALLBACK_SERIES))]
    return timestamps, list(FALLBACK_SERIES), True


def build_forecast(
    rows: Iterable[tuple[datetime, float]],
    horizon: int = 24,
    model_name: str = "weighted_ensemble",
) -> ForecastResult:
    if horizon < 1 or horizon > 168:
        raise ValueError("horizon must be between 1 and 168")
    model = _normalise_model(model_name)
    timestamps, values, used_fallback = _observations(rows)
    cadence = _cadence(timestamps)
    backtest_predictions = [_prediction(values[:index], model) for index in range(1, len(values))]
    metrics = _metrics(values[1:], backtest_predictions, timestamps[1:])
    working_values = list(values)
    last_timestamp = timestamps[-1]
    confidence = max(0.0, min(1.0, 0.95 - (metrics["mape"] / 100) - (0.15 / max(1, len(values)))))
    points: list[ForecastPoint] = []
    for step in range(1, horizon + 1):
        value = _prediction(working_values, model)
        points.append(ForecastPoint(last_timestamp + cadence * step, value, round(confidence, 3)))
        working_values.append(value)
    return ForecastResult(
        model_name=model,
        points=tuple(points),
        metrics=metrics,
        data_points=len(values),
        last_observed_at=last_timestamp,
        last_observed_demand_mw=values[-1],
        used_fallback=used_fallback,
        cadence_minutes=max(1, int(cadence.total_seconds() / 60)),
    )


# Weight each model puts on (recent average - latest) and (trend - latest) for the next step.
# weighted_ensemble = 0.2*last + 0.3*avg + 0.2*trend + 0.3*(last+avg)/2 = last + 0.45*(avg-last) + 0.2*(trend-last)
_STEP_WEIGHTS = {
    "persistence": (0.0, 0.0),
    "moving_average": (1.0, 0.0),
    "trend": (0.0, 1.0),
    "weighted_ensemble": (0.45, 0.20),
}


def explain_next_step(rows: Iterable[tuple[datetime, float]], model_name: str = "weighted_ensemble") -> dict[str, float]:
    """Split the next-step forecast into MW contributions on top of the latest observed load."""
    model = _normalise_model(model_name)
    _, values, _ = _observations(rows)
    last = values[-1]
    average = mean(values[-DEFAULT_WINDOW:])
    trend = max(0.0, last + _slope(values[-max(DEFAULT_WINDOW, 2):]))
    average_weight, trend_weight = _STEP_WEIGHTS[model]
    return {
        "recent_average": round(average_weight * (average - last), 3) + 0.0,  # + 0.0 turns -0.0 into 0.0
        "trend": round(trend_weight * (trend - last), 3) + 0.0,
    }


def compare_models(rows: Iterable[tuple[datetime, float]]) -> list[dict[str, object]]:
    materialized = list(rows)
    results = []
    for model in MODEL_NAMES:
        result = build_forecast(materialized, horizon=1, model_name=model)
        results.append({"model_name": model, "metrics": result.metrics, "forecast_demand_mw": result.points[0].demand_mw})
    return results
