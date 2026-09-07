"""指标语义层工具：命中判定 + 口径校验 + Prompt 渲染。

为什么需要它？
    同一个"销售额"，模型可能写成 SUM(amount)、SUM(amount*quantity)、
    AVG(amount) 甚至 SUM(amount) - SUM(refund)。SQL 都能跑通，口径却完全不同。
    评测里看到的"执行成功但结果错"绝大多数就是这么来的。

校验策略（按误报率从低到高排列，宁可漏报不可错报）：
    1. 问题没提到受管指标        -> not_applicable，不校验
    2. 问题明确要极值/均值/占比   -> 降级为 info，不算偏离
    3. 聚合函数不同              -> deviation
    4. 聚合列包含额外字段        -> info（可能改变了口径，但不确定）
    5. 指标有默认过滤条件但 SQL 完全没提 -> deviation
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from app.repository.models import Metric

# 这些词出现在问题里时，"销售额"不再指代聚合口径，而是单条记录的属性
EXTREME_WORDS = ("单笔", "一笔", "最高的一笔", "最大的一笔", "最贵", "最便宜")
AVG_WORDS = ("平均", "人均", "均值")
RATIO_WORDS = ("占比", "比例", "百分比", "贡献度")

# 太宽泛的词不能进同义词匹配，否则"金额""数据"会命中一切
NEUTRAL_WORDS = {"金额", "数据", "数值", "指标", "情况", "统计", "分析"}

_AGG_KIND = {
    "Sum": "SUM",
    "Avg": "AVG",
    "Count": "COUNT",
    "Max": "MAX",
    "Min": "MIN",
    "Stddev": "STDDEV",
}


@dataclass
class MetricCheck:
    status: str = "not_applicable"  # not_applicable / pass / info / deviation
    metrics: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "metrics": self.metrics,
            "findings": self.findings,
            "message": self.message,
        }

    @classmethod
    def skipped(cls, reason: str = "") -> "MetricCheck":
        return cls(status="not_applicable", message=reason or "问题未涉及受管指标")


# ---------------------------------------------------------------------- #
# 1. 命中判定
# ---------------------------------------------------------------------- #
def _aliases(metric: Metric) -> list[str]:
    names = [metric.display_name, metric.name, *metric.synonyms]
    cleaned = []
    for name in names:
        text = (name or "").strip()
        if not text or text in NEUTRAL_WORDS:
            continue
        cleaned.append(text)
    # 长别名优先："销售额" 应该先于 "销售" 命中
    return sorted(set(cleaned), key=len, reverse=True)


def match_metrics(question: str, metrics: list[Metric]) -> list[Metric]:
    """返回问题中明确提到的指标。长别名优先，避免短词抢占。"""
    if not question or not metrics:
        return []

    matched: list[Metric] = []
    consumed: list[tuple[int, int]] = []  # 已被更长别名覆盖的区间

    candidates: list[tuple[int, str, Metric]] = []
    for metric in metrics:
        for alias in _aliases(metric):
            for m in re.finditer(re.escape(alias), question):
                candidates.append((len(alias), alias, metric))
                break  # 每个别名只取一次
    candidates.sort(key=lambda x: -x[0])

    used_names: set[str] = set()
    for length, alias, metric in candidates:
        if metric.name in used_names:
            continue
        # 如果该别名完全落在已匹配的更长别名区间里，跳过
        start = question.find(alias)
        if start == -1:
            continue
        end = start + length
        if any(s <= start and end <= e for s, e in consumed):
            continue
        consumed.append((start, end))
        matched.append(metric)
        used_names.add(metric.name)
    return matched


# ---------------------------------------------------------------------- #
# 2. 意图护栏
# ---------------------------------------------------------------------- #
def _intent_guard(question: str) -> str:
    """返回 guard 类型，用于判断是否应该放弃严格校验。"""
    if any(w in question for w in EXTREME_WORDS):
        return "extreme"
    if any(w in question for w in AVG_WORDS):
        return "average"
    if any(w in question for w in RATIO_WORDS):
        return "ratio"
    return ""


# ---------------------------------------------------------------------- #
# 3. 聚合结构提取
# ---------------------------------------------------------------------- #
def _aggregations(sql: str) -> list[dict[str, Any]]:
    sql = sql.strip().rstrip(";")
    if not sql:
        return []
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except Exception:
        return []
    if tree is None:
        return []

    out: list[dict[str, Any]] = []
    for node in tree.find_all(exp.AggFunc):
        kind = _AGG_KIND.get(type(node).__name__)
        if not kind:
            continue
        columns = sorted({c.name.lower() for c in node.find_all(exp.Column) if c.name})
        out.append({"kind": kind, "columns": columns})
    return out


def _has_group_by(sql: str) -> bool:
    try:
        tree = sqlglot.parse_one(sql.strip().rstrip(";"), dialect="duckdb")
    except Exception:
        return False
    return bool(tree.find(exp.Group))


# ---------------------------------------------------------------------- #
# 4. 口径校验
# ---------------------------------------------------------------------- #
def check_metric_compliance(
    question: str,
    sql: str,
    metrics: list[Metric],
    table_columns: list[str] | None = None,
    intent_guard: str | None = None,
) -> MetricCheck:
    """判断模型生成的 SQL 是否遵循了指标口径。

    只做"标注"，绝不改写 SQL、也不改任务状态——自动纠偏会让准确率基线
    无法归因，也讲不清"改了之后到底对不对"。
    """
    matched = match_metrics(question, metrics)
    if not matched:
        return MetricCheck.skipped()
    if not sql:
        return MetricCheck.skipped("没有可校验的 SQL")

    guard = intent_guard if intent_guard is not None else _intent_guard(question)
    has_group = _has_group_by(sql)
    actual_aggs = _aggregations(sql)
    actual_kinds = {a["kind"] for a in actual_aggs}
    lower_sql = sql.lower()
    columns_lower = {c.lower() for c in (table_columns or [])}

    findings: list[dict[str, Any]] = []
    status = "pass"

    for metric in matched:
        expected_aggs = _aggregations(metric.expression)
        expected_kind = expected_aggs[0]["kind"] if expected_aggs else ""
        expected_cols = set(expected_aggs[0]["columns"]) if expected_aggs else set()

        # 护栏 1：问的是极值/单笔，且没有分组 —— 本来就该用 MAX/MIN，不判偏离
        if guard == "extreme" and not has_group:
            findings.append(
                {
                    "metric": metric.name,
                    "level": "info",
                    "reason": "问题在问单笔极值，允许使用 MAX/MIN，不计入口径偏离",
                }
            )
            continue

        # 护栏 2：问的是平均值/占比 —— 口径本身就变了，降级为提示
        if guard in {"average", "ratio"}:
            findings.append(
                {
                    "metric": metric.name,
                    "level": "info",
                    "reason": f"问题要求{('平均值' if guard == 'average' else '占比')}，与指标默认聚合不同属预期",
                }
            )
            continue

        # 情况 A：SQL 完全没有聚合（明细查询），但问题在问指标
        if not actual_aggs:
            findings.append(
                {
                    "metric": metric.name,
                    "level": "deviation",
                    "reason": f"问题涉及指标「{metric.display_name}」，但 SQL 没有做任何聚合",
                }
            )
            status = "deviation"
            continue

        # 情况 B：聚合函数不同 —— 这是最典型的口径偏离
        if expected_kind and expected_kind not in actual_kinds:
            findings.append(
                {
                    "metric": metric.name,
                    "level": "deviation",
                    "reason": f"指标要求 {expected_kind}，SQL 实际使用 {'/'.join(sorted(actual_kinds))}",
                    "expected": metric.expression,
                }
            )
            status = "deviation"
            continue

        # 情况 C：聚合类型对上了，检查聚合列
        same_kind = [a for a in actual_aggs if a["kind"] == expected_kind]
        exact = [a for a in same_kind if set(a["columns"]) == expected_cols]
        if exact:
            findings.append({"metric": metric.name, "level": "pass", "reason": "聚合方式与指标定义一致"})
        else:
            superset = [a for a in same_kind if expected_cols and expected_cols <= set(a["columns"])]
            if superset:
                extra = set(superset[0]["columns"]) - expected_cols
                findings.append(
                    {
                        "metric": metric.name,
                        "level": "info",
                        "reason": f"聚合列包含额外字段 {sorted(extra)}，可能改变了口径",
                        "expected": metric.expression,
                    }
                )
                if status == "pass":
                    status = "info"
            else:
                findings.append(
                    {
                        "metric": metric.name,
                        "level": "deviation",
                        "reason": f"聚合列与指标定义不符：期望 {sorted(expected_cols)}，实际 {same_kind[0]['columns']}",
                        "expected": metric.expression,
                    }
                )
                status = "deviation"

        # 情况 D：指标定义了默认过滤条件，但 SQL 里完全没提那一列
        if metric.default_filter and columns_lower:
            filter_cols = {
                c.lower()
                for c in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", metric.default_filter)
                if c.lower() in columns_lower
            }
            missing = {c for c in filter_cols if c not in lower_sql}
            if missing:
                findings.append(
                    {
                        "metric": metric.name,
                        "level": "deviation",
                        "reason": f"指标口径要求过滤 {sorted(missing)}（{metric.default_filter}），SQL 中未出现",
                        "expected": metric.default_filter,
                    }
                )
                status = "deviation"

    if not findings:
        return MetricCheck(status="pass", metrics=[m.name for m in matched], message="口径校验通过")

    deviations = [f for f in findings if f["level"] == "deviation"]
    if deviations:
        message = f"口径偏离（{len(deviations)} 项）：" + deviations[0]["reason"]
        status = "deviation"
    elif any(f["level"] == "info" for f in findings):
        message = "口径提示：" + next(f["reason"] for f in findings if f["level"] == "info")
        if status == "pass":
            status = "info"
    else:
        message = "口径校验通过"

    return MetricCheck(
        status=status,
        metrics=[m.name for m in matched],
        findings=findings,
        message=message,
    )


# ---------------------------------------------------------------------- #
# 5. Prompt 渲染
# ---------------------------------------------------------------------- #
def render_metrics_prompt(metrics: list[Metric]) -> str:
    """渲染成 Prompt 里的"指标口径定义"段落。

    没有指标时返回空字符串 —— 调用方要保证此时完全不加这一段，
    否则「无指标时 Prompt 与旧版逐字符相同」这条验收就断了。
    """
    if not metrics:
        return ""

    lines = [
        "【指标口径定义】",
        "以下是公司统一的指标定义，回答相关问题时必须严格按此计算，不要自行更改聚合方式：",
    ]
    for metric in metrics:
        unit = f"，单位：{metric.unit}" if metric.unit else ""
        synonyms = "、".join(metric.synonyms) if metric.synonyms else "-"
        lines.append(f"- {metric.display_name}（{metric.name}）= {metric.expression}{unit}")
        if metric.description:
            lines.append(f"  说明：{metric.description}")
        if metric.default_filter:
            lines.append(f"  必须附加过滤条件：{metric.default_filter}")
        lines.append(f"  同义说法：{synonyms}")
    lines.append("")
    return "\n".join(lines)


def dataset_signature(tables: list[dict[str, Any]]) -> str:
    """为数据集生成结构签名，用于 few-shot 检索（不依赖向量库）。"""
    parts = []
    for table in sorted(tables, key=lambda t: t.get("table_name", "")):
        cols = sorted(c["name"] for c in table.get("columns", []))
        parts.append(f"{table.get('table_name', '')}({','.join(cols)})")
    signature = "|".join(parts)
    import hashlib

    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
