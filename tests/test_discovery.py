import json
import pytest
from app.discovery import Discovery
from app.errors import DomainError


@pytest.fixture
def discovery(db):
    service = Discovery(db)
    service.configure(10)
    return service


def rule(service, **changes):
    return service.create_rule(**{
        'query': 'Rust compiler updates', 'backend': 'anysearch', 'days': 3,
        'limit': 5, 'interval_minutes': 60, 'enabled': False, **changes,
    })


def envelope(url='https://example.com/release', title='Compiler release'):
    return {
        'schema_version': '1.0', 'routes': [{'backend': 'anysearch', 'status': 'completed'}],
        'coverage': [{'returned_count': 1}], 'errors': [],
        'candidates': [{'candidate_id': 'web:1', 'canonical_url': url, 'url': url,
                        'title': title, 'snippet': 'Search snippet, not verified body',
                        'backend': 'anysearch', 'platform': 'web', 'published_at': None,
                        'provenance': {'private_receipt': 'local-only'}}],
    }


def complete(service, run, result=None):
    service.begin_run(run['id'])
    service.complete_run(run['id'], result or envelope())


def test_search_does_not_create_confirmed_sources(discovery, db):
    saved = rule(discovery)
    run = discovery.queue(saved['id'])
    complete(discovery, run)
    assert len(discovery.candidates()) == 1
    assert db.one('SELECT COUNT(*) n FROM sources')['n'] == 0
    with pytest.raises(DomainError):
        discovery.confirm(1, 'Real body', confirmed=False)


def test_repeated_url_retains_all_hits(discovery, db):
    saved = rule(discovery)
    complete(discovery, discovery.queue(saved['id']))
    complete(discovery, discovery.queue(saved['id']))
    assert len(discovery.candidates()) == 1
    assert db.one('SELECT COUNT(*) n FROM candidate_hits')['n'] == 2
    assert 'raw_json' not in discovery.candidates()[0]


def test_quota_reserved_at_queue_and_active_run_is_reused(discovery, db):
    discovery.configure(1)
    saved = rule(discovery)
    run = discovery.queue(saved['id'])
    assert discovery.queue(saved['id'])['id'] == run['id']
    complete(discovery, run)
    with pytest.raises(DomainError):
        discovery.queue(saved['id'])
    assert db.one('SELECT COUNT(*) n FROM discovery_calls')['n'] == 1


def test_scheduler_does_not_backfill_and_disable_cancels_queue(discovery, db):
    saved = rule(discovery, enabled=True)
    with db.transaction() as conn:
        conn.execute('UPDATE search_rules SET next_run_at=0')
    assert discovery.schedule_due() == 1
    assert discovery.schedule_due() == 0
    discovery.set_enabled(saved['id'], False)
    assert db.one('SELECT status FROM search_runs')['status'] == 'cancelled'


def test_confirm_body_then_create_explicit_language_draft(discovery, db):
    saved = rule(discovery)
    complete(discovery, discovery.queue(saved['id']))
    source = discovery.confirm(1, 'Reviewed official source body', confirmed=True)
    draft = discovery.create_draft(1, 'ja', 'news')
    assert draft['language'] == 'ja'
    assert json.loads(draft['source_ids']) == [source['id']]
    assert db.one('SELECT COUNT(*) n FROM approvals')['n'] == 0


def test_explicit_original_read_is_separate_from_import(discovery, db):
    saved = rule(discovery)
    complete(discovery, discovery.queue(saved['id']))
    read = discovery.queue_read(1)
    assert read['status'] == 'queued'
    assert db.one('SELECT COUNT(*) n FROM sources')['n'] == 0
    assert discovery.queue_read(1)['id'] == read['id']


def test_group_split_and_merge_preserve_candidates(discovery, db):
    saved = rule(discovery)
    result = envelope()
    result['candidates'].append({**result['candidates'][0], 'url': 'https://other.example/release',
                                 'canonical_url': 'https://other.example/release', 'candidate_id': 'web:2'})
    complete(discovery, discovery.queue(saved['id']), result)
    rows = discovery.candidates()
    assert rows[0]['topic_id'] == rows[1]['topic_id']
    new_topic = discovery.split(rows[0]['id'])
    assert new_topic != rows[1]['topic_id']
    discovery.merge(new_topic, rows[1]['topic_id'])
    assert len({row['topic_id'] for row in discovery.candidates()}) == 1


def test_bad_candidates_are_visible_as_partial(discovery, db):
    saved = rule(discovery)
    result = envelope()
    result['candidates'].append({**result['candidates'][0], 'url': 'https://127.0.0.1/private',
                                 'canonical_url': 'https://127.0.0.1/private'})
    complete(discovery, discovery.queue(saved['id']), result)
    assert db.one('SELECT status FROM search_runs')['status'] == 'partial'
    assert len(discovery.candidates()) == 1


def test_missing_dates_are_unknown_and_old_dates_excluded(discovery):
    saved = rule(discovery)
    result = envelope()
    result['candidates'].append({**result['candidates'][0], 'url': 'https://other.example/old',
                                 'canonical_url': 'https://other.example/old',
                                 'published_at': '2020-01-01T00:00:00+00:00'})
    complete(discovery, discovery.queue(saved['id']), result)
    rows = discovery.candidates()
    assert len(rows) == 1
    assert rows[0]['published_at'] is None
