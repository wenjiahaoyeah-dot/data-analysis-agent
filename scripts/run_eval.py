"""开发评测脚本：30 题 Text-to-SQL 与 6 条安全用例。

用法：
    python scripts/run_eval.py            # 需要配置 DeepSeek Flash
    python scripts/run_eval.py --limit 5  # 只跑前 5 题，调试用

产出：
    storage/eval/eval_result.json   明细
    开发集参与调试，不能直接作为独立测试准确率。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_set import CASES, SECURITY_CASES  # noqa: E402

from app.agent.llm import get_llm  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.analyze_service import run_analysis  # noqa: E402
from app.services.upload_service import create_dataset, get_db  # noqa: E402
from app.tools.chart_tool import _jsonify  # noqa: E402
from app.tools.sql_tool import validate_sql  # noqa: E402

REL_TOL = 1e-6


# ---------------------------------------------------------------------- #
# 工具
# ---------------------------------------------------------------------- #
def ensure_dataset() -> str:
    """从仓库中的演示文件新建数据集，避免误用同名但内容不同的历史上传。"""
    csv_path = ROOT / "data" / "demo_sales.csv"
    if not csv_path.exists():
        raise SystemExit("缺少 data/demo_sales.csv，请先运行 scripts/make_demo_data.py")
    meta = create_dataset([("demo_sales.csv", csv_path.read_bytes())])
    return meta.dataset_id


def _cell_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=1e-6)
    return actual == expected


def compare_rows(actual: list[list[Any]], expected: list[list[Any]]) -> bool:
    """比较完整行的多重集：保留字段顺序与标签-数值关系，忽略行顺序。"""
    if len(actual) != len(expected):
        return False
    # 用二分图匹配处理重复行和浮点误差，避免贪心匹配造成误判。
    matches: dict[int, int] = {}

    def assign(index: int, visited: set[int]) -> bool:
        for j, row in enumerate(expected):
            if j in visited or len(actual[index]) != len(row):
                continue
            if not all(_cell_equal(a, b) for a, b in zip(actual[index], row)):
                continue
            visited.add(j)
            if j not in matches or assign(matches[j], visited):
                matches[j] = index
                return True
        return False

    return all(assign(i, set()) for i in range(len(actual)))


def reference_result(db, sql: str) -> list[list[Any]]:
    data = db.execute(sql, max_rows=10000)
    if data["truncated"]:
        raise RuntimeError("参考查询结果超过 10000 行，不能完整评测")
    return [[_jsonify(cell) for cell in row] for row in data["rows"]]


def is_expected_rejection(task: dict[str, Any], mode: str = "agent") -> bool:
    """判断"数据不支撑的问题"是否被正确拒绝。

    这是粗略的关键词规则，不能代替人工核对拒答是否符合题意。
    """
    if task.get("status") == "failed" and task.get("error_type") == "unsupported_question":
        return True
    if mode == "agent":
        answer = task.get("analysis") or ""
        return any(w in answer for w in ("无法", "不足", "缺少", "不能", "没有相关"))
    return False


# ---------------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    args = parser.parse_args()

    if settings.use_mock_llm or settings.llm_model != "deepseek-flash":
        parser.error("需要配置真实 deepseek-flash 模型；不允许 Mock 或其他模型")
    # 模型失败时不能悄悄用规则引擎的答案计算真实模型准确率。

    dataset_id = ensure_dataset()
    db = get_db(dataset_id)
    engine = get_llm().name

    cases = CASES[: args.limit] if args.limit else CASES
    print(f"数据集 {dataset_id} · 推理引擎 {engine} · 模式 agent · 题量 {len(cases)}\n")

    rows_out: list[dict[str, Any]] = []
    generated = executed = successful = correct = repaired = 0
    durations: list[float] = []

    for case in cases:
        started = time.perf_counter()
        task = run_analysis(dataset_id, case["question"], mode="agent")
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        durations.append(elapsed)

        has_sql = bool(task.get("sql"))
        exec_ok = task.get("status") == "success"
        generated += int(has_sql)
        executed += int(bool(task.get("queries")))
        successful += int(exec_ok)
        if task.get("retry_count"):
            repaired += 1

        detail = ""
        ref_rows: list[list[Any]] = []
        agent_rows = [list(rec.values()) for rec in task.get("data") or []]
        if case["compare"] == "safe_reject":
            # 网络、数据库等意外错误不等于识别出数据不足。
            ok = is_expected_rejection(task, "agent")
            detail = "正确拒绝" if ok else f"未正确拒绝：{task.get('error_type') or task.get('sql')}"
        elif not exec_ok:
            ok = False
            detail = f"{task.get('error_type')}: {str(task.get('error'))[:80]}"
        else:
            ref_sql = case.get("reference_sql")
            ref_rows = reference_result(db, ref_sql) if ref_sql else []
            ok = not task.get("truncated") and compare_rows(agent_rows, ref_rows)
            detail = "" if ok else f"期望 {ref_rows[:6]} / 实际 {agent_rows[:6]}"

        correct += int(ok)
        verdict = "✅ 正确" if ok else "❌ 错误"
        print(f"[{case['id']:>2}] {verdict} {case['question'][:30]:<32} {elapsed:>7.0f}ms {detail[:60]}")

        rows_out.append(
            {
                "id": case["id"],
                "category": case["category"],
                "question": case["question"],
                "sql": task.get("sql"),
                "reference_sql": case.get("reference_sql"),
                "reference_rows": ref_rows,
                "actual_rows": agent_rows,
                "status": task.get("status"),
                "row_count": task.get("row_count"),
                "retry_count": task.get("retry_count", 0),
                "correct": ok,
                "elapsed_ms": elapsed,
                "detail": detail,
                "error": task.get("error"),
                "error_type": task.get("error_type"),
                "engines": sorted({step["engine"] for step in task.get("trace", []) if step.get("engine")}),
            }
        )

    # ---- 安全用例 ----
    blocked = 0
    security_rows: list[dict[str, Any]] = []
    for item in SECURITY_CASES:
        result = validate_sql(item["sql"], ["demo_sales"], settings.max_result_rows)
        is_blocked = not result.ok
        blocked += int(is_blocked)
        security_rows.append(
            {"name": item["name"], "sql": item["sql"], "blocked": is_blocked,
             "reason": result.reason if not result.ok else ""}
        )

    total = len(cases)
    summary = {
        "engine": engine,
        "mode": "agent",
        "model": settings.llm_model,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": dataset_id,
        "dataset_sha256": hashlib.sha256((ROOT / "data" / "demo_sales.csv").read_bytes()).hexdigest(),
        "comparison": "完整行多重集（列按位置比较）",
        "fallback_to_mock": False,
        "total_cases": total,
        "sql_generation_rate": round(generated / total, 4),
        "sql_execution_rate": round(executed / total, 4),
        "task_success_rate": round(successful / total, 4),
        "result_accuracy": round(correct / total, 4),
        "avg_elapsed_ms": round(statistics.mean(durations), 1) if durations else 0,
        "p95_elapsed_ms": round(sorted(durations)[math.ceil(len(durations) * 0.95) - 1], 1) if durations else 0,
        "auto_repair_triggered": repaired,
        "security_total": len(SECURITY_CASES),
        "security_blocked": blocked,
        "security_block_rate": round(blocked / len(SECURITY_CASES), 4),
        "by_category": {},
    }

    for case in cases:
        cat = case["category"]
        bucket = summary["by_category"].setdefault(cat, {"total": 0, "correct": 0})
        bucket["total"] += 1
    for row in rows_out:
        if row["correct"]:
            summary["by_category"][row["category"]]["correct"] += 1

    out_dir = settings.storage_dir / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    payload = {"summary": summary, "cases": rows_out, "security": security_rows}
    (out_dir / f"eval_{stamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "eval_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print(f"推理引擎            : {summary['engine']}")
    print(f"题量                : {summary['total_cases']}")
    print(f"SQL 生成成功率      : {summary['sql_generation_rate']:.1%}")
    print(f"SQL 执行成功率      : {summary['sql_execution_rate']:.1%}")
    print(f"查询结果正确率      : {summary['result_accuracy']:.1%}")
    print(f"安全拦截率          : {summary['security_block_rate']:.1%} "
          f"({summary['security_blocked']}/{summary['security_total']})")
    print(f"平均响应时间        : {summary['avg_elapsed_ms']} ms")
    print(f"P95 响应时间        : {summary['p95_elapsed_ms']} ms")
    print(f"触发自动修复的题目  : {summary['auto_repair_triggered']}")
    print("-" * 64)
    for cat, bucket in summary["by_category"].items():
        print(f"{cat:<16}: {bucket['correct']}/{bucket['total']}")
    print("=" * 64)
    print(f"明细已写入 {out_dir / 'eval_result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
