"""Answers checks use only synthetic profiles, resumes and SQLite fixtures."""

import hashlib
import ast
import http.client
import io
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote

import pytest
import yaml

from autoapply.config import read_yaml
from autoapply.dashboard import Dashboard, DashboardHandler, DashboardServer
from autoapply.dashboard_answers import ITEMS, field_label
from autoapply.dashboard_settings import SettingsRejected
from autoapply.dashboard_ui import PAGE
from autoapply.profile_snapshot import ProfileSnapshot


@pytest.fixture
def dashboard(config, db):
    profile = {
        'identity': {'first_name': 'Test', 'last_name': 'Student', 'full_name': 'Test Student'},
        'contact': {'email': 'test@example.test', 'phone': '2025550100', 'address': '1 Example St',
                    'city': 'Example', 'state': 'NY', 'postal_code': '10001', 'country': 'United States'},
        'education': {'school': 'Example University', 'degree': "Bachelor's", 'major': 'Computer Science',
                      'school_year': 'Junior', 'graduation_date': '2028-05', 'gpa': 3.5,
                      'currently_enrolled': True, 'minors': [], 'good_academic_standing': True},
        'employment': [], 'projects': [], 'skills': ['Python'],
        'links': {'personal_website': 'https://example.test', 'github': 'https://github.com/example',
                  'linkedin': 'https://linkedin.com/in/example'},
        'citizenship': {'us_citizen': True, 'citizen_since_birth': True},
        'work_authorization': {'us_authorized': True, 'sponsorship_now': False, 'sponsorship_future': False},
        'availability': {'summer_2027': {'start_date': '2027-05-20', 'end_date': '2027-08-20', 'source': 'USER_PROVIDED'}},
        'locations': {'willing_to_relocate': True},
        'application_preferences': {'race': 'decline', 'gender': 'decline', 'disability': 'decline',
            'veteran': 'not a protected veteran', 'compensation_text': 'Market rate', 'office_five_days': True,
            'discovery_source': 'Job board', 'application_source_answer': 'Other',
            'career_goal': 'Build reliable software', 'willing_to_work_any_location': True,
            'previous_employer_work_default': False, 'academic_honor': False, 'recruiting_sms_whatsapp': False,
            'ordinary_policy_acknowledgement': True, 'technical_interests': ['Software engineering']},
        'demographics': {'gender': 'decline', 'race': 'decline', 'veteran_status': 'not a protected veteran'},
        'qualifications': {'age_18_or_older': True, 'programming_experience': True},
        'eligibility_assertions': [], 'standing_disclosures': {},
        'common_answers': {'Custom question?': {'answer': 'Example', 'verified': True, 'scope': 'global',
                                               'source': 'USER_PROVIDED', 'context': 'User supplied'}},
        'fact_sources': {'education.school': {'file': 'resumes/resume.pdf', 'source': 'RESUME_DERIVED'}},
        'verified_facts': {},
        'resume_revision': {'file': 'resumes/resume.pdf', 'sha256': hashlib.sha256(config.resume.read_bytes()).hexdigest()},
    }
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    value = Dashboard(config, db)
    yield value
    value.close()


class Socket:
    def __init__(self, data):
        self.input, self.output = io.BytesIO(data), io.BytesIO()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.write(data)


def request(dashboard, method, path, body=None, headers=None):
    server = DashboardServer.__new__(DashboardServer)
    server.dashboard, server.server_port = dashboard, 8765
    payload = json.dumps(body).encode() if body is not None else b''
    values = {'Host': '127.0.0.1:8765', 'Content-Length': str(len(payload)),
              'Origin': 'http://127.0.0.1:8765', 'X-Dashboard-Token': dashboard.token}
    values.update(headers or {})
    raw = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in values.items()) + '\r\n'
    socket = Socket(raw.encode() + payload)
    DashboardHandler(socket, ('127.0.0.1', 1), server)
    response = http.client.HTTPResponse(Socket(socket.output.getvalue()))
    response.begin()
    return response.status, json.loads(response.read())


def test_grouped_model(dashboard):
    status, model = request(dashboard, 'GET', '/api/answers')
    assert status == 200
    fs = {f['path']: f for section in model['sections'] for f in section['fields']}
    assert fs['education.school']['source'] == 'Resume'
    assert fs['contact.email']['source'] == 'You'
    assert fs['demographics.veteran_status']['options'] == ['not a protected veteran', 'protected veteran', 'decline']
    assert 'application_preferences.veteran' not in fs
    assert fs['links.personal_website']['help'] == 'Also used for Portfolio and Personal website questions.'
    assert fs['education.gpa']['label'] == 'GPA'
    assert fs['locations.willing_to_relocate']['nullable'] is True
    assert ITEMS['employment'][5]['nullable'] is False
    assert [fs[path]['placeholder'] for path in ('education.minors', 'skills', 'application_preferences.technical_interests')] == ['Add a minor', 'Add a skill', 'Add an interest']
    assert all({'path', 'label', 'type', 'value', 'source', 'help'} <= set(f) for f in fs.values())
    assert all(path in fs for path in ITEMS)
    assert 'resume_revision' not in fs
    assert 'common_answers.Custom question?.answer' in fs


def test_acronym_labels():
    assert field_label('gpa_url_zip_sms_ats_linkedin_github') == 'GPA URL ZIP SMS ATS LinkedIn GitHub'


