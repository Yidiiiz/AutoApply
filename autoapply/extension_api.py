"""Paired, offline extension operations. Final submission remains manual."""

import base64
import calendar
from dataclasses import replace
import json
import re
import time
import uuid
from urllib.parse import urlsplit

from . import answers, cv, descriptions, question_rules
from .dashboard_settings import SettingsRejected
from .field_mapping import answer_signature
from .jobs import ats_identity, canonical_url, normalize
from .models import Answer, Question, now

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
        if parts.hostname.endswith('.myworkdayjobs.com'):
            value = parts._replace(path=re.sub(r'/apply(?:/.*)?$', '', parts.path, flags=re.I)).geturl()
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
            or not isinstance(field.get('key', field['label']), str)
            or not isinstance(field.get('section', ''), str)):
        raise SettingsRejected('Invalid field kind, options, required, key or max_length')
    return Question(field.get('key', field['label']), field['label'], kind,
                    field.get('required', False), options, maximum)


def _education_date_part(q, field, profile):
    label = normalize(q.label)
    part = re.fullmatch(r'(?:education )?(start|end) date (month|year)', label)
    if not part or not re.search(r'\beducation\b', normalize(field.get('section', '') + ' ' + q.label)):
        return False, None
    path = 'start_date' if part[1] == 'start' else 'graduation_date'
    value = profile.get('education', {}).get(path)
    match = re.fullmatch(r'(\d{4})(?:-(\d{2})(?:-\d{2})?)?', str(value or ''))
    if not match or not 1 <= int(match[2] or '01') <= 12:
        return True, None
    year, month = match[1], int(match[2] or '01')
    candidates = [year] if part[2] == 'year' else [calendar.month_name[month], str(month), f'{month:02}', calendar.month_abbr[month]]
    selected = next((fitted for candidate in candidates
                     if (fitted := answers.fit_options(candidate, q.options)) is not None), None)
    return True, Answer(selected, 'profile:education.' + path) if selected is not None else None


def _repeatable_answer(q, field, profile):
    """Use explicit facts from the first saved work/education entry."""
    section, label = normalize(field.get('section', '')), normalize(q.label).strip(' *')
    work = bool(re.search(r'\b(work experience|employment)\b', section))
    education = bool(re.search(r'\beducation\b', section))
    if not work and not education:
        return False, None
    entries = profile.get('employment' if work else 'education_history', [])
    entry = entries[0] if entries and isinstance(entries[0], dict) else {} if work else profile.get('education', {})
    names = ({'job title': 'title', 'company': 'company', 'location': 'location',
              'role description': 'description', 'i currently work here': 'current'} if work else
             {'school or university': 'school', 'school': 'school', 'degree': 'degree', 'field of study': 'major'})
    date = re.fullmatch(r'(start|end|from|to) date (month|year)', label)
    key = names.get(label)
    if date:
        key = 'start_date' if date[1] in {'start', 'from'} else 'end_date' if work else 'graduation_date'
    if not key:
        return False, None
    value = entry.get(key)
    if date:
        match = re.fullmatch(r'(\d{4})(?:-(\d{2})(?:-\d{2})?)?', str(value or ''))
        if not match or not 1 <= int(match[2] or '01') <= 12:
            return True, None
        if work and key == 'end_date' and entry.get('current') is True:
            return True, None
        month = int(match[2] or '01')
        candidates = [match[1]] if date[2] == 'year' else (
            [str(month)] if q.kind == 'number' or not q.options else
            [calendar.month_name[month], str(month), f'{month:02}', calendar.month_abbr[month]])
        value = next((fitted for candidate in candidates
                      if (fitted := answers.fit_options(candidate, q.options)) is not None), None)
    elif value is not None and not isinstance(value, bool):
        value = answers.fit_options(str(value), q.options)
        if education and key == 'degree' and value is None and entry.get('major'):
            value = answers.fit_options(str(entry['major']), q.options)
    return True, Answer(value, 'profile:' + ('employment.0.' if work else 'education.') + key) if value is not None else None


