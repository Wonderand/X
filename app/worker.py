"""Run with python -m app.worker. No external service is called at import time."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import argparse
import json
import re
import time
import uuid
from app.adapters.generator import Generator
from app.adapters.postiz import Postiz
from app.config import Settings
from app.discovery_worker import DiscoveryRunner
from app.db import Database, event, now
from app.drafts import Editorial
from app.errors import DomainError, ServiceError
from app.jobs import Jobs, enqueue
from app.publications import Publications, parse_time


class Worker:
    def __init__(self, db, settings, *, generator_transport=None, postiz_transport=None, discovery_adapter=None):
        self.db, self.settings = db, settings
        self.jobs = Jobs(db)
        self.editorial = Editorial(db)
        self.publications = Publications(db)
        self.generator = Generator(settings, generator_transport)
        self.publisher = Postiz(settings, postiz_transport)
        self.owner = uuid.uuid4().hex
        self.discovery = DiscoveryRunner(db, settings, discovery_adapter)

    def run_once(self):
        self.discovery.schedule_due()
        job = self.jobs.claim(self.owner)
        if not job:
            return False
        try:
            if job['kind'] in {'discover_sources', 'read_candidate'}:
                self.discovery.handle(job)
                delay = None
            elif job['kind'] == 'generate_draft':
                self.generate(job)
                delay = None
            elif job['kind'] == 'submit_publication':
                delay = self.submit(job)
            elif job['kind'] == 'reconcile_publication':
                delay = self.reconcile(job)
            else:
                raise DomainError('unknown_job')
            self.jobs.finish(job, self.owner, delay=delay)
        except (DomainError, ServiceError) as error:
            code = error.code if isinstance(error, ServiceError) else error.message
            self.jobs.finish(job, self.owner, error=code)
            with self.db.transaction() as conn:
                if job['kind'] == 'generate_draft':
                    conn.execute("UPDATE drafts SET status='review_pending',last_error=? WHERE id=? AND status='generating'", (code, job['payload']['draft_id']))
                event(conn, 'job', job['id'], 'failed', code=code)
        except Exception:
            # Never print provider responses or credentials in worker logs.
            self.jobs.finish(job, self.owner, error='internal_error')
            with self.db.transaction() as conn:
                if job['kind'] == 'generate_draft':
                    conn.execute("UPDATE drafts SET status='review_pending',last_error='internal_error' WHERE id=? AND status='generating'", (job['payload']['draft_id'],))
                elif job['kind'] in {'submit_publication', 'reconcile_publication'}:
                    conn.execute("UPDATE publications SET status='reconciliation_required',last_error='internal_error' WHERE id=? AND status NOT IN ('published','cancelled')", (job['payload']['publication_id'],))
                event(conn, 'job', job['id'], 'failed', code='internal_error')
        return True

    def generate(self, job):
        draft_id = job['payload']['draft_id']
        # A crashed billable generation is not automatically resubmitted.
        if job['attempts'] > 1:
            raise ServiceError('generation_interrupted_review_before_retry', uncertain=True)
        if not self.settings.generator_ready:
            raise ServiceError('generator_not_configured')
        draft = self.editorial.get(draft_id)
        with self.db.transaction() as conn:
            account = dict(conn.execute('SELECT * FROM accounts WHERE id=1').fetchone())
            if not account['timezone']:
                raise DomainError('请先配置时区和每日生成上限')
            day = datetime.now(ZoneInfo(account['timezone'])).date().isoformat()
            used = conn.execute('SELECT COUNT(*) FROM usage_records WHERE day=?', (day,)).fetchone()[0]
            if used >= account['generation_limit']:
                raise DomainError('每日生成次数已达到设置上限')
            usage_id = conn.execute("INSERT INTO usage_records(draft_id,day,status,created_at) VALUES(?,?,'reserved',?)", (draft_id, day, now())).lastrowid
        sources = [self.db.one('SELECT * FROM sources WHERE id=?', (source_id,)) for source_id in draft['source_ids']]
        result = self.generator.generate(account, sources, draft['column_name'], draft['language'])
        self.editorial.save_version(draft_id, job['payload']['expected_version'], result['segments'], self.settings.llm_model,
                                    json.dumps({'notes': result.get('editor_notes', ''), 'source_ids': result['source_ids'], 'claim_refs': result.get('claim_refs', [])}, ensure_ascii=False))
        with self.db.transaction() as conn:
            conn.execute("UPDATE usage_records SET status='completed',usage_json=? WHERE id=?", (json.dumps(result['usage']), usage_id))

    def set_publication(self, publication_id, status, *, remote_id=None, code=None, release_url=None, post_id=None):
        with self.db.transaction() as conn:
            conn.execute('UPDATE publications SET status=?,remote_id=COALESCE(?,remote_id),last_error=?,release_url=COALESCE(?,release_url),platform_post_id=COALESCE(?,platform_post_id),updated_at=? WHERE id=?',
                         (status, remote_id, code, release_url, post_id, now(), publication_id))
            event(conn, 'publication', publication_id, status, code=code)

    def submit(self, job):
        publication_id = job['payload']['publication_id']
        with self.db.transaction() as conn:
            row = dict(conn.execute('SELECT * FROM publications WHERE id=?', (publication_id,)).fetchone())
            if row['status'] in {'submitting', 'reconciliation_required'}:
                conn.execute("UPDATE publications SET status='reconciliation_required' WHERE id=?", (publication_id,))
                enqueue(conn, 'reconcile_publication', {'publication_id': publication_id}, f'reconcile:{publication_id}')
                return None
            if row['status'] != 'ready':
                return None
            if conn.execute('SELECT paused FROM accounts WHERE id=1').fetchone()[0] or row['cancel_requested']:
                conn.execute("UPDATE publications SET status='cancelled',updated_at=? WHERE id=?", (now(), publication_id))
                return None
            approved = conn.execute('SELECT 1 FROM approvals a JOIN draft_versions v ON v.id=a.version_id JOIN drafts d ON d.id=v.draft_id WHERE a.version_id=? AND a.content_hash=v.content_hash AND d.latest_version=v.version_no AND d.status=\'approved\'', (row['version_id'],)).fetchone()
            if not approved:
                conn.execute("UPDATE publications SET status='failed',last_error='approval_changed' WHERE id=?", (publication_id,))
                return None
            if parse_time(row['scheduled_at']) <= datetime.now(timezone.utc) + timedelta(seconds=60):
                conn.execute("UPDATE publications SET status='failed',last_error='schedule_expired_review_required' WHERE id=?", (publication_id,))
                return None
            if not self.settings.publisher_ready or self.settings.postiz_integration_id != row['target']:
                conn.execute("UPDATE publications SET status='failed',last_error='publisher_configuration_changed' WHERE id=?", (publication_id,))
                return None
            conn.execute("UPDATE publications SET status='submitting',updated_at=? WHERE id=?", (now(), publication_id))
            event(conn, 'publication', publication_id, 'submitting')
        publication = self.publications.get(publication_id)
        try:
            remote_id = self.publisher.schedule(publication['segments'][0], publication['scheduled_at'], publication['target'])
            self.set_publication(publication_id, 'scheduled', remote_id=remote_id)
        except ServiceError as error:
            if error.code == 'postiz_http_429' and job['attempts'] < 5:
                self.set_publication(publication_id, 'ready', code=error.code)
                return error.retry_after
            self.set_publication(publication_id, 'reconciliation_required' if error.uncertain else 'failed', code=error.code)
            if error.code in {'postiz_http_401', 'postiz_http_403'}:
                self.publications.pause()
            if not error.uncertain:
                return None
        self.jobs.enqueue('reconcile_publication', {'publication_id': publication_id}, f'reconcile:{publication_id}')
        return None

    def reconcile(self, job):
        publication_id = job['payload']['publication_id']
        publication = self.publications.get(publication_id)
        if publication['status'] in {'published', 'cancelled', 'failed', 'ready'}:
            return None
        try:
            remote = self.publisher.inspect(publication)
            state = remote.get('state')
            if state == 'PUBLISHED':
                release = remote.get('releaseURL', '')
                match = re.fullmatch(r'https://(?:www\.)?(?:x\.com|twitter\.com)/(?:[^/]+/status|i/web/status)/(\d+)', release or '')
                self.set_publication(publication_id, 'published', remote_id=remote['id'], release_url=release if match else None,
                                     post_id=match.group(1) if match else None, code=None if match else 'published_without_verified_url')
                return None
            if state == 'ERROR':
                self.set_publication(publication_id, 'failed', remote_id=remote['id'], code='remote_publish_error')
                return None
            if state != 'QUEUE':
                raise ServiceError('postiz_unexpected_state')
            # Recheck a pause that may have arrived while querying the remote.
            fresh = self.publications.get(publication_id)
            paused = self.db.one('SELECT paused FROM accounts WHERE id=1')['paused']
            if fresh['cancel_requested'] or paused:
                self.publisher.cancel(remote['id'])
                self.set_publication(publication_id, 'cancelled', remote_id=remote['id'])
                return None
            self.set_publication(publication_id, 'scheduled', remote_id=remote['id'])
            with self.db.transaction() as conn:
                conn.execute('UPDATE jobs SET failure_streak=0 WHERE id=?', (job['id'],))
            if datetime.now(timezone.utc) > parse_time(publication['scheduled_at']) + timedelta(days=3):
                self.set_publication(publication_id, 'reconciliation_required', code='polling_window_expired_manual_check')
                return None
            return 300
        except ServiceError as error:
            self.set_publication(publication_id, 'reconciliation_required', code=error.code)
            with self.db.transaction() as conn:
                conn.execute('UPDATE jobs SET failure_streak=failure_streak+1 WHERE id=?', (job['id'],))
            if error.code in {'postiz_http_401', 'postiz_http_403'}:
                self.publications.pause()
            return error.retry_after if job['failure_streak'] < 11 else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Process one due task and exit')
    args = parser.parse_args()
    settings = Settings.from_env()
    db = Database(settings.data_dir)
    db.initialize()
    worker = Worker(db, settings)
    try:
        while True:
            handled = worker.run_once()
            if args.once:
                break
            if not handled:
                time.sleep(2)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
