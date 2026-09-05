"""API 层租户隔离测试。

验证：不同 API Key 看到的数据集/任务互相不可见，且拿别人的 id 请求返回 404
（而不是 403——不泄露"这个 id 确实存在"）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings

CSV = Path(__file__).resolve().parent.parent / "data" / "demo_sales.csv"


@pytest.fixture()
def client(isolated_storage):
    from app.main import app
    from app.repository.factory import reset_repository

    reset_repository()
    with TestClient(app) as c:
        yield c
    reset_repository()


@pytest.fixture()
def agent_task_stub(monkeypatch):
    """隔离报告与权限测试，不依赖真实模型或旧工作流。"""
    from app.services import analyze_service
    monkeypatch.setattr(settings, "force_mock", False)
    monkeypatch.setattr(settings, "llm_api_key", "test-only")

    def fake_agent(db, meta, question, task_id, owner_id, history, repo):
        return {"status": "success", "messages": [*history, {"role": "user", "content": question},
                    {"role": "assistant", "content": "示例分析结果"}],
                "analysis": "示例分析结果", "query_result": {"columns": [], "rows": []},
                "llm_mode": "stub", "model_name": "stub"}

    monkeypatch.setattr(analyze_service, "run_tool_agent", fake_agent)


def _upload(client: TestClient, key: str) -> str:
    with CSV.open("rb") as f:
        resp = client.post(
            "/api/upload",
            files=[("files", ("demo_sales.csv", f, "text/csv"))],
            headers={"X-API-Key": key},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["dataset_id"]


def test_tenant_isolation_on_datasets(client):
    ds_a = _upload(client, "key-a")

    list_a = client.get("/api/datasets", headers={"X-API-Key": "key-a"}).json()["items"]
    assert ds_a in [i["dataset_id"] for i in list_a]

    list_b = client.get("/api/datasets", headers={"X-API-Key": "key-b"}).json()["items"]
    assert ds_a not in [i["dataset_id"] for i in list_b]


def test_other_tenant_gets_404_not_403(client):
    ds_a = _upload(client, "key-a")

    resp = client.get(f"/api/schema/{ds_a}", headers={"X-API-Key": "key-b"})
    assert resp.status_code == 404

    resp = client.post(
        "/api/analyze",
        json={"dataset_id": ds_a, "question": "销售额是多少"},
        headers={"X-API-Key": "key-b"},
    )
    assert resp.status_code == 404


def test_other_tenant_cannot_read_task_or_report(client, agent_task_stub):
    ds_a = _upload(client, "key-a")
    task = client.post(
        "/api/analyze",
        json={"dataset_id": ds_a, "question": "各地区的销售额"},
        headers={"X-API-Key": "key-a"},
    ).json()
    task_id = task["task_id"]

    assert client.get(f"/api/task/{task_id}", headers={"X-API-Key": "key-a"}).status_code == 200
    assert client.get(f"/api/task/{task_id}", headers={"X-API-Key": "key-b"}).status_code == 404
    assert client.get(f"/api/report/{task_id}", headers={"X-API-Key": "key-b"}).status_code == 404


def test_same_tenant_can_read_own_report(client, agent_task_stub):
    ds_a = _upload(client, "key-a")
    task = client.post(
        "/api/analyze",
        json={"dataset_id": ds_a, "question": "各地区的销售额"},
        headers={"X-API-Key": "key-a"},
    ).json()

    resp = client.get(f"/api/report/{task['task_id']}", headers={"X-API-Key": "key-a"})
    assert resp.status_code == 200
    assert "数据分析报告" in resp.json()["markdown"]

    download = client.get(
        f"/api/report/{task['task_id']}/download", headers={"X-API-Key": "key-a"}
    )
    assert download.status_code == 200
    assert "text/markdown" in download.headers["content-type"]


@pytest.mark.parametrize("first_request", ["view", "download"])
def test_report_is_generated_only_when_requested_and_reused(client, monkeypatch, first_request, agent_task_stub):
    from app.repository.factory import get_repository
    from app.services import report_service

    ds_a = _upload(client, "key-a")
    task = client.post(
        "/api/analyze",
        json={"dataset_id": ds_a, "question": "各地区的销售额"},
        headers={"X-API-Key": "key-a"},
    ).json()
    assert task["status"] == "success"
    task_id = task["task_id"]
    repo = get_repository()
    assert repo.get_report(task_id) is None

    # 未授权的读取不能触发报告生成。
    assert client.get(f"/api/report/{task_id}", headers={"X-API-Key": "key-b"}).status_code == 404
    assert repo.get_report(task_id) is None

    calls = []
    original = report_service.build_markdown

    def counted_build(task_data):
        calls.append(task_data["task_id"])
        return original(task_data)

    monkeypatch.setattr(report_service, "build_markdown", counted_build)
    suffix = "/download" if first_request == "download" else ""
    first = client.get(f"/api/report/{task_id}{suffix}", headers={"X-API-Key": "key-a"})
    assert first.status_code == 200
    markdown = first.text if first_request == "download" else first.json()["markdown"]
    assert "数据分析报告" in markdown
    assert repo.get_report(task_id) == markdown

    # 预览和下载都复用相同的已保存报告，不重复生成。
    assert client.get(f"/api/report/{task_id}", headers={"X-API-Key": "key-a"}).json()["markdown"] == markdown
    assert client.get(f"/api/report/{task_id}/download", headers={"X-API-Key": "key-a"}).text == markdown
    assert calls == [task_id]


def test_anonymous_tenant_isolated_from_keyed(client):
    ds_anon = _upload(client, "")
    ds_keyed = _upload(client, "key-c")
    assert ds_anon != ds_keyed

    anon_items = client.get("/api/datasets").json()["items"]
    ids = [i["dataset_id"] for i in anon_items]
    assert ds_anon in ids
    assert ds_keyed not in ids


def test_required_key_must_be_authorized(client, monkeypatch):
    monkeypatch.setattr(settings, "require_api_key", True)
    monkeypatch.setattr(settings, "api_keys", ["authorized-a", "authorized-b"])
    assert client.get("/api/datasets").status_code == 401
    assert client.get("/api/datasets", headers={"X-API-Key": "arbitrary"}).status_code == 401
    assert client.get("/api/datasets", headers={"X-API-Key": "authorized-a"}).status_code == 200
    assert client.get("/api/datasets", headers={"X-API-Key": "authorized-b"}).status_code == 200


def test_upload_enforces_combined_size_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    files = [("files", ("a.csv", b"x" * 600_000, "text/csv")),
             ("files", ("b.csv", b"x" * 600_000, "text/csv"))]
    assert client.post("/api/upload", files=files).status_code == 413


def test_graph_endpoint_exposes_only_tool_agent(client):
    graph = client.get("/api/agent/graph").json()
    assert graph["nodes"] == ["model", "tools"]
    assert "select_result" in graph["tools"]
    assert client.get("/api/agent/graph?mode=workflow").status_code == 422
    assert client.get("/api/health").json()["max_upload_mb"] == settings.max_upload_mb


def test_fixed_mode_rejected_and_agent_is_default(client, agent_task_stub):
    ds = _upload(client, "key-a")
    body = {"dataset_id": ds, "question": "有哪些数据？"}
    assert client.post("/api/analyze", json={**body, "mode": "workflow"}, headers={"X-API-Key": "key-a"}).status_code == 422
    task = client.post("/api/analyze", json=body, headers={"X-API-Key": "key-a"})
    assert task.status_code == 200 and task.json()["mode"] == "agent"


def test_dataset_id_rejects_path_traversal(client):
    response = client.post("/api/analyze", json={"dataset_id": "../../private", "question": "统计"})
    assert response.status_code == 422
