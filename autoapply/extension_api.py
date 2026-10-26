"""Paired, offline extension operations. Final submission remains manual."""

import base64
import calendar
from dataclasses import replace
import json
import math
import re
import time
import threading
import uuid
from urllib.parse import urlsplit

from . import answers, cv, descriptions, question_rules
from .dashboard_settings import SettingsRejected
from .field_mapping import answer_signature
from .jobs import ats_identity, canonical_url, normalize
from .models import Answer, Question, now
from .profile_rules import parse_profile_date

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
    if kind == 'checkbox' and len(options) > 1 and field.get('multiple') is True:
        kind = 'multiselect'
    # This observed public label is a brand, not a camelCase machine identifier.
    label = 'Linkedin' if field['label'].strip() == 'LinkedIn' else field['label']
    return Question(field.get('key', field['label']), label, kind,
                    field.get('required', False), options, maximum)


def _education_date_part(q, field, profile):
    label = normalize(q.label)
    part = re.fullmatch(r'(?:education )?(start|end) date (month|year)', label)
    if not part or not re.search(r'\beducation\b', normalize(field.get('section', '') + ' ' + q.label)):
        return False, None
    path = 'start_date' if part[1] == 'start' else 'graduation_date'
    value = profile.get('education', {}).get(path)
    parsed = parse_profile_date(value)
    if not parsed:
        return True, None
    year, month = str(parsed.year), parsed.month
    candidates = [year] if part[2] == 'year' else [calendar.month_name[month], str(month), f'{month:02}', calendar.month_abbr[month]]
    selected = next((fitted for candidate in candidates
                     if (fitted := answers.fit_options(candidate, q.options)) is not None), None)
    return True, Answer(selected, 'profile:education.' + path) if selected is not None else None


def _repeatable_answer(q, field, profile):
    """Use explicit facts from the first saved work/education entry."""
    section, label = normalize(field.get('section', '')), normalize(q.label).strip(' *')
    work = bool(re.search(r'\b(work experience|employment)\b', section))
    education = bool(re.search(r'\beducation\b', section))
    project = bool(re.search(r'\bprojects?\b', section))
    if not work and not education and not project:
        return False, None
    entries = profile.get('employment' if work else 'projects' if project else 'education_history', [])
    entry = entries[0] if entries and isinstance(entries[0], dict) else {} if work or project else profile.get('education', {})
    names = ({'job title': 'title', 'company': 'company', 'location': 'location',
              'role description': 'description', 'i currently work here': 'current'} if work else
             {'school or university': 'school', 'school': 'school', 'degree': 'degree', 'field of study': 'major'})
    date = re.fullmatch(r'(start|end|from|to) date(?: (month|year))?', label)
    key = names.get(label) if not project else None
    if date:
        key = 'start_date' if date[1] in {'start', 'from'} else 'end_date' if work or project else 'graduation_date'
    if not key:
        return False, None
    value = entry.get(key)
    if date:
        parsed = parse_profile_date(value)
        if not parsed:
            return True, None
        if (work or project) and key == 'end_date' and entry.get('current') is True:
            return True, None
        month = parsed.month
        if date[2] is None:
            hint = str(field.get('placeholder') or '') + ' ' + q.label
            candidates = [answers.format_start_date(value, q.kind, hint)]
        elif date[2] == 'year':
            candidates = [str(parsed.year)]
        elif q.kind == 'number' or not q.options:
            candidates = [str(month)]
        else:
            candidates = [calendar.month_name[month], str(month), f'{month:02}', calendar.month_abbr[month]]
        value = next((fitted for candidate in candidates
                      if (fitted := answers.fit_options(candidate, q.options)) is not None), None)
    elif value is not None and not isinstance(value, bool):
        value = answers.fit_options(str(value), q.options)
        if education and key == 'degree' and value is None and entry.get('major'):
            value = answers.fit_options(str(entry['major']), q.options)
    return True, Answer(value, 'profile:' + ('employment.0.' if work else 'projects.0.' if project else 'education.') + key) if value is not None else None


def _graduation_answer(q, field, profile):
    if q.kind not in {'text', 'date', 'select', 'combobox'} or not re.search(
            r'\bwhen\b.*\bgraduate\b|\b(?:expected |anticipated |projected )?graduation date\b', normalize(q.label)):
        return None
    if re.search(r'\b(month|year)\b', normalize(q.label)):
        return None
    raw = str(profile.get('education', {}).get('graduation_date') or '')
    parsed = parse_profile_date(raw)
    if not parsed:
        return None
    year, month, day = str(parsed.year), f'{parsed.month:02}', f'{parsed.day:02}'
    short = calendar.month_abbr[int(month)] + '-' + year[2:]
    candidates = [short, month + '/' + year, month + '/' + day + '/' + year,
                  calendar.month_name[int(month)] + ' ' + year,
                  calendar.month_abbr[int(month)] + ' ' + year, year + '-' + month,
                  parsed.isoformat()]
    if q.options:
        value = next((v for candidate in candidates
                      if (v := answers.fit_options(candidate, q.options)) is not None), None)
    else:
        hint = str(field.get('placeholder') or '') + ' ' + q.label
        value = (parsed.isoformat() if q.kind == 'date' else
                 short if re.search(r'mmm[-/ ]yy\b', hint, re.I) else
                 month + '/' + day + '/' + year if re.search(r'\bdd\b', hint, re.I) else
                 month + '/' + year if re.search(r'\bmm[/ -]yyyy\b', hint, re.I) else
                 short if normalize(q.label) == 'when are you expected to graduate' else month + '/' + year)
    return Answer(value, 'profile:education.graduation_date') if value is not None else None


def profile_sections(dashboard, payload):
    """Explicit resume facts only; no writer or applicant-data mutation."""
    profile = dashboard.config.profile
    def boolean(value):
        return value is True or str(value).lower() == 'true'
    def date(value):
        parsed = parse_profile_date(value)
        return parsed.isoformat()[:7] if parsed else ''
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
                skills=skills, links=profile.get('links') or {},
                identity={'full_name': profile.get('identity', {}).get('full_name') or
                          ' '.join(str(profile.get('identity', {}).get(key) or '').strip()
                                   for key in ('first_name', 'last_name')).strip()},
                application_preferences={'disability': profile.get('application_preferences', {}).get('disability')
                                         or profile.get('demographics', {}).get('disability', '')})


def ping(dashboard, payload):
    identity = dashboard.config.profile.get('identity', {})
    return dict(ok=True, name='AutoApply', profile_revision=dashboard.config.profile_snapshot().revision, profile_name=' '.join(
        str(identity.get(k) or '') for k in ('first_name', 'last_name')).strip())


def match(dashboard, payload):
    with dashboard.connection() as db:
        app = _match(db, _url(payload.get('url')))
    return (dict(application_id=app['id'], company=app['company'], title=app['title'], status=app['status'])
            if app else dict(application_id=None))



