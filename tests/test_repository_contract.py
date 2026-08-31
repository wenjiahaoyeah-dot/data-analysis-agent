"""控制面契约测试：同一套用例，在两个后端上跑出等价结果。

这是「抽象是否真的可替换」的证明。默认只跑 JSON（零依赖）；
设置 TEST_POSTGRES_DSN 后自动追加 PostgreSQL，同一批断言再跑一遍。

    pytest tests/test_repository_contract.py -v
    TEST_POSTGRES_DSN=postgresql://daa:daa@localhost:5433/daa pytest tests/test_repository_contract.py -v
"""
from __future__ import annotations

import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.repository.base import GLOBAL_OWNER
from app.repository.jsonfile import JsonRepository
from app.repository.models import (
    AuditRecord,
    DatasetMeta,
    Metric,
    QAExample,
)

PG_DSN = os.getenv("TEST_POSTGRES_DSN", "")
PG_SCHEMA = os.getenv("TEST_POSTGRES_SCHEMA", "daa_test")
MIGRATIONS = Path(__file__).resolve().parent.parent / "scripts" / "migrations"

BACKENDS = ["json"] + (["postgres"] if PG_DSN else [])


def test_json_user_registration_is_atomic(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    repo = JsonRepository()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(repo.ensure_user, [f"user-{i}" for i in range(40)]))
    assert all(repo.get_user(f"user-{i}") is not None for i in range(40))


def _prepare_postgres() -> None:
    """建独立 schema 并执行迁移，测试结束不删（幂等，下次复用）。"""
    import psycopg

    files = sorted(MIGRATIONS.glob("*.sql"))
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{PG_SCHEMA}"')
            cur.execute(f'SET search_path TO "{PG_SCHEMA}"')
            for path in files:
                body = "\n".join(
                    line for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.strip().startswith("--")
                )
                for stmt in body.split(";"):
                    if stmt.strip():
                        cur.execute(stmt)


def _truncate_postgres() -> None:
    import psycopg

    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{PG_SCHEMA}"')
            cur.execute(
                "TRUNCATE conversations, query_logs, qa_examples, metrics, reports, tasks, datasets, users "
                "RESTART IDENTITY"
            )


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, monkeypatch):
    if request.param == "json":
        # 每个用例一个独立目录：JSON 后端共用 storage 会导致数据互相污染，
        # 而这正是契约测试最不能容忍的（PG 那边靠 TRUNCATE 达到同样效果）
        from app.config import settings

        target = tmp_path / "json_store"
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, "storage_dir", target)
        settings.ensure_dirs()
        instance = JsonRepository()
        yield instance
        instance.close()
        return

    from app.repository.postgres import PostgresRepository

    _prepare_postgres()
    _truncate_postgres()
    instance = PostgresRepository(PG_DSN, schema=PG_SCHEMA, pool_size=2)
    yield instance
    instance.close()


def _dataset(dataset_id: str, owner: str, created: str) -> DatasetMeta:
    return DatasetMeta(
        dataset_id=dataset_id,
        created_at=created,
        files=["demo.csv"],
        tables=[{"table_name": "demo", "row_count": 3,
                 "columns": [{"name": "a", "type": "BIGINT"}]}],
        profiles=[{"table_name": "demo", "columns": []}],
        warnings=["w1"],
        owner_id=owner,
    )


def test_conversation_roundtrip_and_optimistic_conflict(repo):
    from app.repository.base import RepositoryConflict
    item = {"session_id": "a" * 32, "owner_id": "u_a", "dataset_id": "ds", "turns": [[{"role": "user", "content": "问题"}]]}
    assert repo.get_conversation(item["session_id"]) is None
    repo.save_conversation(item, 0)
    assert len(repo.list_conversations("u_a", "ds")) == 1
    assert repo.list_conversations("other", "ds") == []
    saved = repo.get_conversation(item["session_id"])
    saved["turns"][0][0]["content"] = "续问"
    assert repo.get_conversation(item["session_id"])["turns"][0][0]["content"] == "问题"
    repo.save_conversation(saved, 1)
    with pytest.raises(RepositoryConflict):
        repo.save_conversation(saved, 1)
    assert repo.get_conversation(item["session_id"])["version"] == 2
    assert not repo.delete_conversation(item["session_id"], "other")
    assert repo.delete_conversation(item["session_id"], "u_a")
    assert repo.get_conversation(item["session_id"]) is None


def test_conversation_concurrent_writes_do_not_overwrite(repo):
    import threading
    from app.repository.base import RepositoryConflict
    item = {"session_id": "b" * 32, "owner_id": "u", "dataset_id": "ds", "turns": []}
    repo.save_conversation(item, 0)
    barrier = threading.Barrier(2)
    def update(value):
        attempt = {**item, "turns": [[{"role": "user", "content": value}]]}
        barrier.wait()
        try:
            repo.save_conversation(attempt, 1)
            return True
        except RepositoryConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["first", "second"]))
    assert sorted(results) == [False, True]
    assert repo.get_conversation(item["session_id"])["version"] == 2


