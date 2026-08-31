-- 会话仅保存最近的完整对话轮次；版本号用于乐观并发控制。
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_owner_dataset ON conversations (owner_id, dataset_id);
