-- 001 控制面基础表：用户、数据集、任务、报告
-- 时间字段刻意使用 TEXT（ISO-8601），与 JSON 实现保持字节级可比

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL DEFAULT 'anonymous',
    created_at TEXT NOT NULL,
    files      JSONB NOT NULL DEFAULT '[]'::jsonb,
    tables     JSONB NOT NULL DEFAULT '[]'::jsonb,
    profiles   JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings   JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_datasets_owner ON datasets (owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT,
    owner_id    TEXT NOT NULL DEFAULT 'anonymous',
    question    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'failed',
    error_type  TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    row_count   INTEGER NOT NULL DEFAULT 0,
    elapsed_ms  DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    payload     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_dataset ON tasks (dataset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks (owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reports (
    task_id    TEXT PRIMARY KEY,
    markdown   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO users (id, display_name, created_at)
VALUES ('anonymous', '匿名用户', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS'))
ON CONFLICT (id) DO NOTHING;