def test_nullable_boolean_round_trip(dashboard, config):
    for value in (None, False, True, None):
        data = dashboard.answers.save({'locations.willing_to_relocate': value})
        assert config.profile['locations']['willing_to_relocate'] is value
        f = next(f for s in data['sections'] for f in s['fields'] if f['path'] == 'locations.willing_to_relocate')
        assert f['type'] == 'bool' and f['nullable'] and f['value'] is value
    with pytest.raises(SettingsRejected):
        dashboard.answers.save({'employment': [item('employment', current=None)]})


@pytest.mark.parametrize('path,value', [
    ('contact.email', 'bad'), ('links.personal_website', 'javascript:alert(1)'),
    ('links.github', 'https://example.test:bad'), ('education.graduation_date', '2027-13'),
    ('education.graduation_date', '2027-05-01'), ('work_authorization.us_authorized', 'yes'),
    ('availability.summer_2027.start_date', '2027-02-30'), ('skills', ['Python', 1]),
    ('education.gpa', True), ('education.gpa', float('nan')), ('education_history', [{'school': 'Bad'}]),
    ('employment', ['bad']), ('projects', {}), ('links.other', [{'label': 'Site', 'url': 'file:///bad'}]),
    ('resume_revision.sha256', 'changed'), ('application.auto_submit', True),
])
def test_validation_is_atomic(dashboard, config, path, value):
    original = (config.private / 'profile.yaml').read_bytes()
    status, data = request(dashboard, 'PUT', '/api/answers', {'changes': {path: value, 'identity.first_name': 'Changed'}})
    assert status == 400 and path in data['errors']
    assert (config.private / 'profile.yaml').read_bytes() == original
    assert not (config.private / 'backups').exists()


def test_write_backup_provenance_and_revision(dashboard, config, db, monkeypatch):
    path = config.private / 'profile.yaml'
    original = path.read_bytes()
    revision = config.profile['resume_revision']
    monkeypatch.setattr(dashboard, '_child_alive', lambda: True)
    before = db.rows('SELECT * FROM applications')
    status, data = request(dashboard, 'PUT', '/api/answers', {'changes': {'education.school': 'New school', 'skills': ['Python', 'python', 'Rust']}})
    assert status == 200
    profile = read_yaml(path)
    assert profile['resume_revision'] == revision
    assert profile['skills'] == ['Python', 'Rust']
    assert profile['fact_sources']['education.school']['source'] == 'USER_PROVIDED'
    assert profile['fact_sources']['education.school']['basis'] == 'Dashboard edit'
    assert profile['fact_sources']['education.school']['supplied_at']
    assert next((config.private / 'backups').glob('profile-*.yaml')).read_bytes() == original
    ProfileSnapshot.create(profile)
    assert config.profile_snapshot().facts['education']['school'] == 'New school'
    assert db.rows('SELECT * FROM applications') == before
    fields = {f['path']: f for s in data['sections'] for f in s['fields']}
    assert fields['education.school']['source'] == 'You'
    assert not list(config.private.glob('*.tmp'))


def item(path, **extra):
    result = {f['path']: False if f['type'] == 'bool' else [] if f['type'] == 'tags' else None if f['path'] == 'gpa' else '' for f in ITEMS[path]}
    return dict(result, **extra)


def test_lists_add_reorder_remove(dashboard, config):
    changes = {
        'education_history': [item('education_history', school='A'), item('education_history', school='B')],
        'employment': [item('employment', company='Example', current=True)],
        'projects': [item('projects', name='Project', technologies=['Python'])],
        'links.other': [item('links.other', label='Portfolio', url='https://example.test/project')],
        'eligibility_assertions': [item('eligibility_assertions', id='age', meaning='Already 18', answer=True, scope='global')],
    }
    dashboard.answers.save(changes)
    assert isinstance(config.profile['education'], dict)
    assert config.profile['education_history'][0]['school'] == 'A'
    changes['education_history'].reverse()
    dashboard.answers.save(changes)
    assert config.profile['education_history'][0]['school'] == 'B'
    dashboard.answers.save({key: [] for key in changes})
    assert all(not config.profile.get(key) for key in ('education_history', 'employment', 'projects', 'eligibility_assertions'))
    assert config.profile['links']['other'] == []


def test_backup_retention_and_replace_failure(dashboard, config, monkeypatch):
    for index in range(23):
        dashboard.answers.save({'identity.first_name': str(index)})
    assert len(list((config.private / 'backups').glob('profile-*.yaml'))) == 20
    before = (config.private / 'profile.yaml').read_bytes()
    def fail(*args):
        raise PermissionError('fixture failure')
    monkeypatch.setattr('autoapply.dashboard_answers.replace_file', fail)
    with pytest.raises(PermissionError):
        dashboard.answers.save({'identity.first_name': 'Failed'})
    assert (config.private / 'profile.yaml').read_bytes() == before
    assert not list(config.private.glob('*.tmp'))


def test_resume_section_provenance_preserves_edits(dashboard, config):
    from autoapply.resume_migration import refresh_profile
    profile = config.profile
    profile['fact_sources']['education'] = dict(source='RESUME_DERIVED', file='resumes/resume.pdf')
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    dashboard.answers.save({'education.major': 'Mathematics'})
    updated, report = refresh_profile(config.profile, {}, {'education': {'major': 'Old major'}}, 'fixture-hash')
    assert updated['education']['major'] == 'Mathematics'
    assert 'education' in report['conflicts']


