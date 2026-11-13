import hashlib
import json
import re
from app.db import event, now
from app.errors import DomainError
from app.sources import canonical_url


def content_hash(segments):
    return hashlib.sha256(json.dumps(segments, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class Editorial:
    def __init__(self, db):
        self.db = db

    def add_source(self, url, title, body, published_at=None):
        url = canonical_url(url)
        if not title.strip() or not body.strip() or len(body) > 100000:
            raise DomainError('来源标题和正文不能为空，正文最多 100000 字符')
        with self.db.transaction() as conn:
            existing = conn.execute('SELECT * FROM sources WHERE url=?', (url,)).fetchone()
            if existing:
                if existing['title'] != title.strip() or existing['body'] != body.strip():
                    raise DomainError('来源链接已存在；为保持已引用素材稳定，不覆盖正文', 409)
                return dict(existing)
            cursor = conn.execute('INSERT INTO sources(url,title,body,published_at,retrieved_at) VALUES(?,?,?,?,?)',
                                  (url, title.strip()[:300], body.strip(), published_at, now()))
            event(conn, 'source', cursor.lastrowid, 'confirmed', 'admin')
            return dict(conn.execute('SELECT * FROM sources WHERE id=?', (cursor.lastrowid,)).fetchone())

    def create_draft(self, source_ids, language, column):
        if not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', language):
            raise DomainError('请选择明确的输出语言标签，如 zh-CN 或 en')
        if column not in {'news', 'analysis', 'tools'} or not source_ids or len(source_ids) > 10:
            raise DomainError('请选择栏目及 1～10 个来源')
        ids = list(dict.fromkeys(source_ids))
        with self.db.transaction() as conn:
            rows = conn.execute('SELECT id FROM sources WHERE id IN (' + ','.join('?' for _ in ids) + ') AND confirmed=1', ids).fetchall()
            if len(rows) != len(ids):
                raise DomainError('来源不存在或尚未确认')
            cursor = conn.execute('INSERT INTO drafts(source_ids,language,column_name,created_at) VALUES(?,?,?,?)', (json.dumps(ids), language, column, now()))
            event(conn, 'draft', cursor.lastrowid, 'created', 'admin')
            return dict(conn.execute('SELECT * FROM drafts WHERE id=?', (cursor.lastrowid,)).fetchone())

    def get(self, draft_id):
        draft = self.db.one('SELECT * FROM drafts WHERE id=?', (draft_id,))
        if not draft:
            raise DomainError('草稿不存在', 404)
        draft['source_ids'] = json.loads(draft['source_ids'])
        draft['versions'] = self.db.rows('SELECT v.*,a.approved_at FROM draft_versions v LEFT JOIN approvals a ON a.version_id=v.id WHERE draft_id=? ORDER BY version_no DESC', (draft_id,))
        for version in draft['versions']:
            version['segments'] = json.loads(version['segments'])
        return draft

    def save_version(self, draft_id, expected_version, segments, model='manual', editor_notes=''):
        if not isinstance(segments, list) or not 1 <= len(segments) <= 20 or any(not isinstance(s, str) or not s.strip() or len(s) > 10000 for s in segments):
            raise DomainError('内容必须包含 1～20 个非空文本段落')
        segments = [s.strip() for s in segments]
        with self.db.transaction() as conn:
            draft = conn.execute('SELECT * FROM drafts WHERE id=?', (draft_id,)).fetchone()
            if not draft:
                raise DomainError('草稿不存在', 404)
            if draft['latest_version'] != expected_version:
                raise DomainError('草稿已被修改，请刷新后再操作', 409)
            cursor = conn.execute('INSERT INTO draft_versions(draft_id,version_no,segments,source_refs,content_hash,model,editor_notes,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                  (draft_id, expected_version + 1, json.dumps(segments, ensure_ascii=False), draft['source_ids'], content_hash(segments), model, editor_notes[:4000], now()))
            conn.execute("UPDATE drafts SET latest_version=?,status='review_pending',last_error=NULL WHERE id=?", (expected_version + 1, draft_id))
            event(conn, 'draft', draft_id, 'version_created', 'admin' if model == 'manual' else 'generator', version_id=cursor.lastrowid)
            return dict(conn.execute('SELECT * FROM draft_versions WHERE id=?', (cursor.lastrowid,)).fetchone())

    def approve(self, draft_id, version_id):
        with self.db.transaction() as conn:
            version = conn.execute('SELECT v.* FROM draft_versions v JOIN drafts d ON d.id=v.draft_id WHERE v.id=? AND d.id=? AND d.latest_version=v.version_no', (version_id, draft_id)).fetchone()
            if not version or version['content_hash'] != content_hash(json.loads(version['segments'])):
                raise DomainError('只能批准当前内容版本，请刷新后重试', 409)
            conn.execute('INSERT OR IGNORE INTO approvals(version_id,content_hash,actor,approved_at) VALUES(?,?,?,?)', (version_id, version['content_hash'], 'admin', now()))
            conn.execute("UPDATE drafts SET status='approved' WHERE id=?", (draft_id,))
            event(conn, 'draft', draft_id, 'approved', 'admin', version_id=version_id)

    def is_approved(self, version_id):
        return bool(self.db.one('SELECT 1 FROM approvals a JOIN draft_versions v ON v.id=a.version_id WHERE a.version_id=? AND a.content_hash=v.content_hash', (version_id,)))

    def reject(self, draft_id):
        with self.db.transaction() as conn:
            row = conn.execute('SELECT id FROM drafts WHERE id=?', (draft_id,)).fetchone()
            if not row:
                raise DomainError('草稿不存在', 404)
            conn.execute("UPDATE drafts SET status='rejected' WHERE id=?", (draft_id,))
            conn.execute('DELETE FROM approvals WHERE version_id IN (SELECT id FROM draft_versions WHERE draft_id=?)', (draft_id,))
            event(conn, 'draft', draft_id, 'rejected', 'admin')
