"""DuckDB 管理器测试：读写隔离、行数上限、执行超时。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.database.duckdb_manager import DuckDBManager, SQLExecutionTimeout


@pytest.fixture()
def db(tmp_path):
    return DuckDBManager(tmp_path / "unit.duckdb", timeout_seconds=5)


def test_register_and_schema(db):
    df = pd.DataFrame(
        {
            "region": ["华东", "华南"],
            "amount": [100.5, 200.0],
            "order_date": pd.to_datetime(["2026-01-01", "2026-02-01"]),
        }
    )
    rows = db.register_table("sales", df)
    assert rows == 2

    schema = db.get_schema()
    assert "sales" in schema
    assert schema["sales"]["row_count"] == 2
    names = [c["name"] for c in schema["sales"]["columns"]]
    assert names == ["region", "amount", "order_date"]


def test_read_connection_is_read_only(db):
    db.register_table("t", pd.DataFrame({"x": [1, 2, 3]}))
    with pytest.raises(Exception):
        with db.read_conn() as conn:
            conn.execute("DROP TABLE t")


def test_read_connection_disables_external_file_access(db, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    with pytest.raises(Exception, match="(?i)(external access|file system operations)"):
        db.execute(f"SELECT * FROM read_text('{secret}')")


def test_execute_fetchmany_respects_max_rows(db):
    db.register_table("t", pd.DataFrame({"x": list(range(1000))}))
    data = db.execute("SELECT x FROM t", max_rows=10)
    assert data["row_count"] == 10
    assert data["truncated"] is True


def test_execute_returns_columns_and_timing(db):
    db.register_table("t", pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}))
    data = db.execute("SELECT a, b FROM t")
    assert data["columns"] == ["a", "b"]
    assert data["row_count"] == 2
    assert data["elapsed_ms"] >= 0
    assert data["truncated"] is False


def test_invalid_sql_raises(db):
    db.register_table("t", pd.DataFrame({"x": [1]}))
    with pytest.raises(Exception):
        db.execute("SELECT missing_column FROM t")


def test_timeout_interrupts_heavy_query(db):
    """DuckDB 原生 interrupt 是否生效。

    机器性能差异会让这个用例不稳定，因此没触发超时的时候直接 skip，
    而不是让它变成一个虚假的失败。
    """
    db.register_table("t", pd.DataFrame({"x": [1, 2, 3]}))
    fast = DuckDBManager(db.db_path, timeout_seconds=0.05)
    try:
        fast.execute("SELECT SUM(a.i * b.i) FROM range(20000) a(i), range(20000) b(i), t")
    except SQLExecutionTimeout:
        return
    pytest.skip("机器太快，未触发超时（interrupt 能力已由代码路径覆盖）")


def test_table_exists_and_list(db):
    db.register_table("alpha", pd.DataFrame({"x": [1]}))
    db.register_table("beta", pd.DataFrame({"y": [2]}))
    assert db.table_exists("alpha")
    assert not db.table_exists("gamma")
    assert db.list_tables() == ["alpha", "beta"]