def test_verified_fact_boolean_and_audit_retention(dashboard, db):
    key = 'verified_fact:work_authorization.sponsorship_now'
    db.set_setting(key, dict(value=False, source='USER_PROVIDED', question_id=7))
    db.set_setting('answer_edit_log', [{'id': str(i)} for i in range(200)])
    assert request(dashboard, 'PUT', '/api/answers/saved/'+quote(key), {'answer': ['Yes']})[0] == 400
    assert request(dashboard, 'PUT', '/api/answers/saved/'+quote(key), {'answer': 'No'})[0] == 200
    assert len(db.setting('answer_edit_log')) == 200
    assert db.setting('answer_edit_log')[0]['id'] == '1'


def save_answer(db):
    return db.execute('''INSERT INTO known_answers(normalized_question,scope,answer,verified,source,created_at)
        VALUES ('favorite language','global',?,1,'USER_PROVIDED','2026-01-01')''', (json.dumps('Python'),)).lastrowid


def test_saved_edit_delete_and_audit(dashboard, db):
    identifier = str(save_answer(db))
    db.set_setting('verified_fact:contact.city', dict(value='Old', source='USER_PROVIDED', question_id=123))
    before_events = db.rows('SELECT * FROM events')
    status, data = request(dashboard, 'GET', '/api/answers/saved')
    assert status == 200 and len(data['answers']) == 2
    assert request(dashboard, 'PUT', '/api/answers/saved/'+identifier, {'answer': {'bad': 1}})[0] == 400
    assert request(dashboard, 'PUT', '/api/answers/saved/'+identifier, {'answer': ['Python', 'Rust']})[0] == 200
    assert json.loads(db.one('SELECT answer FROM known_answers WHERE id=?', (identifier,))['answer']) == ['Python', 'Rust']
    assert request(dashboard, 'PUT', '/api/answers/saved/'+quote('verified_fact:contact.city'), {'answer': 'New'})[0] == 200
    assert db.setting('verified_fact:contact.city')['value'] == 'New'
    assert request(dashboard, 'DELETE', '/api/answers/saved/'+identifier)[0] == 200
    assert not db.one('SELECT id FROM known_answers WHERE id=?', (identifier,))
    log = db.setting('answer_edit_log')
    assert [entry['action'] for entry in log] == ['edit', 'edit', 'delete']
    assert log[-1]['before'] == ['Python', 'Rust']
    assert db.rows('SELECT * FROM events') == before_events
    assert request(dashboard, 'DELETE', '/api/answers/saved/'+identifier)[0] == 404


@pytest.mark.parametrize('method,path,body', [('PUT', '/api/answers', {'changes': {'identity.first_name': 'No'}}),
    ('PUT', '/api/answers/saved/1', {'answer': 'No'}), ('DELETE', '/api/answers/saved/1', None)])
@pytest.mark.parametrize('headers', [{'Origin': 'http://evil.test'}, {'X-Dashboard-Token': ''}, {'Sec-Fetch-Site': 'cross-site'}])
def test_all_writes_require_origin_and_token(dashboard, method, path, body, headers):
    assert request(dashboard, method, path, body, headers)[0] == 403


@pytest.mark.affirmative
def test_preview_real_answers_no_writes(dashboard, db, config):
    before = {table: db.rows('SELECT * FROM '+table) for table in ('questions', 'known_answers', 'events', 'settings')}
    original = (config.private / 'profile.yaml').read_bytes()
    with dashboard.connection() as connection:
        assert connection.conn.execute('PRAGMA query_only').fetchone()[0] == 1
    status, data = request(dashboard, 'GET', '/api/answers/preview')
    assert status == 200
    rows = {r['question']: r for r in data['questions']}
    assert 38 <= len(rows) <= 45
    assert rows['Veteran Status']['answer'] == 'I am not a protected veteran'
    assert rows['Will you require sponsorship?']['answer'] == 'No'
    assert rows['Portfolio URL']['answer'] == rows['Website']['answer'] == 'https://example.test'
    assert rows['Country']['answer'] == 'United States'
    assert 'software' in rows['What are your career plans?']['answer']
    assert rows['Skills']['answer'] == 'Python'
    assert {table: db.rows('SELECT * FROM '+table) for table in before} == before
    assert (config.private / 'profile.yaml').read_bytes() == original


@pytest.mark.affirmative
@pytest.mark.parametrize('state,expected', [('NY', 'New York'), ('MD', 'Maryland'), ('Virginia', 'Virginia')])
def test_preview_residence_uses_worker_resolver(dashboard, db, state, expected):
    from autoapply.answers import AnswerResolver
    from autoapply.dashboard_answers import CATALOG
    from autoapply.models import Question
    dashboard.answers.save({'contact.state': state})
    preview = {row['question']: row for row in dashboard.answers.preview()['questions']}
    resolver = AnswerResolver(dashboard.config, db)
    app = dict(id=0, company='Example employer', title='Software engineering intern',
               description='', location='United States', ats='preview', canonical_url='')
    for label in ('State', 'Province/Region of residence'):
        _, kind, options = next(row for row in CATALOG if row[0] == label)
        result = resolver.resolve_result(Question('test', label, kind, True, options), app)
        assert preview[label]['answer'] == result.answer.value == (expected if options else state)
        assert preview[label]['resolver_source'] == result.answer.source == 'profile_rule'