def secondary_address(label):
    label = re.sub(r'[^a-z0-9]+', ' ', normalize(label)).strip()
    return bool(re.search(r'\b(?:address(?: line)?\s*2|line\s*2|apt|apartment|suite|unit)\b', label))


_TRACE_LOCK = threading.Lock()


def trace(dashboard, payload):
    """Persist bounded diagnostics from the paired extension, with fixed retention."""
    host, kind, entries = payload.get('host'), payload.get('kind'), payload.get('entries')
    if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', host):
        raise SettingsRejected('Invalid trace host')
    if kind not in {'autofill', 'skills', 'section', 'fill'}:
        raise SettingsRejected('Invalid trace kind')
    if not isinstance(entries, list) or len(entries) > 2000 or any(not isinstance(row, dict) for row in entries):
        raise SettingsRejected('Trace entries must contain at most 2000 objects')
    allowed = {'timestamp', 'step', 'action', 'label', 'automation_id', 'element_id', 'value',
               'texts', 'total', 'scrollTop', 'rows', 'target', 'verified', 'aria_label', 'pill',
               'reason', 'error', 'stack', 'observed'}
    rows = [{key: value for key, value in row.items() if key in allowed} for row in entries]
    data = dict(host=host, kind=kind, started=str(payload.get('started', ''))[:100],
                ended=str(payload.get('ended', ''))[:100], entries=rows,
                truncated=payload.get('truncated') is True)
    encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded.encode('utf-8')) > 1024 * 1024:
        raise SettingsRejected('Trace exceeds 1 MB')
    directory = dashboard.config.private / 'traces'
    with _TRACE_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        filename = host.lower() + '-' + str(time.time_ns()) + '-' + kind + '.json'
        (directory / filename).write_text(encoded, encoding='utf-8')
        files = sorted(directory.glob('*.json'), key=lambda item: item.stat().st_mtime_ns, reverse=True)
        for old in files[30:]:
            old.unlink(missing_ok=True)
    return dict(ok=True, filename=filename)


def _analysis_structure(value):
    if not isinstance(value, dict) or not isinstance(value.get('tag'), str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', value['tag']):
        raise SettingsRejected('Invalid analysis control')
    strings = {'tag', 'role', 'type', 'id', 'name', 'class', 'data-automation-id', 'aria-label',
               'aria-labelledby', 'aria-controls', 'aria-owns', 'aria-haspopup', 'aria-expanded',
               'aria-disabled', 'aria-readonly', 'aria-hidden', 'aria-checked', 'aria-pressed', 'inputmode', 'aria-autocomplete', 'display', 'visibility'}
    row = {}
    if 'root_id' in value:
        if type(value['root_id']) is not int or not 0 <= value['root_id'] < 128:
            raise SettingsRejected('Invalid analysis root')
        row['root_id'] = value['root_id']
    for key in strings & value.keys():
        if not isinstance(value[key], str):
            raise SettingsRejected('Invalid analysis attribute')
        row[key] = value[key][:300]
    if row.get('type') in {'password', 'hidden'}:
        raise SettingsRejected('Sensitive analysis control')
    for key in ('visible', 'disabled', 'readonly', 'transformed'):
        if type(value.get(key, False)) is not bool:
            raise SettingsRejected('Invalid analysis flag')
        row[key] = value.get(key, False)
    opacity, rect = value.get('opacity', 1), value.get('rect', [0, 0, 0, 0])
    if (type(opacity) not in (int, float) or not math.isfinite(opacity) or not 0 <= opacity <= 1 or
            not isinstance(rect, list) or len(rect) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 100000 for n in rect)):
        raise SettingsRejected('Invalid analysis geometry')
    row.update(opacity=opacity, rect=rect)
    transform = value.get('transform', 'none')
    if not isinstance(transform, str) or len(transform) > 200 or not re.fullmatch(r'none|matrix(?:3d)?\([-+0-9.eE, ]+\)', transform):
        raise SettingsRejected('Invalid analysis transform')
    row['transform'] = transform
    return row


def _analysis_active(field):
    """Separate inactive structural context from the current form assessment."""
    control, parents = field['control'], field['ancestors']
    def exposed(shape):
        return (shape['rect'][2] > 0 and shape['rect'][3] > 0 and
                shape.get('display') != 'none' and shape.get('visibility') not in {'hidden', 'collapse'} and
                shape.get('aria-hidden') != 'true')
    if ('hidden_parent' in field['reasons'] or
            any(not exposed(parent) or parent['opacity'] == 0 for parent in parents)):
        return False
    if control.get('display') == 'none' or control.get('visibility') in {'hidden', 'collapse'} or control.get('aria-hidden') == 'true':
        return False
    # Transparent native controls and tiny select-only focus proxies are real
    # controls when their surrounding widget/label is exposed.
    proxy = (control.get('role') == 'combobox' and 'readonly_proxy' in field['reasons'] or
             control.get('tag') == 'input' and control.get('type') in {'checkbox', 'radio', 'file'})
    return bool(exposed(control) and control['opacity'] > 0 or proxy and any(exposed(p) and p['opacity'] > 0 for p in parents))


def _analysis_context(value, host):
    try:
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or parts.hostname != host.lower():
            raise ValueError()
        origin = parts.scheme + '://' + parts.hostname + (f':{parts.port}' if parts.port is not None else '')
        page_url = (origin + (parts.path or '/'))[:2048]
        # Keep query-based application matching ephemeral; never persist it.
        context_url = _url(origin + (parts.path or '/') + ('?' + parts.query if parts.query else ''))
    except (ValueError, TypeError, AttributeError):
        raise SettingsRejected('Invalid analysis context URL') from None
    return context_url, page_url


def _inspection_target(value):
    """Persist T069 public destinations only, never queries, credentials or arbitrary paths."""
    if not isinstance(value, dict):
        raise SettingsRejected('Invalid inspection target')
    if value.get('invalid') is True:
        return dict(invalid=True)
    if value.get('protocol') in {'about:', 'javascript:', 'mailto:', 'tel:', 'blob:', 'data:', 'file:'}:
        return dict(protocol=value['protocol'])
    try:
        parts = urlsplit(value.get('origin', ''))
        if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or
                parts.query or parts.fragment or parts.path not in {'', '/'}):
            raise ValueError()
        origin = parts.scheme + '://' + parts.hostname + (f':{parts.port}' if parts.port else '')
        path = value.get('path', '[redacted]')
        public = (parts.hostname == 'jobs.smartrecruiters.com' and isinstance(path, str) and
                  re.fullmatch(r'/(?:[A-Za-z0-9_-]+/\d+|oneclick-ui/company/[A-Za-z0-9_-]+/publication/[a-fA-F0-9-]+)/?', path) or
                  parts.hostname == 'jobs.keysight.com' and isinstance(path, str) and re.fullmatch(r'/external/jobs/\d+/?', path))
        return dict(origin=origin[:300], path=path[:300] if public else '[redacted]')
    except (ValueError, TypeError, AttributeError):
        raise SettingsRejected('Invalid inspection target') from None


