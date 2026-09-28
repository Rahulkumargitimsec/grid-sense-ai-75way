CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(40) NOT NULL UNIQUE,
    description VARCHAR(160) NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(120) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    role_id INTEGER NOT NULL REFERENCES roles(id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dataset_imports (
    id SERIAL PRIMARY KEY,
    dataset_name VARCHAR(120) NOT NULL,
    version INTEGER NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL,
    row_count INTEGER NOT NULL,
    valid_rows INTEGER NOT NULL,
    invalid_rows INTEGER NOT NULL,
    uploaded_by VARCHAR(64) NOT NULL REFERENCES users(id),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dataset_name, version)
);

CREATE TABLE IF NOT EXISTS historical_load (
    id SERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    demand_mw DOUBLE PRECISION NOT NULL,
    source VARCHAR(80) NOT NULL DEFAULT 'manual',
    import_id INTEGER REFERENCES dataset_imports(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS historical_load_recorded_at_idx ON historical_load (recorded_at);

CREATE TABLE IF NOT EXISTS weather_observations (
    id SERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    temperature_c DOUBLE PRECISION,
    rainfall_mm DOUBLE PRECISION,
    source VARCHAR(80) NOT NULL DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS weather_observations_recorded_at_idx ON weather_observations (recorded_at);

CREATE TABLE IF NOT EXISTS calendar_context (
    id SERIAL PRIMARY KEY,
    calendar_date TIMESTAMPTZ NOT NULL UNIQUE,
    is_weekend BOOLEAN NOT NULL DEFAULT FALSE,
    is_holiday BOOLEAN NOT NULL DEFAULT FALSE,
    festival_name VARCHAR(120)
);

CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    forecast_for TIMESTAMPTZ NOT NULL,
    demand_mw DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION,
    model_name VARCHAR(120) NOT NULL,
    created_by VARCHAR(64) NOT NULL REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS predictions_forecast_for_idx ON predictions (forecast_for);

CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    title VARCHAR(160) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS model_runs (
    id SERIAL PRIMARY KEY,
    model_name VARCHAR(80) NOT NULL,
    algorithm VARCHAR(80) NOT NULL,
    status VARCHAR(20) NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    data_points INTEGER NOT NULL DEFAULT 0,
    trained_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(64) NOT NULL REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS model_runs_trained_at_idx ON model_runs (trained_at);

CREATE TABLE IF NOT EXISTS training_logs (
    id SERIAL PRIMARY KEY,
    model_run_id INTEGER NOT NULL REFERENCES model_runs(id),
    level VARCHAR(20) NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS training_logs_model_run_id_idx ON training_logs (model_run_id);
