"""Schema 工具：从 DuckDB 提取表结构，并渲染成 LLM 友好的 Prompt 文本。

对应方案里的「模块二·Schema 感知」：Agent 在生成 SQL 之前必须先看到真实的
表名、字段名、字段类型、行数，以及低基数字段的枚举值，避免模型凭空编字段。
"""
from __future__ import annotations

from typing import Any

from app.database.duckdb_manager import DuckDBManager

# 常见业务字段的中文语义词典，用于给 LLM 更明确的字段含义（Schema Linking 的一环）
COLUMN_HINTS: dict[str, str] = {
    "order_id": "订单编号，唯一标识一笔订单",
    "order_date": "下单日期",
    "product": "商品名称",
    "product_name": "商品名称",
    "product_id": "商品编号，可与商品维表关联",
    "category": "商品类目",
    "region": "销售区域（省份/大区）",
    "channel": "销售渠道",
    "quantity": "购买数量",
    "unit_price": "成交单价",
    "amount": "订单金额 = 单价 × 数量",
    "customer_id": "客户编号",
    "cost": "商品成本价",
    "launch_date": "商品上市日期",
    "supplier": "供应商",
    "city": "城市",
    "province": "省份",
    "status": "订单状态",
    "user_id": "用户编号",
}


def extract_schema(db: DuckDBManager) -> dict[str, Any]:
    """返回 {"tables": {...}, "table_names": [...], "allowed_tables": [...], "columns_of": {...}}"""
    tables = db.get_schema()
    columns_of = {name: [c["name"] for c in meta["columns"]] for name, meta in tables.items()}
    return {
        "tables": tables,
        "table_names": list(tables.keys()),
        "allowed_tables": list(tables.keys()),
        "columns_of": columns_of,
    }


def render_schema_prompt(schema: dict[str, Any], profile: dict[str, Any] | None = None) -> str:
    """把 schema（+ 数据画像）渲染成 Prompt 中的数据库描述段落。"""
    if not schema or not schema.get("tables"):
        return "（当前数据集没有任何表）"

    lines: list[str] = []
    for table, meta in schema["tables"].items():
        lines.append(f"表名：{table}（共 {meta['row_count']} 行）")
        lines.append("字段：")
        for col in meta["columns"]:
            name, ctype = col["name"], col["type"]
            hint = COLUMN_HINTS.get(name.lower())
            suffix = f"  -- {hint}" if hint else ""
            lines.append(f"  - {name} {ctype}{suffix}")

        if profile and table in profile.get("tables", {}):
            tprofile = profile["tables"][table]
            # 低基数字段的枚举值对 SQL 里的 WHERE 条件极其关键
            for col_name, values in tprofile.get("value_samples", {}).items():
                preview = "、".join(str(v) for v in values[:8])
                lines.append(f"  字段 {col_name} 的取值示例：{preview}")
            for col_name, rng in tprofile.get("ranges", {}).items():
                lines.append(
                    f"  字段 {col_name} 取值范围：{rng.get('min')} ~ {rng.get('max')}"
                )
        lines.append("")

    return "\n".join(lines).strip()


def build_schema_summary(schema: dict[str, Any]) -> dict[str, Any]:
    """给前端展示用的精简结构。"""
    return {
        "tables": [
            {
                "table_name": name,
                "row_count": meta["row_count"],
                "columns": meta["columns"],
            }
            for name, meta in schema.get("tables", {}).items()
        ]
    }
