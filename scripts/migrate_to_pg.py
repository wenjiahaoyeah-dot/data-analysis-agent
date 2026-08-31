"""把本地 JSON 控制面数据迁移到 PostgreSQL。

用法：
    python scripts/migrate_to_pg.py --dsn postgresql://... --dry-run
    python scripts/migrate_to_pg.py --dsn postgresql://...

顺序：users -> datasets -> tasks -> reports -> metrics -> query_logs
逐条打印，遇到单条失败只记录并继续（迁移不应该因为一条脏数据全盘中断）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.repository.base import GLOBAL_OWNER  # noqa: E402
from app.repository.jsonfile import JsonRepository  # noqa: E402
from app.repository.models import ANONYMOUS_USER  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True, help="目标 PostgreSQL DSN")
    parser.add_argument("--schema", default="public")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args()

    src = JsonRepository()
    dst = None
    if not args.dry_run:
        from app.repository.postgres import PostgresRepository

        dst = PostgresRepository(args.dsn, schema=args.schema)

    stats = {"users": 0, "datasets": 0, "tasks": 0, "reports": 0, "metrics": 0, "logs": 0}
    errors: list[str] = []

    def _user(owner: str) -> None:
        if not owner:
            return
        if dst is not None:
            dst.ensure_user(owner, owner)
        stats["users"] += 1

    datasets = src.list_datasets(limit=10000)
    tasks = src.list_tasks(limit=100000)
    metrics = src.list_metrics(owner_id=None)
    audits = []
    for ds in datasets:
        try:
            audits.extend(src.list_audits(dataset_id=ds.dataset_id, limit=10000))
        except Exception:
            pass

    print(f"源（JSON）: 数据集 {len(datasets)} / 任务 {len(tasks)} / 指标 {len(metrics)} / 审计 {len(audits)}")
    if args.dry_run:
        print("dry-run：不写入目标库")
        return 0

    for ds in datasets:
        try:
            _user(ds.owner_id)
            dst.save_dataset(ds)
            stats["datasets"] += 1
        except Exception as exc:
            errors.append(f"dataset {ds.dataset_id}: {exc}")

    for task in tasks:
        try:
            _user(task.get("owner_id") or ANONYMOUS_USER)
            dst.save_task(task)
            stats["tasks"] += 1
            markdown = src.get_report(task["task_id"])
            if markdown:
                dst.save_report(task["task_id"], markdown)
                stats["reports"] += 1
        except Exception as exc:
            errors.append(f"task {task.get('task_id')}: {exc}")

    for metric in metrics:
        try:
            _user(metric.owner_id if metric.owner_id != GLOBAL_OWNER else "")
            dst.upsert_metric(metric)
            stats["metrics"] += 1
        except Exception as exc:
            errors.append(f"metric {metric.name}: {exc}")

    for record in audits:
        try:
            dst.append_audit(record)
            stats["logs"] += 1
        except Exception as exc:
            errors.append(f"audit {record.task_id}: {exc}")

    print("\n迁移完成：")
    for key, value in stats.items():
        print(f"  {key:<9} {value}")
    if errors:
        print(f"\n{len(errors)} 条失败（已跳过）：")
        for err in errors[:10]:
            print(f"  - {err}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
