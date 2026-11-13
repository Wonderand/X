from app.discovery import Discovery
from app.discovery_worker import DiscoveryRunner
from app.jobs import Jobs
from app.worker import Worker
from tests.test_discovery import envelope, rule


class FakeAdapter:
    configured = True

    def __init__(self):
        self.search_calls = 0
        self.read_calls = 0

    def search(self, request):
        self.search_calls += 1
        return envelope()

    def read(self, candidate):
        self.read_calls += 1
        return 'Reviewed source text. ' * 20


def setup_rule(db):
    service = Discovery(db)
    service.configure(10)
    saved = rule(service)
    return service, saved


def test_existing_worker_dispatches_discovery_without_publishing(db, settings):
    service, saved = setup_rule(db)
    run = service.queue(saved['id'])
    adapter = FakeAdapter()
    worker = Worker(db, settings, discovery_adapter=adapter)
    assert worker.run_once()
    assert db.one('SELECT status FROM search_runs WHERE id=?', (run['id'],))['status'] == 'completed'
    assert adapter.search_calls == 1
    assert db.one('SELECT COUNT(*) n FROM publications')['n'] == 0


def test_interrupted_search_is_not_recharged(db, settings):
    service, saved = setup_rule(db)
    run = service.queue(saved['id'])
    job = Jobs(db).claim('old-worker')
    service.begin_run(run['id'])
    with db.transaction() as conn:
        conn.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (job['id'],))
    adapter = FakeAdapter()
    Worker(db, settings, discovery_adapter=adapter).run_once()
    assert adapter.search_calls == 0
    assert db.one('SELECT status FROM search_runs')['status'] == 'failed'
    assert db.one('SELECT COUNT(*) n FROM discovery_calls')['n'] == 1


def test_original_read_requires_explicit_job_and_does_not_confirm(db, settings):
    service, saved = setup_rule(db)
    adapter = FakeAdapter()
    worker = Worker(db, settings, discovery_adapter=adapter)
    service.queue(saved['id'])
    worker.run_once()
    assert adapter.read_calls == 0
    read = service.queue_read(1)
    worker.run_once()
    assert adapter.read_calls == 1
    assert db.one('SELECT status FROM candidate_reads WHERE id=?', (read['id'],))['status'] == 'ready'
    assert db.one('SELECT COUNT(*) n FROM sources')['n'] == 0