def _inspections(values):
    if not isinstance(values, list) or len(values) > 20:
        raise SettingsRejected('Invalid inspections')
    result = []
    statuses = {'controls_exposed', 'access_blocked', 'loading', 'unsupported_shadow_controls',
                'embedded_content_unavailable', 'no_exposed_controls', 'account_private'}
    def integer(value, maximum):
        if type(value) is not int or not 0 <= value <= maximum:
            raise SettingsRejected('Invalid inspection count')
        return value
    for value in values:
        if not isinstance(value, dict):
            raise SettingsRejected('Invalid inspection')
        # T070 intentionally emits only this fixed message on private account pages.
        if value.get('message') == 'Account fields are private.':
            result.append(dict(frame_id=integer(value.get('frame_id', 0), 1000000), status='account_private', roots=[], frames=[]))
            continue
        status = value.get('status')
        roots, frames = value.get('roots', []), value.get('frames', [])
        if (status not in statuses or not isinstance(roots, list) or len(roots) > 128 or
                not isinstance(frames, list) or len(frames) > 20):
            raise SettingsRejected('Invalid inspection inventory')
        safe_roots, safe_frames = [], []
        root_ids = set()
        for root in roots:
            if (not isinstance(root, dict) or root.get('kind') not in {'document', 'open_shadow'} or
                    not isinstance(root.get('host'), str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,79}', root['host'])):
                raise SettingsRejected('Invalid inspection root')
            root_id = integer(root.get('id'), 127)
            if root_id in root_ids:
                raise SettingsRejected('Duplicate inspection root')
            parent = root.get('parent_id')
            if 'parent_id' in root and (root_id != len(safe_roots) or
                    root['kind'] != ('document' if root_id == 0 else 'open_shadow') or
                    root['kind'] == 'document' and parent is not None or
                    root['kind'] == 'open_shadow' and (type(parent) is not int or parent not in root_ids)):
                raise SettingsRejected('Invalid inspection parent')
            root_ids.add(root_id)
            safe_roots.append(dict(id=root_id, host=root['host'], kind=root['kind'],
                                   exposed_controls=integer(root.get('exposed_controls'), 10000)))
            if 'parent_id' in root:
                safe_roots[-1]['parent_id'] = parent
        for frame in frames:
            if (not isinstance(frame, dict) or type(frame.get('visible')) is not bool or
                    type(frame.get('accessible')) is not bool or frame.get('ready_state') not in {'loading', 'interactive', 'complete', 'unavailable'}):
                raise SettingsRejected('Invalid inspection frame')
            safe_frames.append(dict(target=_inspection_target(frame.get('target')), visible=frame['visible'],
                                    accessible=frame['accessible'], ready_state=frame['ready_state']))
        result.append(dict(frame_id=integer(value.get('frame_id', 0), 1000000), status=status,
                           ready_state=value.get('ready_state') if value.get('ready_state') in {'loading', 'interactive', 'complete'} else 'unavailable',
                           roots=safe_roots, frames=safe_frames, truncated=value.get('truncated') is True,
                           closed_shadow_roots='not_observable'))
    return result


def _page_analysis(dashboard, payload, host):
    diagnostic = payload['diagnostic']
    fields = diagnostic.get('fields')
    if not isinstance(fields, list) or len(fields) > 50:
        raise SettingsRejected('Analysis fields must contain at most 50 items')
    safe = []
    reasons = {'unsupported_widget', 'readonly_proxy', 'readonly', 'disabled', 'hidden_parent',
               'transparent_proxy', 'omitted_by_scanner', 'menu_not_exposed', 'hidden_control'}
    for field in fields:
        q = _question(field)
        options, parents, menu = field.get('options', []), field.get('ancestors', []), field.get('menu', {})
        why = field.get('reasons', [])
        if (len(options) > 20 or not isinstance(parents, list) or len(parents) > 4 or
                not isinstance(why, list) or len(why) > 8 or any(not isinstance(r, str) or r not in reasons for r in why) or
                not isinstance(menu, dict) or type(menu.get('observed', False)) is not bool or
                type(menu.get('count', 0)) is not int or not 0 <= menu.get('count', 0) <= 10000 or
                not isinstance(menu.get('roles', []), list) or len(menu.get('roles', [])) > 10 or
                any(not isinstance(r, str) or not re.fullmatch(r'[a-z-]{1,30}', r) for r in menu.get('roles', []))):
            raise SettingsRejected('Invalid analysis field details')
        containers, option_shapes = menu.get('containers', []), menu.get('option_shapes', [])
        if not isinstance(containers, list) or len(containers) > 2 or not isinstance(option_shapes, list) or len(option_shapes) > 3:
            raise SettingsRejected('Invalid analysis menu shape')
        if type(field.get('multiple', False)) is not bool:
            raise SettingsRejected('Invalid analysis multiple state')
        row = dict(label=field['label'][:300], kind=field['kind'], multiple=field.get('multiple', False), required=q.required, options=[s[:200] for s in options],
                   control=_analysis_structure(field.get('control')), ancestors=[_analysis_structure(p) for p in parents],
                   menu=dict(observed=menu.get('observed', False), count=menu.get('count', 0), roles=menu.get('roles', []),
                             containers=[_analysis_structure(c) for c in containers], option_shapes=[_analysis_structure(o) for o in option_shapes]), reasons=why)
        for key in ('detected', 'answered'):
            if type(field.get(key)) is not bool:
                raise SettingsRejected('Invalid analysis field state')
            row[key] = field[key]
        frame_id, identity = field.get('frame_id', 0), field.get('page_identity', '')
        if type(frame_id) is not int or not 0 <= frame_id <= 1000000 or not isinstance(identity, str) or len(identity) > 100 or not re.fullmatch(r'[0-9:]*', identity):
            raise SettingsRejected('Invalid analysis source')
        row.update(frame_id=frame_id, page_identity=identity)
        if 'root_id' in field:
            root_id = field['root_id']
            if type(root_id) is not int or not 0 <= root_id < 128 or row['control'].get('root_id') != root_id:
                raise SettingsRejected('Invalid analysis root membership')
            row['root_id'] = root_id
        repair_status = field.get('repair_status')
        if repair_status is not None:
            if repair_status not in {'optional_skip', 'unresolved', 'verified', 'verification_failed', 'user_edited'}:
                raise SettingsRejected('Invalid repair field status')
            row['repair_status'] = repair_status
        row['active'] = _analysis_active(row)
        safe.append(row)
    for key in ('busy', 'responsive', 'truncated'):
        if type(diagnostic.get(key, False)) is not bool:
            raise SettingsRejected('Invalid analysis state')
    elapsed = diagnostic.get('inspection_ms', 0)
    if type(elapsed) is not int or not 0 <= elapsed <= 30000:
        raise SettingsRejected('Invalid analysis timing')
    findings = []
    context_url, page_url = _analysis_context(payload.get('context_url', 'https://' + host + '/'), host)
    # Read-only canonical resolution; never call the writer, remember, or fill APIs.
    with dashboard.connection() as db:
        app = _application(db, dict(url=context_url))
        resolver = answers.AnswerResolver(dashboard.config, db)
        for field in safe:
            if (field['answered'] or not field['active'] or field.get('repair_status') == 'verified' and field['kind'] == 'checkbox' or
                    field.get('repair_status') == 'optional_skip' and not field['required']):
                continue
            q = _question(field)
            why = field['reasons']
            notes = []
            if 'readonly_proxy' in why:
                notes.append('Read-only focus proxy: use its visible parent control, not text entry.')
            elif 'readonly' in why:
                notes.append('Read-only field; inspect the associated widget before attempting a fill.')
            if 'transparent_proxy' in why:
                notes.append('Input is transparent; parent geometry and classes are captured for review.')
            if 'hidden_parent' in why or 'hidden_control' in why:
                notes.append('Hidden ancestor or mobile fallback; use the visible version of this field.')
            if 'disabled' in why:
                notes.append('Disabled control; a prerequisite may need completing first.')
            if not field['detected']:
                notes.append('Scanner omitted this control; widget or visibility support needs review.')
            if 'menu_not_exposed' in why:
                notes.append('Menu is closed or not exposed. Options were not probed; matching is incomplete.')
            status = 'needs_user_fact'
            try:
                resolution = resolver.resolve_result(q, app)
                if resolution.answer is not None:
                    try:
                        check = replace(q, kind='multiselect') if field['multiple'] else q
                        answers.validate_answer(check, resolution.answer.value)
                        status = 'ready_for_saved_answer'
                    except (ValueError, TypeError):
                        status = 'no_offered_match'
                elif resolution.descriptor.policy in {'REQUIRE_USER', 'DO_NOT_ANSWER'}:
                    status = 'needs_user_fact'
                elif resolution.descriptor.semantic_key in {'UNKNOWN_FIELD', 'REJECTED_FIELD'}:
                    status = 'unknown_concept'
                # Diagnostic-only fact projection: an optionless select must
                # abstain during actual filling, even when a saved fact exists.
                elif q.options and resolver.resolve_result(replace(q, kind='text', options=[]), app).answer is not None:
                    status = 'no_offered_match'
                elif answers.is_writing_question(q):
                    status = 'needs_writing'
            except (ValueError, KeyError, TypeError, OSError):
                status = 'answer_check_unavailable'
            messages = {
                'ready_for_saved_answer': 'A saved answer is available. Use Autofill when this control is accessible.',
                'no_offered_match': 'No saved match among the captured choices; review options and their mapping.',
                'unknown_concept': 'Label has no trusted mapping; clarify the question or add scanner support.',
                'needs_user_fact': 'No usable saved fact was found. Review this question; analysis will not guess.',
                'needs_writing': 'This needs a written answer. Use the answer helper or explicit Autofill.',
                'answer_check_unavailable': 'Saved-answer check unavailable; structural findings are still saved.'}
            notes.append(messages[status])
            if field.get('repair_status') == 'verification_failed':
                status = 'verification_failed'
                notes = ['The offered answer did not remain selected after retry. Review this control on the page.']
            elif field.get('repair_status') == 'user_edited':
                status = 'user_edited'
                notes = ['Your edit was preserved. Complete this answer on the page.']
            findings.append(dict(label=field['label'], kind=q.kind, detected=field['detected'], required=q.required,
                                 status=status, notes=notes))
    total, unanswered = sum(field['active'] for field in safe), len(findings)
    summary = (f'{unanswered} unfilled controls · {sum(not f["detected"] for f in findings)} omitted by scanner'
               if unanswered else 'All inspected controls are filled.' if total else
               'No active form controls found.' if safe else 'No form controls found.')
    repair = diagnostic.get('repair')
    sanitized_repair = None
    if repair is not None:
        if (not isinstance(repair, dict) or type(repair.get('filled')) is not int or not 0 <= repair['filled'] <= 500 or
                type(repair.get('attempts')) is not int or not 0 <= repair['attempts'] <= 2 or
                repair.get('stopped') not in {'none', 'user_edit', 'interrupted'} or
                not isinstance(repair.get('before'), dict) or 'repair' in repair['before']):
            raise SettingsRejected('Invalid repair diagnosis')
        before = _page_analysis(dashboard, dict(payload, diagnostic=dict(repair['before'], reason='page_analysis', inspections=diagnostic.get('inspections', []))), host)
        sanitized_repair = dict(filled=repair['filled'], attempts=repair['attempts'], stopped=repair['stopped'],
                                before=before)
        if total:
            summary = f'Filled {repair["filled"]} \u00b7 {unanswered} need attention'
        if repair['stopped'] != 'none':
            summary += ' \u00b7 Repair stopped; review and retry'
    inspections = _inspections(diagnostic.get('inspections', []))
    for field in safe:
        if 'root_id' not in field:
            continue
        inventory = next((i for i in inspections if i['frame_id'] == field['frame_id']), None)
        ids = {r['id'] for r in inventory['roots']} if inventory else set()
        if field['root_id'] not in ids or any(shape.get('root_id', field['root_id']) not in ids for shape in [field['control'], *field['ancestors']]):
            raise SettingsRejected('Unknown analysis root')
    if not total and inspections:
        messages = {'unsupported_shadow_controls': 'Open shadow controls need scanner support.',
                    'access_blocked': 'Page access or verification requires user attention.',
                    'loading': 'Page is loading; wait for the inputs.',
                    'embedded_content_unavailable': 'Open the application frame and analyze again.',
                    'account_private': 'Account fields are private.'}
        summary = next((messages[i['status']] for i in inspections if i['status'] in messages), summary)
    result = dict(reason='page_analysis', page_url=page_url, fields=safe, findings=findings, summary=summary,
                inspections=inspections,
                busy=diagnostic.get('busy', False), responsive=diagnostic.get('responsive', False), inspection_ms=elapsed,
                truncated=diagnostic.get('truncated', False), provider='not_used',
                note='Details saved for review.')
    if sanitized_repair is not None:
        result['repair'] = sanitized_repair
    return result


def snapshot(dashboard, payload):
    """Save only the documented structural attributes, never field values."""
    host, nodes = payload.get('host'), payload.get('nodes')
    if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', host):
        raise SettingsRejected('Invalid snapshot host')
    if not isinstance(nodes, list) or len(nodes) > 10000:
        raise SettingsRejected('Snapshot nodes must be a list of at most 10000 items')
    attributes = {'tag', 'role', 'type', 'id', 'name', 'class', 'data-automation-id', 'aria-label', 'aria-checked', 'aria-pressed', 'labelledby', 'label', 'text'}
    sanitized = []
    text_tags = {'label', 'legend', 'option', 'button', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('tag'), str):
            raise SettingsRejected('Invalid snapshot node')
        if node.get('type') in {'password', 'hidden'}:
            continue
        row = {key: value[:2000] for key, value in node.items() if key in attributes and isinstance(value, str)}
        if row.get('tag') not in text_tags and row.get('role') not in {'heading', 'option'} and row.get('data-automation-id') not in {'richText', 'formLabel'}:
            row.pop('text', None)
        row['visible'] = node.get('visible') is True
        if 'root_id' in node:
            if type(node['root_id']) is not int or not 0 <= node['root_id'] < 128:
                raise SettingsRejected('Invalid snapshot root')
            row['root_id'] = node['root_id']
        caption = node.get('caption')
        if isinstance(caption, str) and re.fullmatch(r"apply(?: now| for this job| manually| with linkedin)?|i['\u2019]m interested|continue application|submit(?: application)?|next|sign in|log in", caption, re.I):
            row.update(caption=caption.lower(), form_contained=node.get('form_contained') is True)
            if 'target' in node:
                row['target'] = _inspection_target(node['target'])
        sanitized.append(row)
    saved = dict(host=host, nodes=sanitized, truncated=payload.get('truncated') is True)
    if 'inspection' in payload:
        saved['inspection'] = _inspections([payload['inspection']])[0]
    diagnostic = payload.get('diagnostic')
    analysis = isinstance(diagnostic, dict) and diagnostic.get('reason') == 'page_analysis'
    if analysis:
        saved['diagnostic'] = _page_analysis(dashboard, payload, host)
        saved.update(id=str(uuid.uuid4()), created_at=now(), source='automatic_analysis' if payload.get('automatic') is True else 'manual_analysis')
    if isinstance(diagnostic, dict) and diagnostic.get('reason') == 'unresolved_fields':
        fields = diagnostic.get('fields')
        if not isinstance(fields, list) or len(fields) > 50:
            raise SettingsRejected('Diagnostic fields must be a list of at most 50 items')
        bounded = []
        kinds = {'text', 'textarea', 'select', 'radio', 'checkbox', 'combobox', 'file', 'date', 'number', 'email', 'tel', 'url'}
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get('kind'), str) or field['kind'] not in kinds or not isinstance(field.get('label'), str):
                raise SettingsRejected('Invalid diagnostic field')
            options = field.get('options', [])
            if not isinstance(options, list) or len(options) > 20 or not all(isinstance(option, str) for option in options):
                raise SettingsRejected('Diagnostic options must contain at most 20 strings')
            bounded.append(dict(label=field['label'][:300], kind=field['kind'], options=[option[:200] for option in options]))
        saved['diagnostic'] = dict(reason='unresolved_fields', fields=bounded)
    data = json.dumps(saved, ensure_ascii=False)
    if len(data.encode('utf-8')) > 1000000:
        raise SettingsRejected('Snapshot exceeds 1 MB')
    directory = dashboard.config.private / 'snapshots'
    directory.mkdir(parents=True, exist_ok=True)
    filename = host.lower() + '-' + str(time.time_ns()) + '.json'
    (directory / filename).write_text(data, encoding='utf-8')
    repair = dashboard.repairs.enqueue(saved)
    if analysis:
        return dict(ok=True, analysis_id=saved['id'], summary=saved['diagnostic']['summary'],
                    repair=repair, inspections=saved['diagnostic']['inspections'],
                    findings=saved['diagnostic']['findings'], note=saved['diagnostic']['note'],
                    truncated=saved['truncated'] or saved['diagnostic']['truncated'])
    return dict(ok=True, filename=filename, repair=repair)


