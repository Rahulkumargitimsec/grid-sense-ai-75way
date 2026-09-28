-- Hourly Delhi weather (see app/services/weather_collector.py). slot_at is IST local time.
CREATE TABLE IF NOT EXISTS weather_hourly (
    source VARCHAR(20) NOT NULL,
    slot_at TIMESTAMP NOT NULL,
    temperature_c DOUBLE PRECISION,
    humidity_pct DOUBLE PRECISION,
    precipitation_mm DOUBLE PRECISION,
    wind_speed_ms DOUBLE PRECISION,
    is_forecast BOOLEAN NOT NULL DEFAULT FALSE,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, slot_at)
);
