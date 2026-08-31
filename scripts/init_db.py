"""初始化 PostgreSQL 控制面：建 schema + 执行所有迁移。

用法：
    python scripts/init_db.py                        # 用 POSTGRES_DSN 环境变量
    python scripts/init_db.py --dsn postgresql://... --schema daa

幂等：所有 DDL 都是 IF NOT EXISTS，可重复执行。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MIGRATIONS = ROOT / "scripts" / "migrations"


def split_statements(sql: str) -> list[str]:
    """按分号切分语句。

    我们的迁移只包含 DDL，没有 PL/pgSQL 函数体，所以简单的分号切分是安全的。
    逐条执行的原因是 psycopg3 默认走 extended protocol，不支持一次提交多语句。
    """
    lines: list[str] = []
    for raw in sql.splitlines():
        stripped = raw.strip()
        if stripped.startswith("--") or not stripped:
            continue
        lines.append(raw)
    body = "\n".join(lines)
    return [stmt.strip() for stmt in body.split(";") if stmt.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.getenv("POSTGRES_DSN", ""))
    parser.add_argument("--schema", default=os.getenv("POSTGRES_SCHEMA", "public"))
    args = parser.parse_args()

    if not args.dsn:
        print("缺少 DSN：请设置 POSTGRES_DSN 或传 --dsn")
        return 2

    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        print("未安装 psycopg，请执行：pip install 'psycopg[binary]'")
        return 2

    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print(f"在 {MIGRATIONS} 没有找到迁移文件")
        return 1

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            if args.schema != "public":
                cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(args.schema)))
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(args.schema)))

            for path in files:
                statements = split_statements(path.read_text(encoding="utf-8"))
                for stmt in statements:
                    cur.execute(stmt)
                print(f"  applied {path.name} ({len(statements)} statements)")

            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (args.schema,),
            )
            tables = [r[0] for r in cur.fetchall()]

    print(f"\n控制面已就绪（schema={args.schema}）：{', '.join(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
