"""T072: offered choices, real backend facts and actual unpacked MV3 form state."""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from autoapply.answers import AnswerResolver, fit_options
from autoapply.extension_api import resolve_answers as resolve_fields
from autoapply.models import Question
from test_dashboard import dashboard
from test_extension import loaded_extension, extension_server, KEY

FIXTURE = Path(__file__).parent / 'fixtures/extension/choices/greenhouse.html'
EXPORT = ('EXPORT CONTROLS - This position requires access to information and technology that is subject to U.S. export controls. '
          'Your responses to the questions below will be used solely to determine your eligibility under U.S. law to receive '
          'information and materials subject to U.S. export controls.')
VETERANS = ['I am not a protected veteran', 'I identify as one or more of the classifications of a protected veteran', "I don't wish to answer"]
CITIZENS = ['A United States citizen or national', 'A person lawfully admitted for permanent residence of the United States (i.e., “Green Card” holder)',
            'A person admitted as a refugee to the United States under 8 U.S.C. 1157', 'A person admitted as an asylee to the United States under 8 U.S.C. 1158', 'None of the above']
APP = dict(id='fixture', company='Fixture')


@pytest.fixture
def choice_profile(config):
    profile = copy.deepcopy(config.profile)
    profile['demographics'] = dict(veteran_status='I AM NOT A VETERAN', gender='Not declared')
    profile['locations'] = dict(ordered_preferences=['San Francisco, CA', 'San Jose, CA', 'New York, NY', 'Seattle, WA', 'Austin, TX'])
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    return profile


def test_captured_choices_real_resolver(config, db, choice_profile):
    resolver = AnswerResolver(config, db)
    for label, options, expected in [('Veteran Status', VETERANS, VETERANS[0]),
                                    ('Gender', ['Male', 'Female', 'Decline To Self Identify'], 'Decline To Self Identify'),
                                    ('U.S. WORK AUTHORIZATION', ['Yes', 'No'], 'Yes'), (EXPORT, CITIZENS, CITIZENS[0])]:
        for kind in ['combobox', 'select', 'radio']:
            answer = resolver.resolve(Question('q', label, kind, True, options), APP)
            assert answer and answer.value == expected, (label, kind)
    assert resolver.resolve(Question('q', 'What is your top location preference?', 'combobox', True,
                                     ['Costa Mesa, CA (HQ)', 'Ashville, OH']), APP) is None


@pytest.mark.parametrize('citizenship', [{}, {'us_citizen': False}, {'us_citizen': None},
                                       {'us_citizen': True, 'permanent_resident': True}])
def test_export_requires_uncontradicted_explicit_fact(config, db, choice_profile, citizenship):
    choice_profile['citizenship'] = citizenship
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(choice_profile), encoding='utf-8')
    assert AnswerResolver(config, db).resolve(Question('q', EXPORT, 'select', True, CITIZENS), APP) is None


@pytest.mark.parametrize('options', [[], ['Yes', 'No'], ['None of the above'],
                                     [CITIZENS[0], CITIZENS[0].upper()], ['Not a United States citizen or national']])
def test_export_does_not_guess_option_type(config, db, choice_profile, options):
    assert AnswerResolver(config, db).resolve(Question('q', EXPORT, 'combobox', True, options), APP) is None


def test_exact_decline_family_and_negation(config, db, choice_profile):
    for option in ['Decline To Self Identify', 'decline-to-self-identify', "I don’t wish to answer", 'Not declared']:
        assert fit_options('Not declared', ['Male', option]) == option
    for options in [['I decline citizenship', 'Male'], ['I do not decline to answer'], ['Decline', 'Prefer not to answer'],
                    ['Decline (I am a protected veteran)']]:
        assert fit_options('Not declared', options) is None
    assert fit_options('I AM NOT A VETERAN', VETERANS) == VETERANS[0]
    assert fit_options('I am a protected veteran', VETERANS) is None
    assert fit_options('Yes', ['Yes, I am not authorized', 'No']) is None
    assert fit_options('Male', ['Female']) is None
    assert fit_options('not a veteran', [VETERANS[0], VETERANS[0].upper()]) is None
    resolver = AnswerResolver(config, db)
    for label in ['Select one', 'field_123', 'I agree to comply with export controls and license requirements',
                  'Do you hold a security clearance?']:
        assert resolver.resolve(Question('q', label, 'select', True, ['Yes', 'No']), APP) is None


