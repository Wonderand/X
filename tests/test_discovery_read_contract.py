import json
from pathlib import Path
import pytest
from app.adapters.discovery import DiscoveryAdapter
from app.errors import ServiceError


def test_original_candidate_is_kept_intact_for_signature_check(settings, monkeypatch):
    candidate = {'candidate_id': 'web:42', 'backend': 'anysearch', 'url': 'https://example.com/release',
                 'query': 'Rust release', 'provenance': {'receipt': {'signature': 'private-signature'}}}
    adapter = DiscoveryAdapter(settings)
    seen_paths = []
    def invoke(kind, arguments):
        assert kind == 'anysearch'
        assert arguments[0] == 'verify'
        path = Path(arguments[-1][1:])
        seen_paths.append(path)
        assert path.is_relative_to(settings.data_dir)
        assert json.loads(path.read_text(encoding='utf-8')) == candidate
        return {'schema_version': '1.0', 'routes': [], 'coverage': [], 'errors': [],
                'candidates': [{**candidate, 'verification': {'opened_original': True},
                                'platform_fields': {'anysearch': {'extract_markdown': 'Original source text. ' * 20}}}]}
    monkeypatch.setattr(adapter, '_invoke', invoke)
    assert adapter.read(candidate).startswith('Original source text.')
    assert not seen_paths[0].exists()


def test_receipt_failure_does_not_return_body(settings, monkeypatch):
    adapter = DiscoveryAdapter(settings)
    monkeypatch.setattr(adapter, '_invoke', lambda *args: {'schema_version': '1.0', 'routes': [],
                        'coverage': [], 'errors': [{'category': 'expired_receipt'}], 'candidates': []})
    with pytest.raises(ServiceError):
        adapter.read({'candidate_id': 'web:1', 'backend': 'anysearch', 'url': 'https://example.com/r'})


def test_concurrent_reservations_obey_shared_daily_limit(db):
    from concurrent.futures import ThreadPoolExecutor
    from app.discovery import Discovery
    from app.errors import DomainError
    from tests.test_discovery import rule
    service = Discovery(db)
    service.configure(1)
    rules = [rule(service, query='Topic one'), rule(service, query='Topic two')]
    def queue_one(rule_id):
        try:
            service.queue(rule_id)
            return True
        except DomainError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(queue_one, [item['id'] for item in rules]))
    assert sum(results) == 1
    assert db.one('SELECT COUNT(*) n FROM discovery_calls')['n'] == 1
    assert db.one('SELECT COUNT(*) n FROM search_runs')['n'] == 1
