import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import (
    Alert,
    AuditLog,
    CalendarContext,
    DatasetImport,
    Experiment,
    ExplainabilityResult,
    HistoricalLoad,
    ModelRun,
    SldcDailySummary,
    SldcLoad,
    SldcRealtimeReading,
    SldcSnapshot,
    Prediction,
    Recommendation,
    Report,
    ResearchResult,
    SystemSetting,
    TrainingLog,
    User,
    UserPreference,
    WeatherHourly,
)
from ...services import alert_engine, analytics, app_settings, calendar_collector, collection, delhi_load, demand_model, grid_live, recommender, sldc_collector, sldc_realtime_collector, weather_collector
from ...services.forecasting import ForecastPoint, ForecastResult, build_forecast, compare_models, explain_next_step
from ...schemas import (
    AdminUserResponse,
    AuditLogResponse,
    DatasetImportResponse,
    DatasetPreviewResponse,
    DatasetQualityResponse,
    DatasetRecord,
    DatasetRecordResponse,
    ForecastHorizonResponse,
    ForecastPointResponse,
    ExperimentCreate,
    ExperimentResponse,
    ExplainabilityResponse,
    ExplanationRequest,
    ForecastSummaryResponse,
    PeakPredictionResponse,
    AlertResponse,
    ModelComparisonItem,
    ModelComparisonResponse,
    ModelRunResponse,
    ModelTrainingRequest,
    RecommendationCreate,
    RecommendationResponse,
    RecommendationStatusUpdate,
    ReportGenerateRequest,
    ReportResponse,
    ResearchResultResponse,
    SystemSettingResponse,
    SystemSettingUpdate,
    OperationalSettingUpdate,
    PasswordChange,
    UserPreferencesUpdate,
    TrainingLogResponse,
    UserSummary,
)
from ...main import require_active_user, require_roles

