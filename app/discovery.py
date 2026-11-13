"""Bounded source discovery. Candidates are never approved sources by default."""
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import re
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
from app.db import event, now
from app.drafts import Editorial
from app.errors import DomainError
from app.jobs import enqueue


def public_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) <= 32 for c in value):
        raise DomainError('invalid_candidate_url')
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
        if parts.scheme != 'https' or not host or parts.username or parts.password or port not in (None, 443):
            raise ValueError()
        host = host.rstrip('.').lower()
        if host in {'localhost', 'localhost.localdomain'} or host.endswith(('.localhost', '.local', '.internal')):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        if address is None and ('.' not in host or re.fullmatch(r'[0-9.xXa-fA-F]+', host)):
            raise ValueError()
        netloc = '[' + host + ']' if ':' in host else host
        return urlunsplit(('https', netloc, parts.path or '/', parts.query, ''))
    except ValueError:
        raise DomainError('invalid_candidate_url') from None


def parsed_date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def safe_code(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_:-]{1,100}', value) else 'backend_error'


class Discovery:
    def __init__(self, db):
        self.db = db
        self.editorial = Editorial(db)

    def configure(self, daily_limit):
        if type(daily_limit) is not int or not 0 <= daily_limit <= 100:
            raise DomainError('发现调用上限需为 0～100，0 表示停用')
        with self.db.transaction() as conn:
            conn.execute('UPDATE discovery_settings SET daily_limit=? WHERE id=1', (daily_limit,))
            if daily_limit == 0:
                conn.execute("UPDATE search_runs SET status='cancelled',finished_at=? WHERE status='queued'", (now(),))
                conn.execute("UPDATE candidate_reads SET status='cancelled',finished_at=? WHERE status='queued'", (now(),))
                conn.execute("UPDATE jobs SET status='done' WHERE kind IN ('discover_sources','read_candidate') AND status='pending'")

            event(conn, 'discovery', 1, 'limit_updated', 'admin', daily_limit=daily_limit)

    def usage(self):
        day = datetime.now(timezone.utc).date().isoformat()
        limit = self.db.one('SELECT daily_limit FROM discovery_settings WHERE id=1')['daily_limit']
        used = self.db.one('SELECT COUNT(*) n FROM discovery_calls WHERE day=?', (day,))['n']
        return {'day': day, 'limit': limit, 'used': used}

    def create_rule(self, query, backend, days=1, limit=10, interval_minutes=0, enabled=False):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 300 or any(ord(c) < 32 for c in query):
            raise DomainError('查询词需为 2～300 个字符，不含控制字符')
        if backend not in {'aihot', 'anysearch'}:
            raise DomainError('请选择已支持的 AI HOT 或 AnySearch 后端')
        if type(days) is not int or not 1 <= days <= 7 or type(limit) is not int or not 1 <= limit <= 10:
            raise DomainError('时间窗为 1～7 天，每次最多 10 条')
        if type(interval_minutes) is not int or interval_minutes != 0 and not 60 <= interval_minutes <= 10080:
            raise DomainError('手动执行间隔为 0，定时间隔为 60～10080 分钟')
        if enabled and not interval_minutes:
            raise DomainError('启用定时前请选择执行间隔')
        with self.db.transaction() as conn:
            if enabled and conn.execute('SELECT daily_limit FROM discovery_settings WHERE id=1').fetchone()[0] < 1:
                raise DomainError('请先设置发现调用上限')
            cursor = conn.execute('INSERT INTO search_rules(query,backend,days,result_limit,interval_minutes,enabled,next_run_at,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                  (query.strip(), backend, days, limit, interval_minutes, int(enabled), time.time(), now()))
            event(conn, 'search_rule', cursor.lastrowid, 'created', 'admin')
            return dict(conn.execute('SELECT * FROM search_rules WHERE id=?', (cursor.lastrowid,)).fetchone())

    def set_enabled(self, rule_id, enabled):
        with self.db.transaction() as conn:
            rule = conn.execute('SELECT * FROM search_rules WHERE id=?', (rule_id,)).fetchone()
            if not rule:
                raise DomainError('搜索规则不存在', 404)
            if enabled and (not rule['interval_minutes'] or conn.execute('SELECT daily_limit FROM discovery_settings').fetchone()[0] < 1):
                raise DomainError('请先设置定时间隔和发现调用上限')
            conn.execute('UPDATE search_rules SET enabled=?,next_run_at=? WHERE id=?', (int(enabled), time.time(), rule_id))
            if not enabled:
                pending = conn.execute("SELECT id FROM search_runs WHERE rule_id=? AND status='queued'", (rule_id,)).fetchall()
                for row in pending:
                    conn.execute("UPDATE search_runs SET status='cancelled',finished_at=? WHERE id=?", (now(), row['id']))
                    conn.execute("UPDATE jobs SET status='done' WHERE dedupe_key=? AND status='pending'", (f"discover:{row['id']}",))
            event(conn, 'search_rule', rule_id, 'enabled' if enabled else 'disabled', 'admin')

    @staticmethod
    def reserve(conn, kind, reference):
        day = datetime.now(timezone.utc).date().isoformat()
        limit = conn.execute('SELECT daily_limit FROM discovery_settings WHERE id=1').fetchone()[0]
        used = conn.execute('SELECT COUNT(*) FROM discovery_calls WHERE day=?', (day,)).fetchone()[0]
        if used >= limit:
            raise DomainError('今日发现调用次数已达上限；失败和未知结果也计入次数', 409)
        conn.execute('INSERT INTO discovery_calls(day,kind,reference,created_at) VALUES(?,?,?,?)', (day, kind, reference, now()))

    def _queue(self, conn, rule):
        previous = conn.execute("SELECT * FROM search_runs WHERE rule_id=? AND status IN ('queued','running')", (rule['id'],)).fetchone()
        if previous:
            return dict(previous)
        request = {'query': rule['query'], 'backend': rule['backend'], 'days': rule['days'], 'limit': rule['result_limit']}
        self.reserve(conn, 'search', 'rule:' + str(rule['id']))
        cursor = conn.execute('INSERT INTO search_runs(rule_id,request_json,backend,status,created_at) VALUES(?,?,?,?,?)',
                              (rule['id'], json.dumps(request, ensure_ascii=False), rule['backend'], 'queued', now()))
        run_id = cursor.lastrowid
        enqueue(conn, 'discover_sources', {'run_id': run_id}, f'discover:{run_id}')
        event(conn, 'search_run', run_id, 'queued')
        return dict(conn.execute('SELECT * FROM search_runs WHERE id=?', (run_id,)).fetchone())

    def queue(self, rule_id):
        with self.db.transaction() as conn:
            rule = conn.execute('SELECT * FROM search_rules WHERE id=?', (rule_id,)).fetchone()
            if not rule:
                raise DomainError('搜索规则不存在', 404)
            return self._queue(conn, rule)

    def schedule_due(self):
        count = 0
        with self.db.transaction() as conn:
            rules = conn.execute('SELECT * FROM search_rules WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at,id LIMIT 10', (time.time(),)).fetchall()
            for rule in rules:
                try:
                    self._queue(conn, rule)
                except DomainError:
                    # Budget exhaustion is a normal scheduler state, not permission to retry paid calls.
                    continue
                conn.execute('UPDATE search_rules SET next_run_at=? WHERE id=?', (time.time() + rule['interval_minutes'] * 60, rule['id']))
                count += 1
        return count

    def begin_run(self, run_id):
        with self.db.transaction() as conn:
            result = conn.execute("UPDATE search_runs SET status='running',started_at=? WHERE id=? AND status='queued'", (now(), run_id))
            return result.rowcount == 1

    def fail_run(self, run_id, code):
        with self.db.transaction() as conn:
            conn.execute("UPDATE search_runs SET status='failed',last_error=?,finished_at=? WHERE id=? AND status IN ('queued','running')", (safe_code(code), now(), run_id))

    def complete_run(self, run_id, envelope):
        if not isinstance(envelope, dict) or envelope.get('schema_version') != '1.0' or not isinstance(envelope.get('candidates'), list) or len(envelope['candidates']) > 100:
            raise DomainError('invalid_search_envelope')
        with self.db.transaction() as conn:
            run = conn.execute("SELECT * FROM search_runs WHERE id=? AND status='running'", (run_id,)).fetchone()
            if not run:
                raise DomainError('search_run_not_running', 409)
            request = json.loads(run['request_json'])
            errors = [safe_code(e.get('category')) for e in envelope.get('errors', []) if isinstance(e, dict)]
            rejected, old, returned = 0, 0, 0
            since = datetime.now(timezone.utc) - timedelta(days=request['days'])
            for rank, candidate in enumerate(envelope['candidates'][:request['limit']], 1):
                try:
                    if not isinstance(candidate, dict) or candidate.get('backend') != run['backend']:
                        raise DomainError('invalid_candidate_backend')
                    url = public_url(candidate.get('canonical_url') or candidate.get('url'))
                    title = candidate.get('title')
                    snippet = candidate.get('snippet') or ''
                    if not isinstance(title, str) or not title.strip() or not isinstance(snippet, str):
                        raise DomainError('invalid_candidate_content')
                    published = parsed_date(candidate.get('published_at'))
                    if published and (published < since or published > datetime.now(timezone.utc) + timedelta(hours=1)):
                        old += 1
                        continue
                    title, snippet = title.strip()[:300], snippet[:4000]
                    day = published.date().isoformat() if published else datetime.now(timezone.utc).date().isoformat()
                    normalized_title = re.sub(r'[\W_]+', '', title.casefold())
                    key = hashlib.sha256((normalized_title + ':' + day).encode()).hexdigest()
                    conn.execute('INSERT OR IGNORE INTO discovery_topics(group_key,title,created_at) VALUES(?,?,?)', (key, title, now()))
                    topic_id = conn.execute('SELECT id FROM discovery_topics WHERE group_key=?', (key,)).fetchone()[0]
                    terms = re.findall(r'[\w]+', request['query'].casefold())
                    relevance = sum(term in (title + ' ' + snippet).casefold() for term in terms) / max(1, len(terms))
                    exists = conn.execute('SELECT id FROM sources WHERE url=?', (url,)).fetchone()
                    score = round(50 * relevance + (20 if published else 0) - (30 if exists else 0), 2)
                    conn.execute('INSERT INTO discovery_candidates(topic_id,canonical_url,title,snippet,backend,platform,published_at,score,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?) '
                                 'ON CONFLICT(canonical_url) DO UPDATE SET last_seen=excluded.last_seen,score=excluded.score',
                                 (topic_id, url, title, snippet, run['backend'], str(candidate.get('platform') or 'web')[:30], published.isoformat() if published else None, score, now(), now()))
                    candidate_id = conn.execute('SELECT id FROM discovery_candidates WHERE canonical_url=?', (url,)).fetchone()[0]
                    conn.execute('INSERT OR IGNORE INTO candidate_hits(run_id,candidate_id,rank,raw_json) VALUES(?,?,?,?)',
                                 (run_id, candidate_id, rank, json.dumps(candidate, ensure_ascii=False)))
                    returned += 1
                except (DomainError, TypeError, ValueError):
                    rejected += 1
            if rejected:
                errors.append('invalid_candidates_rejected')
            coverage = {'backend_report': envelope.get('coverage', []), 'local_returned': returned,
                        'invalid_rejected': rejected, 'outside_window': old, 'date_unknown_is_not_verified': True}
            limitations = ['搜索摘要不是已核验正文；缺少日期的候选不代表符合时间窗。']
            if request['backend'] == 'anysearch':
                limitations.append('通用网页搜索没有服务端严格时间过滤，本地仅排除元数据中已知过期条目。')
            status = 'partial' if errors and returned else 'failed' if errors else 'completed'
            conn.execute('UPDATE search_runs SET status=?,coverage_json=?,errors_json=?,limitations_json=?,candidate_count=?,finished_at=? WHERE id=?',
                         (status, json.dumps(coverage, ensure_ascii=False), json.dumps(errors), json.dumps(limitations, ensure_ascii=False), returned, now(), run_id))
            event(conn, 'search_run', run_id, status, candidate_count=returned)

    def candidates(self, *, include_ignored=False):
        condition = '' if include_ignored else "WHERE c.state!='ignored'"
        return self.db.rows('SELECT c.*,t.title topic_title,(SELECT COUNT(*) FROM candidate_hits h WHERE h.candidate_id=c.id) hit_count FROM discovery_candidates c '
                            'JOIN discovery_topics t ON t.id=c.topic_id ' + condition + ' ORDER BY c.score DESC,c.last_seen DESC,c.id DESC LIMIT 200')

    def candidate(self, candidate_id):
        row = self.db.one('SELECT * FROM discovery_candidates WHERE id=?', (candidate_id,))
        if not row:
            raise DomainError('候选不存在', 404)
        row['read'] = self.db.one('SELECT * FROM candidate_reads WHERE candidate_id=? ORDER BY id DESC LIMIT 1', (candidate_id,))
        row['hits'] = self.db.rows('SELECT h.run_id,h.rank,r.backend,r.created_at,s.query FROM candidate_hits h JOIN search_runs r ON h.run_id=r.id JOIN search_rules s ON s.id=r.rule_id WHERE h.candidate_id=? ORDER BY h.run_id DESC', (candidate_id,))
        return row

    def ignore(self, candidate_id):
        self.candidate(candidate_id)
        with self.db.transaction() as conn:
            conn.execute("UPDATE discovery_candidates SET state='ignored' WHERE id=?", (candidate_id,))
            event(conn, 'candidate', candidate_id, 'ignored', 'admin')

    def queue_read(self, candidate_id):
        candidate = self.candidate(candidate_id)
        if candidate['source_id']:
            raise DomainError('来源已导入，无需重复读取', 409)
        with self.db.transaction() as conn:
            active = conn.execute("SELECT * FROM candidate_reads WHERE candidate_id=? AND status IN ('queued','running')", (candidate_id,)).fetchone()
            if active:
                return dict(active)
            hit = conn.execute("SELECT h.run_id FROM candidate_hits h JOIN search_runs r ON r.id=h.run_id WHERE h.candidate_id=? AND r.backend='anysearch' ORDER BY h.run_id DESC LIMIT 1", (candidate_id,)).fetchone()
            if not hit:
                raise DomainError('该候选没有 AnySearch 原始回执，请打开来源阅读后粘贴正文')
            cursor = conn.execute("INSERT INTO candidate_reads(candidate_id,hit_run_id,status,created_at) VALUES(?,?,'queued',?)", (candidate_id, hit['run_id'], now()))
            self.reserve(conn, 'read', str(cursor.lastrowid))
            enqueue(conn, 'read_candidate', {'read_id': cursor.lastrowid}, f'read:{cursor.lastrowid}')
            event(conn, 'candidate', candidate_id, 'original_read_requested', 'admin', read_id=cursor.lastrowid)
            return dict(conn.execute('SELECT * FROM candidate_reads WHERE id=?', (cursor.lastrowid,)).fetchone())

    def confirm(self, candidate_id, body, *, confirmed=False):
        if confirmed is not True or not isinstance(body, str) or not body.strip():
            raise DomainError('请阅读正文并明确确认，搜索摘要不会自动成为来源')
        candidate = self.candidate(candidate_id)
        source = self.editorial.add_source(candidate['canonical_url'], candidate['title'], body, candidate['published_at'])
        with self.db.transaction() as conn:
            conn.execute("UPDATE discovery_candidates SET source_id=?,state='imported' WHERE id=?", (source['id'], candidate_id))
            event(conn, 'candidate', candidate_id, 'source_confirmed', 'admin', source_id=source['id'])
        return source

    def create_draft(self, candidate_id, language, column):
        candidate = self.candidate(candidate_id)
        if not candidate['source_id']:
            raise DomainError('请先阅读并确认来源正文', 409)
        return self.editorial.create_draft([candidate['source_id']], language, column)

    def split(self, candidate_id):
        candidate = self.candidate(candidate_id)
        with self.db.transaction() as conn:
            topic_id = conn.execute('INSERT INTO discovery_topics(group_key,title,created_at) VALUES(?,?,?)', (uuid.uuid4().hex, candidate['title'], now())).lastrowid
            conn.execute('UPDATE discovery_candidates SET topic_id=? WHERE id=?', (topic_id, candidate_id))
            event(conn, 'candidate', candidate_id, 'topic_split', 'admin')
            return topic_id

    def merge(self, from_topic_id, into_topic_id):
        with self.db.transaction() as conn:
            rows = conn.execute('SELECT id FROM discovery_topics WHERE id IN (?,?)', (from_topic_id, into_topic_id)).fetchall()
            if from_topic_id == into_topic_id or len(rows) != 2:
                raise DomainError('请选择两个不同且存在的分组')
            conn.execute('UPDATE discovery_candidates SET topic_id=? WHERE topic_id=?', (into_topic_id, from_topic_id))
            event(conn, 'topic', into_topic_id, 'topics_merged', 'admin', from_topic_id=from_topic_id)
