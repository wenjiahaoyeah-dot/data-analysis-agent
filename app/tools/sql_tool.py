"""SQL 工具：AST 级安全校验 + 受限执行 + 异常归类。

为什么不用正则？
    正则只能拦「写操作关键字」这种表层特征，绕过方式极多（注释、大小写、
    子查询里藏 DDL、表函数读本地文件……）。SQLGlot 会把 SQL 解析成语法树，
    我们校验的是真正要执行的结构：语句类型、表引用、函数调用，因此
检查解析出的结构，并配合 DuckDB 自身限制降低执行风险。

安全边界（五道闸门）：
    1. 只允许单条语句；
    2. 只允许查询类语句（SELECT / UNION / WITH），DDL、DML、COPY、ATTACH 一律拒绝；
    3. 所有引用的表必须在该数据集的白名单内（CTE 别名放行）；
    4. 禁止 read_csv / read_parquet / glob 等可越权读文件或联网的表函数；
    5. 强制注入行数上限，并配合只读连接 + 执行超时。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

from app.database.duckdb_manager import DuckDBManager, SQLExecutionTimeout


def _safe_expr(*names: str) -> tuple[type, ...]:
    """按名称收集 sqlglot 表达式类，兼容不同版本。"""
    found = []
    for name in names:
        cls = getattr(exp, name, None)
        if isinstance(cls, type) and issubclass(cls, exp.Expression):
            found.append(cls)
    return tuple(found)


# 语句级黑名单
FORBIDDEN_STATEMENTS = _safe_expr(
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "TruncateTable",
    "Command", "Copy", "Attach", "Detach", "Grant", "Revoke", "Merge",
    "Transaction", "Commit", "Rollback", "Set", "Use", "Pragma", "Export",
)

# 函数级黑名单：这些表函数可以绕过白名单直接读磁盘/网络
FORBIDDEN_FUNCTIONS = {
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_text", "read_blob", "read_xlsx", "glob", "sniff_csv", "install", "load",
    "attach", "detach", "sqlite_scan", "sqlite_attach", "postgres_scan",
    "mysql_scan", "delta_scan", "iceberg_scan", "parquet_scan", "csv_scan",
    "shell", "getenv", "httpfs", "query", "query_table",
}

QUERY_ROOTS = _safe_expr("Select", "Union", "Intersect", "Except", "Subquery", "With")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _identifier_key(name: str) -> str:
    # DuckDB 的标识符按 ASCII 大小写比较；不能用 Unicode casefold 合并不同表名。
    return name.translate(_ASCII_LOWER)


@dataclass
class ValidationResult:
    ok: bool
    sql: str                       # 注入行数上限后的可执行 SQL
    original_sql: str
    tables_used: list[str] = field(default_factory=list)
    limit_applied: int | None = None
    probe_sql: str = ""            # 多取一行，用于准确判断是否发生截断
    error_type: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "sql": self.sql,
            "original_sql": self.original_sql,
            "tables_used": self.tables_used,
            "limit_applied": self.limit_applied,
            "probe_sql": self.probe_sql,
            "error_type": self.error_type,
            "reason": self.reason,
        }


class SQLSecurityError(RuntimeError):
    def __init__(self, message: str, error_type: str = "security") -> None:
        super().__init__(message)
        self.error_type = error_type


# ---------------------------------------------------------------------- #
# 校验
# ---------------------------------------------------------------------- #
def validate_sql(
    sql: str,
    allowed_tables: list[str],
    max_rows: int = 500,
) -> ValidationResult:
    raw = (sql or "").strip()
    if not raw:
        return ValidationResult(False, "", raw, error_type="empty", reason="SQL 为空")

    # 去掉模型可能附带的 markdown 代码围栏
    raw = _strip_code_fence(raw)

    # 闸门 1：单条语句
    try:
        statements = [s for s in sqlglot.parse(raw, dialect="duckdb") if s is not None]
    except ParseError as exc:
        return ValidationResult(
            False, "", raw, error_type="syntax", reason=f"SQL 语法解析失败：{exc}"
        )
    if not statements:
        return ValidationResult(False, "", raw, error_type="empty", reason="未能解析出有效语句")
    if len(statements) > 1:
        return ValidationResult(
            False, "", raw, error_type="multiple_statements",
            reason=f"检测到 {len(statements)} 条语句，只允许执行单条查询",
        )

    expr = statements[0]

    # 闸门 2：只允许查询语句
    if not isinstance(expr, QUERY_ROOTS):
        for cls in FORBIDDEN_STATEMENTS:
            if isinstance(expr, cls):
                return ValidationResult(
                    False, "", raw, error_type="write_operation",
                    reason=f"检测到非查询语句（{cls.__name__}），仅允许只读 SELECT 查询",
                )
        return ValidationResult(
            False, "", raw, error_type="not_select",
            reason=f"仅支持 SELECT 查询，当前语句类型为 {type(expr).__name__}",
        )

    # 闸门 3：按查询作用域区分 CTE 引用与真实表，不能全局放行同名表。
    # 例如 CTE 定义自身内部的同名表，或 main.cte_name，实际可能是物理表。
    allowed = {_identifier_key(t) for t in allowed_tables}
    tables_used: list[str] = []
    seen_tables: set[int] = set()
    try:
        scopes = list(traverse_scope(expr))
        for scope in scopes:
            for table in scope.tables:
                seen_tables.add(id(table))
                # query('SELECT ...') 等表函数可执行动态 SQL，必须全部拒绝。
                if not isinstance(table.this, exp.Identifier):
                    return ValidationResult(
                        False, "", raw, error_type="forbidden_function",
                        reason="禁止使用表函数，只能查询已上传的数据表",
                    )
                name = table.name
                # 只比较实际 CTE 名称，不能把普通表别名当成 CTE 放行。
                is_cte = any(_identifier_key(key) == _identifier_key(name) for key in scope.cte_sources)
                if is_cte and not table.db and not table.catalog:
                    continue
                if table.catalog or (table.db and table.db.lower() != "main"):
                    return ValidationResult(
                        False, "", raw, error_type="unknown_table",
                        reason=f"表 {table.sql(dialect='duckdb')} 不在可访问白名单内",
                    )
                tables_used.append(name)
                if _identifier_key(name) not in allowed:
                    return ValidationResult(
                        False, "", raw, error_type="unknown_table",
                        reason=f"表 {name} 不在可访问白名单内，允许的表：{', '.join(allowed_tables) or '无'}",
                    )
        # 对无法归入查询作用域的表节点拒绝执行，避免解析器遗漏成为绕过路径。
        if any(id(table) not in seen_tables for table in expr.find_all(exp.Table)):
            return ValidationResult(
                False, "", raw, error_type="unknown_table", reason="无法确定表引用的作用域"
            )
    except (ValueError, TypeError) as exc:
        return ValidationResult(
            False, "", raw, error_type="syntax", reason=f"无法解析查询作用域：{exc}"
        )

    # 闸门 4：危险函数（read_csv / glob / install ... 可绕过白名单读磁盘或联网）
    for func in expr.find_all(exp.Func):
        if isinstance(func, exp.Anonymous):
            # 未知函数（如 read_csv_auto）在 AST 里是 Anonymous，this 就是函数名
            raw_name = func.this if isinstance(func.this, str) else ""
            fname = raw_name.lower()
        else:
            try:
                fname = str(func.sql_name()).lower()
            except Exception:  # 少数表达式没有 sql_name
                fname = ""
        if fname in FORBIDDEN_FUNCTIONS:
            return ValidationResult(
                False, "", raw, error_type="forbidden_function",
                reason=f"禁止调用函数 {fname}（可能绕过白名单读取磁盘或网络资源）",
            )

    # 闸门 5：强制行数上限
    capped_sql, limit_applied = _apply_row_limit(expr, max_rows)
    probe_sql, _ = _apply_row_limit(expr, max_rows + 1)

    return ValidationResult(
        ok=True,
        sql=capped_sql,
        original_sql=raw,
        tables_used=tables_used,
        limit_applied=limit_applied,
        probe_sql=probe_sql,
    )


def _strip_code_fence(sql: str) -> str:
    text = sql.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # 去掉结尾分号，避免被判定为多语句
    return text.rstrip(";").strip()


def _apply_row_limit(expr: exp.Expression, max_rows: int) -> tuple[str, int | None]:
    """给查询强制加上（或收紧）LIMIT，返回 (SQL, 实际生效上限)。"""
    if isinstance(expr, exp.Select):
        existing = expr.args.get("limit")
        if existing is None:
            return expr.limit(max_rows).sql(dialect="duckdb"), max_rows
        raw_value = existing.expression
        current: int | None = None
        if isinstance(raw_value, exp.Literal) and not raw_value.is_string:
            try:
                current = int(raw_value.this)
            except (TypeError, ValueError):
                current = None
        if current is not None and 0 <= current <= max_rows:
            return expr.sql(dialect="duckdb"), current
        return expr.limit(max_rows).sql(dialect="duckdb"), max_rows

    # UNION / INTERSECT 等：外层再包一层，保证上限一定生效
    wrapped = f"SELECT * FROM ({expr.sql(dialect='duckdb')}) AS _capped LIMIT {max_rows}"
    return wrapped, max_rows


# ---------------------------------------------------------------------- #
# 执行
# ---------------------------------------------------------------------- #
def execute_sql(
    db: DuckDBManager,
    sql: str,
    allowed_tables: list[str],
    max_rows: int = 500,
) -> dict[str, Any]:
    """校验后执行。任何一步失败都会抛异常，由 Agent 的修复节点接管。"""
    result = validate_sql(sql, allowed_tables, max_rows=max_rows)
    if not result.ok:
        raise SQLSecurityError(result.reason, error_type=result.error_type)

    try:
        data = db.execute(result.probe_sql, max_rows=max_rows)
    except SQLExecutionTimeout as exc:
        raise SQLSecurityError(str(exc), error_type="timeout") from exc
    except Exception as exc:  # DuckDB 的 Binder/Parser 错误
        raise SQLSecurityError(
            f"{type(exc).__name__}: {exc}", error_type=classify_error(str(exc))
        ) from exc

    data["executed_sql"] = result.sql
    data["tables_used"] = result.tables_used
    data["limit_applied"] = result.limit_applied
    return data


def classify_error(message: str) -> str:
    """把数据库报错归类，便于修复节点给出针对性的提示。"""
    msg = message.lower()
    if "did not find column" in msg or "column with name" in msg or "referenced column" in msg:
        return "unknown_column"
    if "table with name" in msg or "does not exist" in msg:
        return "unknown_table"
    if "binder error" in msg:
        return "binder_error"
    if "parser error" in msg or "syntax error" in msg:
        return "syntax"
    if "conversion error" in msg or "cast" in msg:
        return "type_mismatch"
    if "ambiguous" in msg:
        return "ambiguous_column"
    if "interrupted" in msg or "timeout" in msg:
        return "timeout"
    return "execution_error"


def suggest_columns_from_error(message: str, schema: dict[str, Any]) -> list[str]:
    """从 DuckDB 的 Candidate bindings 提示里抽取候选字段，供修复节点直接使用。"""
    candidates: list[str] = []
    if "candidate bindings" in message.lower():
        tail = message.split(":", 1)[-1]
        for token in tail.replace('"', " ").replace(",", " ").split():
            token = token.strip()
            if token and token.replace("_", "").isalnum() and token not in candidates:
                candidates.append(token)
    if candidates:
        return candidates
    # 兜底：返回全部字段名，交给 LLM 判断
    names: list[str] = []
    for meta in schema.get("tables", {}).values():
        names.extend(c["name"] for c in meta["columns"])
    return names
