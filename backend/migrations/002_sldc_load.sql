-- Delhi SLDC 5-minute SCADA load (see app/services/sldc_collector.py). slot_at is IST local time.
CREATE TABLE IF NOT EXISTS sldc_load (
    slot_at TIMESTAMP PRIMARY KEY,
    delhi_mw DOUBLE PRECISION,
    brpl_mw DOUBLE PRECISION,
    bypl_mw DOUBLE PRECISION,
    ndpl_mw DOUBLE PRECISION,
    ndmc_mw DOUBLE PRECISION,
    mes_mw DOUBLE PRECISION,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