@pytest.fixture
def choice_server(dashboard, choice_profile):
    """Fixture-only HTTP; the answers endpoint uses the production resolver."""
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data):
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin', '*'))
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-AutoApply-Key')
            self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_OPTIONS(self):
            self.respond({})

        def do_GET(self):
            calls.append((self.path, None))
            self.respond({'items': []} if self.path == '/ext/run/status' else {'done': True} if self.path == '/ext/run/next' else {'ok': True, 'profile_id': 'synthetic'})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
            calls.append((self.path, payload))
            if self.path == '/ext/answers':
                self.respond(resolve_fields(dashboard, payload))
            elif self.path == '/ext/match':
                self.respond({'application_id': None})
            elif self.path == '/ext/profile-sections':
                self.respond(dict(experience=[], projects=[], education=[], skills=[], links={}))
            else:
                self.respond({'ok': True})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def choice_page(loaded_extension, choice_server):
    context, worker, _ = loaded_extension
    server, calls = choice_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key='synthetic-fixture-key', autoSendStructure=False))
    page = context.new_page()
    page.route('**/choices.html', lambda route: route.fulfill(path=str(FIXTURE), content_type='text/html'))
    page.goto(server + '/choices.html')
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    return page, worker, tab, calls


@pytest.mark.browser
@pytest.mark.parametrize('prefer_ca', [False, True])
def test_actual_mv3_offered_choices_commit_without_search(loaded_extension, choice_server, config, choice_profile, prefer_ca):
    if prefer_ca:
        choice_profile['locations']['preferred_states'] = ['CA']
        (config.private / 'profile.yaml').write_text(yaml.safe_dump(choice_profile), encoding='utf-8')
    page, worker, tab, calls = choice_page(loaded_extension, choice_server)
    page.evaluate("delays.veteran_status=450")
    result = worker.evaluate('id=>autoApplyAutofill(id)', tab)
    assert not result.get('error'), result
    expected = {'veteran_status': VETERANS[0], 'gender': 'Decline To Self Identify',
                'question_13205853007': 'Yes', 'question_13205852007': CITIZENS[0]}
    if prefer_ca:
        expected['question_13205851007'] = 'Costa Mesa, CA (HQ)'
    for field, label in expected.items():
        assert page.locator(f'input[name="{field}"]').input_value() != ''
        assert page.locator(f'#{field}').input_value() == ''
        assert page.locator(f'#{field}').evaluate("e=>e.parentElement.querySelector('.single-value').textContent") == label
    if not prefer_ca:
        assert page.locator('input[name="question_13205851007"]').input_value() == ''
    actions = page.evaluate('actions')
    assert [a for a in actions if a[0] == 'click'] == [['click', k, v] for k, v in expected.items()]
    assert not [a for a in actions if a[0] == 'search']
    first = next(payload for path, payload in calls if path == '/ext/answers')
    assert next(f for f in first['fields'] if f['label'] == 'Veteran Status')['options'] == VETERANS
    assert result['summary']['filled'] == (5 if prefer_ca else 4)
    assert [f['label'] for f in result['pending']] == ([] if prefer_ca else ['What is your top location preference?'])
    if not prefer_ca:
        assert result['pending'][0]['reason'] == 'No offered location matches saved preferences.'
    traces = [e for path, payload in calls if path == '/ext/trace' for e in payload['entries']]
    assert any(e['action'] == 'clicked' and e.get('target') == VETERANS[0] for e in traces)
    assert not any(e['step'] == 'search' for e in traces)
    assert {e.get('value') for e in traces if e['step'] == 'dropdown' and e['action'] == 'verified'
            and e.get('verified') is True} >= set(expected.values())
    assert page.evaluate('submissions') == 0
    assert not any(path in {'/ext/applied', '/ext/run/result', '/ext/resume', '/ext/write'} for path, _ in calls)
    # Opening to activate is necessary; there is no second collection probe.
    assert page.evaluate('opens.veteran_status') == 2


