"""Offline answer editor and side-effect-free autofill preview."""

import copy
from datetime import date, datetime, timezone
import json
import math
import re
import shutil
import tempfile
import threading
from urllib.parse import urlsplit
import os

import yaml

from .answers import AnswerResolver
from .config import read_yaml
from .dashboard_settings import SettingsRejected, replace_file, stamp
from .models import Question
from .profile_snapshot import ProfileSnapshot
from .profile_rules import STATES
from .concepts import canonical_key, REGISTRY, DEMOGRAPHIC_PATHS, demographic_value, demographic_values, equivalent_answer
from .question_groups import CATEGORIES, normalize, group, categorize


def field(path, label, kind='text', **extra):
    return dict(path=path, label=label, type=kind, help=extra.pop('help', ''),
                **({'nullable': True} | extra if kind == 'bool' else extra))


def field_label(name):
    acronyms = {'gpa': 'GPA', 'url': 'URL', 'zip': 'ZIP', 'sms': 'SMS', 'ats': 'ATS',
                'linkedin': 'LinkedIn', 'github': 'GitHub'}
    words = name.replace('_', ' ').split()
    return ' '.join(acronyms.get(word.lower(), word.capitalize() if index == 0 else word.lower())
                    for index, word in enumerate(words))


def fields(prefix, names):
    return [field(prefix + '.' + name, field_label(name), kind)
            for name, kind in names]


ITEMS = {
    'education_history': fields('', [('school', 'text'), ('degree', 'text'), ('major', 'text'),
        ('start_date', 'month'), ('graduation_date', 'month'), ('gpa', 'text'), ('current', 'bool')]),
    'employment': fields('', [('company', 'text'), ('title', 'text'), ('location', 'text'),
        ('start_date', 'month'), ('end_date', 'month'), ('current', 'bool'), ('description', 'textarea')]),
    'projects': fields('', [('name', 'text'), ('role', 'text'), ('url', 'url'), ('start_date', 'month'),
        ('end_date', 'month'), ('technologies', 'tags'), ('description', 'textarea')]),
    'links.other': fields('', [('label', 'text'), ('url', 'url')]),
    'eligibility_assertions': fields('', [('id', 'text'), ('meaning', 'textarea'), ('answer', 'bool'),
        ('source', 'text'), ('scope', 'text'), ('supplied_at', 'text')]),
}
for definitions in ITEMS.values():
    for definition in definitions:
        definition['path'] = definition['path'].lstrip('.')
        if definition['type'] == 'bool':
            definition['nullable'] = False

SECTIONS = [
    ('personal', 'Personal', fields('identity', [('first_name', 'text'), ('last_name', 'text'), ('full_name', 'text')])),
    ('contact', 'Contact', fields('contact', [('email', 'email'), ('phone', 'tel'), ('address', 'text'),
        ('city', 'text'), ('state', 'text'), ('postal_code', 'text'), ('country', 'text')])),
    ('education', 'Education', fields('education', [('school', 'text'), ('degree', 'text'), ('major', 'text'),
        ('school_year', 'text'), ('graduation_date', 'month'), ('gpa', 'text'), ('currently_enrolled', 'bool'),
        ('minors', 'tags'), ('good_academic_standing', 'bool')]) + [field('education_history', 'Additional education', 'list')]),
    ('experience', 'Experience', [field('employment', 'Work experience', 'list')]),
    ('projects', 'Projects', [field('projects', 'Projects', 'list')]),
    ('skills', 'Skills', [field('skills', 'Skills', 'tags')]),
    ('links', 'Links', [field('links.personal_website', 'Website', 'url'), field('links.github', 'GitHub', 'url'),
        field('links.linkedin', 'LinkedIn', 'url'), field('links.other', 'Other links', 'list')]),
    ('authorization', 'Work authorization', fields('citizenship', [('us_citizen', 'bool'), ('citizen_since_birth', 'bool')]) +
        fields('work_authorization', [('us_authorized', 'bool'), ('sponsorship_now', 'bool'), ('sponsorship_future', 'bool')])),
    ('availability', 'Availability', fields('availability.summer_2027', [('start_date', 'date'), ('end_date', 'date'), ('source', 'text')])),
    ('preferences', 'Preferences & demographics', fields('locations', [('willing_to_relocate', 'bool')]) +
        [field('locations.preferred_states', 'Preferred U.S. states (in order)', 'tags',
               help='State abbreviations or full names. Other cities in these states take priority; no default states are assumed.'),
         field('locations.ordered_preferences', 'Preferred cities (in order)', 'tags',
               help='Use City, State. Cities keep this order within preferred states, then elsewhere.')] +
        fields('application_preferences', [('compensation_text', 'textarea'), ('office_five_days', 'bool'), ('discovery_source', 'text'),
        ('application_source_answer', 'text'), ('career_goal', 'textarea'), ('willing_to_work_any_location', 'bool'),
        ('previous_employer_work_default', 'bool'), ('academic_honor', 'bool'), ('recruiting_sms_whatsapp', 'bool'),
        ('ordinary_policy_acknowledgement', 'bool'), ('technical_interests', 'tags')]) +
        fields('demographics', [(key, 'select') for key in ('sexual_orientation',
        'transgender', 'disability', 'armed_forces', 'gender', 'hispanic_latino', 'race', 'veteran_status')])),
    ('eligibility', 'Eligibility', fields('qualifications', [('age_18_or_older', 'bool'), ('programming_experience', 'bool')]) +
        [field('eligibility_assertions', 'Eligibility assertions', 'list')]),
]
OPTIONS = {'veteran': ['not a protected veteran', 'protected veteran', 'decline'],
           'race': ['decline', 'Asian', 'Black or African American', 'White', 'Hispanic or Latino', 'Other'],
           'gender': ['decline', 'Male', 'Female', 'Non-binary', 'Other'],
           'disability': ['decline', 'Yes', 'No']}


