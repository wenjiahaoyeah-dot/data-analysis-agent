-- 002 指标语义层：把"销售额"这类业务指标绑定到唯一口径

CREATE TABLE IF NOT EXISTS metrics (
    id               BIGSERIAL PRIMARY KEY,
    owner_id         TEXT NOT NULL DEFAULT '*',
    name             TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    expression       TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    required_columns JSONB NOT NULL DEFAULT '[]'::jsonb,
    default_filter   TEXT,
    unit             TEXT NOT NULL DEFAULT '',
    synonyms         JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at       TEXT NOT NULL,
    UNIQUE (owner_id, name)
);
CREATE INDEX IF NOT EXISTS idx_metrics_owner ON metrics (owner_id);
