"""预置默认指标（幂等）。

用法：
    python scripts/seed_metrics.py
    python scripts/seed_metrics.py --owner u_abc123   # 写给指定租户
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.repository.base import GLOBAL_OWNER  # noqa: E402
from app.repository.factory import get_repository  # noqa: E402
from app.services.metric_service import seed_default_metrics  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default=GLOBAL_OWNER, help="归属租户，默认 * 表示全局")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的同名指标")
    args = parser.parse_args()

    repo = get_repository()
    created = seed_default_metrics(owner_id=args.owner, overwrite=args.overwrite)
    metrics = repo.list_metrics(owner_id=None)

    print(f"后端 {repo.backend}：新增 {created} 条，当前共 {len(metrics)} 条")
    for m in metrics:
        print(f"  - {m.display_name}({m.name}) = {m.expression}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
