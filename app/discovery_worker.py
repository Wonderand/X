"""Persistent discovery tasks, isolated from publication side effects."""
import json
from app.adapters.discovery import DiscoveryAdapter
from app.db import event, now
from app.discovery import Discovery, safe_code
from app.errors import DomainError, ServiceError


class DiscoveryRunner:
    def __init__(self, db, settings, adapter=None):
        self.db = db
        self.service = Discovery(db)
        self.adapter = adapter or DiscoveryAdapter(settings)

    def schedule_due(self):
        return self.service.schedule_due() if self.adapter.configured else 0

    def owns_lease(self, job):
        return bool(self.db.one("SELECT id FROM jobs WHERE id=? AND lease_owner=? AND status='running'", (job['id'], job['lease_owner'])))

    def handle(self, job):
        try:
            if job['attempts'] > 1:
                raise ServiceError('discovery_interrupted_review_before_retry', uncertain=True)
            if job['kind'] == 'discover_sources':
                run_id = job['payload']['run_id']
                if not self.service.begin_run(run_id):
                    return
                run = self.db.one('SELECT * FROM search_runs WHERE id=?', (run_id,))
                result = self.adapter.search(json.loads(run['request_json']))
                if not self.owns_lease(job):
                    raise ServiceError('discovery_lease_lost', uncertain=True)
                self.service.complete_run(run_id, result)
            else:
                self.read(job)
        except Exception as error:
            code = error.code if isinstance(error, ServiceError) else error.message if isinstance(error, DomainError) else 'discovery_internal_error'
            if self.owns_lease(job):
                if job['kind'] == 'discover_sources':
                    self.service.fail_run(job['payload']['run_id'], code)
                else:
                    with self.db.transaction() as conn:
                        conn.execute("UPDATE candidate_reads SET status='failed',last_error=?,finished_at=? WHERE id=? AND status IN ('queued','running')",
                                     (safe_code(code), now(), job['payload']['read_id']))
            if isinstance(error, (ServiceError, DomainError)):
                raise
            raise ServiceError('discovery_internal_error') from None

    def read(self, job):
        read_id = job['payload']['read_id']
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM candidate_reads WHERE id=? AND status='queued'", (read_id,)).fetchone()
            if not row:
                return
            conn.execute("UPDATE candidate_reads SET status='running' WHERE id=?", (read_id,))
            hit = conn.execute('SELECT raw_json FROM candidate_hits WHERE run_id=? AND candidate_id=?', (row['hit_run_id'], row['candidate_id'])).fetchone()
        if not hit:
            raise ServiceError('candidate_read_receipt_missing')
        body = self.adapter.read(json.loads(hit['raw_json']))
        if not isinstance(body, str) or not 200 <= len(body.strip()) <= 100000:
            raise ServiceError('candidate_original_invalid')
        if not self.owns_lease(job):
            raise ServiceError('discovery_lease_lost', uncertain=True)
        with self.db.transaction() as conn:
            conn.execute("UPDATE candidate_reads SET status='ready',body=?,finished_at=? WHERE id=? AND status='running'", (body.strip(), now(), read_id))
            event(conn, 'candidate', row['candidate_id'], 'original_ready_for_review')
