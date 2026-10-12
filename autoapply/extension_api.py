"""Paired, offline extension operations. Final submission remains manual."""

import base64
from dataclasses import replace
import json
from urllib.parse import urlsplit

from . import answers, descriptions, question_rules
from .dashboard_settings import SettingsRejected
from .field_mapping import answer_signature
from .jobs import ats_identity, canonical_url, normalize
from .models import Question, now

KINDS = {'text', 'textarea', 'select', 'radio', 'checkbox', 'combobox', 'file',
         'date', 'number', 'email', 'tel', 'url'}


def _id(value):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise SettingsRejected('application_id must be a positive integer')
    return value


def _url(value):
    if not isinstance(value, str):
        raise SettingsRejected('url must be an HTTP(S) URL')
    try:
        value = canonical_url(value)
        parts = urlsplit(value)
        if parts.hostname.endswith('.myworkdayjobs.com') and parts.path.endswith('/apply'):
            value = parts._replace(path=parts.path[:-6]).geturl()
        return value
    except ValueError:
        raise SettingsRejected('url must be an HTTP(S) URL') from None


def _match(db, url):
    identity = ats_identity(url)[1]
    for row in db.rows('SELECT a.id,j.canonical_url FROM applications a JOIN jobs j ON j.id=a.job_id ORDER BY a.id'):
        try:
            stored = _url(row['canonical_url'])
        except SettingsRejected:
            continue
        if stored == url or identity and ats_identity(stored)[1] == identity:
            return db.application(row['id'])
    return None


def _application(db, payload):
    url = _url(payload.get('url'))
    if payload.get('application_id') is not None:
        application_id = _id(payload['application_id'])
        try:
            return db.application(application_id)
        except ValueError:
            raise SettingsRejected('Application not found', status=404) from None
    app = _match(db, url)
    if app:
        return app
    page = payload.get('page') or {}
    if not isinstance(page, dict) or any(not isinstance(page.get(k, ''), str) for k in ('company', 'title', 'description')):
        raise SettingsRejected('page must contain text values')
    return dict(id=url, canonical_url=url, company=page.get('company', ''),
                title=page.get('title', ''), description=page.get('description', ''))


def _unprotected(app):
    from .dashboard import PROTECTED_APPLICATIONS
    if app['id'] in PROTECTED_APPLICATIONS:
        raise SettingsRejected('protected application cannot be changed', status=403)


def _question(field):
    if not isinstance(field, dict) or not isinstance(field.get('label'), str):
        raise SettingsRejected('Each field needs a label')
    kind, options = field.get('kind', 'text'), field.get('options', [])
    maximum = field.get('max_length')
    if (not isinstance(kind, str) or kind not in KINDS or not isinstance(options, list) or not all(isinstance(v, str) for v in options)
            or type(field.get('required', False)) is not bool
            or maximum is not None and (type(maximum) is not int or maximum < 0)
            or not isinstance(field.get('key', field['label']), str)):
        raise SettingsRejected('Invalid field kind, options, required, key or max_length')
    return Question(field.get('key', field['label']), field['label'], kind,
                    field.get('required', False), options, maximum)


def ping(dashboard, payload):
    identity = dashboard.config.profile.get('identity', {})
    return dict(ok=True, name='AutoApply', profile_name=' '.join(
        str(identity.get(k) or '') for k in ('first_name', 'last_name')).strip())


def match(dashboard, payload):
    with dashboard.connection() as db:
        app = _match(db, _url(payload.get('url')))
    return (dict(application_id=app['id'], company=app['company'], title=app['title'], status=app['status'])
            if app else dict(application_id=None))


def resolve_answers(dashboard, payload):
    fields = payload.get('fields')
    if not isinstance(fields, list) or len(fields) > 500:
        raise SettingsRejected('fields must be a list of at most 500 fields')
    questions = [_question(field) for field in fields]
    page = payload.get('page') or {}
    if not isinstance(page, dict) or not isinstance(page.get('description', ''), str):
        raise SettingsRejected('page.description must be text')
    with dashboard.settings.mutex, dashboard.connection(writable=True) as db:
        app = _application(db, payload)
        _unprotected(app)
        description = page.get('description', '')
        if 'job_id' in app and len(description) > len(app.get('description') or ''):
            descriptions.store(db, app['job_id'], description)
            app['description'] = description
        resolver = answers.AnswerResolver(dashboard.config, db)
        result = []
        for q in questions:
            if q.kind == 'file':
                result.append(dict(key=q.key, value='cover_letter' if 'cover' in q.label.lower() else 'resume',
                                   source='file', status='FILE'))
                continue
            resolution = resolver.resolve_result(q, app)
            answer = resolution.answer
            # Remember requests omit required/length. Only verified memory may
            # reuse that descriptor, and the actual field is validated below.
            remembered = resolver.resolve_result(replace(q, required=False, max_length=None), app).answer
            if remembered and remembered.source == 'verified_memory':
                answer = remembered
            if answer:
                try:
                    answers.validate_answer(q, answer.value)
                    if q.max_length == 0 and answer.value:
                        raise ValueError('Field cannot accept text')
                except (ValueError, TypeError):
                    answer = None
            status = ('RESOLVED' if answer else 'NEEDS_WRITING' if answers.is_writing_question(q)
                      else 'NEEDS_INPUT' if q.required else 'OPTIONAL_SKIP')
            result.append(dict(key=q.key, value=answer.value if answer else None,
                               source=answer.source if answer else '', status=status))
    return dict(answers=result)