router = APIRouter(prefix="/api/v1", tags=["v1"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
TIMESTAMP_COLUMNS = ("timestamp", "recorded_at", "datetime")
DEMAND_COLUMNS = ("demand_mw", "load_mw", "demand")


def _audit(db: Session, user: User, action: str, resource_type: str, resource_id: str | None = None, details: dict[str, object] | None = None) -> None:
    db.add(AuditLog(actor_id=user.id, action=action, resource_type=resource_type, resource_id=resource_id, details_json=json.dumps(details or {}, sort_keys=True, default=str)))


def _json_object(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _explanation_response(item: ExplainabilityResult, prediction: Prediction) -> ExplainabilityResponse:
    return ExplainabilityResponse(id=item.id, prediction_id=item.prediction_id, model_name=item.model_name, forecast_for=prediction.forecast_for, predicted_demand_mw=float(prediction.demand_mw or 0), feature_contributions=_json_object(item.feature_contributions_json), explanation=item.explanation, created_by=item.created_by, created_at=item.created_at)


def _recommendation_response(item: Recommendation) -> RecommendationResponse:
    return RecommendationResponse(id=item.id, category=item.category, priority=item.priority, action=item.action, expected_reduction_mw=item.expected_reduction_mw, expected_savings=item.expected_savings, time_window=item.time_window, confidence=item.confidence, reason=item.reason[:2000], status=item.status, created_by=item.created_by, created_at=item.created_at, updated_at=item.updated_at, source_key=item.source_key, valid_until=item.valid_until, generated=item.source_key is not None)


def _parse_csv(content: bytes, file_name: str) -> tuple[list[str], list[dict[str, str]], list[dict[str, object]], list[str]]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="CSV files must be 10 MB or smaller")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV files must use UTF-8 encoding") from exc

    reader = csv.DictReader(StringIO(text))
    columns = [column.strip() for column in (reader.fieldnames or []) if column]
    timestamp_column = next((column for column in TIMESTAMP_COLUMNS if column in columns), None)
    demand_column = next((column for column in DEMAND_COLUMNS if column in columns), None)
    if timestamp_column is None or demand_column is None:
        missing = []
        if timestamp_column is None:
            missing.append("timestamp or recorded_at")
        if demand_column is None:
            missing.append("demand_mw or load_mw")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Missing required columns: {', '.join(missing)}")

    rows: list[dict[str, str]] = []
    valid_rows: list[dict[str, object]] = []
    errors: list[str] = []
    for row_number, row in enumerate(reader, start=2):
        clean_row = {str(key).strip(): (value or "").strip() for key, value in row.items() if key}
        rows.append(clean_row)
        try:
            recorded_at = datetime.fromisoformat(clean_row[timestamp_column].replace("Z", "+00:00"))
            demand_mw = float(clean_row[demand_column])
            if demand_mw <= 0:
                raise ValueError("demand must be greater than zero")
            valid_rows.append({"recorded_at": recorded_at, "demand_mw": demand_mw, "source": file_name})
        except (KeyError, TypeError, ValueError):
            if len(errors) < 20:
                errors.append(f"Row {row_number}: timestamp must be ISO formatted and demand must be greater than zero")

    return columns, rows, valid_rows, errors


@router.get("/users/me", response_model=UserSummary, tags=["users"])
def current_user(user: User = Depends(require_active_user)) -> UserSummary:
    return UserSummary(id=user.id, email=user.email, display_name=user.display_name, role=user.role.name)


@router.get("/datasets/quality", response_model=DatasetQualityResponse, tags=["datasets"])
def dataset_quality(
    db: Session = Depends(get_db),
    user: User = Depends(require_active_user),
) -> DatasetQualityResponse:
    total_records = db.scalar(select(func.count(HistoricalLoad.id))) or 0
    earliest_record = db.scalar(select(func.min(HistoricalLoad.recorded_at)))
    latest_record = db.scalar(select(func.max(HistoricalLoad.recorded_at)))
    missing_demand_records = db.scalar(
        select(func.count(HistoricalLoad.id)).where(HistoricalLoad.demand_mw.is_(None))
    ) or 0
    return DatasetQualityResponse(
        total_records=total_records,
        earliest_record=earliest_record,
        latest_record=latest_record,
        missing_demand_records=missing_demand_records,
    )


@router.get("/datasets/records", response_model=list[DatasetRecordResponse], tags=["datasets"])
def list_dataset_records(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_active_user),
) -> list[DatasetRecordResponse]:
    statement = select(HistoricalLoad).order_by(HistoricalLoad.recorded_at.desc()).limit(limit)
    if start is not None:
        statement = statement.where(HistoricalLoad.recorded_at >= start)
    if end is not None:
        statement = statement.where(HistoricalLoad.recorded_at <= end)
    return [DatasetRecordResponse.model_validate(record) for record in db.scalars(statement).all()]


@router.post("/datasets/records", response_model=DatasetRecordResponse, status_code=201, tags=["datasets"])
def create_dataset_record(
    record: DatasetRecord,
    db: Session = Depends(get_db),
    user: User = Depends(require_active_user),
) -> DatasetRecordResponse:
    data = HistoricalLoad(**record.model_dump())
    db.add(data)
    db.commit()
    db.refresh(data)
    return DatasetRecordResponse.model_validate(data)


@router.post("/datasets/imports/preview", response_model=DatasetPreviewResponse, tags=["datasets"])
async def preview_dataset_import(
    file: UploadFile = File(...),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> DatasetPreviewResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a CSV file")
    content = await file.read()
    columns, rows, valid_rows, errors = _parse_csv(content, file.filename)
    return DatasetPreviewResponse(
        file_name=file.filename,
        columns=columns,
        sample_rows=rows[:5],
        row_count=len(rows),
        valid_rows=len(valid_rows),
        invalid_rows=len(rows) - len(valid_rows),
        errors=errors,
    )


@router.get("/datasets/imports", response_model=list[DatasetImportResponse], tags=["datasets"])
def list_dataset_imports(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> list[DatasetImportResponse]:
    statement = select(DatasetImport).order_by(DatasetImport.uploaded_at.desc()).limit(limit)
    return [DatasetImportResponse.model_validate(item) for item in db.scalars(statement).all()]


@router.post("/datasets/imports", response_model=DatasetImportResponse, status_code=201, tags=["datasets"])
async def import_dataset(
    file: UploadFile = File(...),
    dataset_name: str = Form(default="Historical load"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> DatasetImportResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a CSV file")
    content = await file.read()
    _, rows, valid_rows, errors = _parse_csv(content, file.filename)
    invalid_rows = len(rows) - len(valid_rows)
    if invalid_rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Dataset rejected because it contains invalid rows", "invalid_rows": invalid_rows, "errors": errors},
        )
    if not valid_rows:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Dataset must contain at least one data row")

    latest_version = db.scalar(select(func.max(DatasetImport.version)).where(DatasetImport.dataset_name == dataset_name)) or 0
    dataset_import = DatasetImport(
        dataset_name=dataset_name.strip() or "Historical load",
        version=latest_version + 1,
        file_name=file.filename,
        status="validated",
        row_count=len(rows),
        valid_rows=len(valid_rows),
        invalid_rows=0,
        uploaded_by=user.id,
    )
    db.add(dataset_import)
    db.flush()
    db.add_all([HistoricalLoad(import_id=dataset_import.id, **row) for row in valid_rows])
    _audit(db, user, "dataset_import", "dataset_import", str(dataset_import.id), {"file_name": file.filename, "row_count": len(valid_rows)})
    db.commit()
    db.refresh(dataset_import)
    return DatasetImportResponse.model_validate(dataset_import)


AI_MODEL = "gridsense_ai"
HISTORY_DAYS = 14


def _history(db: Session) -> tuple[list[tuple[datetime, float]], str]:
    """The series the baseline models run on: real SLDC hourly load when collected, else uploaded CSV rows, else none
    (build_forecast then uses its built-in sample series)."""
    rows = demand_model.recent_hourly_load(db, HISTORY_DAYS)
    if rows:
        return rows, "sldc"
    records = db.scalars(
        select(HistoricalLoad)
        .where(HistoricalLoad.demand_mw.is_not(None))
        .order_by(HistoricalLoad.recorded_at.asc())
    ).all()
    imported = [(record.recorded_at, float(record.demand_mw)) for record in records]
    return imported, "imported" if imported else "sample"


def _history_rows(db: Session) -> list[tuple[datetime, float]]:
    return _history(db)[0]


def _ai_metrics(metrics: dict) -> dict[str, float]:
    """The demand model's held-out-year scores, under the metric names the baseline models use."""
    return {
        "mae": metrics["mae_mw"], "rmse": metrics.get("rmse_mw", 0.0), "mape": metrics["mape_pct"], "r2": metrics.get("r2", 0.0),
        "peak_timing_error_hours": metrics.get("peak_timing_error_hours", 0.0), "peak_timing_error": metrics.get("peak_timing_error_hours", 0.0),
        "peak_magnitude_error_mw": metrics["daily_peak_mae_mw"], "peak_magnitude_error": metrics["daily_peak_mae_mw"],
    }


def _ai_forecast(db: Session, horizon: int) -> tuple[ForecastResult, str]:
    rows, source = _history(db)
    outlook = demand_model.forecast(db)
    last_at, last_mw = rows[-1] if rows else (None, None)
    upcoming = [hour for hour in outlook["hours"] if last_at is None or hour["at"] > last_at][:horizon]
    if not upcoming or last_at is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No upcoming hours have a weather forecast yet.")
    points = tuple(
        # confidence = 1 - the model's relative spread for that hour (the 80% band is ±1.28 sigma)
        ForecastPoint(hour["at"], hour["predicted_mw"], round(max(0.0, min(1.0, 1 - (hour["high_band_mw"] - hour["predicted_mw"]) / 1.2816 / hour["predicted_mw"])), 3))
        for hour in upcoming
    )
    metrics = outlook["metrics"]
    result = ForecastResult(
        model_name=AI_MODEL, points=points, metrics=_ai_metrics(metrics), data_points=int(metrics["train_hours"] + metrics["test_hours"]),
        last_observed_at=last_at, last_observed_demand_mw=last_mw, used_fallback=False, cadence_minutes=60,
    )
    return result, source


def _forecast_with_source(db: Session, horizon: int, model_name: str) -> tuple[ForecastResult, str]:
    if model_name.strip().lower() == AI_MODEL:
        try:
            return _ai_forecast(db, horizon)
        except demand_model.ModelNotReady:
            model_name = "weighted_ensemble"  # untrained deployment: keep the pages working on the baseline
    rows, source = _history(db)
    try:
        return build_forecast(rows, horizon=horizon, model_name=model_name), source
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _forecast_or_422(db: Session, horizon: int, model_name: str) -> ForecastResult:
    return _forecast_with_source(db, horizon, model_name)[0]


def _model_comparison(db: Session) -> tuple[list[dict], int, str]:
    rows, source = _history(db)
    span = f"last {len(rows)} hours ({rows[0][0]:%d %b} – {rows[-1][0]:%d %b %Y})" if rows else "built-in sample series"
    models = [{**item, "horizon": "1 hour ahead", "evaluated_on": span} for item in compare_models(rows)]
    try:
        result, _ = _ai_forecast(db, 1)
        test_period = demand_model.model_info()["metrics"]["test_period"]
        models.insert(0, {"model_name": AI_MODEL, "metrics": result.metrics, "forecast_demand_mw": result.points[0].demand_mw,
                          "horizon": "up to 7 days ahead", "evaluated_on": f"held-out year {test_period}"})
    except (demand_model.ModelNotReady, HTTPException):
        pass
    data_points = len(rows) if rows else build_forecast(rows, horizon=1).data_points
    return models, data_points, source


def _forecast_points(result) -> list[ForecastPointResponse]:
    return [
        ForecastPointResponse(
            forecast_for=point.forecast_for,
            demand_mw=point.demand_mw,
            confidence=point.confidence,
        )
        for point in result.points
    ]


@router.get("/forecast/summary", response_model=ForecastSummaryResponse, tags=["forecast"])
def forecast_summary(
    model_name: str = Query(default=AI_MODEL),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator")),
) -> ForecastSummaryResponse:
    result, source = _forecast_with_source(db, horizon=1, model_name=model_name)
    return ForecastSummaryResponse(
        data_source=source,
        model_name=result.model_name,
        next_forecast=_forecast_points(result)[0],
        metrics=result.metrics,
        data_points=result.data_points,
        last_observed_at=result.last_observed_at,
        last_observed_demand_mw=result.last_observed_demand_mw,
        used_fallback=result.used_fallback,
    )


@router.get("/forecast/horizon", response_model=ForecastHorizonResponse, tags=["forecast"])
def forecast_horizon(
    horizon: int = Query(default=24, ge=1, le=168),
    model_name: str = Query(default=AI_MODEL),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator")),
) -> ForecastHorizonResponse:
    result, source = _forecast_with_source(db, horizon=horizon, model_name=model_name)
    return ForecastHorizonResponse(
        data_source=source,
        model_name=result.model_name,
        horizon=horizon,
        forecasts=_forecast_points(result),
        metrics=result.metrics,
        data_points=result.data_points,
        last_observed_at=result.last_observed_at,
        last_observed_demand_mw=result.last_observed_demand_mw,
        used_fallback=result.used_fallback,
        cadence_minutes=result.cadence_minutes,
    )


@router.get("/peak-prediction", response_model=PeakPredictionResponse, tags=["forecast"])
def peak_prediction(
    horizon: int = Query(default=24, ge=1, le=168),
    model_name: str = Query(default=AI_MODEL),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator")),
) -> PeakPredictionResponse:
    result, source = _forecast_with_source(db, horizon=horizon, model_name=model_name)
    peak = max(result.points, key=lambda point: point.demand_mw)
    risk_threshold = result.last_observed_demand_mw * 1.05
    # On real SLDC load the daily cycle alone is far more than 5%, so alerts come from the demand model's hourly job
    # (services/demand_model.scheduled_job) instead; this simple rule stays for uploaded datasets.
    if source == "imported" and peak.demand_mw >= risk_threshold:
        alert_title = f"Peak demand risk for {peak.forecast_for.date().isoformat()}"
        open_alert = db.scalar(select(Alert).where(Alert.title == alert_title, Alert.acknowledged.is_(False)))
        if open_alert is None:
            severity = "critical" if peak.demand_mw >= result.last_observed_demand_mw * 1.15 else "high"
            db.add(Alert(
                title=alert_title,
                severity=severity,
                message=f"Forecast demand reaches {peak.demand_mw:.1f} MW at {peak.forecast_for.isoformat()} with {peak.confidence:.0%} confidence.",
            ))
            db.commit()
    return PeakPredictionResponse(
        data_source=source,
        model_name=result.model_name,
        peak_for=peak.forecast_for,
        peak_demand_mw=peak.demand_mw,
        horizon=horizon,
        confidence=peak.confidence,
        metrics=result.metrics,
        used_fallback=result.used_fallback,
    )


ALERT_ROLES = ("super_admin", "grid_operator")
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@router.get("/alerts", response_model=list[AlertResponse], tags=["alerts"])
def list_alerts(
    acknowledged: bool | None = Query(default=None),
    state: Literal["active", "resolved", "all"] = "all",
    category: str | None = None,
    severity: Literal["low", "medium", "high", "critical"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ALERT_ROLES)),
) -> list[AlertResponse]:
    statement = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if acknowledged is not None:
        statement = statement.where(Alert.acknowledged == acknowledged)
    if state == "active":
        statement = statement.where(Alert.resolved_at.is_(None))
    elif state == "resolved":
        statement = statement.where(Alert.resolved_at.is_not(None))
    if category:
        statement = statement.where(Alert.category == category)
    if severity:
        statement = statement.where(Alert.severity == severity)
    items = [AlertResponse.model_validate(alert) for alert in db.scalars(statement).all()]
    return sorted(items, key=lambda item: (item.resolved_at is not None, item.acknowledged, SEVERITY_ORDER.get(item.severity, 9), -item.created_at.timestamp()))


@router.get("/alerts/summary", tags=["alerts"])
def alerts_summary(db: Session = Depends(get_db), user: User = Depends(require_roles(*ALERT_ROLES))) -> dict:
    """Counts for the header badge and the Alerts page, respecting the user's alert preferences."""
    preferences = _preferences(db, user)
    minimum = SEVERITY_ORDER[preferences["alert_min_severity"]]
    categories = set(preferences["alert_categories"]) | ({"demand"} if "demand_forecast" in preferences["alert_categories"] else set())
    active = db.scalars(select(Alert).where(Alert.resolved_at.is_(None))).all()
    unacknowledged = [alert for alert in active if not alert.acknowledged]
    by_category: dict[str, int] = {}
    by_severity = {name: 0 for name in SEVERITY_ORDER}
    for alert in unacknowledged:
        by_category[alert.category] = by_category.get(alert.category, 0) + 1
        by_severity[alert.severity] = by_severity.get(alert.severity, 0) + 1
    for_me = [alert for alert in unacknowledged if SEVERITY_ORDER.get(alert.severity, 9) <= minimum and alert.category in categories]
    return {
        "active": len(active), "unacknowledged": len(unacknowledged), "for_me": len(for_me),
        "critical": by_severity["critical"], "by_severity": by_severity, "by_category": by_category,
        "resolved_last_24h": db.scalar(select(func.count()).select_from(Alert).where(Alert.resolved_at >= datetime.now(timezone.utc) - timedelta(hours=24))) or 0,
    }


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertResponse, tags=["alerts"])
def acknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ALERT_ROLES)),
) -> AlertResponse:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    alert.acknowledged = True
    alert.acknowledged_by = user.display_name
    _audit(db, user, "alert_acknowledged", "alert", str(alert.id), {"category": alert.category, "severity": alert.severity})
    db.commit()
    db.refresh(alert)
    return AlertResponse.model_validate(alert)


