"""指标语义层测试：命中判定、口径校验、Prompt 渲染。

重点不是"能命中"，而是"不误报"——口径校验一旦误报，用户就不会再看它。
"""
from __future__ import annotations

import pytest

from app.repository.models import Metric
from app.tools.metric_tool import (
    check_metric_compliance,
    dataset_signature,
    match_metrics,
    render_metrics_prompt,
)

SALES = Metric(
    name="sales_amount",
    display_name="销售额",
    expression="ROUND(SUM(amount), 2)",
    required_columns=["amount"],
    synonyms=["销售额", "销售金额", "GMV", "成交额"],
    unit="元",
)

QTY = Metric(
    name="total_quantity",
    display_name="销量",
    expression="SUM(quantity)",
    required_columns=["quantity"],
    synonyms=["销量", "销售数量"],
)

ORDERS = Metric(
    name="order_count",
    display_name="订单量",
    expression="COUNT(*)",
    required_columns=["order_id"],
    synonyms=["订单量", "订单数"],
)

METRICS = [SALES, QTY, ORDERS]
COLUMNS = ["order_id", "order_date", "amount", "quantity", "region", "product", "category", "channel"]


# ---------------------------------------------------------------------- #
# 命中判定
# ---------------------------------------------------------------------- #
class TestMatch:
    def test_hits_display_name(self):
        assert [m.name for m in match_metrics("各地区的销售额是多少", METRICS)] == ["sales_amount"]

    def test_hits_synonym(self):
        assert [m.name for m in match_metrics("本月GMV是多少", METRICS)] == ["sales_amount"]

    def test_no_hit_returns_empty(self):
        assert match_metrics("各地区的订单明细", METRICS) == []

    def test_neutral_word_does_not_match(self):
        # "金额" 太宽泛，不应命中任何指标
        assert match_metrics("查一下金额的分布", METRICS) == []

    def test_long_alias_wins(self):
        # "销售额" 必须命中销售额，而不是被 "销量" 抢走
        matched = match_metrics("销售额是多少", METRICS)
        assert [m.name for m in matched] == ["sales_amount"]

    def test_multiple_metrics(self):
        matched = {m.name for m in match_metrics("订单量和销售额分别是多少", METRICS)}
        assert matched == {"order_count", "sales_amount"}


# ---------------------------------------------------------------------- #
# 口径校验：应当通过的场景
# ---------------------------------------------------------------------- #
class TestPass:
    def test_exact_aggregation_passes(self):
        check = check_metric_compliance(
            "各地区的销售额", "SELECT region, ROUND(SUM(amount), 2) FROM t GROUP BY region", [SALES], COLUMNS
        )
        assert check.status == "pass"

    def test_not_applicable_when_metric_not_mentioned(self):
        check = check_metric_compliance("各地区的订单明细", "SELECT * FROM t", [SALES], COLUMNS)
        assert check.status == "not_applicable"

    def test_empty_metrics_skips(self):
        check = check_metric_compliance("销售额", "SELECT SUM(amount) FROM t", [], COLUMNS)
        assert check.status == "not_applicable"


# ---------------------------------------------------------------------- #
# 口径校验：应当告警但不误报
# ---------------------------------------------------------------------- #
class TestNoFalsePositive:
    def test_single_record_extreme_allowed(self):
        """「最高的一笔订单金额」本来就该用 MAX，不能判成偏离。"""
        check = check_metric_compliance(
            "销售额最高的那一笔订单金额是多少",
            "SELECT MAX(amount) FROM t",
            [SALES],
            COLUMNS,
        )
        assert check.status != "deviation"

    def test_average_downgraded_to_info(self):
        """「平均每笔订单金额」的问题，用 AVG 属于预期，不算偏离。"""
        check = check_metric_compliance(
            "平均每笔订单的销售额是多少",
            "SELECT AVG(amount) FROM t",
            [SALES],
            COLUMNS,
        )
        assert check.status == "info"

    def test_ratio_downgraded_to_info(self):
        check = check_metric_compliance(
            "各类目销售额占比",
            "SELECT category, SUM(amount) FROM t GROUP BY category",
            [SALES],
            COLUMNS,
        )
        assert check.status == "info"

    def test_extra_column_is_info_not_deviation(self):
        """SUM(amount * quantity) 口径确实变了，但不应直接判死。"""
        check = check_metric_compliance(
            "各地区的销售额",
            "SELECT region, SUM(amount * quantity) FROM t GROUP BY region",
            [SALES],
            COLUMNS,
        )
        assert check.status == "info"
        assert any("额外字段" in f["reason"] for f in check.findings)


# ---------------------------------------------------------------------- #
# 口径校验：应当判偏离
# ---------------------------------------------------------------------- #
class TestDeviation:
    def test_wrong_aggregation_is_deviation(self):
        check = check_metric_compliance(
            "各地区的销售额", "SELECT region, AVG(amount) FROM t GROUP BY region", [SALES], COLUMNS
        )
        assert check.status == "deviation"
        assert "SUM" in check.message

    def test_no_aggregation_is_deviation(self):
        check = check_metric_compliance(
            "各地区的销售额", "SELECT region, amount FROM t", [SALES], COLUMNS
        )
        assert check.status == "deviation"
        assert "没有做任何聚合" in check.findings[0]["reason"]

    def test_missing_default_filter_is_deviation(self):
        metric = Metric(
            name="net_sales",
            display_name="净销售额",
            expression="ROUND(SUM(amount), 2)",
            default_filter="status <> 'refunded'",
            required_columns=["amount"],
            synonyms=["净销售额"],
        )
        check = check_metric_compliance(
            "净销售额是多少",
            "SELECT SUM(amount) FROM t",
            [metric],
            COLUMNS + ["status"],
        )
        assert check.status == "deviation"
        assert "未出现" in check.findings[-1]["reason"]

    def test_filter_present_passes(self):
        metric = Metric(
            name="net_sales",
            display_name="净销售额",
            expression="ROUND(SUM(amount), 2)",
            default_filter="status <> 'refunded'",
            required_columns=["amount"],
            synonyms=["净销售额"],
        )
        check = check_metric_compliance(
            "净销售额是多少",
            "SELECT SUM(amount) FROM t WHERE status <> 'refunded'",
            [metric],
            COLUMNS + ["status"],
        )
        assert check.status == "pass"


# ---------------------------------------------------------------------- #
# Prompt 渲染
# ---------------------------------------------------------------------- #
class TestRender:
    def test_empty_metrics_returns_empty_string(self):
        # 这条保证"无指标时 SQL Prompt 与旧版逐字符相同"
        assert render_metrics_prompt([]) == ""

    def test_renders_expression_and_synonyms(self):
        text = render_metrics_prompt([SALES])
        assert "ROUND(SUM(amount), 2)" in text
        assert "销售额" in text
        assert "GMV" in text

    def test_renders_default_filter(self):
        metric = Metric(
            name="net_sales",
            display_name="净销售额",
            expression="SUM(amount)",
            default_filter="status <> 'refunded'",
        )
        assert "status <> 'refunded'" in render_metrics_prompt([metric])


# ---------------------------------------------------------------------- #
# 数据集签名
# ---------------------------------------------------------------------- #
def test_dataset_signature_is_stable():
    tables = [{"table_name": "a", "columns": [{"name": "x"}, {"name": "y"}]}]
    same = [{"table_name": "a", "columns": [{"name": "y"}, {"name": "x"}]}]
    assert dataset_signature(tables) == dataset_signature(same)
    other = [{"table_name": "b", "columns": [{"name": "x"}]}]
    assert dataset_signature(tables) != dataset_signature(other)
