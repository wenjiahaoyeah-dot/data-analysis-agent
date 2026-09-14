"""自主工具 Agent 合成数据开发集；不代表泛化测试集。"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.repository.factory import reset_repository  # noqa: E402
from app.services.analyze_service import run_analysis  # noqa: E402
from app.services.upload_service import create_dataset, get_db  # noqa: E402
from scripts.run_eval import compare_rows, reference_result  # noqa: E402

CASES = [
    {"id": "schema", "question": "有哪些数据表和字段？只描述表结构，不需要查询统计数值。"},
    {"id": "region_chart", "question": "按地区汇总销售额，返回 region 和 SUM(amount) AS total 两列，并画柱状图。",
     "sql": "SELECT region, SUM(amount) AS total FROM demo_sales GROUP BY region"},
    {"id": "two_queries", "question": "请分别计算两张结果表：地区 region 的销售额 total；渠道 channel 的销售额 total。",
     "sqls": ["SELECT region, SUM(amount) AS total FROM demo_sales GROUP BY region",
              "SELECT channel, SUM(amount) AS total FROM demo_sales GROUP BY channel"]},
    {"id": "clarify", "question": "统计订单数，但请先问我按数据行数还是去重 order_id，等我回答后再算。",
     "followup": "按去重 order_id 统计。", "sql": "SELECT COUNT(DISTINCT order_id) AS total FROM demo_sales"},
    {"id": "unsupported", "question": "这份数据没有成本字段，我想知道利润。数据不够请明确说明，不要猜。"},
]


def match_query(query: dict, expected: list[list], columns: list[str] | None = None,
                strict: bool = True) -> bool:
    """比较查询结果与参考结果。

    strict=True（默认）：模型返回的列必须与题目要求的列**完全一致**——
    用户说了"返回 region 和 total 两列"，多给一列就是没遵守指令，判失败。
    strict=False：允许额外列，仅按指定列投影后比较（澄清题需要容忍质量统计列）。
    """
    data = query.get("data") or []
    if not data:
        return False
    if columns:
        if any(any(column not in row for column in columns) for row in data):
            return False
        if strict and any(set(row.keys()) != set(columns) for row in data):
            return False
        actual = [[row[column] for column in columns] for row in data]
    else:
        actual = [list(row.values()) for row in data]
    return (not query.get("truncated") and
            compare_rows(actual, expected))


def match_distinct_order_id(query: dict, expected: list[list]) -> bool:
    """允许额外质量统计列（严格模式豁免），但必须证明结果列来自指定的去重聚合。"""
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(query.get("sql") or "", dialect="duckdb")
        for item in tree.expressions:
            count = item.find(exp.Count)
            distinct = count.this if count else None
            column = distinct.find(exp.Column) if isinstance(distinct, exp.Distinct) else None
            if column and column.name.lower() == "order_id":
                return match_query(query, expected, [item.alias_or_name], strict=False)
    except (AttributeError, ValueError, sqlglot.errors.ParseError):
        pass
    return False


def grade(case: dict, task: dict, db, followup: dict | None) -> tuple[bool, str]:
    mode = task["mode"]
    case_id = case["id"]
    if case_id == "clarify":
        if task["status"] != "needs_clarification" or not followup:
            return False, "没有先澄清并继续执行"
        expected = reference_result(db, case["sql"])
        return (followup["status"] == "success" and any(match_distinct_order_id(q, expected) for q in followup["queries"]),
                "澄清后按去重口径与参考 SQL 比较")
    if case_id == "unsupported":
        answer = task.get("analysis") or ""
        return task["status"] == "success" and any(w in answer for w in ("无法", "不足", "缺少", "不能")), "识别缺少成本"
    if task["status"] != "success":
        return False, task.get("error") or task["status"]
    if case_id == "schema":
        return ("demo_sales" in (task.get("analysis") or "") and
                (mode != "agent" or any(c["name"] == "inspect_schema" and c["status"] == "ok" for c in task["tool_calls"])),
                "回答包含表名，Agent 需要调用结构工具")
    queries = task.get("queries") or ([task] if task.get("sql") else [])
    if case_id == "region_chart":
        expected = reference_result(db, case["sql"])
        return (any(match_query(q, expected, ["region", "total"]) for q in queries)
                and bool(task.get("chart_figure")), "列必须精确为 region/total，且需有图表")
    expected = [reference_result(db, sql) for sql in case["sqls"]]
    return all(any(match_query(q, rows, cols) for q in queries)
               for rows, cols in zip(expected, [["region", "total"], ["channel", "total"]])), \
        "列必须精确匹配，两组结果各自比较"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", nargs="+", choices=["agent"], default=["agent"])
    parser.add_argument("--case", action="append", choices=[c["id"] for c in CASES])
    parser.add_argument("--output", type=Path, default=ROOT / "storage" / "eval" / "agent_comparison.json")
    args = parser.parse_args()
    if settings.use_mock_llm or settings.llm_model != "deepseek-flash":
        parser.error("需要配置真实 deepseek-flash 模型；不允许 Mock 或其他模型")
    entries = []
    with tempfile.TemporaryDirectory(prefix="daa_eval_v2_") as directory:
        settings.storage_dir = Path(directory)
        settings.repository_backend = "json"
        settings.ensure_dirs()
        reset_repository()
        meta = create_dataset([("demo_sales.csv", (ROOT / "data" / "demo_sales.csv").read_bytes())])
        db = get_db(meta.dataset_id)
        print(f"模型 {settings.llm_model}；合成演示数据；单次运行，样本量小且未隔离调参。", flush=True)
        for case in CASES:
            if args.case and case["id"] not in args.case:
                continue
            for mode in args.modes:
                task = run_analysis(meta.dataset_id, case["question"], mode=mode)
                followup = None
                if case.get("followup") and task["status"] == "needs_clarification":
                    followup = run_analysis(meta.dataset_id, case["followup"], mode=mode, session_id=task.get("session_id"))
                passed, detail = grade(case, task, db, followup)
                item = {"case": case["id"], "mode": mode, "passed": passed, "detail": detail,
                        "status": task["status"], "elapsed_ms": task["elapsed_ms"], "sql": task.get("sql"),
                        "analysis": task.get("analysis"), "error": task.get("error"),
                        "tool_calls": task.get("tool_calls"), "queries": task.get("queries"),
                        "usage": task.get("usage"), "clarification": task.get("clarification"),
                        "followup": followup}
                entries.append(item)
                print(f"{case['id']:14} {mode:8} {'通过' if passed else '未通过'} · {detail} · {task['elapsed_ms']:.0f}ms", flush=True)
        reset_repository()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"model": settings.llm_model, "timestamp": datetime.now().isoformat(),
        "dataset": "data/demo_sales.csv", "limitations": "5 个合成数据开发案例，单次运行，不代表真实场景准确率",
        "results": entries}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"评测明细已保存到 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
