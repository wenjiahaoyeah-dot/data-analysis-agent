"""Local durable LangGraph checkpoints and per-task process locks."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import settings


class CheckpointUnavailable(RuntimeError):
    """The task has no pending graph step that can be resumed."""


class TaskBusy(RuntimeError):
    """Another request is currently running or resuming this task."""


def checkpoint_thread_id(owner_id: str, dataset_id: str, task_id: str) -> str:
    parts = (owner_id, dataset_id, task_id)
    return hashlib.sha256('\0'.join(parts).encode('utf-8')).hexdigest()


@contextmanager
def task_lock(task_id: str):
    # Shared storage + flock protects simultaneous retries across local workers.
    import fcntl
    if not re.fullmatch(r'[0-9a-f]{12}', task_id):
        raise ValueError('无效任务 ID')
    folder = settings.storage_dir / 'checkpoint_locks'
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / (task_id + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as exc:
            raise TaskBusy('该任务正在执行，请稍后再试') from exc
        yield
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def open_checkpointer():
    """One SQLite connection per invocation; persist under the mounted storage dir."""
    folder = Path(settings.storage_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'checkpoints.sqlite3'
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    try:
        # The graph state is plain dict/list/scalars; do not allow arbitrary class loading.
        serde = JsonPlusSerializer(allowed_json_modules=(), allowed_msgpack_modules=())
        yield SqliteSaver(conn, serde=serde)
    finally:
        conn.close()