def definitions(profile):
    sections = copy.deepcopy(SECTIONS)
    existing = {f['path'] for _, _, fs in sections for f in fs}
    for key, value in (profile.get('qualifications') or {}).items():
        path = 'qualifications.' + key
        if path not in existing and type(value) is bool:
            definition = field(path, field_label(key), 'bool')
            definition['nullable'] = False
            sections[-1][2].append(definition)
    for key, value in (profile.get('dropdown_preferences') or {}).items():
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            sections[-2][2].append(field('dropdown_preferences.' + key, field_label(key), 'tags'))
    for key, value in (profile.get('verified_facts') or {}).items():
        if type(value) is bool or isinstance(value, str) or isinstance(value, list) and all(isinstance(v, str) for v in value):
            kind = 'bool' if type(value) is bool else 'tags' if isinstance(value, list) else 'textarea'
            sections[-1][2].append(field('verified_facts.' + key, 'Verified fact: ' + field_label(key), kind,
                                         parts=['verified_facts', key], source_path=key, nullable=False))
    # Dictionary keys may contain dots; explicit parts avoid interpreting question labels as paths.
    for group in ('standing_disclosures', 'common_answers'):
        for name, record in (profile.get(group) or {}).items():
            if not isinstance(record, dict):
                continue
            for key, value in record.items():
                kind = 'bool' if type(value) is bool else 'tags' if isinstance(value, list) else 'textarea'
                f = field(f'{group}.{name}.{key}', f'{name}: {field_label(key)}', kind,
                          parts=[group, name, key], nullable=False)
                if key == 'answer' and group == 'common_answers' and type(value) is not bool:
                    f['type'] = 'tags' if isinstance(value, list) else 'textarea'
                sections[-1][2].append(f)
    for _, _, fs in sections:
        for f in fs:
            if f['type'] == 'select':
                key = f['path'].split('.')[-1]
                f['options'] = OPTIONS.get('veteran' if key == 'veteran_status' else key, ['decline', 'Yes', 'No', 'Other'])
                f['allow_custom'] = f['path'] != 'application_preferences.veteran'
            if f['type'] == 'list':
                f['items'] = copy.deepcopy(ITEMS[f['path']])
                f['help'] = ('Additional entries are stored for repeatable application sections. '
                             'The common-question resolver uses the primary education above.'
                             if f['path'] == 'education_history' else 'Stored for repeatable application sections; preview uses supported resolver questions.')
            if f['path'] == 'links.personal_website':
                f['help'] = 'Also used for Portfolio and Personal website questions.'
            if f['type'] == 'tags':
                f['placeholder'] = {'education.minors': 'Add a minor', 'skills': 'Add a skill',
                                    'application_preferences.technical_interests': 'Add an interest'}.get(f['path'], 'Add a tag')
            if f['path'].endswith('.gpa'):
                f['nullable_number'] = True
    return sections


