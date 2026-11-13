"""Discovery pages and APIs reuse existing authentication and CSRF helpers."""
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from app.adapters.discovery import DiscoveryAdapter
from app.discovery import Discovery
from app.errors import DomainError


class DiscoverySettingsInput(BaseModel):
    daily_limit: int = Field(ge=0, le=100)


class RuleInput(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    backend: str
    days: int = Field(default=1, ge=1, le=7)
    limit: int = Field(default=5, ge=1, le=10)
    interval_minutes: int = Field(default=0, ge=0, le=10080)
    enabled: bool = False


class EnabledInput(BaseModel):
    enabled: bool


class ConfirmInput(BaseModel):
    body: str = Field(min_length=1, max_length=100000)
    confirmed: bool = False


class CandidateDraftInput(BaseModel):
    output_language: str = Field(max_length=35)
    column: str


class MergeInput(BaseModel):
    from_topic_id: int
    into_topic_id: int


def register_discovery(app, db, settings, page, api_csrf, check_csrf):
    discovery = Discovery(db)
    adapter = DiscoveryAdapter(settings)

    def require_runtime():
        if not adapter.configured:
            raise DomainError('发现运行时未配置：请配置 XOPS_SKILLS_ROOT 并重启 Web 和 worker', 503)

    @app.get('/discovery')
    def discovery_page(request: Request):
        return page(request, 'discovery.html',
                    rules=db.rows('SELECT * FROM search_rules ORDER BY id DESC'),
                    runs=db.rows('SELECT r.*,s.query FROM search_runs r JOIN search_rules s ON r.rule_id=s.id ORDER BY r.id DESC LIMIT 30'),
                    usage=discovery.usage(), runtime_ready=adapter.configured)

    @app.get('/discovery/candidates')
    def candidates_page(request: Request):
        return page(request, 'candidates.html', candidates=discovery.candidates(),
                    topics=db.rows('SELECT t.id,t.title,COUNT(c.id) n FROM discovery_topics t JOIN discovery_candidates c ON c.topic_id=t.id GROUP BY t.id ORDER BY t.id DESC LIMIT 100'))

    @app.get('/discovery/candidates/{candidate_id}')
    def candidate_page(request: Request, candidate_id: int):
        return page(request, 'candidate.html', candidate=discovery.candidate(candidate_id), runtime_ready=adapter.configured)

    @app.get('/api/discovery/candidates')
    def candidates():
        return discovery.candidates()

    @app.get('/api/discovery/candidates/{candidate_id}')
    def candidate(candidate_id: int):
        return discovery.candidate(candidate_id)

    @app.get('/api/discovery/runs')
    def runs():
        return db.rows('SELECT * FROM search_runs ORDER BY id DESC LIMIT 100')

    @app.get('/api/discovery/settings')
    def get_settings():
        return {**discovery.usage(), 'runtime_ready': adapter.configured}

    @app.post('/api/discovery/settings', dependencies=[Depends(api_csrf)])
    def configure(data: DiscoverySettingsInput):
        discovery.configure(data.daily_limit)
        return discovery.usage()

    @app.post('/api/discovery/rules', dependencies=[Depends(api_csrf)])
    def create_rule(data: RuleInput):
        if data.enabled:
            require_runtime()
        return discovery.create_rule(**data.model_dump())

    @app.post('/api/discovery/rules/{rule_id}/run', dependencies=[Depends(api_csrf)])
    def run_rule(rule_id: int):
        require_runtime()
        return discovery.queue(rule_id)

    @app.post('/api/discovery/rules/{rule_id}/enabled', dependencies=[Depends(api_csrf)])
    def set_enabled(rule_id: int, data: EnabledInput):
        if data.enabled:
            require_runtime()
        discovery.set_enabled(rule_id, data.enabled)
        return {'enabled': data.enabled}

    @app.post('/api/discovery/candidates/{candidate_id}/read', dependencies=[Depends(api_csrf)])
    def read_candidate(candidate_id: int):
        require_runtime()
        return discovery.queue_read(candidate_id)

    @app.post('/api/discovery/candidates/{candidate_id}/confirm', dependencies=[Depends(api_csrf)])
    def confirm(candidate_id: int, data: ConfirmInput):
        return discovery.confirm(candidate_id, data.body, confirmed=data.confirmed)

    @app.post('/api/discovery/candidates/{candidate_id}/draft', dependencies=[Depends(api_csrf)])
    def draft(candidate_id: int, data: CandidateDraftInput):
        return discovery.create_draft(candidate_id, data.output_language, data.column)

    @app.post('/api/discovery/candidates/{candidate_id}/ignore', dependencies=[Depends(api_csrf)])
    def ignore(candidate_id: int):
        discovery.ignore(candidate_id)
        return {'status': 'ignored'}

    @app.post('/api/discovery/topics/merge', dependencies=[Depends(api_csrf)])
    def merge(data: MergeInput):
        discovery.merge(data.from_topic_id, data.into_topic_id)
        return {'status': 'merged'}

    @app.post('/api/discovery/candidates/{candidate_id}/split', dependencies=[Depends(api_csrf)])
    def split(candidate_id: int):
        return {'topic_id': discovery.split(candidate_id)}

    @app.post('/actions/discovery/{action}')
    async def discovery_action(request: Request, action: str):
        form = await request.form()
        check_csrf(request, form.get('csrf_token', ''))
        destination = '/discovery'
        try:
            if action == 'configure':
                discovery.configure(int(form['daily_limit']))
            elif action == 'rule':
                enabled = form.get('enabled') == 'on'
                if enabled:
                    require_runtime()
                discovery.create_rule(str(form['query']), str(form['backend']), int(form['days']),
                                      int(form['limit']), int(form['interval_minutes']), enabled)
            elif action == 'run':
                require_runtime()
                discovery.queue(int(form['rule_id']))
            elif action == 'toggle':
                enabled = form.get('enabled') == 'true'
                if enabled:
                    require_runtime()
                discovery.set_enabled(int(form['rule_id']), enabled)
            elif action in {'read', 'confirm', 'draft', 'ignore', 'split'}:
                candidate_id = int(form['candidate_id'])
                destination = f'/discovery/candidates/{candidate_id}'
                if action == 'read':
                    require_runtime()
                    discovery.queue_read(candidate_id)
                elif action == 'confirm':
                    discovery.confirm(candidate_id, str(form['body']), confirmed=form.get('confirmed') == 'on')
                elif action == 'draft':
                    saved = discovery.create_draft(candidate_id, str(form['language']), str(form['column']))
                    destination = f"/drafts/{saved['id']}"
                elif action == 'ignore':
                    discovery.ignore(candidate_id)
                    destination = '/discovery/candidates'
                else:
                    discovery.split(candidate_id)
                    destination = '/discovery/candidates'
            elif action == 'merge':
                discovery.merge(int(form['from_topic_id']), int(form['into_topic_id']))
                destination = '/discovery/candidates'
            else:
                raise DomainError('操作不存在', 404)
        except (KeyError, ValueError):
            raise DomainError('表单内容不完整或格式错误') from None
        return RedirectResponse(destination, status_code=303)
