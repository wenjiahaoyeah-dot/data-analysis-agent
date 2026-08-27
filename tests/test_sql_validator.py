"""SQL 安全校验单元测试。

这是整个项目最需要被测试覆盖的模块：它决定了 Agent 生成的 SQL 能不能碰数据。
"""
from __future__ import annotations

import pytest

from app.tools.sql_tool import SQLSecurityError, classify_error, execute_sql, validate_sql

ALLOWED = ["sales", "products"]
MAX_ROWS = 500


# ---------------------------------------------------------------------- #
# 放行
# ---------------------------------------------------------------------- #
class TestAllowed:
    def test_simple_select(self):
        r = validate_sql("SELECT region FROM sales", ALLOWED, MAX_ROWS)
        assert r.ok
        assert r.tables_used == ["sales"]

    def test_aggregation_with_limit(self):
        sql = "SELECT region, SUM(amount) AS t FROM sales GROUP BY region ORDER BY t DESC LIMIT 5"
        r = validate_sql(sql, ALLOWED, MAX_ROWS)
        assert r.ok
        assert r.limit_applied == 5

    def test_join_between_allowed_tables(self):
        sql = (
            "SELECT s.region, SUM(s.amount) AS t FROM sales s "
            "JOIN products p ON s.product_id = p.product_id GROUP BY s.region"
        )
        r = validate_sql(sql, ALLOWED, MAX_ROWS)
        assert r.ok
        assert set(r.tables_used) == {"sales", "products"}

    def test_cte_alias_not_treated_as_table(self):
        sql = (
            "WITH monthly AS (SELECT region, SUM(amount) AS t FROM sales GROUP BY region) "
            "SELECT region, t FROM monthly ORDER BY t DESC"
        )
        r = validate_sql(sql, ALLOWED, MAX_ROWS)
        assert r.ok, r.reason
        assert "monthly" not in [t.lower() for t in r.tables_used]

    def test_cte_with_alias_is_allowed(self):
        r = validate_sql("WITH x AS (SELECT * FROM sales) SELECT * FROM x AS y", ALLOWED)
        assert r.ok, r.reason

    def test_cte_names_are_case_insensitive_like_duckdb(self):
        r = validate_sql("WITH X AS (SELECT * FROM sales) SELECT * FROM x", ALLOWED)
        assert r.ok, r.reason

    def test_union_wrapped_with_limit(self):
        sql = "SELECT region FROM sales UNION SELECT product FROM sales"
        r = validate_sql(sql, ALLOWED, MAX_ROWS)
        assert r.ok
        assert str(MAX_ROWS) in r.sql


# ---------------------------------------------------------------------- #
# 拦截
# ---------------------------------------------------------------------- #
class TestBlocked:
    @pytest.mark.parametrize(
        "sql,expected_type",
        [
            ("DELETE FROM sales", "write_operation"),
            ("DROP TABLE sales", "write_operation"),
            ("UPDATE sales SET amount = 0", "write_operation"),
            ("INSERT INTO sales VALUES (1)", "write_operation"),
            ("CREATE TABLE evil AS SELECT * FROM sales", "write_operation"),
            ("ATTACH 'other.db' AS other", "write_operation"),
            ("COPY sales TO 'out.csv'", "write_operation"),
        ],
    )
    def test_write_operations_rejected(self, sql, expected_type):
        r = validate_sql(sql, ALLOWED, MAX_ROWS)
        assert not r.ok
        assert r.error_type == expected_type

    def test_multiple_statements_rejected(self):
        r = validate_sql("SELECT * FROM sales; DROP TABLE sales", ALLOWED, MAX_ROWS)
        assert not r.ok
        assert r.error_type in {"multiple_statements", "write_operation"}

    def test_unknown_table_rejected(self):
        r = validate_sql("SELECT * FROM secret_table", ALLOWED, MAX_ROWS)
        assert not r.ok
        assert r.error_type == "unknown_table"

    @pytest.mark.parametrize("sql", [
        "WITH secret AS (SELECT * FROM secret) SELECT * FROM secret",
        "WITH secret AS (SELECT 1 AS x) SELECT * FROM main.secret",
        "SELECT * FROM information_schema.sales",
        "SELECT * FROM query('SELECT * FROM secret')",
        "SELECT * FROM query_table('secret')",
        "WITH x AS (SELECT 1 AS v) SELECT * FROM x AS secret JOIN secret AS s ON true",
    ])
    def test_cte_scope_and_dynamic_queries_cannot_bypass_whitelist(self, sql):
        assert not validate_sql(sql, ALLOWED).ok

    def test_system_table_rejected(self):
        r = validate_sql("SELECT * FROM information_schema.tables", ALLOWED, MAX_ROWS)
        assert not r.ok
        assert r.error_type == "unknown_table"

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM read_csv_auto('/etc/passwd')",
            "SELECT * FROM read_parquet('/tmp/x.parquet')",
            "SELECT * FROM glob('/Users/*')",
        ],
    )
    def test_file_reading_functions_rejected(self, sql):
        r = validate_sql(sql, ALLOWED, MAX_ROWS)
        assert not r.ok
        assert r.error_type in {"forbidden_function", "unknown_table"}

    def test_empty_sql_rejected(self):
        assert not validate_sql("   ", ALLOWED, MAX_ROWS).ok

    def test_syntax_error_reported(self):
        r = validate_sql("SELEC * FROM sales", ALLOWED, MAX_ROWS)
        assert not r.ok
        assert r.error_type in {"syntax", "not_select"}

    def test_markdown_fence_stripped(self):
        sql = "```sql\nSELECT region FROM sales\n```"
        r = validate_sql(sql, ALLOWED, MAX_ROWS)
        assert r.ok