def get_value(profile, f):
    if f['path'] in DEMOGRAPHIC_PATHS:
        return demographic_value(profile, f['path'])
    value = profile
    for part in f.get('parts', f['path'].split('.')):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def validate(value, f):
    kind = f['type']
    if f.get('nullable_number') or f['path'] == 'gpa':
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError('Enter a nonnegative numeric GPA, or leave blank.')
        return value
    if kind == 'bool':
        if value is None and f.get('nullable'):
            return None
        if type(value) is not bool:
            raise ValueError('Use a boolean (true or false).')
    elif kind == 'tags':
        if not isinstance(value, list) or len(value) > 300 or any(not isinstance(v, str) or not v.strip() or len(v) > 500 for v in value):
            raise ValueError('Use a list of nonempty text values.')
        if f['path'] == 'locations.preferred_states':
            from .dropdowns import us_state
            if len(value) > len(STATES) or any(us_state(v) is None for v in value):
                raise ValueError('Use at most 51 U.S. state names or abbreviations (including DC).')
            value = [us_state(v) for v in value]
        if f['path'] == 'locations.ordered_preferences':
            from .dropdowns import location_identity
            if len(value) > 100 or any(location_identity(v) is None for v in value):
                raise ValueError('Use at most 100 U.S. locations in City, State format.')
        value = list({v.strip().casefold(): v.strip() for v in reversed(value)}.values())[::-1]
    elif kind == 'list':
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError('Use a list of at most 100 entries.')
        schema = {i['path']: i for i in f['items']}
        result = []
        for index, item in enumerate(value):
            if not isinstance(item, dict) or set(item) != set(schema):
                raise ValueError(f'Entry {index + 1} must contain exactly: {", ".join(schema)}.')
            checked = {}
            for key, definition in schema.items():
                try:
                    checked[key] = validate(item[key], definition)
                except ValueError as exc:
                    raise ValueError(f'Entry {index + 1}, {key}: {exc}') from None
            result.append(checked)
        return result
    else:
        if not isinstance(value, str) or len(value) > 20000:
            raise ValueError('Use text of at most 20,000 characters.')
        if value:
            if kind == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
                raise ValueError('Enter a valid email address.')
            if kind == 'url':
                try:
                    parsed = urlsplit(value)
                    valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not re.search(r'\s', value)
                    parsed.port
                except ValueError:
                    valid = False
                if not valid:
                    raise ValueError('Enter an HTTP or HTTPS URL.')
            if kind in ('month', 'date'):
                pattern = r'\d{4}-\d{2}' if kind == 'month' else r'\d{4}-\d{2}-\d{2}'
                try:
                    if not re.fullmatch(pattern, value):
                        raise ValueError()
                    date.fromisoformat(value + '-01' if kind == 'month' else value)
                except ValueError:
                    raise ValueError('Use YYYY-MM.' if kind == 'month' else 'Use YYYY-MM-DD.') from None
            if kind == 'select' and not f.get('allow_custom') and value not in f['options']:
                raise ValueError('Choose a listed option.')
    return value


# Typical options matter: preview never guesses what an employer offers.
YN = ['Yes', 'No']
CATALOG = [(label, kind, options) for label, kind, options in [
    ('First name', 'text', []), ('Last name', 'text', []), ('Full name', 'text', []),
    ('Email', 'email', []), ('Phone', 'text', []), ('Street address', 'text', []),
    ('City', 'text', []), ('State', 'select', list(STATES.values())),
    ('Province/Region of residence', 'text', []),
    ('Postal code', 'text', []), ('Country', 'select', ['United States', 'Canada', 'Other']),
    ('School', 'text', []), ('Degree', 'text', []), ('Major', 'text', []),
    ('Expected graduation date', 'text', []), ('Graduation month', 'select', ['May', 'June', 'December']),
    ('Graduation year', 'text', []), ('GPA', 'number', []), ('Academic year', 'text', []),
    ('Are you currently enrolled?', 'select', YN), ('GitHub URL', 'text', []),
    ('LinkedIn URL', 'text', []), ('Website', 'text', []), ('Personal website', 'text', []), ('Portfolio URL', 'text', []),
    ('Are you authorized to work in the United States?', 'select', YN),
    ('Will you require sponsorship?', 'select', YN), ('Are you a US citizen?', 'select', YN),
    ('Are you willing to relocate?', 'select', YN), ('Summer 2027 start date', 'text', []),
    ('Veteran Status', 'select', ['I am not a protected veteran', 'I am a protected veteran', 'I decline to self-identify']),
    ('Gender', 'select', ['Male', 'Female', 'Non-binary', 'I decline to self-identify']),
    ('Race', 'select', ['Asian', 'White', 'Black or African American', 'I decline to self-identify']),
    ('Disability status', 'select', ['Yes', 'No', 'I decline to self-identify']),
    ('Are you Hispanic or Latino?', 'select', ['Yes', 'No', 'I decline to self-identify']),
    ('Salary expectations', 'text', []), ('How did you hear about us?', 'select', ['Job board', 'Other', 'University']),
    ('What are your career plans?', 'textarea', []), ('Have you previously worked for this employer?', 'select', YN),
    ('May we send recruiting SMS messages?', 'select', YN), ('Are you 18 years or older?', 'select', YN),
    ('Are you in good academic standing?', 'select', YN), ('Skills', 'text', []),
]]


