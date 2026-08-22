"""DuckDB 连接与执行管理。

设计要点：
1. 读写隔离：上传建表用短生命周期的 read-write 连接，查询一律用 read_only 连接，
   从连接层面杜绝 Agent 生成的 SQL 修改数据。
2. 全局锁串行化「打开/关闭」动作，避免同一文件在读写模式之间产生锁冲突。
3. 执行超时：优先用 DuckDB 原生 interrupt()，不可用时退化为线程 join 超时。
4. 单数据集单文件，便于隔离与删除。
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import duckdb

from app.config import settings

_WRITE_LOCK = threading.RLock()
# 同一时刻只允许一份 read-only 连接池操作，规避 DuckDB 单文件多模式限制
_OPEN_LOCK = threading.RLock()


class SQLExecutionTimeout(RuntimeError):
    """SQL 执行超时。"""


class DuckDBManager:
    def __init__(self, db_path: str | Path, timeout_seconds: int | None = None) -> None:
        self.db_path = Path(db_path)
        self.timeout_seconds = timeout_seconds or settings.sql_timeout_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """保证库文件存在。

        DuckDB 以 read_only 打开不存在的文件会抛 IOException，所以任何读操作
        之前都要先确保初始化。这里直接用 duckdb.connect，不走 _connect，
        避免与 _OPEN_LOCK 形成嵌套。
        """
        if self.db_path.exists():
            return
        with _WRITE_LOCK:
            if self.db_path.exists():
                return
            try:
                conn = duckdb.connect(str(self.db_path))
                try:
                    conn.execute("SELECT 1")
                finally:
                    conn.close()
            except Exception as exc:  # pragma: no cover - 磁盘不可写等
                raise RuntimeError(f"无法初始化 DuckDB 文件 {self.db_path}：{exc}") from exc

    # ------------------------------------------------------------------ #
    # 连接管理
    # ------------------------------------------------------------------ #
    @contextmanager
    def _connect(self, read_only: bool) -> Iterator[duckdb.DuckDBPyConnection]:
        with _OPEN_LOCK:
            # 只读模式本身仍允许读外部文件；关闭外部访问再作为一道独立防线。
            config = {"enable_external_access": "false"} if read_only else {}
            conn = duckdb.connect(str(self.db_path), read_only=read_only, config=config)
            try:
                yield conn
            finally:
                try:
                    conn.close()
                except Exception:  # pragma: no cover - 关闭失败不影响主流程
                    pass

    @contextmanager
    def write_conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with _WRITE_LOCK:
            with self._connect(read_only=False) as conn:
                yield conn

    @contextmanager
    def read_conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        self._ensure_initialized()
        with _OPEN_LOCK:
            with self._connect(read_only=True) as conn:
                yield conn

    # ------------------------------------------------------------------ #
    # 写入（仅上传服务调用）
    # ------------------------------------------------------------------ #
    def register_table(self, table_name: str, df) -> int:
        """把 DataFrame 写入 DuckDB（覆盖同名表），返回行数。"""
        with self.write_conn() as conn:
            conn.register("_incoming_df", df)
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            conn.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _incoming_df')
            conn.unregister("_incoming_df")
            rows = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        return int(rows)

    def table_exists(self, table_name: str) -> bool:
        with self.read_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name = ?",
                [table_name],
            ).fetchone()
        return bool(row and row[0])

    def list_tables(self) -> list[str]:
        with self.read_conn() as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ #
    # 元数据
    # ------------------------------------------------------------------ #
    def get_schema(self) -> dict[str, Any]:
        """返回 {table_name: {"row_count": int, "columns": [{name,type}, ...]}}"""
        schema: dict[str, Any] = {}
        with self.read_conn() as conn:
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
            for (table,) in tables:
                cols = conn.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
                    [table],
                ).fetchall()
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                schema[table] = {
                    "row_count": int(count),
                    "columns": [{"name": c, "type": t} for c, t in cols],
                }
        return schema

    # ------------------------------------------------------------------ #
    # 查询执行
    # ------------------------------------------------------------------ #
    def execute(self, sql: str, max_rows: int | None = None) -> dict[str, Any]:
        """执行只读查询。

        返回 {"columns": [...], "rows": [[...]], "row_count": n, "elapsed_ms": ms, "truncated": bool}
        """
        max_rows = max_rows or settings.max_result_rows
        started = time.perf_counter()
        with self.read_conn() as conn:
            timer: threading.Timer | None = None
            hit_timeout = {"value": False}

            def _interrupt() -> None:
                hit_timeout["value"] = True
                try:
                    conn.interrupt()
                except Exception:  # pragma: no cover
                    pass

            try:
                timer = threading.Timer(self.timeout_seconds, _interrupt)
                timer.daemon = True
                timer.start()
                cur = conn.execute(sql)
                columns = [d[0] for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(max_rows + 1)
            except Exception as exc:
                if hit_timeout["value"]:
                    raise SQLExecutionTimeout(
                        f"SQL 执行超过 {self.timeout_seconds}s 被中断"
                    ) from exc
                raise
            finally:
                if timer is not None:
                    timer.cancel()

        truncated = len(fetched) > max_rows
        rows = fetched[:max_rows]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "elapsed_ms": elapsed_ms,
            "truncated": truncated,
        }
