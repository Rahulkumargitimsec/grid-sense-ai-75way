from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(160))
    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    role: Mapped[Role] = relationship(back_populates="users")


class DatasetImport(Base):
    __tablename__ = "dataset_imports"
    __table_args__ = (UniqueConstraint("dataset_name", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class HistoricalLoad(Base):
    __tablename__ = "historical_load"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    demand_mw: Mapped[float]
    source: Mapped[str] = mapped_column(String(80), default="manual", nullable=False)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_imports.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class SldcLoad(Base):
    """5-minute SCADA load scraped from Delhi SLDC. slot_at is naive local time (IST), as published."""

    __tablename__ = "sldc_load"

    slot_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    delhi_mw: Mapped[float | None]
    brpl_mw: Mapped[float | None]
    bypl_mw: Mapped[float | None]
    ndpl_mw: Mapped[float | None]
    ndmc_mw: Mapped[float | None]
    mes_mw: Mapped[float | None]
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class SldcDailySummary(Base):
    """SLDC's official daily Delhi load profile: peak/min to the second, from services/sldc_realtime_collector.py."""

    __tablename__ = "sldc_daily_summary"

    day: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    max_mw: Mapped[float | None]
    max_time: Mapped[str | None] = mapped_column(String(8))
    min_mw: Mapped[float | None]
    min_time: Mapped[str | None] = mapped_column(String(8))
    avg_mw: Mapped[float | None]
    samples: Mapped[int | None]
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class SldcSnapshot(Base):
    """System-wide Delhi figures captured every few minutes; captured_at is the IST 5-minute slot of the poll."""

    __tablename__ = "sldc_snapshot"

    captured_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    source_time: Mapped[datetime | None] = mapped_column(DateTime)
    load_mw: Mapped[float | None]
    schedule_mw: Mapped[float | None]
    drawal_mw: Mapped[float | None]
    odud_mw: Mapped[float | None]
    frequency_hz: Mapped[float | None]
    delhi_generation_mw: Mapped[float | None]
    peak_today_mw: Mapped[float | None]
    peak_today_time: Mapped[str | None] = mapped_column(String(8))
    min_today_mw: Mapped[float | None]
    min_today_time: Mapped[str | None] = mapped_column(String(8))
    all_time_peak_mw: Mapped[float | None]
    all_time_peak_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class SldcRealtimeReading(Base):
    """Per-entity real-time readings. Columns are shared across categories; unused ones stay null:
    discom (schedule/actual=drawl/deviation=OD-UD), substation (actual=MW, mvar, voltage_kv, status=RTU ok),
    delhi_genco (schedule/actual/deviation=UI), central_genco (schedule/actual), state (schedule/actual=drawl/deviation/load_mw),
    import and export (actual=MW, mvar)."""

    __tablename__ = "sldc_realtime_reading"

    captured_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    category: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity: Mapped[str] = mapped_column(String(80), primary_key=True)
    schedule_mw: Mapped[float | None]
    actual_mw: Mapped[float | None]
    deviation_mw: Mapped[float | None]
    mvar: Mapped[float | None]
    voltage_kv: Mapped[float | None]
    load_mw: Mapped[float | None]
    status: Mapped[int | None]
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class WeatherHourly(Base):
    """Hourly Delhi weather from services/weather_collector.py. slot_at is naive IST, matching sldc_load."""

    __tablename__ = "weather_hourly"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    slot_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    temperature_c: Mapped[float | None]
    humidity_pct: Mapped[float | None]
    precipitation_mm: Mapped[float | None]
    wind_speed_ms: Mapped[float | None]
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class WeatherObservation(Base):
    __tablename__ = "weather_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    temperature_c: Mapped[float | None]
    rainfall_mm: Mapped[float | None]
    source: Mapped[str] = mapped_column(String(80), default="manual", nullable=False)


class CalendarContext(Base):
    __tablename__ = "calendar_context"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True, index=True, nullable=False)
    is_weekend: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_holiday: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    festival_name: Mapped[str | None] = mapped_column(String(120))


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    forecast_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    demand_mw: Mapped[float]
    confidence: Mapped[float | None]
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    # Added by services/alert_engine.py; older rows keep the defaults.
    category: Mapped[str] = mapped_column(String(40), default="demand", nullable=False, server_default="demand")
    dedupe_key: Mapped[str | None] = mapped_column(String(160), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    link: Mapped[str | None] = mapped_column(String(120))


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    data_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class TrainingLog(Base):
    __tablename__ = "training_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("model_runs.id"), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ExplainabilityResult(Base):
    __tablename__ = "explainability_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    feature_contributions_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    expected_reduction_mw: Mapped[float | None]
    expected_savings: Mapped[float | None]
    time_window: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float] = mapped_column(default=0.0, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    # Set for recommendations generated by services/recommender.py (null = created by a person).
    source_key: Mapped[str | None] = mapped_column(String(160), index=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    generated_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ResearchResult(Base):
    __tablename__ = "research_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    reproducibility_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(80))
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    __table_args__ = (UniqueConstraint("key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    preferences_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
