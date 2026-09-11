"""一键端到端验收脚本。

不需要先启动 uvicorn —— 直接用 FastAPI TestClient 跑完整 HTTP 栈，
把「上传 → 预置指标 → 分析 → 口径校验 → 审计 → 租户隔离」全部走一遍。

用法：
    python scripts/demo_smoke.py
    REPOSITORY_BACKEND=postgres POSTGRES_DSN=... python scripts/demo_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

DEMO = ROOT / "data" / "demo_sales.csv"
QUESTIONS = [
    "各地区的销售额分别是多少？",
    "销量排名前五的商品是哪几个？",
    "分析一下各地区的利润率",
]

OK = "  [OK]"
FAIL = "  [!!]"


def section(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> int:
    if not DEMO.exists():
        print("缺少 demo 数据，请先运行：python scripts/make_demo_data.py")
        return 2

    failures = 0
    if settings.require_api_key:
        if len(settings.api_keys) < 2:
            print("认证模式下需要在 API_KEYS 中配置至少两个不同的 Key，才能测试身份隔离。")
            return 2
        key_a, key_b = settings.api_keys[:2]
        if key_a == key_b:
            print("身份隔离测试需要两个不同的 API Key。")
            return 2
    else:
        key_a, key_b = "tenant-a", "tenant-b"
    with TestClient(app) as client:
        section("0. 环境")
        health = client.get("/api/health").json()
        print(f"  控制面后端 : {settings.repository_backend}")
        print(f"  推理引擎   : {health['llm_mode']} ({health['llm_model']})")
        print(f"  指标语义层 : {'开启' if settings.metrics_enabled else '关闭'}")
        print(f"  查询审计   : {'开启' if settings.audit_enabled else '关闭'}")

        section("1. 上传数据集（租户 A）")
        with DEMO.open("rb") as f:
            resp = client.post(
                "/api/upload",
                files=[("files", ("demo_sales.csv", f, "text/csv"))],
                headers={"X-API-Key": key_a},
            )
        if resp.status_code != 200:
            print(f"{FAIL} 上传失败：{resp.status_code} {resp.text[:200]}")
            return 1
        upload = resp.json()
        dataset_id = upload["dataset_id"]
        print(f"{OK} dataset_id={dataset_id} 行数={upload['total_rows']} "
              f"表={[t['table_name'] for t in upload['tables']]}")

        section("2. 预置指标口径")
        seeded = client.post("/api/metrics/seed", headers={"X-API-Key": key_a}).json()
        metrics = client.get("/api/metrics", headers={"X-API-Key": key_a}).json()["items"]
        print(f"{OK} 新增 {seeded['created']} 条，当前可见 {len(metrics)} 条")
        for m in metrics:
            print(f"      {m['display_name']}({m['name']}) = {m['expression']}")

        section("3. 分析任务")
        for i, question in enumerate(QUESTIONS):
            resp = client.post(
                "/api/analyze",
                json={"dataset_id": dataset_id, "question": question},
                headers={"X-API-Key": key_a},
            )
            if resp.status_code != 200:
                print(f"{FAIL} {question} -> HTTP {resp.status_code} {resp.text[:160]}")
                failures += 1
                continue
            task = resp.json()
            check = task.get("metric_check") or {}
            nodes = " > ".join(t["node"] for t in task["trace"])
            print(f"\n  Q: {question}")
            print(f"     状态   : {task['status']}")
            print(f"     SQL    : {(task['sql'] or '')[:110]}")
            print(f"     行数   : {task['row_count']}   耗时: {task['elapsed_ms']} ms")
            print(f"     口径   : {check.get('status')} — {check.get('message')}")
            print(f"     链路   : {nodes}")
            if task["status"] == "failed":
                print(f"     说明   : {task.get('error_type')} — {task.get('error')}")
            expected = "failed" if i == 2 else "success"
            if task["status"] != expected or (i == 2 and task.get("error_type") != "unsupported_question"):
                print(f"{FAIL} 任务状态或拒绝原因不符合预期")
                failures += 1

        section("4. 查询审计")
        stats = client.get("/api/audit/stats", headers={"X-API-Key": key_a}).json()
        print(f"{OK} 总数={stats['total']} 成功={stats['success']} 失败={stats['failed']} "
              f"成功率={stats['success_rate']:.0%}")
        print(f"     平均耗时={stats['avg_elapsed_ms']}ms  P95={stats['p95_elapsed_ms']}ms")
        logs = client.get("/api/audit/logs?limit=5", headers={"X-API-Key": key_a}).json()["items"]
        for row in logs:
            check_status = (row.get("metric_check") or {}).get("status", "-")
            print(f"     - {row['status']:<8} 口径={check_status:<15} {(row['question'] or '')[:34]}")

        section("5. 租户隔离（租户 B 视角）")
        b_datasets = client.get("/api/datasets", headers={"X-API-Key": key_b}).json()["items"]
        b_ids = [d["dataset_id"] for d in b_datasets]
        if dataset_id in b_ids:
            print(f"{FAIL} 租户 B 看到了租户 A 的数据集")
            failures += 1
        else:
            print(f"{OK} 租户 B 的数据集列表为空（看不到 A 的 {dataset_id}）")

        cross = client.post(
            "/api/analyze",
            json={"dataset_id": dataset_id, "question": "销售额"},
            headers={"X-API-Key": key_b},
        )
        if cross.status_code == 404:
            print(f"{OK} 租户 B 拿 A 的 dataset_id 请求 -> 404（不泄露存在性）")
        else:
            print(f"{FAIL} 期望 404，实际 {cross.status_code}")
            failures += 1

    section("结果")
    if failures:
        print(f"  {failures} 项未通过")
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
