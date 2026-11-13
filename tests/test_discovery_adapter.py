from dataclasses import replace
import json
from pathlib import Path
import pytest
from app.adapters.discovery import DiscoveryAdapter
from app.errors import ServiceError


@pytest.fixture
def adapter(settings, tmp_path):
    scripts = tmp_path / 'skills/yichen-unified-search/scripts'
    scripts.mkdir(parents=True)
    for name in ('route_search.py', 'aihot_search.py', 'anysearch_adapter.py'):
        (scripts / name).write_text('# configured external script', encoding='utf-8')
    return DiscoveryAdapter(replace(settings, skills_root=str(tmp_path / 'skills')))


def test_fixed_script_argv_and_no_publishing_secrets(adapter, monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[2].endswith('route_search.py'):
            payload = {'status': 'ready', 'route': {'backend': 'anysearch', 'login_state_used': False},
                       'steps': [{'argv': ['malicious-command', 'do-not-execute']}]}
        else:
            payload = {'schema_version': '1.0', 'routes': [], 'coverage': [], 'errors': [], 'candidates': []}
        return 0, json.dumps(payload).encode()
    monkeypatch.setenv('XOPS_POSTIZ_API_KEY', 'must-not-leave')
    monkeypatch.setattr('app.adapters.discovery.run_bounded', run)
    adapter.search({'query': '--domain=unexpected', 'backend': 'anysearch', 'days': 1, 'limit': 3})
    assert len(calls) == 2
    argv, options = calls[1]
    assert Path(argv[2]).name == 'anysearch_adapter.py'
    assert argv[-2:] == ['--', '--domain=unexpected']
    assert options['shell'] is False
    assert 'XOPS_POSTIZ_API_KEY' not in options['env']


def test_unconfigured_runtime_is_explicit(settings):
    with pytest.raises(ServiceError) as error:
        DiscoveryAdapter(settings).search({'query': 'AI news', 'backend': 'aihot', 'days': 1, 'limit': 3})
    assert error.value.code == 'discovery_runtime_not_configured'


def test_non_json_output_is_failure(adapter, monkeypatch):
    def run(argv, **kwargs):
        return 0, b'<html>login</html>'
    monkeypatch.setattr('app.adapters.discovery.run_bounded', run)
    with pytest.raises(ServiceError):
        adapter.search({'query': 'Rust', 'backend': 'anysearch', 'days': 1, 'limit': 3})