@router.post("/alerts/acknowledge-all", tags=["alerts"])
def acknowledge_all_alerts(
    category: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ALERT_ROLES)),
) -> dict:
    statement = select(Alert).where(Alert.acknowledged.is_(False))
    if category:
        statement = statement.where(Alert.category == category)
    items = db.scalars(statement).all()
    for alert in items:
        alert.acknowledged = True
        alert.acknowledged_by = user.display_name
    _audit(db, user, "alerts_acknowledged", "alert", None, {"count": len(items), "category": category})
    db.commit()
    return {"acknowledged": len(items)}


@router.post("/alerts/evaluate", tags=["alerts"])
def evaluate_alerts(db: Session = Depends(get_db), user: User = Depends(require_roles(*ALERT_ROLES))) -> dict:
    """Run every alert check now instead of waiting for the next scheduled run (every 5 minutes)."""
    return alert_engine.run(db)


def _model_run_response(run: ModelRun) -> ModelRunResponse:
    try:
        metrics = json.loads(run.metrics_json)
    except (TypeError, json.JSONDecodeError):
        metrics = {}
    return ModelRunResponse(
        id=run.id,
        model_name=run.model_name,
        algorithm=run.algorithm,
        status=run.status,
        metrics=metrics,
        data_points=run.data_points,
        trained_at=run.trained_at,
        created_by=run.created_by,
    )


