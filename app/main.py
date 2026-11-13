from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hmac
import secrets
import time
import uuid
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.accounts import update_account
from app.auth import verify_password
from app.config import Settings
from app.db import Database, event
from app.drafts import Editorial
from app.errors import DomainError
from app.jobs import enqueue
from app.publications import Publications


class SourceInput(BaseModel):
    url: str = Field(max_length=2000)
    title: str = Field(max_length=300)
    body: str = Field(max_length=100000)
    published_at: datetime | None = None


class DraftInput(BaseModel):
    source_ids: list[int] = Field(min_length=1, max_length=10)
    column: str
    output_language: str = Field(max_length=35)


class ContentInput(BaseModel):
    expected_version: int = Field(ge=0)
    segments: list[str] = Field(min_length=1, max_length=20)


class ApprovalInput(BaseModel):
    version_id: int


class PublicationInput(BaseModel):
    version_id: int
    scheduled_at: str


def create_app(settings=None):
    settings = settings or Settings.from_env()
    db = Database(settings.data_dir)
    editorial, publications = Editorial(db), Publications(db)
    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        yield

    app = FastAPI(title='X Operations', version='0.1.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db
    app.state.settings = settings
    root = Path(__file__).parent
    templates = Jinja2Templates(directory=str(root / 'templates'))
    app.mount('/static', StaticFiles(directory=str(root / 'static')), name='static')
    failures = {}

    def page(request, template, **context):
        csrf = request.session.setdefault('csrf', secrets.token_urlsafe(32))
        account = db.one('SELECT * FROM accounts WHERE id=1')
        def local_time(value):
            if not value:
                return '—'
            try:
                return datetime.fromisoformat(value).astimezone(ZoneInfo(account['timezone'] or 'UTC')).strftime('%m-%d %H:%M %Z')
            except ValueError:
                return value
        response = templates.TemplateResponse(request=request, name=template, context={
            'csrf': csrf, 'account': account, 'generator_ready': settings.generator_ready,
            'publisher_ready': settings.publisher_ready, 'auth_ready': settings.auth_ready,
            'local_time': local_time, **context,
        })
        response.set_cookie('xops_csrf', csrf, secure=settings.secure_cookie, samesite='strict', max_age=28800)
        return response

    def check_csrf(request, token):
        expected = request.session.get('csrf', '')
        cookie = request.cookies.get('xops_csrf', '')
        if not expected or not isinstance(token, str) or not hmac.compare_digest(expected, token) or not hmac.compare_digest(expected, cookie):
            raise DomainError('页面已过期或请求校验失败，请刷新重试', 403)

    async def api_csrf(request: Request):
        check_csrf(request, request.headers.get('X-CSRF-Token', ''))

    @app.middleware('http')
    async def access_control(request, call_next):
        path = request.url.path
        public = path in {'/login', '/api/health'} or path.startswith('/static/')
        if not public and (not settings.auth_ready or not request.session.get('user')):
            if path.startswith('/api/'):
                return JSONResponse({'error': 'authentication_required'}, status_code=401)
            return RedirectResponse('/login', status_code=303)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
        return response

    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or secrets.token_urlsafe(48), session_cookie='xops_session', max_age=28800, same_site='strict', https_only=settings.secure_cookie)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[x.strip() for x in settings.allowed_hosts.split(',') if x.strip()])

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        if request.url.path.startswith('/api/'):
            return JSONResponse({'error': error.message}, status_code=error.status)
        response = page(request, 'error.html', error=error.message)
        response.status_code = error.status
        return response

    @app.get('/api/health')
    def health():
        return {'status': 'ok'}

    @app.get('/login', response_class=HTMLResponse)
    def login_page(request: Request):
        return page(request, 'login.html')

    @app.post('/login')
    async def login(request: Request):
        form = await request.form()
        check_csrf(request, form.get('csrf_token', ''))
        address = request.client.host if request.client else 'unknown'
        timestamp = time.monotonic()
        attempts = [t for t in failures.get(address, []) if t > timestamp - 300]
        if len(attempts) >= 5:
            raise DomainError('登录尝试过多，请五分钟后重试', 429)
        if not settings.auth_ready:
            raise DomainError('请先在终端运行 python -m app.auth init', 503)
        if not verify_password(str(form.get('password', ''))[:1024], settings.admin_password_hash):
            failures[address] = attempts + [timestamp]
            raise DomainError('密码不正确', 401)
        failures.pop(address, None)
        request.session.clear()
        request.session.update(user='admin', csrf=secrets.token_urlsafe(32))
        response = RedirectResponse('/', status_code=303)
        response.set_cookie('xops_csrf', request.session['csrf'], secure=settings.secure_cookie, samesite='strict', max_age=28800)
        return response

    @app.post('/logout')
    async def logout(request: Request):
        form = await request.form()
        check_csrf(request, form.get('csrf_token', ''))
        request.session.clear()
        response = RedirectResponse('/login', status_code=303)
        response.delete_cookie('xops_csrf')
        return response

    @app.get('/', response_class=HTMLResponse)
    def dashboard(request: Request):
        return page(request, 'dashboard.html', stats={
            'sources': db.one('SELECT COUNT(*) n FROM sources')['n'],
            'drafts': db.one("SELECT COUNT(*) n FROM drafts WHERE status='review_pending'")['n'],
            'scheduled': db.one("SELECT COUNT(*) n FROM publications WHERE status='scheduled'")['n'],
            'attention': db.one("SELECT COUNT(*) n FROM publications WHERE status IN ('failed','reconciliation_required')")['n'],
        }, events=db.rows('SELECT * FROM events ORDER BY id DESC LIMIT 12'), jobs=db.rows('SELECT * FROM jobs ORDER BY id DESC LIMIT 8'))

    @app.get('/sources', response_class=HTMLResponse)
    def sources_page(request: Request):
        return page(request, 'sources.html', sources=db.rows('SELECT * FROM sources ORDER BY id DESC'))

    @app.get('/drafts', response_class=HTMLResponse)
    def drafts_page(request: Request):
        return page(request, 'drafts.html', drafts=db.rows('SELECT * FROM drafts ORDER BY id DESC'), sources=db.rows('SELECT id,title FROM sources ORDER BY id DESC'))

    @app.get('/drafts/{draft_id}', response_class=HTMLResponse)
    def draft_page(request: Request, draft_id: int):
        draft = editorial.get(draft_id)
        return page(request, 'draft.html', draft=draft, sources=[db.one('SELECT * FROM sources WHERE id=?', (i,)) for i in draft['source_ids']])

    @app.get('/calendar', response_class=HTMLResponse)
    def calendar_page(request: Request):
        return page(request, 'calendar.html', publications=db.rows('SELECT p.*,v.segments,v.draft_id FROM publications p JOIN draft_versions v ON p.version_id=v.id ORDER BY scheduled_at DESC'))

    @app.get('/settings', response_class=HTMLResponse)
    def settings_page(request: Request):
        return page(request, 'settings.html')

    def queue_generation(draft_id):
        if not settings.generator_ready:
            raise DomainError('模型服务未配置，请在 .env 配置后重启服务', 503)
        with db.transaction() as conn:
            draft = conn.execute('SELECT * FROM drafts WHERE id=?', (draft_id,)).fetchone()
            if not draft:
                raise DomainError('草稿不存在', 404)
            if draft['status'] == 'generating':
                raise DomainError('此草稿正在生成', 409)
            account = conn.execute('SELECT * FROM accounts WHERE id=1').fetchone()
            if not account['timezone'] or account['generation_limit'] < 1 or not account['audience']:
                raise DomainError('请先在设置中填写读者、时区和每日生成次数')
            conn.execute("UPDATE drafts SET status='generating',last_error=NULL WHERE id=?", (draft_id,))
            enqueue(conn, 'generate_draft', {'draft_id': draft_id, 'expected_version': draft['latest_version']}, 'generate:' + uuid.uuid4().hex)
            event(conn, 'draft', draft_id, 'generation_queued', 'admin')
        return {'status': 'queued'}

    @app.get('/api/sources')
    def list_sources():
        return db.rows('SELECT * FROM sources ORDER BY id DESC')

    @app.post('/api/sources', dependencies=[Depends(api_csrf)])
    def add_source(data: SourceInput):
        return editorial.add_source(data.url, data.title, data.body, data.published_at.isoformat() if data.published_at else None)

    @app.post('/api/drafts', dependencies=[Depends(api_csrf)])
    def add_draft(data: DraftInput):
        return editorial.create_draft(data.source_ids, data.output_language, data.column)

    @app.get('/api/drafts/{draft_id}')
    def get_draft(draft_id: int):
        return editorial.get(draft_id)

    @app.put('/api/drafts/{draft_id}/content', dependencies=[Depends(api_csrf)])
    def save_draft(draft_id: int, data: ContentInput):
        return editorial.save_version(draft_id, data.expected_version, data.segments)

    @app.post('/api/drafts/{draft_id}/approve', dependencies=[Depends(api_csrf)])
    def approve(draft_id: int, data: ApprovalInput):
        editorial.approve(draft_id, data.version_id)
        return {'status': 'approved'}

    @app.post('/api/drafts/{draft_id}/generate', dependencies=[Depends(api_csrf)])
    def generate(draft_id: int):
        return queue_generation(draft_id)

    @app.post('/api/publications', dependencies=[Depends(api_csrf)])
    def schedule(data: PublicationInput):
        if not settings.publisher_ready:
            raise DomainError('Postiz 未配置', 503)
        return publications.schedule(data.version_id, data.scheduled_at, settings.postiz_integration_id)

    @app.get('/api/publications/{publication_id}')
    def publication(publication_id: int):
        return publications.get(publication_id)

    def manual_reconcile(publication_id):
        publication = publications.get(publication_id)
        if publication['status'] in {'published', 'cancelled', 'ready'}:
            raise DomainError('当前状态无需核对', 409)
        with db.transaction() as conn:
            conn.execute("UPDATE publications SET status='reconciliation_required' WHERE id=?", (publication_id,))
            enqueue(conn, 'reconcile_publication', {'publication_id': publication_id}, 'manual:' + uuid.uuid4().hex)

    @app.post('/api/publications/{publication_id}/reconcile', dependencies=[Depends(api_csrf)])
    def reconcile(publication_id: int):
        manual_reconcile(publication_id)
        return {'status': 'queued'}

    @app.post('/api/publications/{publication_id}/cancel', dependencies=[Depends(api_csrf)])
    def cancel(publication_id: int):
        publications.cancel(publication_id)
        return publications.get(publication_id)

    @app.post('/api/accounts/1/pause', dependencies=[Depends(api_csrf)])
    def pause():
        publications.pause()
        return {'status': 'paused', 'note': '远端排期取消结果请在发布日历查看'}

    @app.post('/actions/{action}')
    async def form_action(request: Request, action: str):
        form = await request.form()
        check_csrf(request, form.get('csrf_token', ''))
        destination = '/'
        try:
            if action == 'source':
                editorial.add_source(str(form['url']), str(form['title']), str(form['body']))
                destination = '/sources'
            elif action == 'draft':
                draft = editorial.create_draft([int(i) for i in form.getlist('source_ids')], str(form['language']), str(form['column']))
                destination = f"/drafts/{draft['id']}"
            elif action in {'save', 'approve', 'reject', 'generate', 'schedule'}:
                draft_id = int(form['draft_id'])
                destination = f'/drafts/{draft_id}'
                if action == 'save':
                    editorial.save_version(draft_id, int(form['expected_version']), str(form['content']).split('\n---\n'))
                elif action == 'approve':
                    editorial.approve(draft_id, int(form['version_id']))
                elif action == 'reject':
                    editorial.reject(draft_id)
                elif action == 'generate':
                    queue_generation(draft_id)
                else:
                    if not settings.publisher_ready:
                        raise DomainError('Postiz 未配置', 503)
                    publications.schedule(int(form['version_id']), str(form['scheduled_at']), settings.postiz_integration_id)
                    destination = '/calendar'
            elif action == 'settings':
                update_account(db, dict(form))
                destination = '/settings'
            elif action == 'pause':
                publications.pause()
                destination = '/calendar'
            elif action == 'resume':
                publications.resume()
                destination = '/settings'
            elif action in {'cancel', 'reconcile'}:
                publication_id = int(form['publication_id'])
                publications.cancel(publication_id) if action == 'cancel' else manual_reconcile(publication_id)
                destination = '/calendar'
            else:
                raise DomainError('操作不存在', 404)
        except (ValueError, KeyError):
            raise DomainError('表单内容不完整或格式错误') from None
        return RedirectResponse(destination, status_code=303)

    return app