# ---------------------------------------------------------------------- #
# 行数上限
# ---------------------------------------------------------------------- #
class TestRowLimit:
    def test_injects_limit_when_missing(self):
        r = validate_sql("SELECT * FROM sales", ALLOWED, 100)
        assert r.ok
        assert r.limit_applied == 100
        assert str(100) in r.sql.upper()

    def test_keeps_smaller_user_limit(self):
        r = validate_sql("SELECT * FROM sales LIMIT 10", ALLOWED, 100)
        assert r.ok
        assert r.limit_applied == 10

    def test_downgrades_oversized_limit(self):
        r = validate_sql("SELECT * FROM sales LIMIT 100000", ALLOWED, 100)
        assert r.ok
        assert r.limit_applied == 100


# ---------------------------------------------------------------------- #
# 执行
# ---------------------------------------------------------------------- #
class TestExecution:
    def test_execute_returns_rows(self, demo_db):
        table = demo_db.list_tables()[0]
        data = execute_sql(
            demo_db,
            f'SELECT region, SUM(amount) AS t FROM "{table}" GROUP BY region ORDER BY t DESC',
            [table],
            max_rows=100,
        )
        assert data["columns"] == ["region", "t"]
        assert data["row_count"] == 7
        # 降序
        values = [r[1] for r in data["rows"]]
        assert values == sorted(values, reverse=True)

    def test_execute_rejects_unknown_column(self, demo_db):
        table = demo_db.list_tables()[0]
        with pytest.raises(SQLSecurityError) as exc:
            execute_sql(demo_db, f'SELECT area FROM "{table}"', [table])
        assert exc.value.error_type == "unknown_column"

    def test_execute_blocks_write(self, demo_db):
        table = demo_db.list_tables()[0]
        with pytest.raises(SQLSecurityError) as exc:
            execute_sql(demo_db, f'DELETE FROM "{table}"', [table])
        assert exc.value.error_type == "write_operation"

    def test_reports_real_truncation_without_misreporting_exact_limit(self, demo_db):
        table = demo_db.list_tables()[0]
        more = execute_sql(demo_db, f'SELECT * FROM "{table}"', [table], max_rows=5)
        exact = execute_sql(demo_db, f'SELECT * FROM "{table}" LIMIT 5', [table], max_rows=5)
        assert more["row_count"] == 5 and more["truncated"] is True
        assert exact["row_count"] == 5 and exact["truncated"] is False

    def test_error_classification(self):
        assert classify_error('Binder Error: Referenced column "x" not found') == "unknown_column"
        assert classify_error("Parser Error: syntax error at or near") == "syntax"
        assert classify_error("Conversion Error: Could not convert string") == "type_mismatch"
