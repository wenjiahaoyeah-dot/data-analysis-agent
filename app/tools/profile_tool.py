"""数据画像工具：统计缺失值、重复值、基数、数值分布和枚举取值。

作用有两个：
1. 上传后立刻给用户一份「数据体检报告」。
2. 把低基数字段的枚举值注入 Prompt，让 LLM 知道 region 里到底有「华东/华南」，
   而不是凭空写出 WHERE region = 'East China'。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.database.duckdb_manager import DuckDBManager

MAX_ENUM_CARDINALITY = 30
MAX_ENUM_SAMPLES = 8
MAX_RANGE_COLUMNS = 6


def profile_dataframe(df: pd.DataFrame, table_name: str) -> dict[str, Any]:
    """基于 DataFrame 的画像（上传时调用，无需再查库）。"""
    columns: list[dict[str, Any]] = []
    for col in df.columns:
        series = df[col]
        null_count = int(series.isna().sum())
        distinct = int(series.nunique(dropna=True))
        item: dict[str, Any] = {
            "name": str(col),
            "dtype": str(series.dtype),
            "null_count": null_count,
            "null_ratio": round(null_count / max(len(df), 1), 4),
            "distinct_count": distinct,
        }
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            desc = series.describe()  # pandas 3.x 的 Series.describe 不再接受 numeric_only
            item["stats"] = {
                "min": _num(desc.get("min")),
                "max": _num(desc.get("max")),
                "mean": _num(desc.get("mean")),
            }
        elif pd.api.types.is_datetime64_any_dtype(series):
            item["stats"] = {
                "min": str(series.min()),
                "max": str(series.max()),
            }
        if distinct and distinct <= MAX_ENUM_CARDINALITY and not pd.api.types.is_numeric_dtype(series):
            item["samples"] = [str(v) for v in series.dropna().unique()[:MAX_ENUM_SAMPLES]]
        columns.append(item)

    duplicate_rows = int(df.duplicated().sum())
    return {
        "table_name": table_name,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "duplicate_rows": duplicate_rows,
        "columns": columns,
    }


def profile_database(db: DuckDBManager) -> dict[str, Any]:
    """从 DuckDB 侧生成画像，供 Prompt 注入（只取枚举值和时间/数值范围）。"""
    schema = db.get_schema()
    result: dict[str, Any] = {"tables": {}}
    for table, meta in schema.items():
        value_samples: dict[str, list[str]] = {}
        ranges: dict[str, dict[str, Any]] = {}
        numeric_used = 0
        for col in meta["columns"]:
            name, ctype = col["name"], col["type"].upper()
            try:
                if any(k in ctype for k in ("VARCHAR", "CHAR", "TEXT", "STRING")):
                    rows = db.execute(
                        f'SELECT DISTINCT "{name}" FROM "{table}" '
                        f'WHERE "{name}" IS NOT NULL LIMIT {MAX_ENUM_CARDINALITY + 1}'
                    )["rows"]
                    values = [r[0] for r in rows]
                    # 只注入基数足够小的字段（枚举型），高基数字段没意义
                    if 0 < len(values) <= MAX_ENUM_CARDINALITY:
                        value_samples[name] = [str(v) for v in values[:MAX_ENUM_SAMPLES]]
                elif any(k in ctype for k in ("DATE", "TIMESTAMP", "TIME")):
                    row = db.execute(
                        f'SELECT MIN("{name}"), MAX("{name}") FROM "{table}"'
                    )["rows"][0]
                    if row and row[0] is not None:
                        ranges[name] = {"min": str(row[0]), "max": str(row[1])}
                elif numeric_used < MAX_RANGE_COLUMNS and any(
                    k in ctype for k in ("INT", "DOUBLE", "DECIMAL", "FLOAT", "BIGINT", "HUGEINT")
                ):
                    row = db.execute(
                        f'SELECT MIN("{name}"), MAX("{name}") FROM "{table}"'
                    )["rows"][0]
                    if row and row[0] is not None:
                        ranges[name] = {"min": _num(row[0]), "max": _num(row[1])}
                        numeric_used += 1
            except Exception:
                # 画像失败不应该阻断主流程
                continue
        result["tables"][table] = {
            "row_count": meta["row_count"],
            "value_samples": value_samples,
            "ranges": ranges,
        }
    return result


def _num(value: Any) -> Any:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return round(f, 4)
