from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
from app.db import event, now
from app.errors import DomainError
from app.jobs import enqueue
from app.drafts import content_hash


def parse_time(value):
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError()
        return timestamp.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        raise DomainError('排期时间必须包含时区，例如 2027-01-01T09:00:00+08:00') from None


class Publications:
    def __init__(self, db):
        self.db = db

    def get(self, publication_id):
        row = self.db.one('SELECT p.*,v.segments,v.draft_id FROM publications p JOIN draft_versions v ON v.id=p.version_id WHERE p.id=?', (publication_id,))
        if not row:
            raise DomainError('发布任务不存在', 404)
        row['segments'] = json.loads(row['segments'])
        return row

    def schedule(self, version_id, scheduled_at, target):
        if not target:
            raise DomainError('请先配置 Postiz 目标账号', 503)
        scheduled = parse_time(scheduled_at)
        if scheduled < datetime.now(timezone.utc) + timedelta(minutes=2):
            raise DomainError('请选择至少两分钟后的发布时间')
        with self.db.transaction() as conn:
            old = conn.execute("SELECT * FROM publications WHERE version_id=? AND status!='cancelled' ORDER BY id DESC LIMIT 1", (version_id,)).fetchone()
            if old:
                return dict(old)
            if conn.execute('SELECT 1 FROM publications WHERE version_id=? AND target=? AND scheduled_at=?', (version_id, target, scheduled.isoformat())).fetchone():
                raise DomainError('该时间的发布意图已取消，请明确选择新的排期时间', 409)
            account = dict(conn.execute('SELECT * FROM accounts WHERE id=1').fetchone())
            if account['paused'] or not account['timezone'] or account['daily_limit'] < 1:
                raise DomainError('请完成账号设置并启用发布', 409)
            version = conn.execute('SELECT v.*,d.latest_version,d.status FROM draft_versions v JOIN drafts d ON v.draft_id=d.id JOIN approvals a ON a.version_id=v.id AND a.content_hash=v.content_hash WHERE v.id=?', (version_id,)).fetchone()
            if not version or version['version_no'] != version['latest_version'] or version['status'] != 'approved':
                raise DomainError('内容当前版本尚未批准', 409)
            segments = json.loads(version['segments'])
            if content_hash(segments) != version['content_hash']:
                raise DomainError('内容校验失败', 409)
            if len(segments) != 1:
                raise DomainError('M1 仅支持单条文字发布；请将 Thread 保存为草稿')
            # Conservative upper bound: all non-ASCII code points count as two;
            # URLs count in full. This deliberately rejects some otherwise valid posts.
            if sum(1 if ord(char) < 128 else 2 for char in segments[0]) > 280:
                raise DomainError('超过保守发布上限（ASCII 280、其他字符按 2 计，链接按全文计算）')
            local_day = scheduled.astimezone(ZoneInfo(account['timezone'])).date()
            existing = conn.execute("SELECT scheduled_at FROM publications WHERE status NOT IN ('cancelled','failed')").fetchall()
            count = sum(parse_time(row['scheduled_at']).astimezone(ZoneInfo(account['timezone'])).date() == local_day for row in existing)
            if count >= account['daily_limit']:
                raise DomainError('该日发布数量已达到设置上限', 409)
            cursor = conn.execute('INSERT INTO publications(version_id,target,scheduled_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                                  (version_id, target, scheduled.isoformat(), 'ready', now(), now()))
            publication_id = cursor.lastrowid
            enqueue(conn, 'submit_publication', {'publication_id': publication_id}, f'submit:{publication_id}')
            event(conn, 'publication', publication_id, 'intent_created', 'admin')
            return dict(conn.execute('SELECT * FROM publications WHERE id=?', (publication_id,)).fetchone())

    def cancel(self, publication_id):
        with self.db.transaction() as conn:
            row = conn.execute('SELECT * FROM publications WHERE id=?', (publication_id,)).fetchone()
            if not row:
                raise DomainError('发布任务不存在', 404)
            if row['status'] == 'published':
                raise DomainError('内容已经发布，此入口不删除已发布帖子', 409)
            if row['status'] == 'cancelled':
                return
            status = 'cancelled' if row['status'] in {'ready', 'failed'} and not row['remote_id'] else row['status']
            conn.execute('UPDATE publications SET cancel_requested=1,status=?,updated_at=? WHERE id=?', (status, now(), publication_id))
            if status != 'cancelled':
                enqueue(conn, 'reconcile_publication', {'publication_id': publication_id}, f'cancel:{publication_id}')
            event(conn, 'publication', publication_id, 'cancellation_requested', 'admin')

    def pause(self):
        with self.db.transaction() as conn:
            conn.execute('UPDATE accounts SET paused=1 WHERE id=1')
            conn.execute("UPDATE publications SET status='cancelled',cancel_requested=1,updated_at=? WHERE status='ready'", (now(),))
            for row in conn.execute("SELECT id FROM publications WHERE status IN ('submitting','scheduled','reconciliation_required')").fetchall():
                conn.execute('UPDATE publications SET cancel_requested=1 WHERE id=?', (row['id'],))
                enqueue(conn, 'reconcile_publication', {'publication_id': row['id']}, f"pause:{row['id']}")
            event(conn, 'account', 1, 'paused', 'admin')

    def resume(self):
        with self.db.transaction() as conn:
            account = conn.execute('SELECT * FROM accounts WHERE id=1').fetchone()
            if not account['timezone'] or account['daily_limit'] < 1 or not account['audience']:
                raise DomainError('请先完成账号设置')
            conn.execute('UPDATE accounts SET paused=0 WHERE id=1')
            event(conn, 'account', 1, 'resumed', 'admin')
