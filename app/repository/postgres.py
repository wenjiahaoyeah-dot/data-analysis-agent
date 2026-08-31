"""控制面的 PostgreSQL 实现（psycopg3 + 连接池）。

关键约定（与 jsonfile.py 严格对齐，否则「双实现可互换」就无从验证）：
    1. 所有时间字段在库里就是 TEXT（ISO-8601），出库不做类型转换；
    2. tasks 表的 `payload` JSONB 保存完整任务字典，其余列只做索引与过滤冗余；
    3. get_* 找不到返回 None，不抛异常；
    4. 每个连接都通过 search_path 固定到配置的 schema，避免污染 public。

主动拒绝连接失败（而不是静默降级到 JSON）：控制面写不进去时若悄悄换后端，
数据会分叉到两处，比直接报错难查得多。
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from typing import Any, Iterator

from app.repository.base import (
    GLOBAL_OWNER,
    RepositoryConflict,
    RepositoryUnavailable,
)
from app.repository.models import (
    ANONYMOUS_USER,
    AuditRecord,
    DatasetMeta,
    Metric,
    QAExample,
    User,
)


class PostgresRepository:
    backend = "postgres"

    def __init__(
        self,
        dsn: str,
        schema: str = "public",
        pool_size: int = 5,
        connect_timeout: float = 5.0,
    ) -> None:
        if not dsn:
            raise RepositoryUnavailable("未配置 POSTGRES_DSN")
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover
            raise RepositoryUnavailable(
                "未安装 psycopg，请执行：pip install 'psycopg[binary,pool]'"
            ) from exc

        self.dsn = dsn
        self.schema = schema
        try:
            self.pool = ConnectionPool(
                conninfo=dsn,
                min_size=1,
                max_size=max(1, pool_size),
                kwargs={"options": f"-c search_path={schema}"},
                open=False,
            )
            self.pool.open()
            self.pool.wait(timeout=connect_timeout)
        except Exception as exc:
            raise RepositoryUnavailable(f"PostgreSQL 连接失败：{exc}") from exc

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def cache_key(self) -> str:
        return f"postgres::{self.schema}"

    def close(self) -> None:
        try:
            self.pool.close()
        except Exception:  # pragma: no cover
            pass

    def ping(self) -> bool:
        try:
            with self._cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception as exc:
            raise RepositoryUnavailable(f"PostgreSQL 不可用：{exc}") from exc

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        from psycopg.rows import dict_row

        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                yield cur
            conn.commit()

    def get_conversation(self, session_id: str) -> dict[str, Any] | None:
        with self._cursor() as cur:
            cur.execute("SELECT payload, version FROM conversations WHERE id = %s", (session_id,))
            row = cur.fetchone()
        if not row:
            return None
        return {**row["payload"], "version": row["version"]}

    def save_conversation(self, conversation: dict[str, Any], expected_version: int) -> None:
        from psycopg.types.json import Jsonb

        payload = copy.deepcopy(conversation)
        payload["version"] = expected_version + 1
        with self._cursor() as cur:
            if expected_version == 0:
                cur.execute(
                    "INSERT INTO conversations (id, owner_id, dataset_id, version, payload) "
                    "VALUES (%s, %s, %s, 1, %s) ON CONFLICT (id) DO NOTHING",
                    (payload["session_id"], payload["owner_id"], payload["dataset_id"], Jsonb(payload)),
                )
            else:
                cur.execute(
                    "UPDATE conversations SET version = %s, payload = %s "
                    "WHERE id = %s AND owner_id = %s AND dataset_id = %s AND version = %s",
                    (expected_version + 1, Jsonb(payload), payload["session_id"],
                     payload["owner_id"], payload["dataset_id"], expected_version),
                )
            if cur.rowcount != 1:
                raise RepositoryConflict("会话已被其他请求更新，请重试")

    def list_conversations(self, owner_id: str, dataset_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute("SELECT payload, version FROM conversations WHERE owner_id = %s AND dataset_id = %s "
                        "ORDER BY payload->>'updated_at' DESC LIMIT %s", (owner_id, dataset_id, max(0, limit)))
            return [{**row["payload"], "version": row["version"]} for row in cur.fetchall()]

    def delete_conversation(self, session_id: str, owner_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM conversations WHERE id = %s AND owner_id = %s", (session_id, owner_id))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # 用户
    # ------------------------------------------------------------------ #
    def ensure_user(self, user_id: str, display_name: str = "") -> User:
        from datetime import datetime

        now = datetime.now().isoformat(timespec="seconds")
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, display_name, created_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (user_id, display_name or user_id, now),
            )
            cur.execute("SELECT id, display_name, created_at FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return User.from_dict(row) if row else User(id=user_id, display_name=display_name, created_at=now)

    def get_user(self, user_id: str) -> User | None:
        with self._cursor() as cur:
            cur.execute("SELECT id, display_name, created_at FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return User.from_dict(row) if row else None

    # ------------------------------------------------------------------ #
    # 数据集
    # ------------------------------------------------------------------ #
    def save_dataset(self, meta: DatasetMeta) -> None:
        from psycopg.types.json import Jsonb

        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO datasets
                    (id, owner_id, created_at, files, tables, profiles, warnings)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    files = EXCLUDED.files,
                    tables = EXCLUDED.tables,
                    profiles = EXCLUDED.profiles,
                    warnings = EXCLUDED.warnings
                """,
                (
                    meta.dataset_id,
                    meta.owner_id,
                    meta.created_at,
                    Jsonb(meta.files),
                    Jsonb(meta.tables),
                    Jsonb(meta.profiles),
                    Jsonb(meta.warnings),
                ),
            )

    def get_dataset(self, dataset_id: str) -> DatasetMeta | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, owner_id, created_at, files, tables, profiles, warnings "
                "FROM datasets WHERE id = %s",
                (dataset_id,),
            )
            row = cur.fetchone()
        return self._row_to_dataset(row) if row else None

    def list_datasets(self, owner_id: str | None = None, limit: int = 20) -> list[DatasetMeta]:
        sql = (
            "SELECT id, owner_id, created_at, files, tables, profiles, warnings FROM datasets "
        )
        params: list[Any] = []
        if owner_id is not None:
            sql += "WHERE owner_id = %s "
            params.append(owner_id)
        sql += "ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._row_to_dataset(r) for r in rows]

    def delete_dataset(self, dataset_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM datasets WHERE id = %s", (dataset_id,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_dataset(row: dict[str, Any]) -> DatasetMeta:
        return DatasetMeta(
            dataset_id=row["id"],
            created_at=row["created_at"],
            files=list(row.get("files") or []),
            tables=list(row.get("tables") or []),
            profiles=list(row.get("profiles") or []),
            warnings=list(row.get("warnings") or []),
            owner_id=row.get("owner_id") or ANONYMOUS_USER,
        )

    # ------------------------------------------------------------------ #
    # 任务
    # ------------------------------------------------------------------ #
    def save_task(self, task: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        payload = copy.deepcopy(task)
        payload.setdefault("owner_id", ANONYMOUS_USER)
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO tasks
                    (id, dataset_id, owner_id, question, status, error_type,
                     retry_count, row_count, elapsed_ms, created_at, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    error_type = EXCLUDED.error_type,
                    retry_count = EXCLUDED.retry_count,
                    row_count = EXCLUDED.row_count,
                    elapsed_ms = EXCLUDED.elapsed_ms,
                    payload = EXCLUDED.payload
                """,
                (
                    payload["task_id"],
                    payload.get("dataset_id"),
                    payload.get("owner_id") or ANONYMOUS_USER,
                    payload.get("question", ""),
                    payload.get("status", "failed"),
                    payload.get("error_type"),
                    int(payload.get("retry_count") or 0),
                    int(payload.get("row_count") or 0),
                    float(payload.get("elapsed_ms") or 0.0),
                    payload.get("created_at"),
                    Jsonb(payload),
                ),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._cursor() as cur:
            cur.execute("SELECT payload FROM tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
        return copy.deepcopy(row["payload"]) if row else None

    def list_tasks(
        self,
        limit: int = 20,
        owner_id: str | None = None,
        dataset_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM tasks "
        where: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            where.append("owner_id = %s")
            params.append(owner_id)
        if dataset_id is not None:
            where.append("dataset_id = %s")
            params.append(dataset_id)
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [copy.deepcopy(r["payload"]) for r in rows]

    # ------------------------------------------------------------------ #
    # 报告
    # ------------------------------------------------------------------ #
    def save_report(self, task_id: str, markdown: str) -> None:
        from datetime import datetime

        now = datetime.now().isoformat(timespec="seconds")
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO reports (task_id, markdown, created_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (task_id) DO UPDATE SET markdown = EXCLUDED.markdown",
                (task_id, markdown, now),
            )

    def get_report(self, task_id: str) -> str | None:
        with self._cursor() as cur:
            cur.execute("SELECT markdown FROM reports WHERE task_id = %s", (task_id,))
            row = cur.fetchone()
        return row["markdown"] if row else None

    # ------------------------------------------------------------------ #
    # 指标
    # ------------------------------------------------------------------ #
    def list_metrics(self, owner_id: str | None = None) -> list[Metric]:
        sql = "SELECT * FROM metrics "
        params: list[Any] = []
        if owner_id is not None:
            sql += "WHERE owner_id = ANY(%s) "
            params.append([owner_id, GLOBAL_OWNER])
        sql += "ORDER BY id ASC"
        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [Metric.from_dict(r) for r in rows]

    def upsert_metric(self, metric: Metric) -> Metric:
        from psycopg.types.json import Jsonb

        owner = metric.owner_id or ANONYMOUS_USER
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO metrics
                    (owner_id, name, display_name, expression, description,
                     required_columns, default_filter, unit, synonyms, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (owner_id, name) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    expression = EXCLUDED.expression,
                    description = EXCLUDED.description,
                    required_columns = EXCLUDED.required_columns,
                    default_filter = EXCLUDED.default_filter,
                    unit = EXCLUDED.unit,
                    synonyms = EXCLUDED.synonyms
                RETURNING *
                """,
                (
                    owner,
                    metric.name,
                    metric.display_name,
                    metric.expression,
                    metric.description,
                    Jsonb(metric.required_columns),
                    metric.default_filter,
                    metric.unit,
                    Jsonb(metric.synonyms),
                    metric.created_at,
                ),
            )
            row = cur.fetchone()
        return Metric.from_dict(row) if row else metric

    def delete_metric(self, name: str, owner_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM metrics WHERE name = %s AND owner_id = %s", (name, owner_id)
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # 审计
    # ------------------------------------------------------------------ #
    def append_audit(self, record: AuditRecord) -> AuditRecord:
        from psycopg.types.json import Jsonb

        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO query_logs
                    (task_id, dataset_id, owner_id, question, sql, status, error_type,
                     retry_count, row_count, elapsed_ms, metric_check, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    record.task_id,
                    record.dataset_id,
                    record.owner_id,
                    record.question,
                    record.sql,
                    record.status,
                    record.error_type,
                    record.retry_count,
                    record.row_count,
                    record.elapsed_ms,
                    Jsonb(record.metric_check) if record.metric_check else None,
                    record.created_at,
                ),
            )
            row = cur.fetchone()
        record.id = row["id"] if row else None
        return record

    def list_audits(
        self,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditRecord]:
        sql = "SELECT * FROM query_logs "
        where: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            where.append("owner_id = %s")
            params.append(owner_id)
        if dataset_id is not None:
            where.append("dataset_id = %s")
            params.append(dataset_id)
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY created_at DESC, id DESC LIMIT %s"
        params.append(limit)
        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [AuditRecord.from_dict(r) for r in rows]

    def audit_stats(self, owner_id: str | None = None) -> dict[str, Any]:
        from app.repository.jsonfile import _compute_stats

        return _compute_stats(self.list_audits(owner_id=owner_id, limit=10000))

    # ------------------------------------------------------------------ #
    # few-shot 示例
    # ------------------------------------------------------------------ #
    def add_example(self, example: QAExample) -> QAExample:
        from psycopg.types.json import Jsonb

        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO qa_examples
                    (dataset_signature, question, sql, metric_name, dimensions,
                     success, use_count, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    example.dataset_signature,
                    example.question,
                    example.sql,
                    example.metric_name,
                    Jsonb(example.dimensions),
                    example.success,
                    example.use_count,
                    example.created_at,
                ),
            )
            row = cur.fetchone()
        example.id = row["id"] if row else None
        return example

    def find_examples(
        self,
        dataset_signature: str,
        metric_name: str | None = None,
        limit: int = 3,
    ) -> list[QAExample]:
        sql = "SELECT * FROM qa_examples WHERE dataset_signature = %s AND success = TRUE "
        params: list[Any] = [dataset_signature]
        if metric_name:
            sql += "AND metric_name = %s "
            params.append(metric_name)
        sql += "ORDER BY id DESC LIMIT %s"
        params.append(limit)
        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        items = [QAExample.from_dict(r) for r in rows]
        items.reverse()  # 与 JSON 实现的 items[-limit:] 顺序保持一致
        return items


def _dumps(value: Any) -> str:  # pragma: no cover
    return json.dumps(value, ensure_ascii=False)