def _choice_answer(dashboard, q, resolution, profile):
    """One cached interpretation through the existing writer; choices are never prose."""
    from .profile_snapshot import fingerprint
    descriptor = resolution.descriptor
    if (descriptor.semantic_key != 'UNKNOWN_FIELD' or not q.options
            or q.kind not in {'multiselect', 'select', 'radio', 'combobox'}
            or descriptor.policy in {'REQUIRE_USER', 'DO_NOT_ANSWER'}):
        return None
    # Undefined legal, identity and demographic claims must stay with the applicant.
    if re.search(r'\b(?:agree\w*|restrict\w*|legal|consent|certify|citizen\w*|sponsor\w*|visa|gender|race|ethnic\w*|disab\w*|veteran|sexual|medical)\b', normalize(q.label)):
        return None
    facts = {key: profile[key] for key in ('languages', 'skills', 'skill_categories',
             'education', 'career_interests') if profile.get(key)}
    preferences = profile.get('application_preferences', {})
    for key in ('role_preferences', 'internship_cohort', 'career_goal'):
        if preferences.get(key):
            facts['application_preferences.' + key] = preferences[key]
    if not facts:
        return None
    cache_key = fingerprint([descriptor.signature, resolution.profile_revision, facts])
    cache = getattr(dashboard, '_ext_choice_answers', {})
    if cache_key in cache:
        return cache[cache_key]
    answer = None
    try:
        result = dashboard.run_writing(dict(task='choice_interpret', profile=facts,
            question=q.label, options=q.options, job={}, timeout_seconds=8,
            instruction=('Interpret this choice question only from explicit supplied profile facts. '
                'Omission is not evidence of a negative fact. Do not infer unlisted abilities. '
                'Return a JSON object with choices (exact offered strings), '
                'fact_ids (nonempty supplied profile keys supporting the choices), and needs_input. '
                'If ambiguous set needs_input true and choices empty. None is exclusive. '
                'Do not write prose. Choose one unless multiple is true.'),
            multiple=q.kind == 'multiselect'))
        proposed = result
        choices, ids = proposed.get('choices'), proposed.get('fact_ids')
        if (proposed.get('needs_input') is False and isinstance(choices, list) and choices
                and isinstance(ids, list) and ids and all(isinstance(k, str) and k in facts for k in ids)):
            value = choices if q.kind == 'multiselect' else choices[0] if len(choices) == 1 else None
            answers.validate_answer(q, value)
            answer = Answer(value, 'grounded_ai:choice', evidence=ids,
                            provenance={'rule': 'validated-profile-choice'})
    except Exception:
        pass  # Unavailable/invalid/ambiguous interpretation remains visible for user input.
    if len(cache) >= 64:
        cache.clear()
    cache[cache_key] = answer
    dashboard._ext_choice_answers = cache
    return answer


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
            from .question_keywords import date_part_label, unusable_label
            if (q.kind in {'select', 'radio', 'multiselect'} or
                    q.kind == 'combobox' and field.get('options_observed') is True and not field.get('options_partial')) and not q.options:
                result.append(dict(key=q.key, value=None, source='', status='NEEDS_INPUT' if q.required else 'OPTIONAL_SKIP',
                                   reason='No offered options are available.'))
                continue
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
            if (re.search(r'^if yes.*(?:name|who).*referred', normalize(q.label))
                    and profile.get('application_preferences', {}).get('referred_by_employee') is False):
                result.append(dict(key=q.key, value='', source='profile:application_preferences.referred_by_employee', status='OPTIONAL_SKIP'))
                continue
            if q.kind == 'file' and not re.search(r'\b(resume|cv|cover)\b', q.label, re.I):
                result.append(dict(key=q.key, value=None, source='', status='NEEDS_INPUT', reason='File purpose is ambiguous; choose the attachment manually.'))
                continue
            if q.kind == 'file':
                result.append(dict(key=q.key, value='cover_letter' if 'cover' in q.label.lower() else 'resume',
                                   source='file', status='FILE'))
                continue
            education_part, education_answer = _education_date_part(q, field, profile)
            repeated, repeated_answer = _repeatable_answer(q, field, profile)
            if date_part_label(q.label) and not education_part and not repeated:
                result.append(dict(key=q.key, value=None, source='', status='NEEDS_INPUT'))
                continue
            resolution = resolver.resolve_result(q, app)
            answer = education_answer if education_part else resolution.answer
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
            if answer is None and not repeated and not education_part:
                answer = _choice_answer(dashboard, q, resolution, resolver.snapshot.facts)
            status = ('RESOLVED' if answer else 'NEEDS_WRITING' if not field.get('section_missing_date') and answers.is_writing_question(q)
                      else 'NEEDS_INPUT' if q.required else 'OPTIONAL_SKIP')
            result.append(dict(key=q.key, value=answer.value if answer else None,
                               source=answer.source if answer else '', status=status,
                               **({'reason': 'No offered location matches saved preferences.'} if answer is None
                                  and re.fullmatch(r'what is your top location preference', normalize(q.label)) else {})))
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
                                       question=question, instruction=instruction, previous=previous, limits=limits, evidence_packet=cv.writing_evidence(job)))
    answer = _writing_result(result, 'answer')
    response = dict(answer=answer, words=len(answer.split()), chars=len(answer))
    warnings = [f'{key} limit exceeded ({response[key]}/{maximum})' for key, maximum in limits.items()
                if response[key] > maximum]
    if warnings:
        response['warning'] = '; '.join(warnings)
    return response