def remember(dashboard, payload):
    q = replace(_question(payload), required=False, max_length=None)
    if q.kind == 'file':
        raise SettingsRejected('File answers cannot be remembered')
    value = payload.get('value')
    try:
        answers.validate_answer(q, value)
    except (ValueError, TypeError) as exc:
        raise SettingsRejected(str(exc)) from None
    with dashboard.settings.mutex, dashboard.connection(writable=True) as db:
        app = _application(db, payload)
        _unprotected(app)
        resolver = answers.AnswerResolver(dashboard.config, db)
        resolution = resolver.resolve_result(q, app)
        descriptor = resolution.descriptor
        q.scope, q.semantic_key, q.policy = descriptor.scope, descriptor.semantic_key, descriptor.policy
        provenance = dict(signature=descriptor.signature, profile_revision=resolution.profile_revision,
                          provenance=dict(canonical_profile_revision=dashboard.config.profile_snapshot().revision))
        # Same verified store and signatures as Database.save_answer; no receipts,
        # lifecycle transitions or history exports are needed for extension input.
        db.conn.execute('BEGIN IMMEDIATE')
        try:
            db.execute('''INSERT INTO known_answers(normalized_question,scope,answer,verified,source,created_at)
                VALUES (?,?,?,1,'USER_PROVIDED',?) ON CONFLICT(normalized_question,scope)
                DO UPDATE SET answer=excluded.answer,verified=1,source=excluded.source''',
                       (normalize(q.label), q.scope, json.dumps(value), now()))
            stored = db.one('SELECT id FROM known_answers WHERE normalized_question=? AND scope=?',
                            (normalize(q.label), q.scope))
            db.execute('UPDATE known_answers SET concept=?,question_signature=?,provenance=? WHERE id=?',
                       (descriptor.semantic_key, descriptor.signature, json.dumps(provenance), stored['id']))
            db.set_setting('known_answer_signature:' + str(stored['id']), answer_signature(q.label, q.kind, q.options))
            question_rules.remember(db, dashboard.config, q, value, stored['id'])
            db.conn.execute('COMMIT')
        except BaseException:
            db.conn.execute('ROLLBACK')
            raise
    return dict(ok=True)


def resume(dashboard, payload):
    return dict(filename=dashboard.config.resume.name, mime='application/pdf',
                base64=base64.b64encode(dashboard.settings.resume_bytes()).decode('ascii'))


def run_status(dashboard, payload):
    with dashboard._ext_mutex:
        items = [dict(item) for item in dashboard._ext_run]
    return dict(items=items, current=next((i['application_id'] for i in items if i['state'] == 'opened'), None))


def run_start(dashboard, payload):
    from .dashboard import PROTECTED_APPLICATIONS
    ids = payload.get('application_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        raise SettingsRejected('application_ids must contain 1 to 500 ids')
    ids = [_id(value) for value in ids]
    if len(set(ids)) != len(ids):
        raise SettingsRejected('application_ids must be unique')
    if any(value in PROTECTED_APPLICATIONS for value in ids):
        raise SettingsRejected('protected application cannot be changed', status=403)
    available = {row['application_id']: row for row in
                 dashboard.priority()['rows'] + dashboard.manual()['applications']}
    if any(value not in available or not available[value]['url'] for value in ids):
        raise SettingsRejected('Applications must be in the current priority or manual lists')
    with dashboard._ext_mutex:
        dashboard._ext_run = [dict(application_id=value, **{k: available[value][k] for k in ('company', 'title', 'url')},
                                   state='pending', detail='') for value in ids]
    return run_status(dashboard, {})


def run_cancel(dashboard, payload):
    with dashboard._ext_mutex:
        for item in dashboard._ext_run:
            if item['state'] in {'pending', 'opened'}:
                item.update(state='skipped', detail='Run cancelled')
    return run_status(dashboard, {})


def run_next(dashboard, payload):
    with dashboard._ext_mutex:
        for item in dashboard._ext_run:
            if item['state'] == 'pending':
                item['state'] = 'opened'
                return {k: item[k] for k in ('application_id', 'url', 'company', 'title')}
    return dict(done=True)


def run_result(dashboard, payload):
    application_id = _id(payload.get('application_id'))
    state, detail = payload.get('state'), payload.get('detail', '')
    if not isinstance(state, str) or state not in {'filled', 'needs_input', 'failed', 'skipped'} or not isinstance(detail, str) or len(detail) > 2000:
        raise SettingsRejected('Invalid result state or detail')
    with dashboard._ext_mutex:
        item = next((i for i in dashboard._ext_run if i['application_id'] == application_id), None)
        if not item or item['state'] != 'opened':
            raise SettingsRejected('Application is not open in this run', status=409)
        item.update(state=state, detail=detail)
    return dict(ok=True)


ROUTES = {('GET', '/ext/ping'): ping, ('POST', '/ext/match'): match,
          ('POST', '/ext/answers'): resolve_answers, ('POST', '/ext/remember'): remember,
          ('GET', '/ext/resume'): resume, ('GET', '/ext/run/next'): run_next,
          ('POST', '/ext/run/result'): run_result}


def dispatch(dashboard, payload, *, method, path):
    handler = ROUTES.get((method, path))
    if handler is None:
        raise SettingsRejected('endpoint not found', status=404)
    return handler(dashboard, payload)