class DashboardAnswers:
    def __init__(self, config, connection, mutex=None):
        self.config, self.connection = config, connection
        self.mutex = mutex or threading.RLock()

    def get(self):
        with self.mutex:
            profile = read_yaml(self.config.private / 'profile.yaml')
            sections = []
            sources = profile.get('fact_sources') or {}
            for key, label, fs in definitions(profile):
                for f in fs:
                    f['value'] = get_value(profile, f)
                    if f['path'] in DEMOGRAPHIC_PATHS:
                        values = demographic_values(profile, f['path'])
                        if len({str(equivalent_answer(v['value'])).strip().casefold() for v in values}) > 1:
                            f['conflicts'] = values
                            f['conflict_resolved'] = f['value'] is not None
                    source_path = f.get('source_path', f['path'])
                    source = sources.get(source_path, sources.get(source_path.split('.')[0], {}))
                    f['source'] = 'You' if source.get('source') == 'USER_PROVIDED' or not (source.get('file') or source.get('source') in ('RESUME', 'resume', 'RESUME_DERIVED')) else 'Resume'
                sections.append(dict(key=key, label=label, fields=fs))
            return dict(sections=sections, note='Changes apply to the next field resolved. Employer options and context may change an answer; missing facts need you.')

    def save(self, changes):
        if not isinstance(changes, dict):
            raise SettingsRejected('changes must be an object', errors={'changes': 'Use an object of field paths and values.'})
        with self.mutex:
            path = self.config.private / 'profile.yaml'
            profile = read_yaml(path)
            allowed = {f['path']: f for _, _, fs in definitions(profile) for f in fs}
            errors, checked = {}, {}
            for dotted, value in changes.items():
                try:
                    if dotted not in allowed:
                        raise ValueError('This field cannot be edited.')
                    checked[dotted] = validate(value, allowed[dotted])
                except ValueError as exc:
                    errors[dotted] = str(exc)
            if errors:
                raise SettingsRejected('Answer validation failed', errors=errors)
            supplied_at = datetime.now(timezone.utc).isoformat()
            for dotted, value in checked.items():
                target = profile
                parts = allowed[dotted].get('parts', dotted.split('.'))
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
                profile.setdefault('fact_sources', {})[dotted] = dict(source='USER_PROVIDED', basis='Dashboard edit', supplied_at=supplied_at)
                # Resume refresh replaces whole sections. Preserve a section when
                # its existing resume provenance would otherwise discard this edit.
                parent = parts[0]
                parent_source = profile['fact_sources'].get(parent, {})
                if parent_source.get('source') in ('RESUME', 'RESUME_DERIVED', 'resume') or parent_source.get('file'):
                    profile['fact_sources'][parent] = dict(source='USER_PROVIDED', basis='Dashboard edit', supplied_at=supplied_at)
            try:
                ProfileSnapshot.create(profile)
            except (ValueError, TypeError) as exc:
                raise SettingsRejected('Invalid profile', errors={'profile': str(exc)}) from None
            if checked:
                folder = path.parent / 'backups'
                folder.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    shutil.copy2(path, folder / ('profile-' + stamp() + '.yaml'))
                for old in sorted(folder.glob('profile-*.yaml'), reverse=True)[20:]:
                    old.unlink()
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='profile-', suffix='.tmp', delete=False) as stream:
                        temporary = stream.name
                        stream.write(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True))
                        stream.flush()
                        os.fsync(stream.fileno())
                    replace_file(temporary, path)
                finally:
                    if temporary and os.path.exists(temporary):
                        os.unlink(temporary)
            return dict(saved=checked, saved_at=supplied_at, **self.get())

    def saved(self):
        with self.connection() as db:
            answers = [dict(id=str(r['id']), question=r['normalized_question'], scope=r['scope'],
                            answer=json.loads(r['answer']), source=r['source'], last_used=r['last_used_at'],
                            usage=r['usage_count'], kind='known') for r in db.rows('SELECT * FROM known_answers ORDER BY id')]
            for r in db.rows("SELECT key,value FROM settings WHERE key LIKE 'verified_fact:%' ORDER BY key"):
                record = json.loads(r['value'])
                answers.append(dict(id=r['key'], question=r['key'].removeprefix('verified_fact:'), scope='global',
                                    answer=record.get('value'), source=record.get('source'), last_used=None, usage=0, kind='fact'))
            kinds = {}
            for r in db.rows('SELECT normalized_question,scope,field_type,options FROM questions'):
                options = json.loads(r['options'])
                kind = ('boolean' if set(options) == {'Yes', 'No'} else 'select'
                        if r['field_type'] in {'select', 'radio', 'combobox', 'multiselect'} else 'text')
                kinds.setdefault((r['normalized_question'], r['scope']), set()).add(kind)
            settings = {r['key']: json.loads(r['value']) for r in db.rows("SELECT key,value FROM settings WHERE key LIKE 'question_category:%'")}
            labels = {f['path']: f['label'] for _, _, fs in SECTIONS for f in fs}
            for item in answers:
                observed = kinds.get((item['question'], item['scope']), set())
                if len(observed) == 1:
                    item['answer_kind'] = next(iter(observed))
                elif len(observed) > 1:
                    item['answer_kind'] = 'mixed:' + item['id']
                item['normalized'] = normalize(item['question'])
                item['category'], item['confidence'] = categorize(item['question'], settings)
                item['suggested_category'] = categorize(item['question'])[0]
                item['category_overridden'] = 'question_category:' + item['normalized'] in settings
                item['display_question'] = labels.get(item['question'], item['question'])
                if item['question'] == 'contact.address':
                    item['display_question'] = 'Street address'
                question_words = re.sub(r'[^a-z0-9]+', ' ', item['question'].lower()).strip()
                answer_words = re.sub(r'[^a-z0-9]+', ' ', str(item['answer']).lower()).strip()
                item['check_answer'] = bool(question_words and re.search(r'\b' + re.escape(question_words) + r'\b', answer_words))
            groups = []
            for variants in group(answers):
                representative = variants[0]
                spec = REGISTRY.get(canonical_key(representative['question']) or representative['question'])
                groups.append(dict(representative, variants=variants,
                    conflict=len({json.dumps(equivalent_answer(v['answer']), sort_keys=True) for v in variants}) > 1,
                    check_answer=any(v['check_answer'] for v in variants),
                    profile_path=spec.profile_path if spec and spec.factual_fallback else None))
            return dict(answers=answers, groups=groups, categories=CATEGORIES)

    def categories(self, changes):
        if not isinstance(changes, dict) or len(changes) > 5000:
            raise SettingsRejected('Use question/category pairs')
        if any(not isinstance(q, str) or len(q) > 20000 or c not in CATEGORIES for q, c in changes.items()):
            raise SettingsRejected('Choose an existing answer category')
        with self.connection(writable=True) as db:
            with db.conn:
                for question, category in changes.items():
                    db.set_setting('question_category:' + normalize(question), category)
        return dict(saved=len(changes))

    def link_profile(self, identifier):
        item = next((a for a in self.saved()['answers'] if a['id'] == identifier), None)
        if not item:
            raise SettingsRejected('Saved answer not found', status=404)
        spec = REGISTRY.get(canonical_key(item['question']) or item['question'])
        if not spec or not spec.profile_path or not spec.factual_fallback:
            raise SettingsRejected('This question has no editable profile field')
        value = item['answer']
        if spec.answer_type == 'boolean' and type(value) is not bool:
            value = {'yes': True, 'no': False}.get(str(value).strip().lower())
            if value is None:
                raise SettingsRejected('Use Yes or No for this profile field')
        if spec.profile_path == 'skills' and isinstance(value, str):
            value = [v.strip() for v in value.split(',') if v.strip()]
        result = self.save({spec.profile_path: value})
        # An existing verified override must agree with this explicit choice.
        override = 'verified_fact:' + spec.profile_path
        if any(a['id'] == override for a in self.saved()['answers']):
            self.edit_saved(override, item['answer'])
        return result

    def edit_saved(self, identifier, answer=None, *, delete=False):
        if not delete and not (isinstance(answer, str) or isinstance(answer, list) and all(isinstance(v, str) for v in answer)
                               or identifier.startswith('verified_fact:') and type(answer) is bool):
            raise SettingsRejected('Use text or a list of text answers', errors={'answer': 'Use a string or a list of strings.'})
        if not delete:
            try:
                encoded = json.dumps(answer, allow_nan=False)
                if len(encoded) > 100000:
                    raise ValueError()
            except (ValueError, TypeError):
                raise SettingsRejected('Answer is too large or is not JSON serializable') from None
        with self.connection(writable=True) as db:
            # Raw SQLite transaction avoids any history export or event creation.
            db.conn.execute('BEGIN IMMEDIATE')
            try:
                is_fact = identifier.startswith('verified_fact:')
                if is_fact:
                    row = db.one('SELECT value FROM settings WHERE key=?', (identifier,))
                    record = json.loads(row['value']) if row else None
                    previous = record.get('value') if record else None
                else:
                    if not re.fullmatch(r'[1-9]\d*', identifier) or int(identifier) > 2**63 - 1:
                        raise SettingsRejected('Invalid answer id', status=404)
                    row = db.one('SELECT * FROM known_answers WHERE id=?', (int(identifier),))
                    previous = json.loads(row['answer']) if row else None
                if not row:
                    raise SettingsRejected('Saved answer not found', status=404)
                if is_fact and not delete:
                    from .concepts import REGISTRY
                    spec = next((s for s in REGISTRY.values() if s.profile_path == identifier.removeprefix('verified_fact:')), None)
                    if spec and spec.answer_type == 'boolean' and type(answer) is not bool and (not isinstance(answer, str) or answer.strip().lower() not in ('yes', 'no')):
                        raise SettingsRejected('Use Yes or No for this verified fact', errors={'answer': 'Use Yes or No.'})
                if delete:
                    db.conn.execute('DELETE FROM settings WHERE key=?' if is_fact else 'DELETE FROM known_answers WHERE id=?', (identifier,))
                elif is_fact:
                    record.update(value=answer, source='USER_PROVIDED')
                    db.set_setting(identifier, record)
                else:
                    db.conn.execute("UPDATE known_answers SET answer=?,source='USER_PROVIDED',verified=1 WHERE id=?", (encoded, identifier))
                log = db.setting('answer_edit_log', [])
                log.append(dict(id=identifier, action='delete' if delete else 'edit', before=previous,
                                after=None if delete else answer, supplied_at=datetime.now(timezone.utc).isoformat()))
                db.set_setting('answer_edit_log', log[-200:])
                db.conn.execute('COMMIT')
            except BaseException:
                db.conn.execute('ROLLBACK')
                raise
        return dict(id=identifier, deleted=delete, answer=answer)

    def preview(self):
        app = dict(id=0, company='Example employer', title='Software engineering intern',
                   description='', location='United States', ats='preview', canonical_url='')
        with self.connection() as db:
            db.conn.execute('BEGIN')
            try:
                resolver = AnswerResolver(self.config, db)
                rows = []
                for index, (label, kind, options) in enumerate(CATALOG):
                    result = resolver.resolve_result(Question(f'preview-{index}', label, kind, True, options), app)
                    answer = result.answer
                    raw = answer.source if answer else ''
                    source = ('Needs you' if not answer else 'Saved' if raw in ('verified_memory', 'USER_PROVIDED') or 'memory' in raw
                              else 'Rule' if 'rule' in raw or raw == 'USER_PROVIDED_STANDING_ELIGIBILITY_ASSERTIONS' or raw.startswith(('derived:', 'affirmative')) else 'Profile')
                    rows.append(dict(question=label, answer=answer.value if answer else None, source=source, resolver_source=raw))
            finally:
                db.conn.execute('ROLLBACK')
        return dict(questions=rows, note='Live saved values; typical options and an example employer. Preview does not create questions or usage receipts.')