def _skill_key(value):
    # Preserve meaningful language punctuation (C, C++ and C# are different skills).
    return re.sub(r'[^a-z0-9+#]', '', value.casefold())


def skill_aliases(dashboard, payload):
    with dashboard.connection() as db:
        rows = db.rows("SELECT key,value FROM settings WHERE key LIKE 'skill_alias:%'")
    return dict(aliases={row['key'].split(':', 1)[1]: json.loads(row['value']) for row in rows})


def skill_choose(dashboard, payload):
    skill = _text(payload, 'skill', 200, required=True).strip()
    options = payload.get('options')
    if not isinstance(options, list) or len(options) > 2000 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 300 for item in options):
        raise SettingsRejected('options must be a list of at most 2000 skill names')
    aliases = skill_aliases(dashboard, {})['aliases']
    targets = {_skill_key(skill), _skill_key(aliases.get(_skill_key(skill), ''))}
    choice = next((item for item in options if _skill_key(item) in targets), None)
    if choice is None and options:
        try:
            result = dashboard.run_writing(dict(task='skill_choose', skill=skill, options=options,
                                                timeout_seconds=8))
            answer = result.get('choice', '') if isinstance(result, dict) else result
            if isinstance(answer, str) and answer.strip() in options:
                choice = answer.strip()
        except Exception:
            pass  # Unavailable AI leaves the field unchanged.
    if choice is not None:
        with dashboard.settings.mutex, dashboard.connection(writable=True) as db:
            db.set_setting('skill_alias:' + _skill_key(skill), choice)
    return dict(choice=choice)


