"""Recovery and authorization using real SQLite checkpoint storage, no model API."""
from fastapi.testclient import TestClient

from app.config import settings
from app.repository.factory import get_repository, reset_repository
from tests.test_tool_agent import ScriptedLLM, call, response


def test_resume_after_graph_crash_keeps_tool_results_and_is_owner_scoped(demo_dataset, monkeypatch):
    from app.main import app

    def crash(_messages):
        raise RuntimeError('simulated process interruption')

    llm = ScriptedLLM(
        response(call('run_safe_sql', {'sql': 'SELECT COUNT(*) AS n FROM demo_sales'}, 'query')),
        crash,
        response(content='查询完成，共 1691 行。'),
    )
    monkeypatch.setattr('app.agent.tool_agent.get_llm', lambda: llm)
    monkeypatch.setattr(settings, 'force_mock', False)
    monkeypatch.setattr(settings, 'llm_api_key', 'test-only')
    reset_repository()
    with TestClient(app) as client:
        body = {'dataset_id': demo_dataset.dataset_id, 'question': '一共有多少行？'}
        first = client.post('/api/analyze', json=body)
        assert first.status_code == 200
        failed = first.json()
        assert failed['status'] == 'failed' and failed['error_type'] == 'graph_error'
        task_id, session_id = failed['task_id'], failed['session_id']
        assert client.post(f'/api/task/{task_id}/resume', headers={'X-API-Key': 'other'}).status_code == 404
        assert any(t['task_id'] == task_id for t in client.get('/api/tasks').json()['items'])
        reset_repository()  # reload control-plane and checkpoint from disk
        restored = client.post(f'/api/task/{task_id}/resume')
        assert restored.status_code == 200, restored.text
        result = restored.json()
        assert result['status'] == 'success' and result['data'] == [{'n': 1691}]
        assert [c['name'] for c in result['tool_calls']].count('run_safe_sql') == 1
        assert any(m.get('tool_call_id') == 'query' for m in llm.seen[-1])
        assert client.post(f'/api/task/{task_id}/resume').status_code == 409
        conversation = get_repository().get_conversation(session_id)
        assert conversation['task_ids'].count(task_id) == 1
        assert len(conversation['turns']) == 1


def test_checkpoint_resume_rejects_busy_task(demo_dataset, monkeypatch):
    from app.agent.checkpoints import task_lock
    from app.main import app
    monkeypatch.setattr(settings, 'force_mock', False)
    monkeypatch.setattr(settings, 'llm_api_key', 'test-only')
    repo = get_repository()
    task_id = 'a1b2c3d4e5f6'
    repo.save_task({'task_id': task_id, 'dataset_id': demo_dataset.dataset_id, 'owner_id': 'anonymous',
                    'question': '问题', 'status': 'running', 'created_at': '2026-11-02T00:00:00'})
    with TestClient(app) as client, task_lock(task_id):
        assert client.post(f'/api/task/{task_id}/resume').status_code == 409


def test_resume_finalizes_completed_checkpoint_without_replaying_model(demo_dataset, monkeypatch):
    from app.main import app
    from app.services import task_store

    llm = ScriptedLLM(response(call('run_safe_sql', {'sql': 'SELECT COUNT(*) AS n FROM demo_sales'}, 'q')),
                      response(content='共有 1691 行。'))
    monkeypatch.setattr('app.agent.tool_agent.get_llm', lambda: llm)
    monkeypatch.setattr(settings, 'force_mock', False)
    monkeypatch.setattr(settings, 'llm_api_key', 'test-only')
    original_save = task_store.save_task
    saved_id = []

    def fail_final_save(task):
        if task.get('status') == 'running':
            saved_id.append(task['task_id'])
            return original_save(task)
        raise RuntimeError('simulated crash before final persistence')

    monkeypatch.setattr(task_store, 'save_task', fail_final_save)
    with TestClient(app) as client:
        response_ = client.post('/api/analyze', json={'dataset_id': demo_dataset.dataset_id, 'question': '多少行？'})
        assert response_.status_code == 500 and saved_id
        monkeypatch.setattr(task_store, 'save_task', original_save)
        result = client.post(f'/api/task/{saved_id[0]}/resume')
        assert result.status_code == 200, result.text
        assert result.json()['data'] == [{'n': 1691}]
        assert len(llm.seen) == 2  # A completed graph is finalized, not run again.
        assert sum(a.task_id == saved_id[0] for a in get_repository().list_audits(owner_id='anonymous', limit=1000)) == 1