@pytest.mark.browser
@pytest.mark.parametrize('change', ['changed', 'disabled', 'empty', 'occupied', 'during-open', 'uncommitted', 'duplicate', 'stale', 'new-label', 'during-open-force', 'changed-force'])
def test_actual_mv3_revalidates_and_abstains(loaded_extension, choice_server, change):
    page, worker, tab, calls = choice_page(loaded_extension, choice_server)
    scan = worker.evaluate('async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{const s=await autoApplyContent.scan();await autoApplyContent.collectOptions(s.fields.map(f=>f.key));return s;}}))[0].result', tab)
    field = next(f for f in scan['fields'] if f['label'] == 'Veteran Status')
    if change in {'changed', 'changed-force'}:
        page.evaluate("specs.veteran_status[1]=[\"I identify as one or more of the classifications of a protected veteran\"]")
    elif change == 'empty':
        page.evaluate('specs.veteran_status[1]=[]')
    elif change == 'duplicate':
        page.evaluate('specs.veteran_status[1]=[specs.veteran_status[1][0],specs.veteran_status[1][0].toUpperCase()]')
    elif change == 'disabled':
        page.locator('#veteran_status').evaluate('e=>e.disabled=true')
    elif change == 'occupied':
        page.locator('#veteran_status').fill('User edit')
    elif change in {'during-open', 'during-open-force'}:
        page.evaluate("onOpen=id=>{if(id==='veteran_status')document.getElementById(id).value='User edit'}")
    elif change == 'uncommitted':
        page.evaluate('searchOnly=true')
    elif change == 'stale':
        page.evaluate("history.pushState({},'',location.pathname+'?other-page')")
    elif change == 'new-label':
        page.locator('label[for=veteran_status]').evaluate("e=>e.textContent='A different question'")
    outcome = worker.evaluate('''async args=>(await chrome.scripting.executeScript({target:{tabId:args.id},func:async answer=>autoApplyContent.fill([answer]),args:[args.answer]}))[0].result''',
                              dict(id=tab, answer=dict(key=field['key'], status='RESOLVED', value=VETERANS[0], force=change.endswith('-force'))))
    assert outcome['filled'] == 0, outcome
    assert page.locator('input[name="veteran_status"]').input_value() == ''
    assert page.evaluate('submissions') == 0
    if change in {'occupied', 'during-open', 'during-open-force'}:
        assert page.locator('#veteran_status').input_value() == 'User edit'


@pytest.mark.browser
@pytest.mark.parametrize('kind', ['select', 'radio', 'checkbox'])
def test_actual_mv3_native_current_offered_choices(loaded_extension, choice_server, kind):
    page, worker, tab, calls = choice_page(loaded_extension, choice_server)
    page.evaluate('''kind=>{
      const fields=document.getElementById('fields');fields.replaceChildren();
      for(const [id,[label,options]] of Object.entries(specs)){
        const group=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=label;group.append(legend);
        const controlKind=kind==='checkbox'&&id!=='question_13205853007'?'select':kind;
        if(controlKind==='select'){
          const select=document.createElement('select');select.id=id;select.setAttribute('aria-label',label);select.required=true;
          select.add(new Option('Select one',''));options.forEach((o,i)=>select.add(new Option(o,String(i+1))));group.append(select);
        }else for(const [index,option] of options.entries()){
          const row=document.createElement('label'),input=document.createElement('input');input.type=controlKind;input.name=id;input.id=id+'-'+index;input.value=String(index+1);input.required=true;row.append(input,document.createTextNode(option));group.append(row);
        }
        fields.append(group);
      }
    }''', kind)
    result = worker.evaluate('id=>autoApplyAutofill(id)', tab)
    assert not result.get('error'), result
    for field in ['veteran_status', 'gender', 'question_13205853007', 'question_13205852007']:
        if kind == 'select' or kind == 'checkbox' and field != 'question_13205853007':
            assert page.locator('#' + field).input_value() == ('3' if field == 'gender' else '1')
        else:
            assert page.locator(f'input[name="{field}"]:checked').input_value() == ('3' if field == 'gender' else '1')
    assert result['summary']['filled'] == 4, result
    assert len(result['pending']) == 1
    assert page.evaluate('submissions') == 0


@pytest.mark.browser
def test_observed_choices_reused_before_activation(loaded_extension, choice_server):
    page, worker, tab, _ = choice_page(loaded_extension, choice_server)
    worker.evaluate('''async id=>chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
      const api=autoApplyContent,s=await api.scan(),keys=s.fields.map(f=>f.key);
      await api.collectOptions(keys);await api.collectOptions(keys);
      const again=await api.scan();await api.collectOptions(again.fields.map(f=>f.key));
    }})''', tab)
    assert page.evaluate('opens') == {field: 1 for field in ['veteran_status', 'gender', 'question_13205853007', 'question_13205852007', 'question_13205851007']}