def skill_plan(dashboard, payload):
    page = payload.get('page', {})
    if not isinstance(page, dict) or any(not isinstance(page.get(key, ''), str)
                                        for key in ('title', 'company', 'description')):
        raise SettingsRejected('page must contain text values')
    application_id = payload.get('application_id')
    if application_id is not None:
        _id(application_id)
    skills = profile_sections(dashboard, {})['skills']
    page = {key: page.get(key, '')[:12000] for key in ('title', 'company', 'description')}
    cache_key = json.dumps([application_id, page, skills], sort_keys=True)
    with dashboard._ext_mutex:
        cache = getattr(dashboard, '_ext_skill_plans', {})
        if cache_key in cache:
            return {key: list(value) for key, value in cache[cache_key].items()}
    description = page['description'].casefold()
    matched = [skill for skill in skills if re.search(
        r'(?<![\w+#])' + re.escape(skill.casefold()) + r'(?![\w+#])', description)]
    # Normal planning is deterministic and profile-supported; never invoke a writer.
    missing = []
    plan = dict(matched=matched, missing=missing, other=[skill for skill in skills if skill not in matched])
    with dashboard._ext_mutex:
        cache = getattr(dashboard, '_ext_skill_plans', {})
        if len(cache) >= 64:
            cache.clear()
        cache[cache_key] = plan
        dashboard._ext_skill_plans = cache
    return {key: list(value) for key, value in plan.items()}


def chat_replacement_intent(text):
    return (isinstance(text, str)
        and not re.search(r"\b(?:don.t|do not|never|cannot|can.t|draft\s+only|only\s+(?:a\s+)?draft|without\s+(?:changing|updating|replacing|filling|applying))\b", text, re.I)
        and bool(re.search(r'\b(?:field|textarea|text box|cover letter|answer)\b', text, re.I))
        and bool(re.match(r'^(?:(?:please|can you|could you|would you)\s+)*(?:(?:replace|overwrite|fill|put|insert|paste|change|update|generate|write|revise|rewrite|include|add|mention|make)\b|for\s+(?:the |my )?cover letter[,:]?\s+(?:include|add|mention|write|revise)\b|(?:the|my)\s+(?:answer|cover letter)\s+(?:should|needs to)\s+(?:be|say|include|mention)\b)', text.strip(), re.I)))