@router.post("/models/training", response_model=ModelRunResponse, status_code=201, tags=["models"])
@router.post("/models/train", response_model=ModelRunResponse, status_code=201, tags=["models"])
def train_model(
    request: ModelTrainingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> ModelRunResponse:
    if request.model_name.strip().lower() == AI_MODEL:
        try:
            trained = demand_model.train(db)
        except demand_model.ModelNotReady as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        model_name, algorithm = AI_MODEL, "HistGradientBoostingRegressor"
        metrics = _ai_metrics(trained["metrics"])
        data_points = int(trained["metrics"]["train_hours"] + trained["metrics"]["test_hours"])
        messages = [
            f"Trained on {trained['metrics']['train_period']}, tested on the held-out year {trained['metrics']['test_period']}",
            f"Hourly error ±{trained['metrics']['mae_mw']} MW ({trained['metrics']['mape_pct']}%), daily peak ±{trained['metrics']['daily_peak_mae_mw']} MW",
            f"High days flagged in advance: {trained['metrics']['high_days_flagged_watch_or_above']} of {trained['metrics']['high_days_in_test']}",
            f"Production model refit on all data up to {trained['data_until']}",
        ]
    else:
        rows, source = _history(db)
        try:
            result = build_forecast(rows, horizon=1, model_name=request.model_name)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        model_name = algorithm = result.model_name
        metrics, data_points = result.metrics, result.data_points
        messages = [f"Deterministic {result.model_name} training completed on {result.data_points} {'SLDC hourly' if source == 'sldc' else source} observations"]
    run = ModelRun(
        model_name=model_name,
        algorithm=algorithm,
        status="completed",
        metrics_json=json.dumps(metrics, sort_keys=True),
        data_points=data_points,
        created_by=user.id,
    )
    db.add(run)
    db.flush()
    for message in messages:
        db.add(TrainingLog(model_run_id=run.id, level="info", message=message))
    _audit(db, user, "model_training", "model_run", str(run.id), {"model_name": model_name, "data_points": data_points})
    db.commit()
    db.refresh(run)
    return _model_run_response(run)


@router.get("/models/runs", response_model=list[ModelRunResponse], tags=["models"])
def list_model_runs(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> list[ModelRunResponse]:
    statement = select(ModelRun).order_by(ModelRun.trained_at.desc()).limit(limit)
    return [_model_run_response(run) for run in db.scalars(statement).all()]


@router.get("/models/runs/{run_id}/logs", response_model=list[TrainingLogResponse], tags=["models"])
def list_training_logs(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> list[TrainingLogResponse]:
    if db.get(ModelRun, run_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model run not found")
    statement = select(TrainingLog).where(TrainingLog.model_run_id == run_id).order_by(TrainingLog.created_at.asc())
    return [TrainingLogResponse.model_validate(log) for log in db.scalars(statement).all()]


@router.get("/models/comparison", response_model=ModelComparisonResponse, tags=["models"])
@router.get("/models/compare", response_model=ModelComparisonResponse, tags=["models"])
def compare_model_metadata(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> ModelComparisonResponse:
    models, data_points, source = _model_comparison(db)
    return ModelComparisonResponse(
        models=[ModelComparisonItem.model_validate(item) for item in models],
        data_points=data_points,
        used_fallback=source == "sample",
        data_source=source,
    )
# Phase 9: deterministic forecast explanations
@router.post("/explanations/forecast", response_model=ExplainabilityResponse, status_code=201, tags=["explainability"])
@router.post("/explanations", response_model=ExplainabilityResponse, status_code=201, tags=["explainability"])
def create_forecast_explanation(
    request: ExplanationRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator", "research_analyst")),
) -> ExplainabilityResponse:
    result, data_source = _forecast_with_source(db, horizon=request.horizon, model_name=request.model_name)
    point = result.points[-1] if request.horizon > 1 else result.points[0]
    why = demand_model.explain_hour(db, point.forecast_for) if result.model_name == AI_MODEL else None
    if why is not None:
        # Reasons are measured against a normal hour (typical weather, ordinary weekday, last year's level), so they sum
        # to the forecast minus that normal value.
        contributions = {driver["label"]: driver["effect_mw"] for driver in why["drivers"]}
        explanation = demand_model.reason_sentence(
            why, f"The GridSense AI model expects {point.demand_mw:,.0f} MW at {point.forecast_for:%d %b %H}:00 IST against {why['normal_mw']:,.0f} MW for a normal hour,",
        ) + f" Based on {result.data_points:,} hours of real Delhi SLDC load; confidence is {point.confidence:.0%}."
    else:
        contributions = explain_next_step(_history_rows(db), result.model_name)
        change = point.demand_mw - result.last_observed_demand_mw
        movement = f"{'rise' if change > 0 else 'fall'} of {abs(change):.1f} MW" if change else "no change"
        drivers = sorted(contributions.items(), key=lambda item: -abs(item[1]))
        driver_text = (
            "; ".join(f"{name.replace('_', ' ')} {value:+.1f} MW" for name, value in drivers if value)
            or "no recent movement, so it repeats the latest value"
        )
        source = {"sldc": f"{result.data_points} hourly Delhi SLDC load readings", "imported": f"{result.data_points} imported load readings"}.get(
            data_source, "the built-in sample series because no load history has been collected yet")
        explanation = (
            f"The {result.model_name.replace('_', ' ')} model expects {point.demand_mw:.1f} MW at "
            f"{point.forecast_for.isoformat()}, a {movement} from the latest observed "
            f"{result.last_observed_demand_mw:.1f} MW. Drivers: {driver_text}. "
            f"Based on {source}; confidence is {point.confidence:.0%}."
        )
    prediction = Prediction(
        forecast_for=point.forecast_for,
        demand_mw=point.demand_mw,
        confidence=point.confidence,
        model_name=result.model_name,
        created_by=user.id,
    )
    db.add(prediction)
    db.flush()
    item = ExplainabilityResult(
        prediction_id=prediction.id,
        model_name=result.model_name,
        feature_contributions_json=json.dumps(contributions, sort_keys=True),
        explanation=explanation,
        created_by=user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    db.refresh(prediction)
    return _explanation_response(item, prediction)


@router.get("/explanations", response_model=list[ExplainabilityResponse], tags=["explainability"])
@router.get("/explanations/history", response_model=list[ExplainabilityResponse], tags=["explainability"])
def list_explanations(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator", "research_analyst")),
) -> list[ExplainabilityResponse]:
    rows = db.execute(
        select(ExplainabilityResult, Prediction)
        .join(Prediction, Prediction.id == ExplainabilityResult.prediction_id)
        .order_by(ExplainabilityResult.created_at.desc())
        .limit(limit)
    ).all()
    return [_explanation_response(item, prediction) for item, prediction in rows]


# Phase 10: operational recommendations
@router.get("/recommendations", response_model=list[RecommendationResponse], tags=["recommendations"])
def list_recommendations(
    status_filter: Literal["open", "accepted", "rejected", "completed", "expired"] | None = Query(default=None, alias="status"),
    source: Literal["generated", "manual"] | None = None,
    category: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator")),
) -> list[RecommendationResponse]:
    statement = select(Recommendation).order_by(Recommendation.created_at.desc()).limit(limit)
    if status_filter is not None:
        statement = statement.where(Recommendation.status == status_filter)
    if source == "generated":
        statement = statement.where(Recommendation.source_key.is_not(None))
    elif source == "manual":
        statement = statement.where(Recommendation.source_key.is_(None))
    if category:
        statement = statement.where(Recommendation.category == category)
    priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items = [_recommendation_response(item) for item in db.scalars(statement).all()]
    return sorted(items, key=lambda item: (item.status != "open", priority.get(item.priority, 9), item.valid_until or item.created_at))


@router.get("/recommendations/summary", tags=["recommendations"])
def recommendations_summary(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "grid_operator"))) -> dict:
    items = db.scalars(select(Recommendation)).all()
    open_items = [item for item in items if item.status == "open"]
    accepted = [item for item in items if item.status in ("accepted", "completed")]
    return {
        "open": len(open_items), "accepted": len([item for item in items if item.status == "accepted"]), "completed": len([item for item in items if item.status == "completed"]),
        "rejected": len([item for item in items if item.status == "rejected"]), "expired": len([item for item in items if item.status == "expired"]),
        "open_reduction_mw": round(sum(item.expected_reduction_mw or 0 for item in open_items if item.category == "peak_management"), 0),
        "open_savings_inr": round(sum(item.expected_savings or 0 for item in open_items), 0),
        "accepted_savings_inr": round(sum(item.expected_savings or 0 for item in accepted), 0),
        "by_category": {category: len([item for item in open_items if item.category == category]) for category in sorted({item.category for item in open_items})},
        "settings": {key: value for key, value in app_settings.values(db).items() if key.startswith("recommendations.")},
    }


@router.post("/recommendations/generate", tags=["recommendations"])
def generate_recommendations(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "grid_operator"))) -> dict:
    """Rebuild forecast- and grid-based recommendations now (also runs hourly)."""
    result = recommender.generate(db)
    _audit(db, user, "recommendations_generated", "recommendation", None, result)
    db.commit()
    return result


@router.post("/recommendations", response_model=RecommendationResponse, status_code=201, tags=["recommendations"])
def create_recommendation(
    request: RecommendationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator")),
) -> RecommendationResponse:
    item = Recommendation(created_by=user.id, **request.model_dump())
    db.add(item)
    db.flush()
    _audit(db, user, "recommendation_created", "recommendation", str(item.id), {"status": item.status})
    db.commit()
    db.refresh(item)
    return _recommendation_response(item)


@router.patch("/recommendations/{recommendation_id}/status", response_model=RecommendationResponse, tags=["recommendations"])
@router.put("/recommendations/{recommendation_id}/status", response_model=RecommendationResponse, tags=["recommendations"])
def update_recommendation_status(
    recommendation_id: int,
    request: RecommendationStatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator")),
) -> RecommendationResponse:
    item = db.get(Recommendation, recommendation_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")
    item.status = request.status
    item.updated_at = datetime.now(timezone.utc)
    _audit(db, user, "recommendation_updated", "recommendation", str(item.id), {"status": item.status})
    db.commit()
    db.refresh(item)
    return _recommendation_response(item)


# Phase 11: persisted reports and safe exports
@router.get("/reports", response_model=list[ReportResponse], tags=["reports"])
def list_reports(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator", "research_analyst")),
) -> list[ReportResponse]:
    statement = select(Report).order_by(Report.created_at.desc()).limit(limit)
    return [ReportResponse.model_validate(item) for item in db.scalars(statement).all()]


def _report_payload(request: ReportGenerateRequest, db: Session, user: User) -> object:
    if request.type == "forecast":
        result = _forecast_or_422(db, request.horizon, request.model_name)
        return {
            "model_name": result.model_name,
            "data_source": _history(db)[1],
            "metrics": result.metrics,
            "data_points": result.data_points,
            "forecasts": [{"forecast_for": point.forecast_for.isoformat(), "demand_mw": point.demand_mw, "confidence": point.confidence} for point in result.points],
        }
    if request.type == "recommendations":
        return [{"id": item.id, "category": item.category, "priority": item.priority, "action": item.action, "status": item.status, "confidence": item.confidence} for item in db.scalars(select(Recommendation).order_by(Recommendation.created_at.desc()).limit(500)).all()]
    if request.type == "model_comparison":
        models, data_points, source = _model_comparison(db)
        return {"models": models, "data_points": data_points, "data_source": source}
    if user.role.name not in ("super_admin", "research_analyst"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Audit reports require an admin or research role")
    return [{"action": item.action, "resource_type": item.resource_type, "resource_id": item.resource_id, "created_at": item.created_at.isoformat()} for item in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)).all()]


def _format_report(payload: object, report_format: str) -> str:
    if report_format == "json":
        return json.dumps(payload, sort_keys=True, indent=2, default=str)
    if not isinstance(payload, list):
        payload = [payload]
    rows = payload if payload else [{}]
    columns = sorted({key for row in rows if isinstance(row, dict) for key in row})
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=columns or ["value"])
    writer.writeheader()
    for row in rows:
        writer.writerow(row if isinstance(row, dict) else {"value": row})
    return output.getvalue()


@router.post("/reports/generate", response_model=ReportResponse, status_code=201, tags=["reports"])
def generate_report(
    request: ReportGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> ReportResponse:
    payload = _report_payload(request, db, user)
    report = Report(type=request.type, format=request.format, status="completed", generated_by=user.id, content=_format_report(payload, request.format))
    db.add(report)
    db.flush()
    _audit(db, user, "report_generated", "report", str(report.id), {"type": report.type, "format": report.format})
    db.commit()
    db.refresh(report)
    return ReportResponse.model_validate(report)


@router.get("/reports/{report_id}/export", tags=["reports"])
def export_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "grid_operator", "research_analyst")),
) -> Response:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    media_type = "application/json" if report.format == "json" else "text/csv"
    extension = "json" if report.format == "json" else "csv"
    return Response(content=report.content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="gridsense-report-{report.id}.{extension}"'})


# Phase 12: reproducible deterministic research and administration

def _research_result_response(item: ResearchResult) -> ResearchResultResponse:
    return ResearchResultResponse(id=item.id, experiment_id=item.experiment_id, model_name=item.model_name, metrics=_json_object(item.metrics_json), reproducibility=_json_object(item.reproducibility_json), created_at=item.created_at)


def _experiment_response(item: Experiment, results: list[ResearchResult]) -> ExperimentResponse:
    return ExperimentResponse(id=item.id, name=item.name, dataset_version=item.dataset_version, parameters=_json_object(item.parameters_json), status=item.status, created_by=item.created_by, created_at=item.created_at, results=[_research_result_response(result) for result in results])


@router.post("/research/experiments", response_model=ExperimentResponse, status_code=201, tags=["research"])
def create_experiment(
    request: ExperimentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> ExperimentResponse:
    item = Experiment(name=request.name, dataset_version=request.dataset_version, parameters_json=json.dumps(request.parameters, sort_keys=True), created_by=user.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _experiment_response(item, [])


@router.get("/research/experiments", response_model=list[ExperimentResponse], tags=["research"])
def list_experiments(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> list[ExperimentResponse]:
    experiments = db.scalars(select(Experiment).order_by(Experiment.created_at.desc()).limit(limit)).all()
    return [_experiment_response(item, db.scalars(select(ResearchResult).where(ResearchResult.experiment_id == item.id).order_by(ResearchResult.created_at.asc())).all()) for item in experiments]


@router.post("/research/experiments/{experiment_id}/run", response_model=ExperimentResponse, tags=["research"])
def run_experiment(
    experiment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> ExperimentResponse:
    item = db.get(Experiment, experiment_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found")
    rows, source = _history(db)
    fingerprint = hashlib.sha256(json.dumps([(timestamp.isoformat(), value) for timestamp, value in rows], sort_keys=True).encode()).hexdigest()
    for comparison in compare_models(rows):
        db.add(ResearchResult(experiment_id=item.id, model_name=comparison["model_name"], metrics_json=json.dumps(comparison["metrics"], sort_keys=True), reproducibility_json=json.dumps({"data_fingerprint": fingerprint, "algorithm": "pure_python_forecasting", "data_points": len(rows), "data_source": source}, sort_keys=True)))
    item.status = "completed"
    _audit(db, user, "research_experiment_run", "experiment", str(item.id), {"data_fingerprint": fingerprint, "data_points": len(rows)})
    db.commit()
    db.refresh(item)
    results = db.scalars(select(ResearchResult).where(ResearchResult.experiment_id == item.id).order_by(ResearchResult.created_at.asc())).all()
    return _experiment_response(item, results)


@router.get("/research/experiments/{experiment_id}/results", response_model=list[ResearchResultResponse], tags=["research"])
def list_research_results(
    experiment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "research_analyst")),
) -> list[ResearchResultResponse]:
    if db.get(Experiment, experiment_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found")
    return [_research_result_response(item) for item in db.scalars(select(ResearchResult).where(ResearchResult.experiment_id == experiment_id).order_by(ResearchResult.created_at.asc())).all()]


@router.get("/admin/users", response_model=list[AdminUserResponse], tags=["admin"])
def admin_users(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
) -> list[AdminUserResponse]:
    return [AdminUserResponse(id=item.id, email=item.email, display_name=item.display_name, role=item.role.name, is_active=item.is_active, created_at=item.created_at) for item in db.scalars(select(User).order_by(User.created_at.asc())).all()]


@router.patch("/admin/users/{user_id}/active", response_model=AdminUserResponse, tags=["admin"])
@router.patch("/admin/users/{user_id}/toggle", response_model=AdminUserResponse, tags=["admin"])
def set_user_active(
    user_id: str,
    active: bool = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
) -> AdminUserResponse:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == user.id and not active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate your own account")
    target.is_active = active
    _audit(db, user, "user_status_changed", "user", target.id, {"is_active": active})
    db.commit()
    db.refresh(target)
    return AdminUserResponse(id=target.id, email=target.email, display_name=target.display_name, role=target.role.name, is_active=target.is_active, created_at=target.created_at)


@router.get("/admin/audit-logs", response_model=list[AuditLogResponse], tags=["admin"])
def admin_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
) -> list[AuditLogResponse]:
    return [AuditLogResponse(id=item.id, actor_id=item.actor_id, action=item.action, resource_type=item.resource_type, resource_id=item.resource_id, details=_json_object(item.details_json), created_at=item.created_at) for item in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).all()]


@router.get("/admin/settings", response_model=list[SystemSettingResponse], tags=["admin"])
def list_system_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
) -> list[SystemSettingResponse]:
    return [SystemSettingResponse(key=item.key, value=item.value, updated_by=item.updated_by, updated_at=item.updated_at) for item in db.scalars(select(SystemSetting).order_by(SystemSetting.key.asc())).all()]


@router.put("/admin/settings/{key}", response_model=SystemSettingResponse, tags=["admin"])
def update_system_setting(
    key: str,
    request: SystemSettingUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
) -> SystemSettingResponse:
    clean_key = key.strip()
    if not clean_key or len(clean_key) > 100:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Setting key must be 1-100 characters")
    try:
        request = SystemSettingUpdate(value=app_settings.validate(clean_key, request.value))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    item = db.scalar(select(SystemSetting).where(SystemSetting.key == clean_key))
    if item is None:
        item = SystemSetting(key=clean_key, value=request.value, updated_by=user.id)
        db.add(item)
    else:
        item.value = request.value
        item.updated_by = user.id
        item.updated_at = datetime.now(timezone.utc)
    db.flush()
    _audit(db, user, "setting_changed", "system_setting", clean_key, {"value_length": len(request.value)})
    db.commit()
    db.refresh(item)
    return SystemSettingResponse(key=item.key, value=item.value, updated_by=item.updated_by, updated_at=item.updated_at)



# Operational settings (services/app_settings.py): everyone can read them, Super Admins change them
@router.get("/settings/operational", tags=["settings"])
def operational_settings(db: Session = Depends(get_db), user: User = Depends(require_active_user)) -> dict:
    thresholds = None
    try:
        thresholds = demand_model.thresholds(db)
    except demand_model.ModelNotReady:
        pass
    return {"can_edit": user.role.name == "super_admin", "settings": app_settings.describe(db), "demand_thresholds": thresholds}


@router.put("/settings/operational/{key}", tags=["settings"])
def update_operational_setting(key: str, request: OperationalSettingUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin"))) -> dict:
    if key not in app_settings.BY_KEY:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown setting")
    raw = "" if request.value is None else str(request.value).lower() if isinstance(request.value, bool) else str(request.value)
    try:
        value = app_settings.validate(key, raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    item = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if item is None:
        db.add(SystemSetting(key=key, value=value, updated_by=user.id))
    else:
        item.value, item.updated_by, item.updated_at = value, user.id, datetime.now(timezone.utc)
    _audit(db, user, "setting_changed", "system_setting", key, {"value": value or "default"})
    db.commit()
    return operational_settings(db=db, user=user)


@router.delete("/settings/operational/{key}", tags=["settings"])
def reset_operational_setting(key: str, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin"))) -> dict:
    item = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if item is not None:
        db.delete(item)
        _audit(db, user, "setting_reset", "system_setting", key)
        db.commit()
    return operational_settings(db=db, user=user)


# Profile: preferences, activity, password
DEFAULT_PREFERENCES = {
    "landing_page": None, "theme": "dark", "alert_min_severity": "medium",
    "alert_categories": ["demand_forecast", "low_demand", "live_load", "frequency", "overdrawal", "voltage", "peak_record", "data_quality"],
}


def _preferences(db: Session, user: User) -> dict:
    stored = db.get(UserPreference, user.id)
    values = {**DEFAULT_PREFERENCES, "landing_page": "/dashboard" if user.role.name != "research_analyst" else "/analytics"}
    if stored is not None:
        values.update({key: value for key, value in _json_object(stored.preferences_json).items() if key in DEFAULT_PREFERENCES and value is not None})
    return values


@router.get("/users/me/preferences", tags=["users"])
def get_preferences(db: Session = Depends(get_db), user: User = Depends(require_active_user)) -> dict:
    return _preferences(db, user)


@router.put("/users/me/preferences", tags=["users"])
def update_preferences(request: UserPreferencesUpdate, db: Session = Depends(get_db), user: User = Depends(require_active_user)) -> dict:
    current = _preferences(db, user)
    current.update({key: value for key, value in request.model_dump().items() if value is not None})
    stored = db.get(UserPreference, user.id)
    if stored is None:
        db.add(UserPreference(user_id=user.id, preferences_json=json.dumps(current)))
    else:
        stored.preferences_json = json.dumps(current)
        stored.updated_at = datetime.now(timezone.utc)
    db.commit()
    return current


@router.get("/users/me/activity", response_model=list[AuditLogResponse], tags=["users"])
def my_activity(limit: int = Query(default=25, ge=1, le=200), db: Session = Depends(get_db), user: User = Depends(require_active_user)) -> list[AuditLogResponse]:
    items = db.scalars(select(AuditLog).where(AuditLog.actor_id == user.id).order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return [AuditLogResponse(id=item.id, actor_id=item.actor_id, action=item.action, resource_type=item.resource_type, resource_id=item.resource_id, details=_json_object(item.details_json), created_at=item.created_at) for item in items]


@router.post("/users/me/password", tags=["users"])
def change_password(request: PasswordChange, db: Session = Depends(get_db), user: User = Depends(require_active_user)) -> dict:
    from ...main import password_hash

    if not password_hash.verify(request.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    if request.new_password == request.current_password:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a password different from the current one.")
    if not any(c.isdigit() for c in request.new_password) or not any(c.isalpha() for c in request.new_password):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Use at least 10 characters with both letters and numbers.")
    user.hashed_password = password_hash.hash(request.new_password)
    _audit(db, user, "password_changed", "user", user.id)
    db.commit()
    return {"changed": True}


# Historical analytics of real Delhi load (services/analytics.py)
@router.get("/analytics/overview", tags=["analytics"])
def analytics_overview(db: Session = Depends(get_db), user: User = Depends(require_active_user)) -> dict:
    return analytics.overview(db)

# Delhi load study: precomputed by analysis/delhi_load/analyze.py
DELHI_ROLES = ("super_admin", "grid_operator", "research_analyst")


@router.get("/delhi-load/summary", tags=["delhi-load"])
def delhi_load_summary(user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    return delhi_load.summary()


@router.get("/delhi-load/scenarios", tags=["delhi-load"])
def delhi_load_scenarios(
    zone: Literal["Low", "Medium", "High"] | None = None,
    day_type: str | None = None,
    weather: str | None = None,
    user: User = Depends(require_roles(*DELHI_ROLES)),
) -> list[dict]:
    return delhi_load.scenarios(zone=zone, day_type=day_type, weather=weather)


@router.get("/delhi-load/days", tags=["delhi-load"])
def delhi_load_days(
    notable_only: bool = False,
    user: User = Depends(require_roles(*DELHI_ROLES)),
) -> list[dict]:
    return delhi_load.days(notable_only=notable_only)


@router.get("/delhi-load/days/{date}", tags=["delhi-load"])
def delhi_load_day(date: str, user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    item = delhi_load.day(date)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Delhi load data for that date. Use a date between 2023-01-01 and 2023-12-31.")
    return item


# Delhi SLDC real load: collected by services/sldc_collector.py (5-minute slots, IST)
SLDC_MAX_DAYS = {"5min": 31, "hour": 366, "day": 3660}


@router.get("/sldc/status", tags=["sldc"])
def sldc_status(db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    first, last, rows, fetched = db.execute(select(func.min(SldcLoad.slot_at), func.max(SldcLoad.slot_at), func.count(), func.max(SldcLoad.fetched_at))).one()
    return {"first_slot": first, "last_slot": last, "rows": rows, "last_fetched_at": fetched, "sync_enabled": collection.sync_enabled(), "timezone": "Asia/Kolkata"}


@router.get("/sldc/load", tags=["sldc"])
def sldc_load(
    start: datetime,
    end: datetime,
    resolution: Literal["5min", "hour", "day"] = "hour",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*DELHI_ROLES)),
) -> list[dict]:
    """Delhi and DISCOM load in MW. Hourly/daily buckets report the mean, and daily also the peak."""
    start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    if end <= start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end must be after start")
    if (end - start).days > SLDC_MAX_DAYS[resolution]:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{resolution} resolution allows at most {SLDC_MAX_DAYS[resolution]} days")
    columns = list(sldc_collector.COLUMNS.values())
    rows = db.scalars(select(SldcLoad).where(SldcLoad.slot_at >= start, SldcLoad.slot_at < end).order_by(SldcLoad.slot_at)).all()
    if resolution == "5min":
        return [{"slot_at": row.slot_at, **{column: getattr(row, column) for column in columns}} for row in rows]

    buckets: dict[datetime, list[SldcLoad]] = {}
    for row in rows:
        key = row.slot_at.replace(minute=0) if resolution == "hour" else row.slot_at.replace(hour=0, minute=0)
        buckets.setdefault(key, []).append(row)
    result = []
    for key, items in buckets.items():
        point: dict = {"slot_at": key, "samples": len(items)}
        for column in columns:
            values = [value for value in (getattr(item, column) for item in items) if value is not None]
            point[column] = round(sum(values) / len(values), 2) if values else None
        if resolution == "day":
            peak = max(items, key=lambda item: item.delhi_mw or 0)
            point["delhi_peak_mw"], point["delhi_peak_at"] = peak.delhi_mw, peak.slot_at
        result.append(point)
    return result


REALTIME_CATEGORIES = Literal["discom", "substation", "delhi_genco", "central_genco", "state", "import", "export"]
READING_FIELDS = ("schedule_mw", "actual_mw", "deviation_mw", "mvar", "voltage_kv", "load_mw", "status")


def _row_dict(row, exclude: tuple[str, ...] = ("fetched_at",)) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name not in exclude}


@router.get("/sldc/realtime", tags=["sldc"])
def sldc_realtime(db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    """Latest captured grid state: system snapshot plus readings grouped by category."""
    snapshot = db.scalar(select(SldcSnapshot).order_by(SldcSnapshot.captured_at.desc()).limit(1))
    latest = db.scalar(select(func.max(SldcRealtimeReading.captured_at)))
    readings: dict[str, list[dict]] = {}
    if latest is not None:
        for row in db.scalars(select(SldcRealtimeReading).where(SldcRealtimeReading.captured_at == latest).order_by(SldcRealtimeReading.category, SldcRealtimeReading.entity)):
            readings.setdefault(row.category, []).append({"entity": row.entity, **{field: getattr(row, field) for field in READING_FIELDS}})
    return {"snapshot": _row_dict(snapshot) if snapshot else None, "readings_captured_at": latest, "readings": readings}


@router.get("/sldc/realtime/history", tags=["sldc"])
def sldc_realtime_history(
    start: datetime,
    end: datetime,
    category: REALTIME_CATEGORIES | None = None,
    entity: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*DELHI_ROLES)),
) -> list[dict]:
    """Without category: system snapshots. With category (and optionally entity): per-entity readings. Max 31 days."""
    start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    if end <= start or (end - start).days > 31:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end must be after start and at most 31 days later")
    if category is None:
        query = select(SldcSnapshot).where(SldcSnapshot.captured_at >= start, SldcSnapshot.captured_at < end).order_by(SldcSnapshot.captured_at)
        return [_row_dict(row) for row in db.scalars(query)]
    query = select(SldcRealtimeReading).where(SldcRealtimeReading.captured_at >= start, SldcRealtimeReading.captured_at < end, SldcRealtimeReading.category == category)
    if entity:
        query = query.where(SldcRealtimeReading.entity == entity)
    return [_row_dict(row) for row in db.scalars(query.order_by(SldcRealtimeReading.captured_at, SldcRealtimeReading.entity))]


@router.get("/sldc/daily-summary", tags=["sldc"])
def sldc_daily_summary(start: datetime, end: datetime, db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> list[dict]:
    """SLDC's official daily peak/min (to the second) and average. Months SLDC doesn't publish are absent."""
    query = select(SldcDailySummary).where(SldcDailySummary.day >= start.replace(tzinfo=None), SldcDailySummary.day < end.replace(tzinfo=None)).order_by(SldcDailySummary.day)
    return [{**_row_dict(row), "day": row.day.date().isoformat()} for row in db.scalars(query)]



def _refresh_in_background() -> None:
    from ...database import SessionLocal

    with SessionLocal() as session:
        grid_live.refresh_if_stale(session)


@router.get("/grid/live", tags=["grid"])
def grid_live_state(background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    """Everything the live grid view shows. Served from stored captures; a stale capture is refreshed in the background,
    or inline when nothing has been captured yet."""
    if db.scalar(select(func.count()).select_from(SldcSnapshot)) == 0:
        grid_live.refresh_if_stale(db)
    else:
        background.add_task(_refresh_in_background)
    return grid_live.live_state(db)


@router.get("/grid/load-curve", tags=["grid"])
def grid_load_curve(day: date | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    """5-minute load for Delhi and each DISCOM on one day (IST). Defaults to today."""
    return grid_live.daily_curve(db, day or grid_live.now_ist().date())


@router.get("/grid/peak-weather", tags=["grid"])
def grid_peak_weather(days: int = Query(default=30, ge=7, le=366), db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> list[dict]:
    """Daily peak demand with that day's temperature, humidity and rain, for the last `days` days."""
    return grid_live.peak_vs_weather(db, days)


@router.get("/grid/substations/{name}/transformers", tags=["grid"])
def grid_substation_transformers(name: str, user: User = Depends(require_roles(*DELHI_ROLES))) -> list[dict]:
    """Live transformer loading inside one substation, fetched from SLDC on demand (cached for a minute)."""
    try:
        return grid_live.substation_transformers(name)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="SLDC did not return transformer data for this substation right now.") from exc



# Demand model: when will load be high or low, and why (services/demand_model.py)
def _demand(call):
    try:
        return call()
    except demand_model.ModelNotReady as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/demand/forecast", tags=["demand"])
def demand_forecast(db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    """Hourly load for the next 7 days with the chance of each hour being high or low, and per-day peak/minimum with reasons."""
    return _demand(lambda: demand_model.forecast(db))


@router.get("/demand/explain", tags=["demand"])
def demand_explain(day: date, db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    """Why a day's peak and minimum were (or will be) where they were, split into weather, calendar and demand-level reasons."""
    result = _demand(lambda: demand_model.explain_day(db, day))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No load or weather data for that day. Pick a date from 2018 to 6 days ahead.")
    return result


@router.get("/demand/ranked-days", tags=["demand"])
def demand_ranked_days(
    kind: Literal["high", "low"] = "high",
    days: int = Query(default=365, ge=7, le=3650),
    limit: int = Query(default=15, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*DELHI_ROLES)),
) -> list[dict]:
    """The highest-peak or lowest-minimum days in the recent past, each with its main reasons."""
    return _demand(lambda: demand_model.ranked_days(db, kind, days, limit))


@router.get("/demand/festivals", tags=["demand"])
def demand_festivals(year: int = Query(ge=2018, le=2030), db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> list[dict]:
    """Every holiday and festival in a year with its measured (past), forecast or typical (upcoming) effect on demand."""
    return _demand(lambda: demand_model.festival_days(db, year))


@router.get("/demand/insights", tags=["demand"])
def demand_insights(user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    """Measured effect of each holiday / festival, and how demand responds to temperature, humidity and rain by season."""
    return _demand(demand_model.insights)


@router.get("/demand/model", tags=["demand"])
def demand_model_info(user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    return _demand(demand_model.model_info)


@router.post("/demand/model/train", tags=["demand"])
def demand_model_train(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "research_analyst"))) -> dict:
    """Retrain on all collected data now (the scheduler also retrains daily)."""
    result = _demand(lambda: demand_model.train(db))
    _audit(db, user, "train", "demand_model", details={"mae_mw": result["metrics"]["mae_mw"], "data_until": result["data_until"]})
    db.commit()
    return result


@router.get("/weather/hourly", tags=["weather"])
def weather_hourly(
    start: datetime,
    end: datetime,
    source: Literal["nasa_power", "open_meteo"] | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*DELHI_ROLES)),
) -> list[dict]:
    """Hourly Delhi weather (IST). Without `source`, NASA POWER is preferred and Open-Meteo fills recent hours and the forecast."""
    start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    if end <= start or (end - start).days > 366:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end must be after start and at most 366 days later")
    query = select(WeatherHourly).where(WeatherHourly.slot_at >= start, WeatherHourly.slot_at < end).order_by(WeatherHourly.slot_at)
    if source:
        query = query.where(WeatherHourly.source == source)
    rows = db.scalars(query).all()
    if source is None:
        # NASA stamps land on :30 IST, Open-Meteo on :30 too (both are whole UTC hours), so slots line up exactly
        nasa_slots = {row.slot_at for row in rows if row.source == "nasa_power"}
        rows = [row for row in rows if row.source == "nasa_power" or row.slot_at not in nasa_slots]
    fields = ("source", "slot_at", *weather_collector.FIELDS, "is_forecast")
    return [{field: getattr(row, field) for field in fields} for row in rows]


def _ist_date(value: datetime) -> str:
    # Postgres hands back TIMESTAMPTZ in UTC (IST midnight is 18:30 the day before); SQLite returns it naive
    return (value.astimezone(calendar_collector.IST) if value.tzinfo else value).date().isoformat()


@router.get("/calendar", tags=["calendar"])
def calendar_days(
    start: datetime,
    end: datetime,
    special_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*DELHI_ROLES)),
) -> list[dict]:
    """Delhi calendar: weekends, gazetted holidays (is_holiday) and restricted-holiday festivals (festival_name only)."""
    start, end = (value if value.tzinfo else value.replace(tzinfo=calendar_collector.IST) for value in (start, end))
    query = select(CalendarContext).where(CalendarContext.calendar_date >= start, CalendarContext.calendar_date < end).order_by(CalendarContext.calendar_date)
    if special_only:
        query = query.where(CalendarContext.festival_name.is_not(None))
    return [
        {"date": _ist_date(row.calendar_date), "is_weekend": row.is_weekend, "is_holiday": row.is_holiday, "festival_name": row.festival_name}
        for row in db.scalars(query).all()
    ]


@router.get("/collection/status", tags=["sldc", "weather", "calendar"])
def collection_status(db: Session = Depends(get_db), user: User = Depends(require_roles(*DELHI_ROLES))) -> dict:
    weather = {
        source: {"first_slot": first, "last_slot": last, "rows": rows, "last_fetched_at": fetched}
        for source, first, last, rows, fetched in db.execute(
            select(WeatherHourly.source, func.min(WeatherHourly.slot_at), func.max(WeatherHourly.slot_at), func.count(), func.max(WeatherHourly.fetched_at)).group_by(WeatherHourly.source)
        )
    }
    first_day, last_day, days = db.execute(select(func.min(CalendarContext.calendar_date), func.max(CalendarContext.calendar_date), func.count())).one()
    snapshots, first_snapshot, last_snapshot = db.execute(select(func.count(), func.min(SldcSnapshot.captured_at), func.max(SldcSnapshot.captured_at))).one()
    daily, first_daily, last_daily = db.execute(select(func.count(), func.min(SldcDailySummary.day), func.max(SldcDailySummary.day))).one()
    return {
        "sync_enabled": collection.sync_enabled(),
        "sldc_load": sldc_status(db=db, user=user),
        "sldc_realtime": {"snapshots": snapshots, "first": first_snapshot, "last": last_snapshot, "readings": db.scalar(select(func.count()).select_from(SldcRealtimeReading))},
        "sldc_daily_summary": {"days": daily, "first_day": first_daily, "last_day": last_daily},
        "weather": weather,
        "calendar": {"first_day": first_day, "last_day": last_day, "days": days},
    }