def _graduation_answer(q, field, profile):
    if q.kind not in {'text', 'date', 'select', 'combobox'} or not re.search(
            r'\bwhen\b.*\bgraduate\b|\b(?:expected |anticipated |projected )?graduation date\b', normalize(q.label)):
        return None
    if re.search(r'\b(month|year)\b', normalize(q.label)):
        return None
    raw = str(profile.get('education', {}).get('graduation_date') or '')
    match = re.fullmatch(r'(\d{4})-(0[1-9]|1[0-2])(?:-\d{2})?', raw)
    if not match:
        return None
    year, month = match[1], match[2]
    short = calendar.month_abbr[int(month)] + '-' + year[2:]
    candidates = [short, month + '/' + year, month + '/01/' + year,
                  calendar.month_name[int(month)] + ' ' + year,
                  calendar.month_abbr[int(month)] + ' ' + year, year + '-' + month,
                  year + '-' + month + '-01']
    if q.options:
        value = next((v for candidate in candidates
                      if (v := answers.fit_options(candidate, q.options)) is not None), None)
    else:
        hint = str(field.get('placeholder') or '') + ' ' + q.label
        value = (year + '-' + month + '-01' if q.kind == 'date' else
                 short if re.search(r'mmm[-/ ]yy\b', hint, re.I) else
                 month + '/01/' + year if re.search(r'\bdd\b', hint, re.I) else
                 month + '/' + year if re.search(r'\bmm[/ -]yyyy\b', hint, re.I) else
                 short if normalize(q.label) == 'when are you expected to graduate' else month + '/' + year)
    return Answer(value, 'profile:education.graduation_date') if value is not None else None


def profile_sections(dashboard, payload):
    """Explicit resume facts only; no writer or applicant-data mutation."""
    profile = dashboard.config.profile
    def boolean(value):
        return value is True or str(value).lower() == 'true'
    def date(value):
        value = str(value or '')
        if re.fullmatch(r'\d{4}', value):
            return value + '-01'
        return value[:7] if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])(?:-\d{2})?', value) else ''
    def current(entry):
        value = entry.get('current')
        if value is not None and value != '':
            return boolean(value)
        return bool(date(entry.get('start_date')) and not entry.get('end_date'))
    def description(value):
        if isinstance(value, list):
            return '\n'.join(str(line).strip() for line in value)
        return str(value or '').strip()
    experience = [dict(company=str(e.get('company') or ''), title=str(e.get('title') or ''),
                       location=str(e.get('location') or ''), start_date=date(e.get('start_date')),
                       end_date=date(e.get('end_date')), current=current(e),
                       description=description(e.get('description')))
                  for e in profile.get('employment', []) if isinstance(e, dict)]
    projects = [dict(title=str(e.get('name') or ''), organization=str(e.get('role') or ''),
                     url=str(e.get('url') or ''), start_date=date(e.get('start_date')),
                     end_date=date(e.get('end_date')), current=current(e),
                     technologies=e.get('technologies') or [], description=description(e.get('description')))
                for e in profile.get('projects', []) if isinstance(e, dict)]
    for entries in (experience, projects):
        entries.sort(key=lambda e: (e['current'], e['start_date']), reverse=True)
    history = profile.get('education_history') or [profile.get('education', {})]
    education = [dict(school=str(e.get('school') or ''), degree=str(e.get('degree') or ''),
                      major=str(e.get('major') or ''), location=str(e.get('location') or ''),
                      gpa=str(e.get('gpa') or ''), start_date=date(e.get('start_date')),
                      end_date=date(e.get('end_date') or e.get('graduation_date')),
                      current=boolean(e.get('currently_enrolled')),
                      description=description(e.get('relevant_coursework')))
                 for e in history if isinstance(e, dict) and e]
    skills, seen = [], set()
    groups = list((profile.get('skill_categories') or {}).values()) + [profile.get('skills') or []]
    for group in groups:
        for skill in group:
            skill = str(skill).strip()
            if skill and skill.casefold() not in seen:
                skills.append(skill)
                seen.add(skill.casefold())
    languages = [dict(language=str(e.get('language') or ''), fluent=boolean(e.get('fluent')),
                      proficiency=str(e.get('proficiency') or ''))
                 for e in profile.get('languages', []) if isinstance(e, dict) and e.get('language')]
    return dict(experience=experience, projects=projects, education=education, languages=languages,
                skills=skills, links=profile.get('links') or {})