@pytest.mark.browser
def test_preserved_workday_degree_and_virtual_study(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, calls = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    page.goto(server + '/t047.html')
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    profile = dict(education=[dict(school='University of Maryland - College Park', degree="Bachelor's", major='Computer Science',
                                   gpa='3.5', start_date='2024', end_date='2028')])
    worker.evaluate('''async args=>(await chrome.scripting.executeScript({target:{tabId:args.id},func:profile=>autoApplyContent.prepareSections(profile),args:[args.profile]}))[0].result''', dict(id=tab, profile=profile))
    steps = [entry for path, _, payload in calls if path == '/ext/trace' for entry in payload['entries']]
    assert page.locator('#education-8--degree').inner_text() == "Bachelor's Degree", json.dumps([s for s in steps if s.get('element_id') == 'education-8--degree'], indent=2)
    assert page.locator('[data-automation-id=formField-fieldOfStudy] [data-automation-id=selectedItem]').inner_text() == 'Computer Science'


@pytest.mark.browser
def test_remote_school_ambiguous_campuses_abstain(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    worker.evaluate('config=>chrome.storage.local.set(config)', dict(server=server, key=KEY))
    page = context.new_page()
    page.goto(server + '/greenhouse.html')
    page.locator('.select[data-id="school"]').evaluate('''e=>{
      e.dataset.options=JSON.stringify(['Fixture University - North', 'Fixture University - South']);
    }''')
    tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    outcome = worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
      const scan=await autoApplyContent.scan(),field=scan.fields.find(f=>f.label==='School');
      return autoApplyContent.fill([{key:field.key,status:'RESOLVED',value:'Fixture University'}]);
    }}))[0].result''',tab)
    assert outcome['filled'] == 0 and outcome['needs_input'] == ['School']
    assert page.locator('[name=school]').input_value() == ''
    assert page.locator('#school').input_value() == ''
    searches=page.evaluate('schoolSearches')
    assert len([term for term in searches if term]) <= 2
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


@pytest.mark.browser
def test_explicit_force_replaces_only_unchanged_initial_selection(loaded_extension, choice_server):
    page, worker, tab, _ = choice_page(loaded_extension, choice_server)
    page.locator('#veteran_status').evaluate('''e=>{
      e.parentElement.querySelector('.single-value').textContent="I don't wish to answer";
      e.parentElement.querySelector('[type=hidden]').value='3';
    }''')
    result=worker.evaluate('''async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
      const scan=await autoApplyContent.scan(),field=scan.fields.find(f=>f.label==='Veteran Status');
      return autoApplyContent.fill([{key:field.key,status:'RESOLVED',value:'I am not a protected veteran',force:true}]);
    }}))[0].result''',tab)
    assert result['filled'] == 1
    assert page.locator('[name=veteran_status]').input_value() == '1'
    assert page.locator('#veteran_status').evaluate("e=>e.parentElement.querySelector('.single-value').textContent") == VETERANS[0]
    assert page.evaluate('submissions') == 0


@pytest.mark.browser
def test_internal_language_caller_preserves_occupied_combo(loaded_extension, choice_server):
    page, worker, tab, _ = choice_page(loaded_extension, choice_server)
    page.locator('#veteran_status').evaluate('''e=>{
      document.querySelector('h1').textContent='Voluntary Self-Identification of Disability';
      e.closest('.field').dataset.automationId='formField-language';
      e.closest('.field').querySelector('label').textContent='Language';
      e.parentElement.querySelector('.single-value').textContent='Spanish';
      e.parentElement.querySelector('[type=hidden]').value='user-language';
    }''')
    worker.evaluate('''async id=>chrome.scripting.executeScript({target:{tabId:id},
      func:()=>autoApplyContent.prepareSections({})})''',tab)
    assert page.locator('[name=veteran_status]').input_value() == 'user-language'
    assert page.locator('#veteran_status').evaluate("e=>e.parentElement.querySelector('.single-value').textContent") == 'Spanish'
    assert page.evaluate('actions') == []
    assert page.evaluate('submissions') == 0
