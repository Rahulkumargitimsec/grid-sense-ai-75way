-- Delhi SLDC real-time captures and official daily profile (see app/services/sldc_realtime_collector.py).
-- All naive timestamps are IST.
CREATE TABLE IF NOT EXISTS sldc_daily_summary (
    day TIMESTAMP PRIMARY KEY,
    max_mw DOUBLE PRECISION,
    max_time VARCHAR(8),
    min_mw DOUBLE PRECISION,
    min_time VARCHAR(8),
    avg_mw DOUBLE PRECISION,
    samples INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sldc_snapshot (
    captured_at TIMESTAMP PRIMARY KEY,
    source_time TIMESTAMP,
    load_mw DOUBLE PRECISION,
    schedule_mw DOUBLE PRECISION,
    drawal_mw DOUBLE PRECISION,
    odud_mw DOUBLE PRECISION,
    frequency_hz DOUBLE PRECISION,
    delhi_generation_mw DOUBLE PRECISION,
    peak_today_mw DOUBLE PRECISION,
    peak_today_time VARCHAR(8),
    min_today_mw DOUBLE PRECISION,
    min_today_time VARCHAR(8),
    all_time_peak_mw DOUBLE PRECISION,
    all_time_peak_at TIMESTAMP,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sldc_realtime_reading (
    captured_at TIMESTAMP NOT NULL,
    category VARCHAR(20) NOT NULL,
    entity VARCHAR(80) NOT NULL,
    schedule_mw DOUBLE PRECISION,
    actual_mw DOUBLE PRECISION,
    deviation_mw DOUBLE PRECISION,
    mvar DOUBLE PRECISION,
    voltage_kv DOUBLE PRECISION,
    load_mw DOUBLE PRECISION,
    status INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (captured_at, category, entity)
);

CREATE INDEX IF NOT EXISTS sldc_realtime_reading_category_entity_idx ON sldc_realtime_reading (category, entity, captured_at);
