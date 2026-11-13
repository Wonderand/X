import json
import time


def enqueue(conn, kind, payload, key, delay=0):
    conn.execute('INSERT INTO jobs(kind,payload,dedupe_key,run_after) VALUES(?,?,?,?) ON CONFLICT(dedupe_key) DO NOTHING',
                 (kind, json.dumps(payload), key, time.time() + delay))


class Jobs:
    def __init__(self, db):
        self.db = db

    def enqueue(self, kind, payload, key, delay=0):
        with self.db.transaction() as conn:
            enqueue(conn, kind, payload, key, delay)

    def claim(self, owner):
        timestamp = time.time()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE (status='pending' AND run_after<=?) OR (status='running' AND lease_until<?) ORDER BY run_after,id LIMIT 1", (timestamp, timestamp)).fetchone()
            if not row:
                return None
            conn.execute("UPDATE jobs SET status='running',lease_owner=?,lease_until=?,attempts=attempts+1 WHERE id=?", (owner, timestamp + 180, row['id']))
            result = dict(conn.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
            result['payload'] = json.loads(result['payload'])
            return result

    def finish(self, job, owner, *, error=None, delay=None):
        with self.db.transaction() as conn:
            conn.execute('UPDATE jobs SET status=?,last_error=?,run_after=?,lease_owner=NULL,lease_until=NULL WHERE id=? AND lease_owner=?',
                         ('pending' if delay is not None else ('failed' if error else 'done'), error, time.time() + (delay or 0), job['id'], owner))