def ping(dashboard, payload):
    identity = dashboard.config.profile.get('identity', {})
    return dict(ok=True, name='AutoApply', profile_name=' '.join(
        str(identity.get(k) or '') for k in ('first_name', 'last_name')).strip())


def match(dashboard, payload):
    with dashboard.connection() as db:
        app = _match(db, _url(payload.get('url')))
    return (dict(application_id=app['id'], company=app['company'], title=app['title'], status=app['status'])
            if app else dict(application_id=None))



def secondary_address(label):
    label = re.sub(r'[^a-z0-9]+', ' ', normalize(label)).strip()
    return bool(re.search(r'\b(?:address(?: line)?\s*2|line\s*2|apt|apartment|suite|unit)\b', label))


def snapshot(dashboard, payload):
    """Save only the documented structural attributes, never field values."""
    host, nodes = payload.get('host'), payload.get('nodes')
    if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', host):
        raise SettingsRejected('Invalid snapshot host')
    if not isinstance(nodes, list) or len(nodes) > 10000:
        raise SettingsRejected('Snapshot nodes must be a list of at most 10000 items')
    attributes = {'tag', 'role', 'type', 'id', 'name', 'data-automation-id', 'aria-label', 'labelledby', 'label', 'text'}
    sanitized = []
    text_tags = {'label', 'legend', 'option', 'button', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('tag'), str):
            raise SettingsRejected('Invalid snapshot node')
        row = {key: value[:2000] for key, value in node.items() if key in attributes and isinstance(value, str)}
        if row.get('tag') not in text_tags and row.get('role') not in {'heading', 'option'} and row.get('data-automation-id') not in {'richText', 'formLabel'}:
            row.pop('text', None)
        row['visible'] = node.get('visible') is True
        sanitized.append(row)
    data = json.dumps(dict(host=host, nodes=sanitized, truncated=payload.get('truncated') is True), ensure_ascii=False)
    if len(data.encode('utf-8')) > 1024 * 1024:
        raise SettingsRejected('Snapshot exceeds 1 MB')
    directory = dashboard.config.private / 'snapshots'
    directory.mkdir(parents=True, exist_ok=True)
    filename = host.lower() + '-' + str(time.time_ns()) + '.json'
    (directory / filename).write_text(data, encoding='utf-8')
    return dict(ok=True, filename=filename)


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
        description = page.get('description', '')[:6000]
        if 'job_id' in app and len(description) > len(app.get('description') or ''):
            descriptions.store(db, app['job_id'], description)
            app['description'] = description
        resolver = answers.AnswerResolver(dashboard.config, db)
        profile = dashboard.config.profile
        result = []
        for q, field in zip(questions, fields):
            from .question_keywords import unusable_label
            if unusable_label(q.label):
                result.append(dict(key=q.key, value=None, source='', status='NEEDS_INPUT'))
                continue
            if secondary_address(q.label):
                result.append(dict(key=q.key, value='', source='policy:empty_address_line_2', status='OPTIONAL_SKIP'))
                continue
            if re.fullmatch(r'phone (?:device )?type', normalize(q.label).strip(' *')):
                preferred = str(profile.get('contact', {}).get('phone_device_type') or 'Mobile')
                value = next((option for option in q.options if normalize(option) in {'mobile', 'cell', 'mobile phone', normalize(preferred)}), None) if q.options else preferred
                result.append(dict(key=q.key, value=value, source='profile:contact.phone_device_type' if value else '',
                                   status='RESOLVED' if value else 'NEEDS_INPUT' if q.required else 'OPTIONAL_SKIP'))
                continue
            if q.kind == 'file':
                result.append(dict(key=q.key, value='cover_letter' if 'cover' in q.label.lower() else 'resume',
                                   source='file', status='FILE'))
                continue
            resolution = resolver.resolve_result(q, app)
            answer = resolution.answer
            education_part, education_answer = _education_date_part(q, field, profile)
            if education_part:
                answer = education_answer
            repeated, repeated_answer = _repeatable_answer(q, field, profile)
            if repeated:
                answer = repeated_answer
            if (resolution.descriptor.semantic_key == 'start_date'
                    and resolution.descriptor.policy not in {'REQUIRE_USER', 'DO_NOT_ANSWER'}
                    and (answer is None or answer.source == 'profile:application_preferences.desired_start_date')):
                value = answers.format_start_date(profile.get('application_preferences', {}).get('desired_start_date'),
                                                 q.kind, str(field.get('placeholder') or '') + ' ' + q.label)
                answer = Answer(value, 'profile:application_preferences.desired_start_date') if value is not None else None
            graduation = _graduation_answer(q, field, profile)
            if graduation is not None:
                answer = graduation
            # Remember requests omit required/length. Only verified memory may
            # reuse that descriptor, and the actual field is validated below.
            remembered = resolver.resolve_result(replace(q, required=False, max_length=None), app).answer
            if remembered and remembered.source == 'verified_memory':
                answer = remembered
            if field.get('section_missing_date') is True:
                sections = profile_sections(dashboard, {})
                labels = [e['title'] + ' at ' + (e.get('company') or e.get('organization') or '') + ': start date'
                          for kind in ('experience', 'projects') for e in sections[kind] if not e['start_date']]
                labels += [e['school'] + ': start date' for e in sections['education'] if not e['start_date']]
                answer = None
                if q.label in labels and q.kind == 'text':
                    rows = db.rows("SELECT answer,provenance FROM known_answers WHERE normalized_question=? AND verified=1 AND source='USER_PROVIDED' ORDER BY id DESC", (normalize(q.label),))
                    revision = dashboard.config.profile_snapshot().revision
                    for row in rows:
                        data = json.loads(row.get('provenance') or '{}')
                        value = json.loads(row['answer'])
                        if (data.get('provenance', {}).get('canonical_profile_revision') == revision
                                and isinstance(value, str) and re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])(?:-01)?', value)):
                            answer = Answer(value, 'verified_memory')
                            break
            if answer:
                try:
                    if q.kind == 'checkbox' and isinstance(answer.value, bool) and len(q.options) <= 1:
                        pass
                    elif isinstance(answer.value, list) and (field.get('multiple') is True or q.kind == 'checkbox'):
                        answers.validate_answer(replace(q, kind='multiselect'), answer.value)
                    else:
                        answers.validate_answer(q, answer.value)
                    if q.max_length == 0 and answer.value:
                        raise ValueError('Field cannot accept text')
                except (ValueError, TypeError):
                    answer = None
            status = ('RESOLVED' if answer else 'NEEDS_WRITING' if not field.get('section_missing_date') and answers.is_writing_question(q)
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
                          field=dict(label=q.label, kind=q.kind, options=q.options),
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


def _writing_job(dashboard, payload):
    page = payload.get('page', {})
    if not isinstance(page, dict) or any(not isinstance(page.get(k, ''), str)
                                        for k in ('url', 'title', 'company', 'description')):
        raise SettingsRejected('page must contain text values')
    enabled = payload.get('use_job_description', True)
    if type(enabled) is not bool:
        raise SettingsRejected('use_job_description must be boolean')
    url = payload.get('url', page.get('url'))
    application_id = payload.get('application_id')
    if application_id is not None:
        application_id = _id(application_id)
    elif url is not None:
        url = _url(url)
        with dashboard.connection() as db:
            app = _match(db, url)
        if app:
            _unprotected(app)
            application_id = app['id']
    if application_id is not None:
        job = dashboard._cv_job(application_id)
        description = page.get('description', '')[:6000]
        if enabled and len(description) > len(job.get('description') or ''):
            with dashboard.connection(writable=True) as db:
                descriptions.store(db, job['job_id'], description)
            job['description'] = description
    else:
        job = dict(canonical_url=url or '', **{k: page.get(k, '')
                   for k in ('title', 'company', 'description')})
    job['description'] = (job.get('description') or '')[:6000] if enabled else ''
    if not enabled:
        job['canonical_url'] = ''
    return job, application_id


def _text(payload, key, maximum, *, required=False):
    value = payload.get(key, '')
    if not isinstance(value, str) or len(value) > maximum or required and not value.strip():
        raise SettingsRejected(key + ' must be text' + (' and non-empty' if required else '') +
                               ' of at most ' + str(maximum) + ' characters')
    return value


def _limits(question):
    limits = {}
    for key, unit in (('words', 'words?'), ('chars', 'characters?|chars?')):
        matches = re.findall(r'\b(?:' + unit + r')\s+limit\s*[:=]?\s*(\d[\d,]*)\b|'
                             r'\b(\d[\d,]*)\s*(?:' + unit + r')\b', question, re.I)
        if matches:
            limits[key] = min(int(a.replace(',', '') or b.replace(',', '')) for a, b in matches)
    return limits


def _writing_result(value, key):
    if (not isinstance(value, dict) or set(value) != {key} or
            not isinstance(value[key], str) or not value[key].strip()):
        raise SettingsRejected('AI provider returned invalid output; expected ' + key + ' text')
    return value[key].strip()


def write(dashboard, payload):
    question = _text(payload, 'question', 8000, required=True)
    instruction = _text(payload, 'instruction', 8000)
    previous = _text(payload, 'previous', 16000)
    limits = _limits(question)
    dashboard._writing_settings()
    job, _ = _writing_job(dashboard, payload)
    if payload.get("use_job_description", True):
        dashboard.writing_job(job)
    job["description"] = (job.get("description") or "")[:6000]
    with dashboard.answers.mutex:
        facts = cv.profile_facts(dashboard.config.profile)
    result = dashboard.run_writing(dict(task='question_answer', profile=facts, job=dict(cv.job_context(job), canonical_url=job.get("canonical_url", "")),
                                       question=question, instruction=instruction, previous=previous, limits=limits))
    answer = _writing_result(result, 'answer')
    response = dict(answer=answer, words=len(answer.split()), chars=len(answer))
    warnings = [f'{key} limit exceeded ({response[key]}/{maximum})' for key, maximum in limits.items()
                if response[key] > maximum]
    if warnings:
        response['warning'] = '; '.join(warnings)
    return response


def chat(dashboard, payload):
    messages = payload.get('messages')
    if not isinstance(messages, list) or not messages or any(
            not isinstance(item, dict) or item.get('role') not in ('user', 'assistant') or
            not isinstance(item.get('text'), str) or not item['text'].strip() for item in messages):
        raise SettingsRejected('messages must contain user or assistant text')
    history = [dict(role=item['role'], text=item['text'][:2000]) for item in messages[-12:]]
    dashboard._writing_settings()
    job, _ = _writing_job(dashboard, payload)
    if payload.get("use_job_description", True):
        dashboard.writing_job(job)
    job["description"] = (job.get("description") or "")[:6000]
    with dashboard.answers.mutex:
        facts = cv.profile_facts(dashboard.config.profile)
    result = dashboard.run_writing(dict(task='chat', profile=facts, job=dict(cv.job_context(job), canonical_url=job.get("canonical_url", "")), messages=history))
    return dict(reply=_writing_result(result, 'reply'))


def cover_letter(dashboard, payload):
    job, application_id = _writing_job(dashboard, payload)
    return dashboard.generate_cover_letter(job, payload.get('answers'),
                                           application_id=application_id, extension=True)


def extension_run_status(dashboard, payload):
    with dashboard._ext_mutex:
        dashboard._ext_last_poll = time.monotonic()
    return run_status(dashboard, payload)


def run_status(dashboard, payload):
    with dashboard._ext_mutex:
        items = [dict(item) for item in dashboard._ext_run]
        last_poll = getattr(dashboard, '_ext_last_poll', None)
    return dict(items=items, current=next((i['application_id'] for i in items if i['state'] == 'opened'), None),
                poll_age_seconds=time.monotonic() - last_poll if last_poll is not None else None)


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
        if any(item['state'] in {'pending', 'opened'} for item in dashboard._ext_run):
            raise SettingsRejected('Cancel the active Chrome run first', status=409)
        run_id = uuid.uuid4().hex
        dashboard._ext_run = [dict(application_id=value, run_id=run_id, **{k: available[value][k] for k in ('company', 'title', 'url')},
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
        dashboard._ext_last_poll = time.monotonic()
        if any(item['state'] == 'opened' for item in dashboard._ext_run):
            return dict(wait=True)
        for item in dashboard._ext_run:
            if item['state'] == 'pending':
                item['state'] = 'opened'
                return dict(**{k: item[k] for k in ('application_id', 'url', 'company', 'title', 'run_id')},
                            index=dashboard._ext_run.index(item) + 1, total=len(dashboard._ext_run))
    return dict(done=True)


def run_result(dashboard, payload):
    application_id = _id(payload.get('application_id'))
    state, detail = payload.get('state'), payload.get('detail', '')
    if not isinstance(state, str) or state not in {'filled', 'needs_input', 'failed', 'skipped'} or not isinstance(detail, str) or len(detail) > 2000:
        raise SettingsRejected('Invalid result state or detail')
    with dashboard._ext_mutex:
        item = next((i for i in dashboard._ext_run if i['application_id'] == application_id), None)
        if not item or item['state'] != 'opened' or payload.get('run_id', item['run_id']) != item['run_id']:
            raise SettingsRejected('Application is not open in this run', status=409)
        item.update(state=state, detail=detail)
    return dict(ok=True)


def applied(dashboard, payload):
    application_id = _id(payload.get('application_id'))
    url = _url(payload.get('url'))
    evidence = _text(payload, 'evidence', 2000, required=True)
    result = dashboard.mark_applied(application_id, evidence=json.dumps(dict(url=url, evidence=evidence)))
    with dashboard._ext_mutex:
        for item in dashboard._ext_run:
            if item['application_id'] == application_id and item['state'] != 'skipped':
                item.update(state='submitted', detail=evidence)
    return result


def unapplied(dashboard, payload):
    return dashboard.mark_applied(_id(payload.get('application_id')), undo=True)


ROUTES = {('POST', '/ext/snapshot'): snapshot, ('GET', '/ext/profile-sections'): profile_sections, ('GET', '/ext/ping'): ping, ('POST', '/ext/match'): match,
          ('POST', '/ext/applied'): applied, ('POST', '/ext/unapplied'): unapplied,
          ('POST', '/ext/answers'): resolve_answers, ('POST', '/ext/remember'): remember,
          ('POST', '/ext/write'): write, ('POST', '/ext/chat'): chat,
          ('POST', '/ext/cover-letter'): cover_letter,
          ('GET', '/ext/resume'): resume, ('GET', '/ext/run/next'): run_next,
          ('POST', '/ext/run/result'): run_result, ('GET', '/ext/run/status'): extension_run_status}


def dispatch(dashboard, payload, *, method, path):
    handler = ROUTES.get((method, path))
    if handler is None:
        raise SettingsRejected('endpoint not found', status=404)
    return handler(dashboard, payload)
