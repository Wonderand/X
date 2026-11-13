from dataclasses import replace
from datetime import datetime, timedelta, timezone
import httpx
from app.jobs import Jobs
from app.publications import Publications
from app.worker import Worker
from tests.test_publications import future


def test_polling_lasts_until_actual_schedule(db, settings, approved):
    service = Publications(db)
    publication = service.schedule(approved[1]['id'], (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(), 'channel')
    with db.transaction() as conn:
        conn.execute("UPDATE publications SET status='scheduled',remote_id='r1' WHERE id=?", (publication['id'],))
        conn.execute("UPDATE jobs SET status='done'")
    jobs = Jobs(db)
    jobs.enqueue('reconcile_publication', {'publication_id': publication['id']}, 'poll')
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET attempts=300 WHERE dedupe_key='poll'")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'posts': [{'id': 'r1', 'integration': {'id': 'channel'}, 'state': 'QUEUE'}]}))
    config = replace(settings, postiz_base_url='https://p.example/public/v1', postiz_api_key='key', postiz_integration_id='channel')
    Worker(db, config, postiz_transport=transport).run_once()
    assert db.one("SELECT status FROM jobs WHERE dedupe_key='poll'")['status'] == 'pending'


def test_cancellation_near_release_attempts_remote_delete(db, settings, approved):
    service = Publications(db)
    publication = service.schedule(approved[1]['id'], future(), 'channel')
    with db.transaction() as conn:
        conn.execute("UPDATE publications SET status='scheduled',remote_id='r1',scheduled_at=? WHERE id=?", ((datetime.now(timezone.utc) + timedelta(seconds=40)).isoformat(), publication['id']))
        conn.execute("UPDATE jobs SET status='done'")
    service.cancel(publication['id'])
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={'id': 'r1'} if request.method == 'DELETE' else {'posts': [{'id': 'r1', 'integration': {'id': 'channel'}, 'state': 'QUEUE'}]})
    config = replace(settings, postiz_base_url='https://p.example/public/v1', postiz_api_key='key', postiz_integration_id='channel')
    Worker(db, config, postiz_transport=httpx.MockTransport(handler)).run_once()
    assert calls == ['GET', 'DELETE']
    assert service.get(publication['id'])['status'] == 'cancelled'


def test_cancel_failure_keeps_result_uncertain(db, settings, approved):
    service = Publications(db)
    publication = service.schedule(approved[1]['id'], future(), 'channel')
    with db.transaction() as conn:
        conn.execute("UPDATE publications SET status='scheduled',remote_id='r1' WHERE id=?", (publication['id'],))
        conn.execute("UPDATE jobs SET status='done'")
    service.cancel(publication['id'])
    def handler(request):
        return httpx.Response(500) if request.method == 'DELETE' else httpx.Response(200, json={'posts': [{'id': 'r1', 'integration': {'id': 'channel'}, 'state': 'QUEUE'}]})
    config = replace(settings, postiz_base_url='https://p.example/public/v1', postiz_api_key='key', postiz_integration_id='channel')
    Worker(db, config, postiz_transport=httpx.MockTransport(handler)).run_once()
    assert service.get(publication['id'])['status'] == 'reconciliation_required'


def test_published_result_is_not_deleted_by_pause(db, settings, approved):
    service = Publications(db)
    publication = service.schedule(approved[1]['id'], future(), 'channel')
    with db.transaction() as conn:
        conn.execute("UPDATE publications SET status='scheduled',remote_id='r1' WHERE id=?", (publication['id'],))
        conn.execute("UPDATE jobs SET status='done'")
    service.pause()
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={'posts': [{'id': 'r1', 'integration': {'id': 'channel'}, 'state': 'PUBLISHED', 'releaseURL': 'https://x.com/demo/status/123456'}]})
    config = replace(settings, postiz_base_url='https://p.example/public/v1', postiz_api_key='key', postiz_integration_id='channel')
    Worker(db, config, postiz_transport=httpx.MockTransport(handler)).run_once()
    assert calls == ['GET']
    assert service.get(publication['id'])['platform_post_id'] == '123456'
