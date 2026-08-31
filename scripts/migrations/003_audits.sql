-- 003 查询审计 + few-shot 示例库

CREATE TABLE IF NOT EXISTS query_logs (
    id          BIGSERIAL PRIMARY KEY,
    task_id     TEXT NOT NULL,
    dataset_id  TEXT NOT NULL DEFAULT '',
    owner_id    TEXT NOT NULL DEFAULT 'anonymous',
    question    TEXT NOT NULL DEFAULT '',
    sql         TEXT,
    status      TEXT NOT NULL DEFAULT 'failed',
    error_type  TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    row_count   INTEGER NOT NULL DEFAULT 0,
    elapsed_ms  DOUBLE PRECISION NOT NULL DEFAULT 0,
    metric_check JSONB,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON query_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_dataset ON query_logs (dataset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_owner ON query_logs (owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS qa_examples (
    id                BIGSERIAL PRIMARY KEY,
    dataset_signature TEXT NOT NULL,
    question          TEXT NOT NULL,
    sql               TEXT NOT NULL,
    metric_name       TEXT,
    dimensions        JSONB NOT NULL DEFAULT '[]'::jsonb,
    success           BOOLEAN NOT NULL DEFAULT TRUE,
    use_count         INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_sig ON qa_examples (dataset_signature, id DESC);