def chat_field_context(value):
    if value is None:
        return {'fields': [], 'current_key': None}
    if not isinstance(value, dict) or not isinstance(value.get('fields'), list) or len(value['fields']) > 80:
        raise SettingsRejected('Invalid field context')
    if value.get('current_key') is not None and (not isinstance(value['current_key'], str) or len(value['current_key']) > 100):
        raise SettingsRejected('Invalid current field key')
    fields = []
    for item in value['fields']:
        if (not isinstance(item, dict) or not isinstance(item.get('kind'), str) or item.get('kind') not in {'text', 'textarea', 'email', 'tel', 'url', 'file'}
                or any(not isinstance(item.get(k), str) or len(item[k]) > limit for k, limit in
                       [('key', 100), ('label', 500), ('identity', 100)])
                or item.get('max_length') is not None and (type(item['max_length']) is not int or item['max_length'] < 0)):
            raise SettingsRejected('Invalid writable field')
        if item['kind'] == 'file' and (item.get('purpose') != 'cover_letter' or not re.search(r'cover.*letter', item['label'], re.I) or re.search(r'resume|\bcv\b', item['label'], re.I)):
            raise SettingsRejected('Only explicit cover letter attachments are writable')
        fields.append({k: item.get(k) for k in ('key', 'label', 'kind', 'identity', 'max_length')})
    if len({f['key'] for f in fields}) != len(fields):
        raise SettingsRejected('Ambiguous field keys')
    current_key = value.get('current_key')
    if current_key is not None and current_key not in {f['key'] for f in fields}:
        raise SettingsRejected('Invalid selected field')
    result = dict(fields=fields, current_key=current_key)
    answer = value.get('current_answer')
    if answer is not None:
        if (not isinstance(answer, dict) or answer.get('key') != current_key or current_key is None
                or not isinstance(answer.get('text'), str) or len(answer['text']) > 12000):
            raise SettingsRejected('Invalid selected answer')
        result['current_answer'] = dict(key=current_key, text=answer['text'])
    return result


def chat_replacement_source(request):
    literal = re.search(r"\b(?:with|to)\s+([\"'])(.+)\1\s*$", request, re.I | re.S)
    if literal:
        return 'literal', literal[2]
    if re.search(r'\b(?:with|using)\s+(?:that|it|(?:the |this |that )?(?:draft|answer|text)(?: above)?|the above)\s*[.!]?$', request, re.I):
        return 'reference', None
    if re.search(r'\b(?:say|shorter|longer|concise|concisely|rewrite|rephrase|summarize|simplify|shorten|expand|formal|professional|friendly)\b|\b(?:include|mention|add|generate|write|revise|make)\b|\bto (?:be|include|mention|remove|add)\b', request, re.I):
        return 'rewrite', None
    return None, None


def chat_replacement(messages, context, *, last_draft=None, writer=None):
    request = messages[-1]['text'] if messages[-1]['role'] == 'user' else ''
    if not chat_replacement_intent(request):
        return None
    fields = context['fields']
    if re.search(r'\b(?:current|focused|this)\s+(?:field|textarea|text box|answer)\b', request, re.I):
        candidates = [f for f in fields if f['key'] == context['current_key']]
    else:
        candidates = [f for f in fields if f['label'].strip() and f['label'].strip().casefold() in request.casefold()]
        if not candidates and re.search(r'\b(?:my|the) answer\b', request, re.I):
            candidates = [f for f in fields if f['key'] == context['current_key']]
        if not candidates and re.search(r'cover letter', request, re.I):
            candidates = [f for f in fields if 'cover' in f['label'].casefold() and 'letter' in f['label'].casefold()]
    if len(candidates) != 1:
        return dict(reply='Select the text field on the page, then ask me to replace the current field with the draft.', kind='receipt')
    field = candidates[0]
    mode, draft = chat_replacement_source(request)
    if mode == 'reference':
        # Only explicitly identified drafts are paste candidates. Untyped legacy
        # assistant messages may be receipts, errors or selection guidance.
        draft = last_draft if last_draft is not None else next((m['text'] for m in reversed(messages[:-1])
                    if m['role'] == 'assistant' and m.get('kind') == 'draft'), '')
    elif mode == 'rewrite':
        answer = context.get('current_answer')
        cover = bool(re.search(r'cover.*letter', field['label'], re.I) and re.search(r'\b(?:include|mention|add|generate|write|revise)\b', request, re.I))
        if writer is None or (not cover and (not answer or answer['key'] != field['key'] or not answer['text'].strip())):
            return dict(reply='Select the field containing the answer to rewrite, then retry. The field is unchanged.', kind='receipt')
        draft = writer(field, answer['text'] if answer and answer['key'] == field['key'] else '', request)
    elif mode is None:
        return dict(reply='What text should replace the field? Quote it, refer to the draft, or specify how to rewrite the current answer.', kind='receipt')
    if not draft.strip() or len(draft) > 12000:
        return dict(reply='Draft the answer first, then ask me to replace the selected field. Drafts must be at most 12000 characters.', kind='receipt')
    if field['max_length'] is not None and len(draft) > field['max_length']:
        return dict(reply='The draft exceeds this field limit. Shorten it before replacing the field.', kind='receipt')
    return dict(reply='Replacement is ready for verification.', kind='receipt', replacement_mode=mode, draft=draft,
                actions=[dict(type='replace_cover_letter' if field['kind'] == 'file' else 'replace_text', key=field['key'], text=draft)])