@pytest.mark.affirmative
def test_preview_missing_or_unmatched_state_needs_user(dashboard):
    for state in ('', 'Ontario'):
        dashboard.answers.save({'contact.state': state})
        preview = {row['question']: row for row in dashboard.answers.preview()['questions']}
        assert preview['State']['answer'] is None
        assert preview['Province/Region of residence']['answer'] == (state or None)


@pytest.mark.affirmative
def test_preview_state_preserves_worker_overrides_and_policy(dashboard, db, config):
    db.set_setting('verified_fact:contact.state', dict(value='MD', source='USER_PROVIDED'))
    preview = {row['question']: row for row in dashboard.answers.preview()['questions']}
    assert preview['State']['answer'] == 'Maryland'
    assert preview['Province/Region of residence']['answer'] == 'MD'
    profile = config.profile
    profile['autofill_policies'] = {'contact.state': 'DO_NOT_ANSWER'}
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    preview = {row['question']: row for row in dashboard.answers.preview()['questions']}
    assert preview['State']['answer'] is None


def test_answers_ui_markup_and_script(tmp_path):
    assert PAGE.index('id="tab-applications"') < PAGE.index('id="tab-answers"') < PAGE.index('id="tab-settings"')
    assert 'id="answers-nav"' in PAGE and 'id="answers-footer"' in PAGE
    assert 'What we will answer' in PAGE and 'Move entry ' in PAGE
    assert 'innerHTML' not in PAGE and 'insertAdjacentHTML' not in PAGE
    executable = shutil.which('node')
    if executable:
        script = PAGE.split('<script nonce="__TOKEN__">')[1].split('</script>')[0]
        path = tmp_path / 'dashboard.js'
        path.write_text(script, encoding='utf-8')
        result = subprocess.run([executable, '--check', str(path)], text=True, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr


def test_answers_ui_tags_lists_preview_and_discard(dashboard, tmp_path):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is unavailable')
    # Reuse the existing offline DOM model, without editing its out-of-scope test.
    tree = ast.parse(Path('tests/test_dashboard.py').read_text(encoding='utf-8'))
    fn = next(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == 'test_javascript_display_formatting_and_renderers')
    assignment = next(n for n in fn.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'harness' for t in n.targets))
    harness = ast.literal_eval(assignment.value)
    additions = r'''
Node.prototype.matches=function(selector){return selector.split(',').includes(this.tag);};
Node.prototype.querySelector=function(selector){for(const child of this.children){if(child.matches(selector))return child;const nested=child.querySelector(selector);if(nested)return nested;}return null;};
document.body=new Node('body');
'''
    script = PAGE.split('<script nonce="__TOKEN__">')[1].split('</script>')[0]
    script = script[:script.index('loadAnswers();setInterval')]
    checks = r'''
function descendants(el){return [el,...el.children.flatMap(descendants)];}
answerSection='skills';drawAnswers();
let content=document.getElementById('answers-content'),input=content.querySelector('input');
assert.equal(input.placeholder,'Add a skill');
input.value='Rust,python';input.listeners.keydown({key:'Enter',preventDefault(){}});
assert.deepEqual(answerChanges.skills,['Python','Rust']);assert.equal(answerDirty(),true);
let remove=descendants(content).find(el=>el.attributes['aria-label']==='Remove Rust');remove.click();
assert.equal(answerDirty(),false);
answerSection='education';drawAnswers();content=document.getElementById('answers-content');
descendants(content).find(el=>el.tag==='button'&&el.textContent==='Add education').click();
let school=descendants(content).find(el=>el.attributes['aria-label']==='School'&&el.id==='answer-school');
school.value='Another university';school.listeners.input();
descendants(content).find(el=>el.tag==='button'&&el.textContent==='Done').click();
assert.equal(answerChanges.education_history[0].school,'Another university');
assert.equal(typeof answersData.sections.find(s=>s.key==='education').fields.find(f=>f.path==='education.school').value,'string');
document.getElementById('answers-discard').click();assert.equal(answerDirty(),false);
answerSection='overview';answerPreview=[{question:'Veteran Status',answer:'I am not a protected veteran',source:'Rule'},{question:'Unsupported',answer:null,source:'Needs you'}];drawAnswers();
assert.match(document.getElementById('answers-content').textContent,/I am not a protected veteran/);
input=document.getElementById('answers-content').querySelector('input');input.value='unsupported';input.listeners.input();
assert.doesNotMatch(document.getElementById('answers-content').textContent,/Veteran Status/);
assert.match(document.getElementById('answers-content').textContent,/Needs you/);
assert.equal(document.getElementById('answers-nav').children[0].textContent,'Autofill preview');
let selected;
const nullable=answerControl({path:'relocate',label:'Relocate',type:'bool',nullable:true},null,v=>selected=v);
assert.equal(nullable.attributes.role,'group');assert.deepEqual(nullable.children.map(b=>b.textContent),['Yes','No','Not set']);
nullable.children[0].click();assert.equal(selected,true);assert.equal(nullable.children[0].attributes['aria-pressed'],'true');
nullable.children[1].click();assert.equal(selected,false);nullable.children[2].click();assert.equal(selected,null);
const required=answerControl({path:'current',label:'Current',type:'bool',nullable:false},false,v=>selected=v);
assert.equal(required.children[0].attributes.role,'switch');assert.equal(required.textContent,'No');
required.children[0].checked=true;required.children[0].listeners.change();assert.equal(required.textContent,'Yes');assert.equal(selected,true);
const veteran=answerControl({path:'veteran',label:'Veteran',type:'select',options:['decline','not a protected veteran','protected veteran']},'decline',v=>selected=v);
assert.deepEqual(veteran.children.map(o=>o.textContent),['Decline to answer','I am not a protected veteran','I am a protected veteran']);
veteran.value='protected veteran';veteran.listeners.change();assert.equal(selected,'protected veteran');
answerSection='saved';answerSavedView='list';answerSavedRows=[{id:'1',question:'Favorite language',answer:'Python',scope:'global',source:'USER_PROVIDED',last_used:null},{id:'2',question:'Role preference',answer:'Engineering',scope:'application:6415',source:'profile_rule',last_used:null},{id:'3',question:'Qualification',answer:'Yes',scope:'global',source:'USER_PROVIDED_STANDING_ELIGIBILITY_ASSERTIONS',last_used:null}];drawAnswers();
content=document.getElementById('answers-content');
assert.deepEqual(descendants(content).filter(el=>el.tag==='th').map(el=>el.textContent),['Question','Answer','Applies to','Last used','Actions']);
assert.match(content.textContent,/All applications/);assert.match(content.textContent,/Application #6415/);
assert.deepEqual(descendants(content).filter(el=>el.className==='answer-badge').map(el=>el.textContent),['You','Rule','Eligibility']);
let edit=descendants(content).find(el=>el.attributes['aria-label']==='Edit answer for Favorite language');assert.equal(edit.title,'Edit answer for Favorite language');edit.click();
assert.equal(content.querySelector('textarea').value,'Python');
descendants(content).find(el=>el.tag==='button'&&el.textContent==='Cancel').click();assert.equal(content.querySelector('textarea'),null);
input=content.querySelector('input');input.value='Application #6415';input.listeners.input();assert.match(content.textContent,/Engineering/);assert.doesNotMatch(content.textContent,/Python/);
input.value='';input.listeners.input();descendants(content).find(el=>el.attributes['aria-label']==='Delete answer for Favorite language').click();
assert.doesNotMatch(document.getElementById('answers-content').textContent,/Python/);assert.equal(answerPendingDeletes.has('1'),true);
descendants(document.body).find(el=>el.tag==='button'&&el.textContent==='Undo').click();assert.equal(answerPendingDeletes.has('1'),false);assert.match(document.getElementById('answers-content').textContent,/Python/);
(async()=>{
 let writes=[];answerWrite=async(path,body)=>{writes.push({path,body});answerSavedRows[0].answer=body.answer;};
 loadSavedAnswers=async()=>drawSavedAnswers();loadAnswerPreview=async()=>{};
 content=document.getElementById('answers-content');descendants(content).find(el=>el.attributes['aria-label']==='Edit answer for Favorite language').click();
 content.querySelector('textarea').value='Rust';let save=descendants(content).find(el=>el.tag==='button'&&el.textContent==='Save');await save.listeners.click();
 assert.deepEqual(writes,[{path:'/api/answers/saved/1',body:{answer:'Rust'}}]);
 content=document.getElementById('answers-content');assert.match(content.textContent,/Rust/);assert.equal(content.querySelector('textarea'),null);
 answerSavedRows[0].answer=['Rust'];drawSavedAnswers();content=document.getElementById('answers-content');descendants(content).find(el=>el.attributes['aria-label']==='Edit answer for Favorite language').click();
 content.querySelector('textarea').value='invalid JSON';save=descendants(content).find(el=>el.tag==='button'&&el.textContent==='Save');await save.listeners.click();
 assert.equal(writes.length,1);assert.deepEqual(answerSavedRows[0].answer,['Rust']);assert.equal(save.disabled,false);
 assert.ok(descendants(content).find(el=>el.attributes.role==='alert').textContent);assert.ok(content.querySelector('textarea'));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    path = tmp_path / 'answers-ui-test.js'
    path.write_text(harness + additions + script + '\nanswersData=' + json.dumps(dashboard.answers.get()) + ';\n' + checks, encoding='utf-8')
    result = subprocess.run([executable, str(path)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_existing_dashboard_renderers_from_file(tmp_path):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is unavailable')
    tree = ast.parse(Path('tests/test_dashboard.py').read_text(encoding='utf-8'))
    fn = next(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == 'test_javascript_display_formatting_and_renderers')
    strings = {}
    for statement in fn.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in ('harness', 'assertions'):
                    strings[target.id] = ast.literal_eval(statement.value)
    script = PAGE.split('<script nonce="__TOKEN__">')[1].split('</script>')[0]
    path = tmp_path / 'existing-renderers.js'
    path.write_text(strings['harness'] + script[:script.index('restoreSearch();loadSettings')] + strings['assertions'], encoding='utf-8')
    result = subprocess.run([executable, str(path)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('label,path,value,options,expected', [
    ('Are you currently enrolled?', 'education.currently_enrolled', True, ['Yes', 'No'], 'Yes'),
    ('Are you currently a student?', 'education.currently_enrolled', False, ['Yes', 'No'], 'No'),
    ('Are you enrolled in a degree program?', 'education.currently_enrolled', True, ['Yes', 'No'], 'Yes'),
    ('Are you 18 years or older?', 'qualifications.age_18_or_older', True, ['Yes', 'No'], 'Yes'),
    ('Are you at least 18?', 'qualifications.age_18_or_older', False, ['Yes', 'No'], 'No'),
    ('Are you 18 years of age or older?', 'qualifications.age_18_or_older', True, ['Yes', 'No'], 'Yes'),
    ('Are you in good academic standing?', 'education.good_academic_standing', True, ['Yes', 'No'], 'Yes'),
    ('Gender', 'demographics.gender', 'Female', ['Male', 'Female'], 'Female'),
    ('What is your gender identity?', 'demographics.gender', 'Female', ['Male', 'Female'], 'Female'),
    ('Race', 'demographics.race', 'Asian', ['Asian', 'White'], 'Asian'),
    ('Skills', 'skills', ['Python', 'SQL'], [], 'Python, SQL'),
    ('Veteran Status', 'demographics.veteran_status', 'protected veteran', ['I am a protected veteran', 'I am not a protected veteran'], 'I am a protected veteran'),
])
def test_explicit_facts_worker_and_preview(dashboard, db, label, path, value, options, expected):
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    dashboard.answers.save({path: value})
    app = dict(id=0, company='Example employer', title='Intern', description='', location='United States', ats='preview', canonical_url='')
    answer = AnswerResolver(dashboard.config, db).resolve(Question('fact', label, 'select' if options else 'text', True, options), app)
    assert answer and answer.value == expected
    rows = {r['question']: r for r in dashboard.answers.preview()['questions']}
    if label in rows:
        assert rows[label]['answer'] == expected


@pytest.mark.affirmative
@pytest.mark.parametrize('label,path', [
    ('Can you confirm you are currently enrolled?', 'education.currently_enrolled'),
    ('Are you currently a student?', 'education.currently_enrolled'),
    ('Are you 18 years or older?', 'qualifications.age_18_or_older'),
    ('Are you in good academic standing?', 'education.good_academic_standing'),
])
def test_missing_sensitive_fact_never_affirmative(dashboard, db, label, path):
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    dashboard.answers.save({path: None})
    assert AnswerResolver(dashboard.config, db).resolve(Question('missing', label, 'select', True, ['Yes','No']), dict(id=0, company='Fixture')) is None


def test_demographic_fallback_conflict_and_explicit_choice(dashboard, config, db):
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    profile = config.profile
    profile['demographics'] = {'gender_identity': 'Female', 'racial_ethnic_background': 'Asian'}
    profile['application_preferences']['gender'] = 'Male'
    profile['application_preferences']['race'] = 'Asian'
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    fields = {f['path']: f for s in dashboard.answers.get()['sections'] for f in s['fields']}
    assert fields['demographics.race']['value'] == 'Asian'
    assert fields['demographics.gender']['value'] is None
    assert {v['value'] for v in fields['demographics.gender']['conflicts']} == {'Male','Female'}
    assert not any(p in fields for p in ('demographics.gender_identity','demographics.racial_ethnic_background','application_preferences.gender','application_preferences.race','application_preferences.disability','application_preferences.veteran'))
    q = Question('gender','Gender','select',True,['Female','Male'])
    app = dict(id=0,company='Fixture')
    assert AnswerResolver(config, db).resolve(q, app) is None
    before = config.profile
    dashboard.answers.save({'demographics.gender':'Female'})
    assert config.profile['application_preferences']['gender'] == before['application_preferences']['gender']
    assert config.profile['demographics']['gender_identity'] == 'Female'
    assert AnswerResolver(config, db).resolve(q, app).value == 'Female'


def test_saved_groups_categories_and_link(dashboard, db, config):
    for label,scope,answer in [('are you currently enrolled','global','Yes'),('are you currently a student','application:2','No')]:
        db.execute("INSERT INTO known_answers(normalized_question,scope,answer,verified,source,created_at) VALUES (?,?,?,1,'USER_PROVIDED','2026-01-01')", (label,scope,json.dumps(answer)))
    status,data = request(dashboard,'GET','/api/answers/saved')
    assert status == 200 and len(data['groups']) == 1
    assert data['groups'][0]['conflict'] and len(data['groups'][0]['variants']) == 2
    assert data['groups'][0]['profile_path'] == 'education.currently_enrolled'
    assert request(dashboard,'PUT','/api/answers/categories',{'changes':{'are you currently enrolled':'Other'}})[0] == 200
    assert dashboard.answers.saved()['answers'][0]['category'] == 'Other'
    assert request(dashboard,'PUT','/api/answers/categories',{'changes':{'q':'invalid'}})[0] == 400
    assert request(dashboard,'PUT','/api/answers/categories',{'changes':{'q':'Other'}},{'Origin':'http://evil.test'})[0] == 403
    revision = config.profile['resume_revision']
    db.set_setting('verified_fact:education.currently_enrolled', dict(value=True,source='USER_PROVIDED'))
    assert request(dashboard,'PUT','/api/answers/saved/2',{'link_profile':True})[0] == 200
    assert config.profile['education']['currently_enrolled'] is False
    assert db.setting('verified_fact:education.currently_enrolled')['value'] == 'No'
    assert config.profile['resume_revision'] == revision


def test_saved_grouped_ui_actions(dashboard, tmp_path):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is unavailable')
    tree = ast.parse(Path('tests/test_dashboard.py').read_text(encoding='utf-8'))
    fn = next(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == 'test_javascript_display_formatting_and_renderers')
    assignment = next(n for n in fn.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'harness' for t in n.targets))
    harness = ast.literal_eval(assignment.value)
    script = PAGE.split('<script nonce="__TOKEN__">')[1].split('</script>')[0]
    script = script[:script.index('loadAnswers();setInterval')]
    checks = r'''
