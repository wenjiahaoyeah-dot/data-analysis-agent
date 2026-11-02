"""工具调用与多轮状态的行为测试；不访问模型服务。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agent.tool_agent import run_tool_agent
from app.config import settings
from app.repository.base import RepositoryConflict
from app.repository.factory import get_repository, reset_repository


def call(name, arguments, ident="call_1"):
    return {"id": ident, "type": "function", "function": {"name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)}}


def response(*calls, content=None):
    return {"message": {"role": "assistant", "content": content, "tool_calls": list(calls)},
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}


class ScriptedLLM:
    name = "scripted-test"

    def __init__(self, *steps):
        self.steps = list(steps)
        self.seen = []

    def complete_with_tools(self, messages, tools, timeout=None):
        self.seen.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        assert tools and timeout > 0
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


def run(demo_dataset, demo_db, llm, question="分析数据"):
    return run_tool_agent(demo_db, demo_dataset.to_dict(), question, "test_task", "anonymous", llm=llm)


def test_real_tool_protocol_multiple_queries_and_chart(demo_dataset, demo_db):
    llm = ScriptedLLM(
        response(call("inspect_schema", {}, "schema"),
                 call("run_safe_sql", {"sql": "SELECT COUNT(*) AS n FROM demo_sales"}, "sql1")),
        response(call("run_safe_sql", {"sql": "SELECT region, COUNT(*) AS n FROM demo_sales GROUP BY region"}, "sql2")),
        response(call("build_chart", {"result_id": "q2", "type": "bar", "x": "region", "y": "n"}, "chart")),
        response(content="按地区统计完成。"),
    )
    result = run(demo_dataset, demo_db, llm)
    assert result["status"] == "success"
    assert len(result["queries"]) == 2
    assert result["queries"][0]["data"][0]["n"] == 1691
    assert result["figure"] and result["chart"]["type"] == "bar"
    assert result["charts"][0]["result_id"] == "q2"
    assert [m["tool_call_id"] for m in llm.seen[1] if m["role"] == "tool"] == ["schema", "sql1"]
    assert result["usage"]["total_tokens"] == 52


@pytest.mark.parametrize("sql", [
    "DROP TABLE demo_sales", "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM other_tenant_data", "SELECT * FROM demo_sales; DROP TABLE demo_sales",
])
def test_unsafe_sql_is_rejected_and_model_can_repair(demo_dataset, demo_db, sql):
    llm = ScriptedLLM(response(call("run_safe_sql", {"sql": sql})),
        response(call("run_safe_sql", {"sql": "SELECT COUNT(*) AS n FROM demo_sales"}, "repair")),
        response(content="查询完成。"))
    result = run(demo_dataset, demo_db, llm)
    assert result["status"] == "success"
    assert result["calls"][0]["status"] == "error"
    assert result["repairs"][0]["ok"] is True
    assert result["queries"][0]["data"][0]["n"] == 1691


def test_invalid_unknown_repeated_tools_are_not_executed(demo_dataset, demo_db):
    llm = ScriptedLLM(
        response(call("run_safe_sql", "{bad", "bad"), call("delete_files", {}, "unknown")),
        response(call("inspect_schema", {"dataset_id": "elsewhere"}, "extra")),
        response(call("inspect_schema", {}, "ok")),
        response(call("inspect_schema", {}, "repeated")),
        response(content="表结构已读取。"))
    result = run(demo_dataset, demo_db, llm)
    # 本轮只有 inspect_schema 成功、没有任何数据查询，因此必须判"证据不足"
    assert result["status"] == "failed"
    assert result["error_type"] == "insufficient_evidence"
    assert [c["status"] for c in result["calls"]] == ["error", "error", "error", "ok", "error"]
    assert result["calls"][-1]["error_type"] == "repeated_call"
    assert result["queries"] == []


def test_schema_supports_column_pagination(demo_dataset, demo_db):
    meta = demo_dataset.to_dict()
    meta["tables"][0]["columns"] = [{"name": f"c{i}", "type": "VARCHAR"} for i in range(25)]
    llm = ScriptedLLM(response(call("inspect_schema", {"table": "demo_sales", "column_offset": 20})),
                      response(content="字段已读取。"))
    result = run_tool_agent(demo_db, meta, "查看字段", "page", "anonymous", llm=llm)
    output = json.loads(next(m["content"] for m in result["messages"] if m["role"] == "tool"))
    assert [c["name"] for c in output["tables"][0]["columns"]] == [f"c{i}" for i in range(20, 25)]
    assert output["tables"][0]["next_column_offset"] is None


def test_answer_without_evidence_is_rejected(demo_dataset, demo_db):
    # 一次查询都没执行就报数字，必须被拦。错误码从 missing_evidence 收紧为
    # insufficient_evidence：语义更准，也与 inspect_schema 只算"上下文准备"一致。
    result = run(demo_dataset, demo_db, ScriptedLLM(response(content="销售额为 999。")))
    assert result["status"] == "failed" and result["error_type"] == "insufficient_evidence"


def test_truncated_model_response_is_not_treated_as_final(demo_dataset, demo_db):
    truncated = response(call("inspect_schema", {}))
    truncated["finish_reason"] = "length"
    result = run(demo_dataset, demo_db, ScriptedLLM(truncated))
    assert result["status"] == "failed" and result["error_type"] == "model_error"
    assert "截断" in result["error"] and not result["calls"]


def test_budget_pairs_all_tool_call_ids_before_stopping(demo_dataset, demo_db, monkeypatch):
    monkeypatch.setattr(settings, "agent_max_tool_calls", 1)
    llm = ScriptedLLM(response(call("inspect_schema", {}, "first"),
                               call("run_safe_sql", {"sql": "SELECT 1"}, "second")))
    result = run(demo_dataset, demo_db, llm)
    assert result["status"] == "failed" and result["error_type"] == "budget_exhausted"
    assert [m["tool_call_id"] for m in result["messages"] if m["role"] == "tool"] == ["first", "second"]
    assert result["queries"] == []


def test_sql_budget_counts_failed_validation_attempts(demo_dataset, demo_db, monkeypatch):
    monkeypatch.setattr(settings, "agent_max_sql_calls", 1)
    llm = ScriptedLLM(response(call("run_safe_sql", {"sql": "DROP TABLE demo_sales"})),
        response(call("run_safe_sql", {"sql": "SELECT COUNT(*) FROM demo_sales"}, "second")),
        response(call("inspect_schema", {}, "schema")), response(content="无法执行更多查询。"))
    result = run(demo_dataset, demo_db, llm)
    assert result["calls"][1]["error_type"] == "sql_budget"
    assert result["queries"] == []


def test_timeout_stops_remaining_calls(demo_dataset, demo_db, monkeypatch):
    from app.tools.sql_tool import SQLSecurityError
    def timeout(*args, **kwargs):
        raise SQLSecurityError("timeout", "timeout")
    monkeypatch.setattr("app.agent.tool_agent.execute_sql", timeout)
    llm = ScriptedLLM(response(call("run_safe_sql", {"sql": "SELECT * FROM demo_sales"}, "slow"),
                               call("inspect_schema", {}, "after")))
    result = run(demo_dataset, demo_db, llm)
    assert result["status"] == "failed" and result["error_type"] == "timeout"
    assert result["calls"][1]["error_type"] == "skipped"


def test_clarification_stops_later_calls_in_same_batch(demo_dataset, demo_db):
    llm = ScriptedLLM(response(call("ask_clarification", {"question": "按行数还是去重订单数？",
        "options": ["按行数", "按去重订单数"]}, "ask"),
        call("run_safe_sql", {"sql": "SELECT COUNT(*) FROM demo_sales"}, "skipped")))
    result = run(demo_dataset, demo_db, llm)
    assert result["status"] == "needs_clarification"
    assert result["clarification"]["options"] == ["按行数", "按去重订单数"]
    assert result["calls"][1]["error_type"] == "skipped" and not result["queries"]
    assert [m["tool_call_id"] for m in result["messages"] if m["role"] == "tool"] == ["ask", "skipped"]


def test_chart_cannot_reference_another_turn_or_unknown_columns(demo_dataset, demo_db):
    llm = ScriptedLLM(response(call("build_chart", {"result_id": "q999", "type": "bar", "x": "x", "y": "y"})),
                      response(call("inspect_schema", {}, "schema")), response(content="结果不存在。"))
    result = run(demo_dataset, demo_db, llm)
    assert result["calls"][0]["error_type"] == "unknown_result" and not result["figure"]


def test_api_persists_clarification_and_isolates_sessions(isolated_storage, demo_dataset, monkeypatch):
    from app.main import app
    from app.services.upload_service import create_dataset

    llm = ScriptedLLM(
        response(call("ask_clarification", {"question": "按行数还是去重订单数？", "options": ["按行数", "去重"]})),
        response(call("run_safe_sql", {"sql": "SELECT COUNT(*) AS n FROM demo_sales"}, "resume")),
        response(content="按行数统计完成。"),
    )
    monkeypatch.setattr("app.agent.tool_agent.get_llm", lambda: llm)
    # API 层会拦截"自主模式 + mock 环境"，这里注入的是 ScriptedLLM 替身，
    # 所以把配置伪装成已接入真实模型，只测试会话与隔离逻辑本身。
    monkeypatch.setattr(settings, "force_mock", False)
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    reset_repository()
    with TestClient(app) as client:
        graph = client.get("/api/agent/graph?mode=agent").json()
        assert set(graph["nodes"]) == {"model", "tools"}
        assert "ask_clarification" in graph["tools"]
        body = {"dataset_id": demo_dataset.dataset_id, "question": "订单数多少", "mode": "agent"}
        first = client.post("/api/analyze", json=body).json()
        assert first["status"] == "needs_clarification" and first["session_id"]
        assert get_repository().get_report(first["task_id"]) is None
        session = first["session_id"]
        assert client.post("/api/analyze", json={**body, "session_id": session},
                           headers={"X-API-Key": "different-user"}).status_code == 404
        other = create_dataset([("other.csv", b"x\n1\n")])
        assert client.post("/api/analyze", json={**body, "dataset_id": other.dataset_id,
                                                  "session_id": session}).status_code == 404
        reset_repository()  # 模拟后端重启后重新建立 Repository 和连接。
        resumed = client.post("/api/analyze", json={**body, "question": "按行数", "session_id": session}).json()
        assert resumed["status"] == "success" and resumed["data"][0]["n"] == 1691
        assert get_repository().get_report(resumed["task_id"]) is None
        assert any(m.get("tool_call_id") == "call_1" for m in llm.seen[1])
        assert llm.seen[1][-1] == {"role": "user", "content": "按行数"}
        assert get_repository().get_conversation(session)["version"] == 3
        sessions = client.get(f"/api/conversations?dataset_id={demo_dataset.dataset_id}").json()["items"]
        assert session in [s["session_id"] for s in sessions]
        restored = client.get(f"/api/conversations/{session}").json()
        assert [x["question"] for x in restored["items"]] == ["订单数多少", "按行数"]
        assert client.get(f"/api/conversations/{session}", headers={"X-API-Key": "different-user"}).status_code == 404
        assert client.delete(f"/api/conversations/{session}", headers={"X-API-Key": "different-user"}).status_code == 404
        assert client.delete(f"/api/conversations/{session}").json()["deleted"]
        assert client.get(f"/api/conversations/{session}").status_code == 404
        assert client.get(f"/api/task/{resumed['task_id']}").status_code == 200


def test_session_id_validation_prevents_path_traversal(isolated_storage, demo_dataset):
    from app.main import app
    with TestClient(app) as client:
        r = client.post("/api/analyze", json={"dataset_id": demo_dataset.dataset_id,
            "question": "数据", "mode": "agent", "session_id": "../../secret"})
        assert r.status_code == 422


def test_json_session_compare_and_swap(isolated_storage):
    from app.repository.jsonfile import JsonRepository
    import uuid
    repo = JsonRepository()
    conversation = {"session_id": uuid.uuid4().hex, "owner_id": "u", "dataset_id": "ds", "turns": []}
    repo.save_conversation(conversation, 0)
    first = repo.get_conversation(conversation["session_id"])
    repo.save_conversation(first, 1)
    with pytest.raises(RepositoryConflict):
        repo.save_conversation(first, 1)
    assert repo.get_conversation(conversation["session_id"])["version"] == 2


def test_eval_compares_named_columns_and_distinct_expression():
    from scripts.run_agent_eval import match_distinct_order_id, match_query
    rows = {"sql": "SELECT COUNT(*) AS rows, COUNT(DISTINCT order_id) AS n FROM demo_sales",
            "data": [{"rows": 1691, "n": 1690}], "truncated": False}
    assert match_distinct_order_id(rows, [[1690]])
    assert not match_distinct_order_id({**rows, "sql": "SELECT 1690 AS n FROM demo_sales"}, [[1690]])

    # 统一口径后：默认严格，多一列即失败；澄清题显式放宽才投影
    extra = {"data": [{"region": "华东", "rows": 2, "total": 42}]}
    assert not match_query(extra, [["华东", 42]], ["region", "total"])
    assert match_query(extra, [["华东", 42]], ["region", "total"], strict=False)
    exact = {"data": [{"region": "华东", "total": 42}]}
    assert match_query(exact, [["华东", 42]], ["region", "total"])


def test_report_includes_each_query_and_clarification():
    from app.services.report_service import build_markdown
    task = {"status": "success", "columns": ["x"], "data": [{"x": 2}], "row_count": 1,
            "queries": [{"result_id": "q1", "sql": "SELECT 1 AS x", "columns": ["x"], "data": [{"x": 1}], "row_count": 1},
                        {"result_id": "q2", "sql": "SELECT 2 AS x", "columns": ["x"], "data": [{"x": 2}], "row_count": 1}],
            "charts": [{"result_id": "q1"}]}
    report = build_markdown(task)
    assert "共返回 1 行" in report and "| 1 |" in report and "| 2 |" in report
    assert "SELECT 1 AS x" in report and "SELECT 2 AS x" in report and "图表对应查询：q1" in report
    assert "应该怎样统计" in build_markdown({"status": "needs_clarification", "clarification": {"question": "应该怎样统计"}})


def test_native_tool_request_and_provider_errors_do_not_expose_secret(monkeypatch):
    from app.agent.llm import LLMError, OpenAICompatLLM
    captured = {}
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": None,
                     "tool_calls": [call("inspect_schema", {})]}, "finish_reason": "tool_calls"}],
                    "usage": {"total_tokens": 5}}
    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, endpoint, headers, json):
            captured.update(endpoint=endpoint, headers=headers, body=json)
            return FakeResponse()
    monkeypatch.setattr("app.agent.llm.httpx.Client", FakeClient)
    client = OpenAICompatLLM(base_url="https://example.invalid/v1", api_key="secret-test", model="test")
    result = client.complete_with_tools([{"role": "user", "content": "问题"}], [{"type": "function"}], timeout=2)
    assert result["message"]["tool_calls"][0]["id"] == "call_1"
    assert captured["body"]["tool_choice"] == "auto" and captured["timeout"] == 2
    FakeResponse.status_code = 401
    with pytest.raises(LLMError) as exc:
        client.complete_with_tools([], [], timeout=2)
    assert "secret-test" not in str(exc.value) and "401" in str(exc.value)


# ---------------------------------------------------------------------- #
# 本轮加固：证据门槛、签名归一化、异常兜底、多查询审计
# ---------------------------------------------------------------------- #
def test_select_one_is_not_data_evidence(demo_dataset, demo_db):
    """SELECT 1 不是数据证据：没查表就报数字必须被拦。"""
    llm = ScriptedLLM(
        response(call("run_safe_sql", {"sql": "SELECT 1 AS n"})),
        response(content="销售额是 12345 元。"),
    )
    result = run(demo_dataset, demo_db, llm)
    assert result["status"] == "failed"
    assert result["error_type"] == "insufficient_evidence"


def test_empty_result_is_valid_data_evidence(demo_dataset, demo_db):
    """空结果可以支撑“没有匹配记录”，不应一律拦截。"""
    llm = ScriptedLLM(
        response(call("run_safe_sql", {"sql": "SELECT region FROM demo_sales WHERE region = '不存在'"})),
        response(content="没有符合条件的记录。"),
    )
    result = run(demo_dataset, demo_db, llm)
    assert result["status"] == "success"
    assert result["query_result"]["rows"] == []


def test_schema_only_answer_and_mixed_question(demo_dataset, demo_db):
    def llm():
        return ScriptedLLM(response(call("inspect_schema", {})), response(content="有 demo_sales 表。"))
    assert run(demo_dataset, demo_db, llm(), "有哪些表和字段？")["status"] == "success"
    assert run(demo_dataset, demo_db, llm(), "有哪些数据表和字段？只描述表结构，不需要查询统计数值。")["status"] == "success"
    mixed = run(demo_dataset, demo_db, llm(), "有哪些字段？销售额是多少？")
    assert mixed["error_type"] == "insufficient_evidence"


def test_from_inside_literal_is_not_evidence(demo_dataset, demo_db):
    llm = ScriptedLLM(response(call("run_safe_sql", {"sql": "SELECT ' from ' AS x"})),
                      response(content="销售额为 100。"))
    assert run(demo_dataset, demo_db, llm)["error_type"] == "insufficient_evidence"


def test_sql_signature_preserves_literal_case_and_spacing():
    from app.agent.tool_agent import _normalize_sql
    assert _normalize_sql("SELECT * FROM t WHERE x = 'A'") != _normalize_sql("SELECT * FROM t WHERE x = 'a'")
    assert _normalize_sql("SELECT * FROM t WHERE x = 'a  b'") != _normalize_sql("SELECT * FROM t WHERE x = 'a b'")


def test_select_main_result_when_later_query_is_auxiliary(demo_dataset, demo_db):
    llm = ScriptedLLM(
        response(call("run_safe_sql", {"sql": "SELECT COUNT(*) AS n FROM demo_sales"}, "a")),
        response(call("run_safe_sql", {"sql": "SELECT region FROM demo_sales LIMIT 1"}, "b")),
        response(call("select_result", {"result_id": "q1"}, "select")),
        response(content="共有 1691 条记录。"),
    )
    result = run(demo_dataset, demo_db, llm)
    assert result["selected_result_id"] == "q1"
    assert result["query_result"]["rows"] == [[1691]]
    assert len(result["queries"]) == 2


def test_cannot_select_result_from_another_turn(demo_dataset, demo_db):
    llm = ScriptedLLM(response(call("select_result", {"result_id": "q99"})), response(content="完成"))
    result = run(demo_dataset, demo_db, llm)
    assert result["calls"][0]["error_type"] == "unknown_result"
    assert result["status"] == "failed"


def test_normalized_sql_counts_as_repeated_call(demo_dataset, demo_db):
    """改大小写/空白不能绕过重复调用检测。"""
    llm = ScriptedLLM(
        response(call("run_safe_sql", {"sql": "SELECT COUNT(*) AS n FROM demo_sales"}, "a")),
        response(call("run_safe_sql", {"sql": "select  count( * )  as  N  from  demo_sales"}, "b")),
        response(content="共 1691 单。"),
    )
    result = run(demo_dataset, demo_db, llm)
    assert result["calls"][1]["error_type"] == "repeated_call"
    assert len(result["queries"]) == 1


def test_multi_query_audit_covers_every_sql(demo_dataset, demo_db):
    """多查询任务：审计要能看到全部 SQL，不能只记最后一条。"""
    repo = get_repository()
    before = len(repo.list_audits(limit=1000))
    llm = ScriptedLLM(
        response(call("run_safe_sql", {"sql": "SELECT region, SUM(amount) AS t FROM demo_sales GROUP BY region"}, "a")),
        response(call("run_safe_sql", {"sql": "SELECT channel, SUM(amount) AS t FROM demo_sales GROUP BY channel"}, "b")),
        response(content="两组结果见上。"),
    )
    run_tool_agent(demo_db, demo_dataset.to_dict(), "按地区和渠道分别汇总销售额", "multi1", "anonymous", llm=llm)

    audits = repo.list_audits(limit=1000)
    assert len(audits) == before + 1
    assert "region" in (audits[0].sql or "")
    assert "channel" in (audits[0].sql or "")


def test_graph_exception_becomes_failed_task(demo_dataset, demo_db, monkeypatch):
    """图执行抛异常时要收敛成任务失败，而不是冒泡成 500。"""

    class _FakeGraph:
        def add_node(self, *a, **k):
            return self

        def add_edge(self, *a, **k):
            return self

        def add_conditional_edges(self, *a, **k):
            return self

        def compile(self, **kwargs):
            class _Compiled:
                def invoke(self, *a, **k):
                    raise RuntimeError("boom")

            return _Compiled()

    monkeypatch.setattr("app.agent.tool_agent.StateGraph", lambda *a, **k: _FakeGraph())
    result = run_tool_agent(demo_db, demo_dataset.to_dict(), "q", "g1", "anonymous",
                            llm=ScriptedLLM(response(content="x")))
    assert result["status"] == "failed"
    assert result["error_type"] == "graph_error"
    assert result["query_result"]["row_count"] == 0


def test_graph_exception_preserves_completed_observations(demo_dataset, demo_db, monkeypatch):
    class Graph:
        def __init__(self): self.nodes = {}
        def add_node(self, name, fn): self.nodes[name] = fn
        def add_edge(self, *args): pass
        def add_conditional_edges(self, *args): pass
        def compile(self, **kwargs):
            nodes = self.nodes
            class Compiled:
                def invoke(self, state, config, **kwargs):
                    state = nodes['model'](state)
                    nodes['tools'](state)
                    raise RuntimeError('after tool')
            return Compiled()
    monkeypatch.setattr('app.agent.tool_agent.StateGraph',lambda *args:Graph())
    llm=ScriptedLLM(response(call('run_safe_sql',{'sql':'SELECT COUNT(*) AS n FROM demo_sales'})))
    result=run(demo_dataset,demo_db,llm)
    assert result['status']=='failed' and result['error_type']=='graph_error'
    assert result['usage']['total_tokens']==13
    assert result['queries'][0]['data'][0]['n']==1691
    assert [x['node'] for x in result['trace']][-1]=='graph'
    assert not any(m.get('tool_calls') for m in result['messages'])


def test_agent_mode_requires_real_model(demo_dataset, monkeypatch):
    """没配真实模型时，自主模式应该直接 400，而不是白跑一轮再失败。"""
    from app.main import app

    monkeypatch.setattr(settings, "force_mock", True)
    monkeypatch.setattr(settings, "llm_api_key", "")
    with TestClient(app) as client:
        resp = client.post("/api/analyze",
                           json={"dataset_id": demo_dataset.dataset_id,
                                 "question": "订单数", "mode": "agent"})
    assert resp.status_code == 400
    assert "Function Calling" in resp.json()["detail"]