def chat(dashboard, payload):
    messages = payload.get('messages')
    if not isinstance(messages, list) or not messages or any(
            not isinstance(item, dict) or item.get('role') not in ('user', 'assistant') or
            not isinstance(item.get('text'), str) or not item['text'].strip() or
            item.get('kind') not in (None, 'draft', 'receipt') for item in messages):
        raise SettingsRejected('messages must contain user or assistant text')
    history = [dict(role=item['role'], text=item['text'][:12000]) for item in messages[-12:]]
    context = chat_field_context(payload.get('field_context'))
    job, _ = _writing_job(dashboard, payload)
    last_draft = payload.get('last_draft')
    if last_draft is not None and (not isinstance(last_draft, str) or not last_draft.strip() or len(last_draft) > 12000):
        raise SettingsRejected('Invalid last draft reference')

    def rewrite(field, previous, instruction):
        maximum = min(field['max_length'] if field['max_length'] is not None else 12000, 12000)
        response = write(dashboard, dict(payload, question=field['label'], previous=previous,
                         instruction=('Write the complete cover letter using only supported profile/job facts and the user supplied writing context. Do not invent dates, roles, metrics or contributions.\n' if re.search(r'cover.*letter', field['label'], re.I) else '') + instruction + f'\nUse at most {maximum} characters.'))
        answer = response['answer']
        if len(answer) > maximum or 'warning' in response or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]|```', answer):
            raise SettingsRejected('The rewritten draft is invalid or exceeds the field limit. The field is unchanged; review and retry.')
        return answer

    replacement = chat_replacement(messages[-12:], context, last_draft=last_draft, writer=rewrite)
    if replacement is not None:
        for action in replacement.get('actions', []):
            if action['type'] == 'replace_cover_letter':
                try:
                    action['file'] = cv.cover_letter_draft_artifact(action['text'])
                except Exception:
                    return dict(reply=replacement['draft'], kind='draft', warning='Cover letter PDF rendering failed. The draft is preserved; the attachment is unchanged.')
        return replacement
    dashboard._writing_settings()
    if payload.get("use_job_description", True):
        dashboard.writing_job(job)
    job["description"] = (job.get("description") or "")[:6000]
    with dashboard.answers.mutex:
        facts = cv.profile_facts(dashboard.config.profile)
    result = dashboard.run_writing(dict(task='chat', profile=facts, job=dict(cv.job_context(job), canonical_url=job.get("canonical_url", "")), messages=history, field_context=context, evidence_packet=cv.writing_evidence(job)))
    return dict(reply=_writing_result(result, 'reply'), kind='draft')


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
        if payload.get('run_id') and any(item['run_id'] != payload['run_id'] for item in dashboard._ext_run):
            raise SettingsRejected('Run has changed', status=409)
        for item in dashboard._ext_run:
            if item['state'] in {'pending', 'opened'}:
                item.update(state='skipped', detail='Run cancelled')
    return run_status(dashboard, {})


def run_skip(dashboard, payload):
    application_id = _id(payload.get('application_id'))
    with dashboard._ext_mutex:
        item = next((i for i in dashboard._ext_run if i['application_id'] == application_id), None)
        if not item or item['state'] != 'opened' or payload.get('run_id') != item['run_id']:
            raise SettingsRejected('Application is not open in this run', status=409)
        item.update(state='skipped', detail='Skipped by user')
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
        if not item or payload.get('run_id') != item['run_id']:
            raise SettingsRejected('Application is not open in this run', status=409)
        # Only closing/explicitly skipping releases a claim. Filling, sign-in and
        # recoverable errors still belong to the current application lifecycle.
        if item['state'] == 'skipped' and state == 'skipped':
            return dict(ok=True)  # A lost response may be retried safely.
        if item['state'] != 'opened':
            raise SettingsRejected('Application is not open in this run', status=409)
        item.update(state='skipped' if state == 'skipped' else 'opened', phase=state, detail=detail)
    return dict(ok=True)


def applied(dashboard, payload):
    application_id = _id(payload.get('application_id'))
    if payload.get('run_id'):
        with dashboard._ext_mutex:
            if not any(i['application_id'] == application_id and i['run_id'] == payload['run_id']
                       and i['state'] in {'opened', 'submitted'} for i in dashboard._ext_run):
                raise SettingsRejected('Application is not open in this run', status=409)
    url = _url(payload.get('url'))
    evidence = _text(payload, 'evidence', 2000, required=True)
    result = dashboard.mark_applied(application_id, evidence=json.dumps(dict(url=url, evidence=evidence)))
    with dashboard._ext_mutex:
        for item in dashboard._ext_run:
            if (item['application_id'] == application_id and item['state'] != 'skipped'
                    and (not payload.get('run_id') or item['run_id'] == payload['run_id'])):
                item.update(state='submitted', detail=evidence)
    return result


def unapplied(dashboard, payload):
    return dashboard.mark_applied(_id(payload.get('application_id')), undo=True)


def credentials(dashboard, payload):
    """Release only to the paired extension for a stored, same-origin ATS job."""
    rejected = 'Account autofill requires an authorized HTTPS application sign-in form'
    if set(payload) - {'url', 'frame_url', 'application_id', 'run_id', 'mode'} or payload.get('mode') not in {'login', 'signup'}:
        raise SettingsRejected(rejected, status=403)
    application_id = _id(payload.get('application_id'))
    try:
        page, frame = urlsplit(payload['url']), urlsplit(payload['frame_url'])
        with dashboard.connection() as db:
            app = db.application(application_id)
            _unprotected(app)
            job = urlsplit(app['canonical_url'])
            host = job.hostname or ''
            supported = any(host == suffix or host.endswith('.' + suffix) for suffix in (
                'myworkdayjobs.com', 'greenhouse.io', 'lever.co', 'smartrecruiters.com',
                'pinpointhq.com', 'applytojob.com', 'icims.com', 'taleo.net', 'successfactors.com'))
            safe = supported and all(p.scheme == 'https' and p.hostname == host and
                                     p.port in (None, 443) and not p.username and not p.password
                                     for p in (job, page, frame))
            matched = _match(db, _url(payload['url'])) if safe else None
        with dashboard._ext_mutex:
            active = any(i['application_id'] == application_id and i['state'] == 'opened' and
                         i['run_id'] == payload.get('run_id') for i in dashboard._ext_run)
        if not safe or not (matched and matched['id'] == application_id or active):
            raise ValueError
    except (ValueError, KeyError, TypeError, SettingsRejected):
        raise SettingsRejected(rejected, status=403) from None
    value = dashboard.settings.credentials.load(profile_email=True)
    if not value['password'] or not value['email']:
        return dict(configured=False)
    return dict(configured=True, **value)


ROUTES = {('GET', '/ext/repairs'): lambda d, p: d.repairs.status(),
          ('POST', '/ext/repairs'): lambda d, p: d.repairs.configure(p),
          ('POST', '/ext/credentials'): credentials, ('POST', '/ext/trace'): trace, ('POST', '/ext/skills/choose'): skill_choose, ('GET', '/ext/skills/aliases'): skill_aliases,
          ('POST', '/ext/skills/plan'): skill_plan, ('POST', '/ext/snapshot'): snapshot, ('GET', '/ext/profile-sections'): profile_sections, ('GET', '/ext/ping'): ping, ('POST', '/ext/match'): match,
          ('POST', '/ext/applied'): applied, ('POST', '/ext/unapplied'): unapplied,
          ('POST', '/ext/answers'): resolve_answers, ('POST', '/ext/remember'): remember,
          ('POST', '/ext/write'): write, ('POST', '/ext/chat'): chat,
          ('POST', '/ext/cover-letter'): cover_letter,
          ('GET', '/ext/resume'): resume, ('GET', '/ext/run/next'): run_next,
          ('POST', '/ext/run/cancel'): run_cancel, ('POST', '/ext/run/skip'): run_skip,
          ('POST', '/ext/run/result'): run_result, ('GET', '/ext/run/status'): extension_run_status}


def dispatch(dashboard, payload, *, method, path):
    handler = ROUTES.get((method, path))
    if handler is None:
        raise SettingsRejected('endpoint not found', status=404)
    if path != '/ext/credentials':
        cleaned = dashboard.settings.credentials.redact(payload)
        if path == '/ext/remember' and (cleaned != payload or re.search(
                r'password|user\s*name|sign.?in|log.?in|account.*email', str(payload.get('label', '')), re.I)):
            raise SettingsRejected('Account credentials cannot be remembered')
        payload = cleaned
    return handler(dashboard, payload)
