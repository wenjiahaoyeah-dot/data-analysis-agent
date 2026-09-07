"""指标语义层服务：读取、预置、渲染。"""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.repository.base import GLOBAL_OWNER
from app.repository.factory import get_repository
from app.repository.models import Metric

# 面向 demo 电商数据的默认指标。seed 是幂等的：已存在同名指标就不覆盖，
# 避免把用户手动改过的口径又冲掉。
DEFAULT_METRICS: list[dict[str, Any]] = [
    {
        "name": "sales_amount",
        "display_name": "销售额",
        "expression": "ROUND(SUM(amount), 2)",
        "description": "直接汇总 amount 字段；不自动排除退款或重复记录",
        "required_columns": ["amount"],
        "unit": "元",
        "synonyms": ["销售额", "销售金额", "销售总额", "GMV", "成交额", "营收"],
    },
    {
        "name": "order_count",
        "display_name": "订单量",
        "expression": "COUNT(*)",
        "description": "订单记录行数；不按订单号去重",
        "required_columns": ["order_id"],
        "unit": "笔",
        "synonyms": ["订单量", "订单数", "订单数量", "单量"],
    },
    {
        "name": "avg_order_value",
        "display_name": "客单价",
        "expression": "ROUND(AVG(amount), 2)",
        "description": "订单记录行的平均金额；不自动去重",
        "required_columns": ["amount"],
        "unit": "元",
        "synonyms": ["客单价", "平均订单金额", "平均消费"],
    },
    {
        "name": "total_quantity",
        "display_name": "销量",
        "expression": "SUM(quantity)",
        "description": "商品件数合计",
        "required_columns": ["quantity"],
        "unit": "件",
        "synonyms": ["销量", "销售数量", "件数"],
    },
]


def load_metrics(owner_id: str | None = None) -> list[Metric]:
    """按开关决定是否返回指标；关闭时返回空列表，Prompt 会自动省略该段。"""
    if not settings.metrics_enabled:
        return []
    try:
        return get_repository().list_metrics(owner_id=owner_id)
    except Exception:
        # 指标读取失败不应阻断分析，降级为"没有指标"
        return []


def seed_default_metrics(owner_id: str = GLOBAL_OWNER, overwrite: bool = False) -> int:
    """写入默认指标，返回新增条数。"""
    repo = get_repository()
    existing = {m.name for m in repo.list_metrics(owner_id=None) if m.owner_id == owner_id}
    created = 0
    for spec in DEFAULT_METRICS:
        if spec["name"] in existing and not overwrite:
            continue
        repo.upsert_metric(Metric(owner_id=owner_id, **spec))
        created += 1
    return created
