"""控制面的 JSON 文件实现。

为什么不让它"重新发明一套存储"？
    现有历史数据在 `storage/datasets/{id}/meta.json` 和 `storage/tasks/{id}.json`，
    直接复用这两个位置，意味着：
      1. 升级后旧数据立即可用，不需要先跑迁移；
      2. scripts/run_eval.py 扫 meta.json 的逻辑不会失配；
      3. 测试用 JSON 后端时零外部依赖，秒级完成。

路径必须**每次操作时动态解析** settings.storage_dir —— conftest 会在 session
级把 storage_dir 指到临时目录，如果在 __init__ 时缓存路径就会写到真实目录。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.repository.base import GLOBAL_OWNER, RepositoryConflict, RepositoryUnavailable
from app.repository.models import (
    ANONYMOUS_USER,
    AuditRecord,
    DatasetMeta,
    Metric,
    QAExample,
    User,
)

_CACHE_MAX = 200


class JsonRepository:
    backend = "json"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, Any] = OrderedDict()

    # ------------------------------------------------------------------ #
    # 路径（动态解析，绝不缓存）
    # ------------------------------------------------------------------ #
    def _root(self) -> Path:
        return Path(settings.storage_dir)

    def _datasets_dir(self) -> Path:
        return self._root() / "datasets"

    def _tasks_dir(self) -> Path:
        return self._root() / "tasks"

    def _repo_dir(self) -> Path:
        path = self._root() / "repository"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _dataset_meta(self, dataset_id: str) -> Path:
        return self._datasets_dir() / dataset_id / "meta.json"

    def _task_file(self, task_id: str) -> Path:
        return self._tasks_dir() / f"{task_id}.json"

    def _report_file(self, task_id: str) -> Path:
        return self._tasks_dir() / f"{task_id}.md"

    def _conversation_file(self, session_id: str) -> Path:
        if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
            raise ValueError("无效会话 ID")
        return self._root() / "conversations" / f"{session_id}.json"

    def get_conversation(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read_json(self._conversation_file(session_id), None)

    def save_conversation(self, conversation: dict[str, Any], expected_version: int) -> None:
        with self._lock:
            path = self._conversation_file(conversation["session_id"])
            old = self._read_json(path, None)
            if (old.get("version", 0) if old else 0) != expected_version:
                raise RepositoryConflict("会话已被其他请求更新，请重试")
            payload = copy.deepcopy(conversation)
            payload["version"] = expected_version + 1
            self._write_json(path, payload)

    def list_conversations(self, owner_id: str, dataset_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            directory = self._root() / "conversations"
            items = [self._read_json(path, None) for path in directory.glob("*.json")] if directory.exists() else []
            items = [item for item in items if item and item.get("owner_id") == owner_id
                     and item.get("dataset_id") == dataset_id]
            items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
            return copy.deepcopy(items[:max(0, limit)])

    def delete_conversation(self, session_id: str, owner_id: str) -> bool:
        with self._lock:
            path = self._conversation_file(session_id)
            item = self._read_json(path, None)
            if not item or item.get("owner_id") != owner_id:
                return False
            path.unlink(missing_ok=True)
            return True

    def _collection(self, name: str) -> Path:
        return self._repo_dir() / f"{name}.json"

    def cache_key(self) -> str:
        return f"json::{self._root()}"

    def close(self) -> None:
        self._cache.clear()

    def ping(self) -> bool:
        try:
            self._repo_dir()
            return True
        except OSError as exc:  # pragma: no cover
            raise RepositoryUnavailable(f"存储目录不可写：{exc}") from exc

    # ------------------------------------------------------------------ #
    # 低层读写
    # ------------------------------------------------------------------ #
    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return copy.deepcopy(default)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return copy.deepcopy(default)

    def _write_json(self, path: Path, data: Any) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)  # 原子替换，避免半截文件

    def _cached(self, key: str, loader):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return copy.deepcopy(self._cache[key])
            value = loader()
            self._cache[key] = copy.deepcopy(value)
            if len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)
            return value

    def _invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._cache.clear()
            else:
                self._cache.pop(key, None)

    # ------------------------------------------------------------------ #
    # 用户
    # ------------------------------------------------------------------ #
    def ensure_user(self, user_id: str, display_name: str = "") -> User:
        with self._lock:
            users = self._read_json(self._collection("users"), {})
            if user_id in users:
                return User.from_dict(users[user_id])
            user = User(id=user_id, display_name=display_name or user_id)
            users[user_id] = user.to_dict()
            self._write_json(self._collection("users"), users)
            return user

    def get_user(self, user_id: str) -> User | None:
        users = self._read_json(self._collection("users"), {})
        data = users.get(user_id)
        return User.from_dict(data) if data else None

    # ------------------------------------------------------------------ #
    # 数据集
    # ------------------------------------------------------------------ #
    def save_dataset(self, meta: DatasetMeta) -> None:
        self._write_json(self._dataset_meta(meta.dataset_id), meta.to_dict())
        self._invalidate(f"dataset::{meta.dataset_id}")

    def get_dataset(self, dataset_id: str) -> DatasetMeta | None:
        def loader():
            data = self._read_json(self._dataset_meta(dataset_id), None)
            return DatasetMeta.from_dict(data) if data else None

        return self._cached(f"dataset::{dataset_id}", loader)

    def list_datasets(self, owner_id: str | None = None, limit: int = 20) -> list[DatasetMeta]:
        root = self._datasets_dir()
        if not root.exists():
            return []
        items: list[DatasetMeta] = []
        for path in root.iterdir():
            if not path.is_dir():
                continue
            data = self._read_json(path / "meta.json", None)
            if not data:
                continue
            meta = DatasetMeta.from_dict(data)
            if owner_id is not None and meta.owner_id != owner_id:
                continue
            items.append(meta)
        items.sort(key=lambda m: m.created_at, reverse=True)
        return items[:limit]

    def delete_dataset(self, dataset_id: str) -> bool:
        import shutil

        target = self._datasets_dir() / dataset_id
        if not target.exists():
            return False
        shutil.rmtree(target, ignore_errors=True)
        self._invalidate(f"dataset::{dataset_id}")
        return True

    # ------------------------------------------------------------------ #
    # 任务
    # ------------------------------------------------------------------ #
    def save_task(self, task: dict[str, Any]) -> None:
        payload = copy.deepcopy(task)
        payload.setdefault("owner_id", ANONYMOUS_USER)
        self._write_json(self._task_file(task["task_id"]), payload)
        self._invalidate(f"task::{task['task_id']}")

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._cached(
            f"task::{task_id}",
            lambda: self._read_json(self._task_file(task_id), None),
        )

    def list_tasks(
        self,
        limit: int = 20,
        owner_id: str | None = None,
        dataset_id: str | None = None,
    ) -> list[dict[str, Any]]:
        root = self._tasks_dir()
        if not root.exists():
            return []
        files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        items: list[dict[str, Any]] = []
        for path in files:
            data = self._read_json(path, None)
            if not data:
                continue
            if owner_id is not None and data.get("owner_id", ANONYMOUS_USER) != owner_id:
                continue
            if dataset_id is not None and data.get("dataset_id") != dataset_id:
                continue
            items.append(data)
            if len(items) >= limit:
                break
        return items

    # ------------------------------------------------------------------ #
    # 报告
    # ------------------------------------------------------------------ #
    def save_report(self, task_id: str, markdown: str) -> None:
        with self._lock:
            path = self._report_file(task_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(markdown, encoding="utf-8")
            os.replace(tmp, path)
            self._invalidate(f"report::{task_id}")

    def get_report(self, task_id: str) -> str | None:
        path = self._report_file(task_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ #
    # 指标
    # ------------------------------------------------------------------ #
    def list_metrics(self, owner_id: str | None = None) -> list[Metric]:
        raw = self._read_json(self._collection("metrics"), [])
        items = [Metric.from_dict(d) for d in raw]
        if owner_id is None:
            return items
        return [m for m in items if m.owner_id in (owner_id, GLOBAL_OWNER)]

    def upsert_metric(self, metric: Metric) -> Metric:
        with self._lock:
            raw = self._read_json(self._collection("metrics"), [])
            owner = metric.owner_id or ANONYMOUS_USER
            next_id = max((int(d.get("id") or 0) for d in raw), default=0) + 1
            for idx, item in enumerate(raw):
                if item.get("name") == metric.name and item.get("owner_id") == owner:
                    metric.id = item.get("id") or next_id
                    metric.created_at = item.get("created_at") or metric.created_at
                    raw[idx] = metric.to_dict()
                    break
            else:
                metric.id = next_id
                metric.owner_id = owner
                raw.append(metric.to_dict())
            self._write_json(self._collection("metrics"), raw)
            return metric

    def delete_metric(self, name: str, owner_id: str) -> bool:
        with self._lock:
            raw = self._read_json(self._collection("metrics"), [])
            kept = [d for d in raw if not (d.get("name") == name and d.get("owner_id") == owner_id)]
            if len(kept) == len(raw):
                return False
            self._write_json(self._collection("metrics"), kept)
            return True

    # ------------------------------------------------------------------ #
    # 审计
    # ------------------------------------------------------------------ #
    def append_audit(self, record: AuditRecord) -> AuditRecord:
        with self._lock:
            raw = self._read_json(self._collection("audits"), [])
            record.id = max((int(d.get("id") or 0) for d in raw), default=0) + 1
            raw.append(record.to_dict())
            # 只保留最近 2000 条，避免文件无限膨胀
            if len(raw) > 2000:
                raw = raw[-2000:]
            self._write_json(self._collection("audits"), raw)
            return record

    def audit_exists(self, task_id: str, owner_id: str) -> bool:
        with self._lock:
            return any(d.get("task_id") == task_id and d.get("owner_id") == owner_id
                       for d in self._read_json(self._collection("audits"), []))

    def list_audits(
        self,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditRecord]:
        raw = self._read_json(self._collection("audits"), [])
        items = [AuditRecord.from_dict(d) for d in raw]
        if owner_id is not None:
            items = [r for r in items if r.owner_id == owner_id]
        if dataset_id is not None:
            items = [r for r in items if r.dataset_id == dataset_id]
        # created_at 只精确到秒，同秒的多条必须用 id 兜底排序，
        # 否则"最新一条"是不确定的（PG 侧是 ORDER BY created_at DESC, id DESC）
        items.sort(key=lambda r: (r.created_at, r.id or 0), reverse=True)
        return items[:limit]

    def audit_stats(self, owner_id: str | None = None) -> dict[str, Any]:
        records = self.list_audits(owner_id=owner_id, limit=10000)
        return _compute_stats(records)

    # ------------------------------------------------------------------ #
    # few-shot 示例
    # ------------------------------------------------------------------ #
    def add_example(self, example: QAExample) -> QAExample:
        with self._lock:
            raw = self._read_json(self._collection("qa_examples"), [])
            example.id = max((int(d.get("id") or 0) for d in raw), default=0) + 1
            raw.append(example.to_dict())
            if len(raw) > 1000:
                raw = raw[-1000:]
            self._write_json(self._collection("qa_examples"), raw)
            return example

    def find_examples(
        self,
        dataset_signature: str,
        metric_name: str | None = None,
        limit: int = 3,
    ) -> list[QAExample]:
        raw = self._read_json(self._collection("qa_examples"), [])
        items = [QAExample.from_dict(d) for d in raw]
        items = [e for e in items if e.success and e.dataset_signature == dataset_signature]
        if metric_name:
            preferred = [e for e in items if e.metric_name == metric_name]
            items = preferred or items
        return items[-limit:]


def _compute_stats(records: list[AuditRecord]) -> dict[str, Any]:
    import math

    total = len(records)
    if total == 0:
        return {
            "total": 0,
            "success": 0,
            "failed": 0,
            "needs_clarification": 0,
            "success_rate": 0.0,
            "avg_elapsed_ms": 0.0,
            "p95_elapsed_ms": 0.0,
            "slow_queries": 0,
            "by_error_type": {},
        }
    success = sum(1 for r in records if r.status == "success")
    failed = sum(1 for r in records if r.status == "failed")
    pending = sum(1 for r in records if r.status == "needs_clarification")
    durations = sorted(r.elapsed_ms for r in records)
    avg = sum(durations) / total
    # nearest-rank 法：n=2 时 P95 应等于最大值，而不是最小值
    p95_idx = min(total - 1, max(0, math.ceil(0.95 * total) - 1))
    by_error: dict[str, int] = {}
    for r in records:
        if r.error_type:
            by_error[r.error_type] = by_error.get(r.error_type, 0) + 1
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "needs_clarification": pending,
        "success_rate": round(success / (success + failed), 4) if success + failed else 0.0,
        "avg_elapsed_ms": round(avg, 2),
        "p95_elapsed_ms": round(durations[p95_idx], 2),
        "slow_queries": sum(1 for d in durations if d > 5000),
        "by_error_type": by_error,
    }


def _now() -> str:  # pragma: no cover - 保留给未来扩展
    return datetime.now().isoformat(timespec="seconds")
