from app.discovery import Discovery
from tests.test_discovery import rule, complete


def test_full_budget_does_not_leave_ghost_run(db):
    service = Discovery(db)
    service.configure(1)
    saved = rule(service, enabled=True)
    complete(service, service.queue(saved['id']))
    assert service.schedule_due() == 0
    assert db.one('SELECT COUNT(*) n FROM search_runs')['n'] == 1
    assert db.one('SELECT COUNT(*) n FROM discovery_calls')['n'] == 1


def test_zero_limit_cancels_pending_work_but_preserves_reserved_usage(db):
    service = Discovery(db)
    service.configure(3)
    saved = rule(service)
    run = service.queue(saved['id'])
    service.configure(0)
    assert db.one('SELECT status FROM search_runs WHERE id=?', (run['id'],))['status'] == 'cancelled'
    assert db.one('SELECT COUNT(*) n FROM discovery_calls')['n'] == 1
    assert db.one("SELECT COUNT(*) n FROM jobs WHERE kind='discover_sources' AND status='pending'")['n'] == 0


def test_canonicalization_keeps_signed_query_bytes():
    from app.discovery import public_url
    url = 'https://example.com/release?signature=a%20b%2Fc&token=x%2By#section'
    assert public_url(url) == url.split('#')[0]
