from dataclasses import replace
import httpx
from app.jobs import Jobs
from app.publications import Publications
from app.worker import Worker
from tests.test_publications import future


def test_job_only_claimed_once(db):
    jobs = Jobs(db)
    jobs.enqueue('example', {'value': 1}, 'unique')
    assert jobs.claim('worker-a')
    assert jobs.claim('worker-b') is None


def test_uncertain_create_only_reconciles(db, settings, approved):
    publication = Publications(db).schedule(approved[1]['id'], future(), 'channel')
    calls = []
    def handle(request):
        calls.append(request.method)
        if request.method == 'POST':
            raise httpx.ReadTimeout('response lost', request=request)
        return httpx.Response(200, json={'posts': []})
    config = replace(settings, postiz_base_url='https://postiz.example/public/v1', postiz_api_key='key', postiz_integration_id='channel')
    worker = Worker(db, config, postiz_transport=httpx.MockTransport(handle))
    worker.run_once()
    assert Publications(db).get(publication['id'])['status'] == 'reconciliation_required'
    worker.run_once()
    assert calls.count('POST') == 1
    assert 'GET' in calls


def test_expired_submit_job_does_not_resend(db, settings, approved):
    publication = Publications(db).schedule(approved[1]['id'], future(), 'channel')
    job = Jobs(db).claim('dead-worker')
    with db.transaction() as conn:
        conn.execute("UPDATE publications SET status='submitting' WHERE id=?", (publication['id'],))
        conn.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (job['id'],))
    calls = []
    config = replace(settings, postiz_base_url='https://postiz.example/public/v1', postiz_api_key='key', postiz_integration_id='channel')
    transport = httpx.MockTransport(lambda r: (calls.append(r.method) or httpx.Response(200, json={'posts': []})))
    Worker(db, config, postiz_transport=transport).run_once()
    assert 'POST' not in calls


def test_pause_cancels_local_ready_without_remote_create(db, approved):
    publication = Publications(db).schedule(approved[1]['id'], future(), 'channel')
    Publications(db).pause()
    assert Publications(db).get(publication['id'])['status'] == 'cancelled'
