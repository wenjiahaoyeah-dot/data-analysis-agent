"""Exercise the dataset-selected UI path without a running API or model."""
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from streamlit.testing.v1 import AppTest


class _Response:
    def __init__(self, data):
        self.data = data

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
