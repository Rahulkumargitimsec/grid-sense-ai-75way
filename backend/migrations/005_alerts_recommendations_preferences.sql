-- Alert engine, generated recommendations and per-user preferences.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS category VARCHAR(40) NOT NULL DEFAULT 'demand';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(160);
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(64);
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS link VARCHAR(120);
CREATE INDEX IF NOT EXISTS alerts_dedupe_key_idx ON alerts (dedupe_key);

ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS source_key VARCHAR(160);
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS recommendations_source_key_idx ON recommendations (source_key);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id VARCHAR(64) PRIMARY KEY REFERENCES users(id),
    preferences_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