# ---------------------------------------------------------------------- #
# 用户
# ---------------------------------------------------------------------- #
def test_ensure_user_is_idempotent(repo):
    first = repo.ensure_user("u_test", "测试用户")
    second = repo.ensure_user("u_test", "另一个名字")
    assert first.id == second.id == "u_test"
    assert repo.get_user("u_test") is not None
    assert repo.get_user("nobody") is None


# ---------------------------------------------------------------------- #
# 数据集
# ---------------------------------------------------------------------- #
def test_dataset_roundtrip(repo):
    repo.save_dataset(_dataset("ds1", "u_a", "2026-01-01T00:00:00"))
    loaded = repo.get_dataset("ds1")
    assert loaded is not None
    assert loaded.dataset_id == "ds1"
    assert loaded.owner_id == "u_a"
    assert loaded.files == ["demo.csv"]
    assert loaded.tables[0]["row_count"] == 3
    assert loaded.total_rows == 3
    assert isinstance(loaded.created_at, str)


def test_get_missing_dataset_returns_none(repo):
    assert repo.get_dataset("does-not-exist") is None


def test_list_datasets_sorted_and_filtered(repo):
    repo.save_dataset(_dataset("old", "u_a", "2026-01-01T00:00:00"))
    repo.save_dataset(_dataset("new", "u_a", "2026-03-01T00:00:00"))
    repo.save_dataset(_dataset("other", "u_b", "2026-02-01T00:00:00"))

    all_items = repo.list_datasets()
    assert [m.dataset_id for m in all_items][:3] == ["new", "other", "old"]

    mine = repo.list_datasets(owner_id="u_a")
    assert [m.dataset_id for m in mine] == ["new", "old"]


def test_dataset_returns_deep_copy(repo):
    repo.save_dataset(_dataset("ds_copy", "u_a", "2026-01-01T00:00:00"))
    loaded = repo.get_dataset("ds_copy")
    loaded.files.append("污染字段.csv")
    assert repo.get_dataset("ds_copy").files == ["demo.csv"]


def test_delete_dataset(repo):
    repo.save_dataset(_dataset("ds_del", "u_a", "2026-01-01T00:00:00"))
    assert repo.delete_dataset("ds_del") is True
    assert repo.get_dataset("ds_del") is None
    assert repo.delete_dataset("ds_del") is False


# ---------------------------------------------------------------------- #
# 任务
# ---------------------------------------------------------------------- #
def _task(task_id: str, dataset_id: str, owner: str, created: str) -> dict:
    return {
        "task_id": task_id,
        "dataset_id": dataset_id,
        "owner_id": owner,
        "question": "各地区的销售额",
        "status": "success",
        "sql": "SELECT 1",
        "columns": ["a"],
        "data": [{"a": 1}],
        "row_count": 1,
        "elapsed_ms": 12.5,
        "retry_count": 0,
        "created_at": created,
    }


def test_task_roundtrip_keeps_all_fields(repo):
    task = _task("t1", "ds1", "u_a", "2026-01-01T00:00:00")
    repo.save_task(task)
    loaded = repo.get_task("t1")
    assert loaded["sql"] == "SELECT 1"
    assert loaded["data"] == [{"a": 1}]
    assert loaded["elapsed_ms"] == 12.5
    assert loaded["owner_id"] == "u_a"


def test_task_upsert_overwrites(repo):
    task = _task("t2", "ds1", "u_a", "2026-01-01T00:00:00")
    repo.save_task(task)
    task["status"] = "failed"
    repo.save_task(task)
    assert repo.get_task("t2")["status"] == "failed"


def test_list_tasks_filtered(repo):
    repo.save_task(_task("ta", "ds1", "u_a", "2026-01-01T00:00:00"))
    repo.save_task(_task("tb", "ds1", "u_b", "2026-02-01T00:00:00"))
    repo.save_task(_task("tc", "ds2", "u_a", "2026-03-01T00:00:00"))

    assert len(repo.list_tasks(owner_id="u_a")) == 2
    assert [t["task_id"] for t in repo.list_tasks(owner_id="u_a", limit=1)] == ["tc"]
    assert [t["task_id"] for t in repo.list_tasks(dataset_id="ds1")][0] == "tb"


def test_missing_task_returns_none(repo):
    assert repo.get_task("nope") is None


# ---------------------------------------------------------------------- #
# 报告
# ---------------------------------------------------------------------- #
def test_report_roundtrip(repo):
    repo.save_report("t1", "# 标题\n内容")
    assert repo.get_report("t1") == "# 标题\n内容"
    repo.save_report("t1", "# 新标题")
    assert repo.get_report("t1") == "# 新标题"
    assert repo.get_report("none") is None


