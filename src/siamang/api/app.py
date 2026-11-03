"""Authenticated FastAPI transport for SIAMANG's proposal-only workflow."""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from siamang import __version__
from siamang.approval.errors import ApprovalError
from siamang.documents.catalog import CATALOG
from siamang.jsondata import thaw
from siamang.log import redact
from siamang.projects import ProjectRegistry
from siamang.projects.errors import ProjectError
from siamang.storage import Database, EventStore
from siamang.tasks import TaskEngine, TaskRequest
from siamang.tasks.errors import TaskError
from siamang.tasks.lifecycle import TaskStatus

from . import model_settings
from .runtime import Runtime, RuntimeRefusal
from .settings import APISettings
from .store import RunStore, now

bearer = HTTPBearer(auto_error=False)


def public(value):
    """Plain text/JSON only; withhold machine paths and credential material."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if k not in ('root_path', 'seal', 'snapshot')}
    if isinstance(value, (list, tuple)):
        return [public(v) for v in value]
    if isinstance(value, str):
        return re.sub(r'(?<!\w)(?:/Users/|/home/|/private/|/tmp/|~/|[A-Za-z]:\\)[^\s"\']+',
                      '[path withheld]', redact(value))
    return value


class Submit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    project_id: str
    description: str = Field(min_length=1, max_length=4000)
    request_id: str
    title: str | None = None
    priority: Literal['low', 'normal', 'high', 'urgent'] = 'normal'
    feature_id: str | None = None
    revises_task_id: str | None = None


class NewDraft(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content: str = Field(min_length=1, max_length=4000)
    priority: Literal['low', 'normal', 'high', 'urgent'] = 'normal'
    feature_id: str | None = None
    revises_task_id: str | None = None


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    decision: Literal['approve', 'reject']
    reason: str | None = Field(default=None, max_length=300)


class FeatureBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=4000)


class NewProject(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=120)
    repository: str = Field(max_length=200)


class AgentModelBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    agent: str = Field(max_length=32)
    provider: str = Field(min_length=1, max_length=128)
    provider_model: str = Field(min_length=1, max_length=256)


class MessageBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content: str = Field(min_length=1, max_length=4000)


def repository_name(value):
    """``owner/name`` or a github.com URL; anything else is refused."""
    match = re.fullmatch(r'(?:https://github\.com/)?([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?',
                         value.strip())
    if not match:
        raise RuntimeRefusal('invalid_repository', 422)
    return match.group(1)


def create_app(settings: APISettings | None = None, *, models=None, authors=None, repos=None):
    settings = settings or APISettings.from_env()
    runtime = Runtime(settings, models, authors, repos)

    @asynccontextmanager
    async def lifespan(app):
        runtime.open()
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(title='SIAMANG HTTP API', version='0.1.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    if settings.origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.origins),
                           allow_methods=['GET', 'POST'], allow_headers=['Authorization', 'Content-Type'])

    @app.middleware('http')
    async def limits(request: Request, call_next):
        # Protect even chunked requests; do not echo rejected bodies or credentials.
        if request.method == 'POST':
            size, chunks = 0, []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 65536:
                    return JSONResponse({'error': {'code': 'body_too_large'}}, status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        principal = settings.authenticate(credentials.credentials) if credentials and credentials.scheme.lower() == 'bearer' else None
        if principal is None:
            raise HTTPException(401, detail={'code': 'unauthorized'}, headers={'WWW-Authenticate': 'Bearer'})
        return principal

    def permission(name):
        def check(principal=Depends(identity)):
            if name not in principal.permissions:
                raise HTTPException(403, detail={'code': 'forbidden'})
            return principal
        return check

    async def database():
        with Database(settings.database) as db:
            yield db

    def scoped_task(db, task_id, principal):
        task = TaskEngine(db, ProjectRegistry(db)).get(task_id)
        if not principal.allows_project(task.project_id):
            raise RuntimeRefusal('not_found', 404)
        return task

    def execution_snapshot(task, store):
        snapshot = store.snapshot(store.latest(task.task_id))
        snapshot['activity_known'] = snapshot['activity_known'] and task.status == 'running'
        snapshot['recovery_required'] = task.status == 'running' and snapshot['state'] == 'unknown'
        return snapshot

    @app.exception_handler(RuntimeRefusal)
    async def refused(request, exc):
        return JSONResponse({'error': {'code': exc.code}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'error': {'code': 'invalid_request'}}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({'error': exc.detail}, status_code=exc.status_code, headers=exc.headers)

    async def domain_error(request, exc):
        code = str(exc.code)
        status = 404 if 'not_found' in code else 403 if 'unauthorized' in code else 409 if (
            any(term in code for term in ('transition', 'duplicate', 'eligible', 'retry', 'archived'))
        ) else 422
        return JSONResponse({'error': {'code': code}}, status_code=status)

    for error in (TaskError, ProjectError, ApprovalError):
        app.add_exception_handler(error, domain_error)

    @app.exception_handler(Exception)
    async def internal(request, exc):
        return JSONResponse({'error': {'code': 'internal_error'}}, status_code=500)

    @app.get('/v0/openapi.json')
    async def schema(principal=Depends(permission('read'))):
        return app.openapi()

    @app.get('/v0/health')
    async def health(principal=Depends(permission('read')), db=Depends(database)):
        return {'status': 'healthy' if not db.check() else 'unhealthy', 'version': __version__,
                'contract_version': '0.6', 'core_schema': 3, 'api_schema': 4, 'observed_at': now()}

    @app.get('/v0/capabilities')
    async def capabilities(principal=Depends(permission('read'))):
        try:
            runtime.models()
            configured = True
        except RuntimeRefusal:
            configured = False
        return {'transport': 'http', 'updates': 'polling', 'streaming': False, 'proposal_only': True,
                'patch_application': False, 'publishing': 'pull_request_only', 'model_configured': configured, 'permissions': sorted(principal.permissions),
                'agent_models': runtime.assignments(),
                'documents': [dict(agent=s.agent, stage=s.stage, kind=s.kind, title=s.title, source=str(s.source))
                              for s in CATALOG],
                'task_statuses': [s.value for s in TaskStatus],
                'execution_phases': ['queued', 'clarification', 'briefing', 'planning', 'analysis', 'design_approval', 'programming',
                                     'qa', 'reviewing', 'reporting', 'awaiting_approval', 'blocked', 'interrupted', 'finished', 'failed', 'cancelled']}

    # Plain def: asking Ollama for its tags must not hold up the event loop.
    @app.get('/v0/settings/models')
    def model_catalog(principal=Depends(permission('read'))):
        return public(model_settings.catalog(settings.config))

    @app.post('/v0/settings/models')
    def choose_model(body: AgentModelBody, principal=Depends(permission('configure'))):
        if not principal.allows_project('*'):  # the file applies to every project
            raise RuntimeRefusal('forbidden', 403)
        return public(model_settings.assign(settings.config, body.agent, body.provider, body.provider_model))

    @app.get('/v0/projects')
    async def projects(principal=Depends(permission('read')), db=Depends(database)):
        return {'items': [public(p.to_dict()) for p in ProjectRegistry(db).list(status=None)
                          if principal.allows_project(p.project_id)], 'observed_at': now()}

    @app.get('/v0/projects/{project_id}')
    async def project(project_id: str, principal=Depends(permission('read')), db=Depends(database)):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        return public(ProjectRegistry(db).get(project_id).to_dict())

    @app.post('/v0/projects', status_code=202)
    async def new_project(body: NewProject, principal=Depends(permission('submit'))):
        if not principal.allows_project('*'):  # a new project is outside any finite scope
            raise RuntimeRefusal('forbidden', 403)
        return public(runtime.create_project(body.name.strip(), repository_name(body.repository)).to_dict())

    @app.post('/v0/projects/{project_id}/clone', status_code=202)
    async def retry_clone(project_id: str, principal=Depends(permission('submit'))):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        runtime.retry_clone(project_id)
        return {'project_id': project_id, 'state': 'cloning'}

    @app.get('/v0/projects/{project_id}/intake')
    async def intake(project_id: str, principal=Depends(permission('read')), db=Depends(database)):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        ProjectRegistry(db).get(project_id)
        store = RunStore(db)
        return public({'repository': store.repo(project_id), 'spec': store.spec(project_id),
                       'messages': store.messages(project_id)})

    @app.post('/v0/projects/{project_id}/intake/messages', status_code=202)
    async def intake_message(project_id: str, body: MessageBody, principal=Depends(permission('submit'))):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        runtime.post_intake(project_id, body.content)
        return {'project_id': project_id, 'state': 'thinking'}

    @app.post('/v0/projects/{project_id}/intake/approve')
    async def approve_spec(project_id: str, principal=Depends(permission('approve'))):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        return public(runtime.approve_spec(project_id, principal))

    @app.get('/v0/projects/{project_id}/features')
    async def features(project_id: str, principal=Depends(permission('read')), db=Depends(database)):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        ProjectRegistry(db).get(project_id)
        return {'items': public(RunStore(db).features(project_id))}

    @app.post('/v0/projects/{project_id}/features', status_code=201)
    async def create_feature(project_id: str, body: FeatureBody, principal=Depends(permission('submit')),
                             db=Depends(database)):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        ProjectRegistry(db).get(project_id)
        return public(RunStore(db).create_feature(project_id, body.title, body.description))

    @app.get('/v0/portfolio')
    async def portfolio(principal=Depends(permission('read')), db=Depends(database)):
        items, store = [], RunStore(db)
        for p in ProjectRegistry(db).list(status=None):
            if not principal.allows_project(p.project_id):
                continue
            counts = db.conn.execute('SELECT status, COUNT(*) AS n FROM tasks WHERE project_id=? GROUP BY status',
                                     (p.project_id,)).fetchall()
            waiting = db.conn.execute("SELECT r.wait_reason, COUNT(*) AS n FROM api_runs r JOIN tasks t ON t.task_id=r.task_id "
                                      "WHERE t.project_id=? AND r.state='waiting' GROUP BY r.wait_reason",
                                      (p.project_id,)).fetchall()
            items.append(public(p.to_dict()) | {'task_counts': {r['status']: r['n'] for r in counts},
                                                'waiting': {r['wait_reason']: r['n'] for r in waiting},
                                                'features': public(store.features(p.project_id)),
                                                'repository': public(store.repo(p.project_id)),
                                                'spec_state': (store.spec(p.project_id) or {}).get('state')})
        return {'items': items, 'observed_at': now()}

    @app.get('/v0/office')
    async def office(principal=Depends(permission('read')), db=Depends(database)):
        """Every project at once: what the Lead holds, and which task the team is on now."""
        store, registry = RunStore(db), ProjectRegistry(db)
        engine, items, current = TaskEngine(db, registry), [], None
        with db.transaction():  # one snapshot, as in the task route
            for p in registry.list(status=None):
                if not principal.allows_project(p.project_id) or str(p.status) == 'archived':
                    continue
                rows = db.conn.execute(
                    "SELECT task_id FROM tasks WHERE project_id=? AND status IN ('queued','running','waiting') "
                    "ORDER BY created_at DESC LIMIT 20", (p.project_id,)).fetchall()
                rows += db.conn.execute(
                    "SELECT task_id FROM tasks WHERE project_id=? AND status IN ('completed','failed') "
                    "ORDER BY updated_at DESC LIMIT 3", (p.project_id,)).fetchall()
                tasks = []
                for row in rows:
                    t = engine.get(row['task_id'])
                    snapshot = execution_snapshot(t, store)
                    if snapshot['state'] in ('running', 'scheduled') and t.status in ('running', 'queued'):
                        current = dict(task_id=t.task_id, project_id=p.project_id)
                    tasks.append(public(t.to_dict()) | {'execution': snapshot, 'branch': public(store.branch(t.task_id))})
                counts = db.conn.execute('SELECT status, COUNT(*) AS n FROM tasks WHERE project_id=? GROUP BY status',
                                         (p.project_id,)).fetchall()
                waiting = db.conn.execute("SELECT r.wait_reason, COUNT(*) AS n FROM api_runs r JOIN tasks t ON t.task_id=r.task_id "
                                          "WHERE t.project_id=? AND r.state='waiting' GROUP BY r.wait_reason",
                                          (p.project_id,)).fetchall()
                items.append(public(p.to_dict()) | {'tasks': tasks, 'task_counts': {r['status']: r['n'] for r in counts},
                                                    'waiting': {r['wait_reason']: r['n'] for r in waiting},
                                                    'repository': public(store.repo(p.project_id)),
                                                    'spec_state': (store.spec(p.project_id) or {}).get('state')})
        return {'items': items, 'current': current, 'observed_at': now()}

    @app.get('/v0/tasks')
    async def tasks(project_id: str, status: TaskStatus | None = None, limit: int = Query(100, ge=1, le=100),
              offset: int = Query(0, ge=0), principal=Depends(permission('read')), db=Depends(database)):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        engine, store = TaskEngine(db, ProjectRegistry(db)), RunStore(db)
        with db.transaction():  # one snapshot, as in the task route
            return {'items': [public(t.to_dict()) | {'execution': execution_snapshot(t, store), 'branch': public(store.branch(t.task_id))}
                              for t in engine.list(project_id=project_id, status=status, limit=limit, offset=offset)],
                    'observed_at': now()}

    def check_task_links(store, project_id, feature_id, revises_task_id):
        if feature_id and not any(f['feature_id'] == feature_id for f in store.features(project_id)):
            raise RuntimeRefusal('feature_not_found', 404)
        repo = store.repo(project_id)
        if revises_task_id:
            base = store.branch(revises_task_id)
            if not repo or not base or base['project_id'] != project_id:
                raise RuntimeRefusal('revision_base_not_found', 404)
            if base['publication'] != 'published':
                raise RuntimeRefusal('revision_base_not_published')

    def create_task(db, store, body):
        """Both ways to a task: a direct submission, and a draft the owner confirmed with the Lead."""
        check_task_links(store, body.project_id, body.feature_id, body.revises_task_id)
        repo = store.repo(body.project_id)
        if repo and (repo['state'] != 'ready' or (store.spec(body.project_id) or {}).get('state') != 'approved'):
            raise RuntimeRefusal('spec_not_approved')
        with db.transaction():
            submission = TaskEngine(db, ProjectRegistry(db)).create(TaskRequest.from_dict(
                {**body.model_dump(exclude={'feature_id', 'revises_task_id'}), 'source': 'api'}))
            if body.feature_id:
                store.link_feature(submission.task.task_id, body.feature_id)
            if repo:
                runtime.assign_branch(store, submission.task, body.revises_task_id)
        return submission

    @app.post('/v0/tasks', status_code=202)
    async def submit(body: Submit, principal=Depends(permission('submit')), db=Depends(database)):
        if not principal.allows_project(body.project_id):
            raise RuntimeRefusal('forbidden', 403)
        submission = create_task(db, RunStore(db), body)
        return {'task': public(submission.task.to_dict()), 'duplicate': submission.duplicate,
                'scheduled': False, 'observed_at': now()}

    def scoped_draft(store, project_id, draft_id, principal):
        draft = store.draft(draft_id)
        if not principal.allows_project(project_id) or not draft or draft['project_id'] != project_id:
            raise RuntimeRefusal('not_found', 404)
        return draft

    @app.get('/v0/projects/{project_id}/drafts')
    async def drafts(project_id: str, principal=Depends(permission('read')), db=Depends(database)):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        ProjectRegistry(db).get(project_id)
        return {'items': public(RunStore(db).open_drafts(project_id))}

    @app.post('/v0/projects/{project_id}/drafts', status_code=202)
    async def new_draft(project_id: str, body: NewDraft, principal=Depends(permission('submit')), db=Depends(database)):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        check_task_links(RunStore(db), project_id, body.feature_id, body.revises_task_id)
        options = body.model_dump(exclude={'content'})
        return {'draft_id': runtime.create_draft(project_id, body.content, options), 'state': 'thinking'}

    @app.get('/v0/projects/{project_id}/drafts/{draft_id}')
    async def get_draft(project_id: str, draft_id: str, principal=Depends(permission('read')), db=Depends(database)):
        return public(scoped_draft(RunStore(db), project_id, draft_id, principal))

    @app.post('/v0/projects/{project_id}/drafts/{draft_id}/messages', status_code=202)
    async def draft_message(project_id: str, draft_id: str, body: MessageBody, principal=Depends(permission('submit')),
                            db=Depends(database)):
        runtime.post_draft(scoped_draft(RunStore(db), project_id, draft_id, principal), body.content)
        return {'draft_id': draft_id, 'state': 'thinking'}

    @app.post('/v0/projects/{project_id}/drafts/{draft_id}/discard')
    async def discard_draft(project_id: str, draft_id: str, principal=Depends(permission('submit')), db=Depends(database)):
        store = RunStore(db)
        if scoped_draft(store, project_id, draft_id, principal)['state'] == 'created':
            raise RuntimeRefusal('draft_not_open')
        store.update_draft(draft_id, state='discarded')
        return {'draft_id': draft_id, 'state': 'discarded'}

    @app.post('/v0/projects/{project_id}/drafts/{draft_id}/confirm', status_code=202)
    async def confirm_draft(project_id: str, draft_id: str, principal=Depends(permission('submit')), db=Depends(database)):
        """The owner agrees with the Lead's brief: it becomes the task, and the task starts."""
        store = RunStore(db)
        draft = scoped_draft(store, project_id, draft_id, principal)
        if draft['state'] not in ('ready', 'created'):
            raise RuntimeRefusal('draft_not_ready')
        options = draft['options']
        body = Submit(project_id=project_id, description=draft['brief'], title=draft['title'] or None,
                      request_id='req_' + draft_id[4:], priority=options.get('priority', 'normal'),
                      feature_id=options.get('feature_id'), revises_task_id=options.get('revises_task_id'))
        with db.transaction():
            submission = create_task(db, store, body)
            task_id = submission.task.task_id
            if draft['state'] == 'ready':
                for m in draft['messages']:
                    store.add_message(project_id, task_id, m['role'], {k: v for k, v in m.items() if k not in ('role', 'position')})
                store.update_draft(draft_id, state='created', task_id=task_id)
        scheduled, reason = False, None
        if 'run' not in principal.permissions:
            reason = 'forbidden'
        elif draft['state'] == 'ready':
            try:
                runtime.schedule(task_id)
                scheduled = True
            except RuntimeRefusal as exc:  # the task exists: it can still be started from the task list
                reason = exc.code
        return {'task': public(submission.task.to_dict()), 'duplicate': draft['state'] == 'created',
                'scheduled': scheduled, 'reason': reason, 'draft_id': draft_id, 'observed_at': now()}

    @app.get('/v0/tasks/{task_id}')
    async def task(task_id: str, principal=Depends(permission('read')), db=Depends(database)):
        store = RunStore(db)
        with db.transaction():  # one snapshot: the worker commits task and execution state together
            t = scoped_task(db, task_id, principal)
            return public(t.to_dict()) | {'execution': execution_snapshot(t, store), 'feature_id': store.feature_of(task_id),
                                          'branch': public(store.branch(task_id)), 'packages': public(store.public_packages(task_id)),
                                          'ci': public(store.ci(task_id)), 'observed_at': now()}

    @app.get('/v0/tasks/{task_id}/messages')
    async def task_messages(task_id: str, principal=Depends(permission('read')), db=Depends(database)):
        task = scoped_task(db, task_id, principal)
        return {'items': public(RunStore(db).messages(task.project_id, task_id))}

    @app.post('/v0/tasks/{task_id}/messages', status_code=202)
    async def task_message(task_id: str, body: MessageBody, principal=Depends(permission('run')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        return runtime.post_task_message(task_id, body.content)

    @app.post('/v0/tasks/{task_id}/publish', status_code=202)
    async def publish(task_id: str, principal=Depends(permission('approve')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        runtime.retry_publish(task_id)
        return {'task_id': task_id, 'publication': 'publishing'}

    @app.get('/v0/tasks/{task_id}/documents')
    async def documents(task_id: str, principal=Depends(permission('read')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        return {'items': public(RunStore(db).documents(task_id)), 'observed_at': now()}

    @app.post('/v0/tasks/{task_id}/design-decision')
    async def design_decision(task_id: str, body: DecisionBody, principal=Depends(permission('approve')),
                              db=Depends(database)):
        scoped_task(db, task_id, principal)
        return runtime.decide_design(task_id, principal, body.decision, body.reason)

    @app.get('/v0/tasks/{task_id}/events')
    async def events(task_id: str, after_seq: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
               principal=Depends(permission('read')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        store = EventStore(db)
        rows = db.conn.execute('SELECT event_id FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?',
                               (task_id, after_seq, limit)).fetchall()
        items = [store.get(row['event_id']) for row in rows]
        return {'items': public([e.to_dict() for e in items]), 'next_after_seq': items[-1].seq if items else after_seq}

    @app.get('/v0/tasks/{task_id}/execution-events')
    async def execution_events(task_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
                         principal=Depends(permission('read')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        items = RunStore(db).events(task_id, after, limit)
        return {'items': items, 'next_after': items[-1]['position'] if items else after}

    @app.post('/v0/tasks/{task_id}/run', status_code=202)
    async def run(task_id: str, principal=Depends(permission('run')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        return {'run_id': runtime.schedule(task_id), 'task_id': task_id, 'state': 'scheduled'}

    @app.post('/v0/tasks/{task_id}/cancel')
    async def cancel(task_id: str, principal=Depends(permission('cancel')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        return public(runtime.cancel(task_id).to_dict())

    @app.post('/v0/tasks/{task_id}/retry')
    async def retry(task_id: str, principal=Depends(permission('retry')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        return {'task': public(runtime.retry(task_id).to_dict()), 'scheduled': False}

    @app.get('/v0/tasks/{task_id}/reports')
    async def reports(task_id: str, principal=Depends(permission('read')), db=Depends(database)):
        scoped_task(db, task_id, principal)
        rows = db.conn.execute('SELECT * FROM api_runs WHERE task_id=? AND bundle IS NOT NULL ORDER BY rowid',
                               (task_id,)).fetchall()
        items = []
        for row in rows:
            plan, analysis, loop = runtime.load_bundle(dict(row))
            items.append({'run_id': row['run_id'], 'plan': plan.to_dict(),
                          'analysis': analysis.to_dict(), 'loop': loop.to_dict(),
                          'patch': loop.final_patch.to_dict() if loop.final_patch else None,
                          'qa': loop.final_qa_result.to_dict() if loop.final_qa_result else None,
                          'review': loop.final_review_result.to_dict() if loop.final_review_result else None})
        return {'items': public(items), 'patch_applied': False, 'sanitized': True}

    @app.get('/v0/approvals')
    async def approvals(project_id: str, principal=Depends(permission('read')), db=Depends(database)):
        if not principal.allows_project(project_id):
            raise RuntimeRefusal('not_found', 404)
        rows = db.conn.execute("SELECT r.* FROM api_runs r JOIN tasks t ON t.task_id=r.task_id "
                               "WHERE t.project_id=? AND r.state='waiting' AND r.bundle IS NOT NULL "
                               "ORDER BY r.rowid LIMIT 100", (project_id,)).fetchall()
        items = []
        for row in rows:
            _, _, loop = runtime.load_bundle(dict(row))
            if not all((loop.final_patch, loop.final_qa_result, loop.final_review_result)):
                continue
            request = runtime.evidence(dict(row))
            items.append({'run_id': row['run_id'], 'task_id': row['task_id'],
                          'eligible': request.loop_result.passed, 'wait_reason': row['wait_reason'],
                          'provenance': request.provenance.to_dict()})
        return {'items': items}

    @app.post('/v0/approvals/{run_id}')
    async def decide(run_id: str, body: DecisionBody, principal=Depends(permission('approve'))):
        return public(runtime.decide(run_id, principal, body.decision, body.reason))

    return app
