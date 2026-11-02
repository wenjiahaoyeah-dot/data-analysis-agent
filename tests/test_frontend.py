"""Exercise the dataset-selected UI path without a running API or model."""
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from streamlit.testing.v1 import AppTest


class _Response:
    def __init__(self, data):
        self.data = data
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class _Client:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url):
        path = urlsplit(url).path
        if path == '/api/health':
            return _Response({'llm_mode': 'real', 'llm_model': 'deepseek-flash', 'max_upload_mb': 50})
        if path.startswith('/api/schema/'):
            return _Response({'tables': [{'table_name': 'sales', 'row_count': 1,
                                          'columns': [{'name': 'amount', 'type': 'INTEGER'}]}]})
        if path == '/api/audit/stats':
            return _Response({})
        return _Response({'items': []})


def test_dataset_sidebar_remains_usable_without_mode_selector(monkeypatch):
    monkeypatch.setattr(httpx, 'Client', _Client)
    app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'frontend' / 'streamlit_app.py'))
    app.session_state['dataset'] = {'dataset_id': '123456abcdef', 'tables': [], 'total_rows': 1}
    app.run()
    assert not app.exception
    assert any('恢复或删除会话记忆' in expander.label for expander in app.sidebar.expander)
    assert not app.sidebar.radio


def test_interrupted_task_can_be_resumed_from_sidebar(monkeypatch):
    task_id = 'a1b2c3d4e5f6'
    session_id = 'f' * 32
    calls = []

    class ResumeClient(_Client):
        def get(self, url):
            if urlsplit(url).path == '/api/tasks':
                return _Response({'items': [{'task_id': task_id, 'dataset_id': '123456abcdef',
                    'status': 'running', 'question': '多少行？', 'created_at': '2026-11-02T12:00:00'}]})
            if urlsplit(url).path == f'/api/conversations/{session_id}':
                return _Response({'items': []})
            return super().get(url)

        def post(self, url, json=None):
            calls.append(urlsplit(url).path)
            return _Response({'task_id': task_id, 'dataset_id': '123456abcdef',
                'status': 'success', 'session_id': session_id, 'analysis': '结果为 1',
                'data': [{'n': 1}], 'columns': ['n'], 'row_count': 1})

    monkeypatch.setattr(httpx, 'Client', ResumeClient)
    app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'frontend' / 'streamlit_app.py'))
    app.session_state['dataset'] = {'dataset_id': '123456abcdef', 'tables': [], 'total_rows': 1}
    app.run()
    assert not app.exception
    app.button(key='resume_task_button').click().run()
    assert not app.exception
    assert calls == [f'/api/task/{task_id}/resume']
    assert app.session_state['session_id'] == session_id