# ---------------------------------------------------------------------- #
# 指标
# ---------------------------------------------------------------------- #
def _metric(name: str, owner: str = GLOBAL_OWNER) -> Metric:
    return Metric(
        name=name,
        display_name="销售额",
        expression="ROUND(SUM(amount), 2)",
        required_columns=["amount"],
        synonyms=["销售额", "GMV"],
        owner_id=owner,
    )


def test_metric_upsert_is_idempotent(repo):
    first = repo.upsert_metric(_metric("sales_amount"))
    second = repo.upsert_metric(_metric("sales_amount"))
    assert first.name == second.name
    assert len(repo.list_metrics()) == 1


def test_metric_visibility_includes_global(repo):
    repo.upsert_metric(_metric("global_metric", GLOBAL_OWNER))
    repo.upsert_metric(_metric("mine", "u_a"))
    repo.upsert_metric(_metric("other", "u_b"))

    visible = {m.name for m in repo.list_metrics("u_a")}
    assert visible == {"global_metric", "mine"}


def test_delete_metric(repo):
    repo.upsert_metric(_metric("tmp_metric", "u_a"))
    assert repo.delete_metric("tmp_metric", "u_a") is True
    assert repo.delete_metric("tmp_metric", "u_a") is False


# ---------------------------------------------------------------------- #
# 审计
# ---------------------------------------------------------------------- #
def _audit(task_id: str, owner: str, status: str, elapsed: float, created: str) -> AuditRecord:
    return AuditRecord(
        task_id=task_id,
        dataset_id="ds1",
        owner_id=owner,
        question="q",
        sql="SELECT 1",
        status=status,
        error_type=None if status == "success" else "unknown_column",
        retry_count=0,
        row_count=10,
        elapsed_ms=elapsed,
        created_at=created,
    )


def test_append_and_list_audits(repo):
    repo.append_audit(_audit("t1", "u_a", "success", 10.0, "2026-01-01T00:00:01"))
    repo.append_audit(_audit("t2", "u_a", "failed", 20.0, "2026-01-01T00:00:02"))
    repo.append_audit(_audit("t3", "u_b", "success", 30.0, "2026-01-01T00:00:03"))

    mine = repo.list_audits(owner_id="u_a")
    assert len(mine) == 2
    assert mine[0].task_id == "t2"  # 倒序
    assert all(r.owner_id == "u_a" for r in mine)


def test_audit_stats(repo):
    repo.append_audit(_audit("t1", "u_a", "success", 10.0, "2026-01-01T00:00:01"))
    repo.append_audit(_audit("t2", "u_a", "failed", 20.0, "2026-01-01T00:00:02"))
    repo.append_audit(_audit("t3", "u_a", "success", 30.0, "2026-01-01T00:00:03"))

    stats = repo.audit_stats("u_a")
    assert stats["total"] == 3
    assert stats["success"] == 2
    assert stats["failed"] == 1
    assert stats["success_rate"] == round(2 / 3, 4)
    assert stats["avg_elapsed_ms"] == 20.0
    assert stats["by_error_type"] == {"unknown_column": 1}


def test_audit_stats_empty(repo):
    stats = repo.audit_stats("nobody")
    assert stats["total"] == 0
    assert stats["success_rate"] == 0.0


def test_clarification_is_not_counted_as_failure(repo):
    repo.append_audit(AuditRecord(task_id="a", dataset_id="ds", owner_id="u_pending", question="问题", status="success"))
    repo.append_audit(AuditRecord(task_id="b", dataset_id="ds", owner_id="u_pending", question="问题", status="needs_clarification"))
    stats = repo.audit_stats("u_pending")
    assert stats["total"] == 2 and stats["failed"] == 0
    assert stats["needs_clarification"] == 1 and stats["success_rate"] == 1.0


# ---------------------------------------------------------------------- #
# few-shot 示例
# ---------------------------------------------------------------------- #
def test_examples(repo):
    repo.add_example(QAExample(question="q1", sql="SELECT 1", dataset_signature="sig1",
                               metric_name="sales_amount", dimensions=["region"]))
    repo.add_example(QAExample(question="q2", sql="SELECT 2", dataset_signature="sig1",
                               metric_name="order_count"))
    repo.add_example(QAExample(question="q3", sql="SELECT 3", dataset_signature="sig2"))

    sig1 = repo.find_examples("sig1")
    assert {e.question for e in sig1} == {"q1", "q2"}

    preferred = repo.find_examples("sig1", metric_name="order_count")
    assert [e.question for e in preferred] == ["q2"]

    assert repo.find_examples("unknown") == []