def test_checkpoint_survives_abrupt_process_exit(demo_dataset, monkeypatch):
    import os
    from pathlib import Path
    import subprocess
    import sys
    from app.services.analyze_service import run_analysis

    task_id = 'b1c2d3e4f5a6'
    script = r'''
import os, sys
from app.agent import tool_agent
from app.services.analyze_service import run_analysis
class Model:
    name = 'process-test'
    def __init__(self): self.calls = 0
    def complete_with_tools(self, messages, tools, timeout=None):
        self.calls += 1
        if self.calls > 1: os._exit(23)
        return {'message': {'role':'assistant','content':None,'tool_calls':[
            {'id':'q','type':'function','function':{'name':'run_safe_sql',
             'arguments':'{"sql":"SELECT COUNT(*) AS n FROM demo_sales"}'}}]},
            'usage': {'total_tokens': 1}}
tool_agent.get_llm = lambda: Model()
run_analysis(sys.argv[1], '多少行？', task_id=sys.argv[2])
'''
    project = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, 'STORAGE_DIR': str(settings.storage_dir), 'REPOSITORY_BACKEND': 'json',
           'USE_MOCK_LLM': 'false', 'LLM_API_KEY': 'test-only', 'METRICS_ENABLED': 'false',
           'PYTHONPATH': os.pathsep.join([project, *sys.path])}
    child = subprocess.run([sys.executable, '-c', script, demo_dataset.dataset_id, task_id],
                           cwd=project, env=env, capture_output=True, text=True, timeout=60)
    assert child.returncode == 23, child.stderr
    reset_repository()
    pending = get_repository().get_task(task_id)
    assert pending['status'] == 'running' and pending['session_id']
    llm = ScriptedLLM(response(content='共有 1691 行。'))
    monkeypatch.setattr('app.agent.tool_agent.get_llm', lambda: llm)
    result = run_analysis(demo_dataset.dataset_id, '不应覆盖原问题', task_id=task_id,
                          owner_id='anonymous', resume=True)
    assert result['status'] == 'success' and result['data'] == [{'n': 1691}], result
    assert result['question'] == '多少行？'
    assert [c['name'] for c in result['tool_calls']].count('run_safe_sql') == 1


def test_checkpoint_can_be_deleted_only_by_owner(demo_dataset, monkeypatch):
    from app.main import app

    def crash(_messages):
        raise RuntimeError('interrupted')

    llm = ScriptedLLM(response(call('run_safe_sql', {'sql': 'SELECT COUNT(*) AS n FROM demo_sales'}, 'q')),
                      crash)
    monkeypatch.setattr('app.agent.tool_agent.get_llm', lambda: llm)
    monkeypatch.setattr(settings, 'force_mock', False)
    monkeypatch.setattr(settings, 'llm_api_key', 'test-only')
    with TestClient(app) as client:
        result = client.post('/api/analyze', json={'dataset_id': demo_dataset.dataset_id, 'question': '多少行？'}).json()
        task_id = result['task_id']
        assert client.delete(f'/api/task/{task_id}/checkpoint', headers={'X-API-Key': 'other'}).status_code == 404
        assert client.delete(f'/api/task/{task_id}/checkpoint').json() == {'deleted': True}
        assert client.post(f'/api/task/{task_id}/resume').status_code == 409
        assert client.get(f'/api/task/{task_id}').status_code == 200


def test_resume_rejected_if_conversation_has_advanced(demo_dataset, monkeypatch):
    from app.main import app

    def crash(_messages):
        raise RuntimeError('interrupted')

    llm = ScriptedLLM(
        response(call('run_safe_sql', {'sql': 'SELECT COUNT(*) AS n FROM demo_sales'}, 'first')),
        crash,
        response(call('run_safe_sql', {'sql': 'SELECT COUNT(*) AS n FROM demo_sales'}, 'second')),
        response(content='查询完成。'),
    )
    monkeypatch.setattr('app.agent.tool_agent.get_llm', lambda: llm)
    monkeypatch.setattr(settings, 'force_mock', False)
    monkeypatch.setattr(settings, 'llm_api_key', 'test-only')
    with TestClient(app) as client:
        first = client.post('/api/analyze', json={'dataset_id': demo_dataset.dataset_id, 'question': '多少行？'}).json()
        next_turn = client.post('/api/analyze', json={'dataset_id': demo_dataset.dataset_id,
            'question': '重新统计', 'session_id': first['session_id']}).json()
        assert next_turn['status'] == 'success'
        assert client.post(f"/api/task/{first['task_id']}/resume").status_code == 409