Node.prototype.matches=function(selector){return selector.split(',').includes(this.tag);};
Node.prototype.querySelector=function(selector){for(const child of this.children){if(child.matches(selector))return child;const nested=child.querySelector(selector);if(nested)return nested;}return null;};
document.body=new Node('body');
function descendants(el){return [el,...el.children.flatMap(descendants)];}
answerSection='saved';answerSavedView='grouped';answerCategories=['Education','Work authorization','Other'];
answerSavedRows=[{id:'1',question:'Are you currently enrolled?',answer:'Yes',scope:'global',category:'Education',suggested_category:'Education'}, {id:'2',question:'Are you currently a student?',answer:'No',scope:'application:2',category:'Education',suggested_category:'Education'}, {id:'3',question:'Will you require sponsorship?',answer:'No',scope:'global',category:'Work authorization',suggested_category:'Work authorization',category_overridden:true}];
function rebuild(){answerGroups=[{...answerSavedRows[0],variants:answerSavedRows.slice(0,2),conflict:answerSavedRows[0].answer!==answerSavedRows[1].answer,profile_path:'education.currently_enrolled'},{...answerSavedRows[2],variants:[answerSavedRows[2]]}];}
rebuild();answerGroups[1].check_answer=true;drawSavedAnswers();let content=document.getElementById('answers-content');
assert.match(content.textContent,/Conflict/);assert.match(content.textContent,/\+1 similar/);assert.match(content.textContent,/Application #2/);assert.match(content.textContent,/Education \(1\)/);
assert.doesNotMatch(content.textContent,/\+0 similar/);assert.match(content.textContent,/Check answer/);
assert.equal(descendants(content).filter(el=>el.tag==='select'&&el.attributes['aria-label']?.startsWith('Answer to use')).length,1);
assert.equal(descendants(content).filter(el=>el.className==='answer-group-row').length,2);
const categoryChip=descendants(content).find(el=>el.attributes['aria-label']==='Change category for Are you currently enrolled?');
let menu=descendants(content).find(el=>el.attributes['aria-label']==='Category for Are you currently enrolled?');assert.equal(menu.hidden,true);categoryChip.listeners.click();assert.equal(menu.hidden,false);menu.listeners.keydown({key:'Escape'});assert.equal(menu.hidden,true);
const expander=descendants(content).find(el=>el.textContent==='+1 similar');expander.listeners.click();assert.equal(expander.attributes['aria-expanded'],'true');
let search=content.querySelector('input');search.value='visa';search.listeners.input();assert.match(content.textContent,/Will you require sponsorship/);assert.doesNotMatch(content.textContent,/currently enrolled/);
search.value='';search.listeners.input();
(async()=>{
 const writes=[];answerWrite=async(path,body)=>{writes.push({path,body});if(path.startsWith('/api/answers/saved/')&&!body.link_profile){const item=answerSavedRows.find(v=>v.id===path.split('/').pop());item.answer=body.answer;}if(body.link_profile)return answersData;};
 loadSavedAnswers=async()=>{rebuild();drawSavedAnswers();};loadAnswerPreview=async()=>{};
 content=document.getElementById('answers-content');let button=descendants(content).find(el=>el.tag==='button'&&el.attributes['aria-label']==='Use this answer for all');assert.equal(button.title,'Use this answer for all');await button.listeners.click();
 assert.deepEqual(answerSavedRows.slice(0,2).map(v=>v.answer),['Yes','Yes']);assert.equal(writes.length,2);
 await descendants(document.body).find(el=>el.tag==='button'&&el.textContent==='Undo').listeners.click();assert.deepEqual(answerSavedRows.slice(0,2).map(v=>v.answer),['Yes','No']);
 content=document.getElementById('answers-content');let category=descendants(content).find(el=>el.attributes['aria-label']==='Category for Are you currently enrolled?');category.value='Other';await category.listeners.change();assert.deepEqual(writes.at(-1),{path:'/api/answers/categories',body:{changes:{'Are you currently enrolled?':'Other','Are you currently a student?':'Other'}}});
 content=document.getElementById('answers-content');await descendants(content).find(el=>el.tag==='button'&&el.textContent==='Accept suggested categories').listeners.click();assert.equal(Object.hasOwn(writes.at(-1).body.changes,'Will you require sponsorship?'),false);
 content=document.getElementById('answers-content');await descendants(content).find(el=>el.tag==='button'&&el.attributes['aria-label']==='Link to profile field').listeners.click();assert.deepEqual(writes.at(-1),{path:'/api/answers/saved/1',body:{link_profile:true}});
 answerSavedFilter='';answerCategory='';perPage=50;pages.saved=1;
 answerGroups=Array.from({length:51},(_,i)=>({question:'question '+i,answer:'No',category:'Other',variants:[{id:String(i),question:'question '+i,answer:'No',scope:'global',category:'Other'}]}));drawSavedAnswers();
 content=document.getElementById('answers-content');assert.equal(descendants(content).filter(el=>el.className==='answer-group').length,50);
 const question=descendants(content).find(el=>el.tag==='strong');assert.equal(question.textContent,'Question 0');assert.equal(question.title,'question 0');
 pages.saved=2;drawSavedAnswers();content=document.getElementById('answers-content');assert.equal(descendants(content).filter(el=>el.className==='answer-group').length,1);
 assert.doesNotMatch(content.textContent,/\+0 similar/);

})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    path = tmp_path / 'grouped-ui.js'
    path.write_text(harness + script + '\nanswersData=' + json.dumps(dashboard.answers.get()) + ';\n' + checks, encoding='utf-8')
    result = subprocess.run([executable, str(path)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr



@pytest.mark.affirmative
@pytest.mark.parametrize('decline', ["Decline to self-identify", "I don't wish to answer", 'decline',
    'I decline to self-identify', 'Prefer not to say'])
def test_decline_equivalence_in_profile_and_preview(dashboard, config, decline):
    profile = config.profile
    profile['demographics'].update(gender=decline, gender_identity='Prefer not to say',
        race="I don't wish to answer", racial_ethnic_background='I decline to self-identify',
        hispanic_latino=decline)
    before = yaml.safe_dump(profile)
    (config.private / 'profile.yaml').write_text(before, encoding='utf-8')
    fields = {f['path']: f for s in dashboard.answers.get()['sections'] for f in s['fields']}
    for key in ('gender', 'race'):
        assert fields['demographics.' + key]['value'] == 'decline'
        assert 'conflicts' not in fields['demographics.' + key]
    preview = {q['question']: q for q in dashboard.answers.preview()['questions']}
    for question in ('Gender', 'Race', 'Are you Hispanic or Latino?'):
        assert preview[question]['answer'] == 'I decline to self-identify'
        assert preview[question]['source'] != 'Needs you'
    assert (config.private / 'profile.yaml').read_text(encoding='utf-8') == before


def test_saved_equivalent_answers_and_warning(dashboard, db):
    records = [('Gender', 'Decline to self-identify'), ('What is your gender identity?', "I don't wish to answer"),
        ('Race', 'decline'), ('What is your race or ethnicity?', 'Prefer not to say'),
        ('Are you currently enrolled?', 'YES'), ('Are you currently a student?', 'yes'),
        ('Will you now or in the future require sponsorship to be employed at RAVE Aerospace?',
         'Will you now, or in the future require sponsorship to be employed at RAVE Aerospace? YES')]
    for question, answer in records:
        db.execute("INSERT INTO known_answers(normalized_question,scope,answer,verified,source,created_at) VALUES (?,'global',?,1,'USER_PROVIDED','2026-01-01')", (question, json.dumps(answer)))
    before = [dict(r) for r in db.rows('SELECT * FROM known_answers')]
    result = dashboard.answers.saved()
    assert len(result['groups']) == 4
    assert all(not g['conflict'] for g in result['groups'])
    warnings = [g for g in result['groups'] if g['check_answer']]
    assert len(warnings) == 1 and warnings[0]['answer'] == records[-1][1]
    assert [dict(r) for r in db.rows('SELECT * FROM known_answers')] == before


def test_saved_boolean_controls_group_with_echoed_sponsorship(dashboard, db, listing):
    from autoapply.models import Question
    db.ingest(listing, dashboard.config)
    records = [
        ('Are you authorized to work in the US?', 'No', 'select', ['yes', 'no']),
        ('Are you legally work authorized to work in the US?', 'No', 'radio', ['Yes', 'No']),
        ('Will you now or in the future require company sponsorship for a visa or work authorization to work in the country where this role is based?', 'No', 'radio', ['Yes', 'No']),
        ('Will you now or in the future require visa sponsorship in order to work in the US?', 'No', 'select', ['yes', 'no']),
        ('Will you now or in the future require Immuta to sponsor an employment visa or other employer sponsored immigration benefit in order for you to begin or continue working for Immuta?', 'No', 'radio', []),
        ('Will you now or in the future require sponsorship to be employed at RAVE Aerospace?',
         'Will you now, or in the future require sponsorship to be employed at RAVE Aerospace? YES', 'select', []),
    ]
    for i, (label, answer, kind, options) in enumerate(records):
        row = db.question(1, Question(str(i), label, kind, True, options))
        db.execute("INSERT INTO known_answers(normalized_question,scope,answer,verified,source,created_at) VALUES (?,?,?,1,'USER_PROVIDED','2026-01-01')", (row['normalized_question'], row['scope'], json.dumps(answer)))
    before = [dict(r) for r in db.rows('SELECT * FROM known_answers')]
    result = dashboard.answers.saved()
    assert sorted(len(g['variants']) for g in result['groups']) == [2, 4]
    sponsorship = next(g for g in result['groups'] if g['check_answer'])
    echoed = next(v for v in sponsorship['variants'] if v['check_answer'])
    assert echoed['answer'] == records[-1][1] and echoed['answer_kind'] == 'select'
    assert [dict(r) for r in db.rows('SELECT * FROM known_answers')] == before


def test_saved_profile_path_labels_and_boundaries(dashboard, db):
    for key in ('contact.address', 'contact.city', 'contact.country', 'contact.state'):
        db.set_setting('verified_fact:' + key, dict(value='Fixture', source='USER_PROVIDED'))
    result = dashboard.answers.saved()
    assert len(result['groups']) == 4
    assert {g['display_question'] for g in result['groups']} == {'Street address', 'City', 'Country', 'State'}
