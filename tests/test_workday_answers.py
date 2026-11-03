"""T088 synthetic semantic answers; browser gates are for independent root execution."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import json
import subprocess
import threading
import time

import pytest
import yaml
from playwright.sync_api import expect

from autoapply import answers, extension_api
from autoapply.dashboard_settings import SettingsRejected
from autoapply.models import Question
from test_dashboard import dashboard, offline_descriptions
from test_t078_accounts import backend, loaded_extension, URL

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / 'fixtures/extension/workday/answers.html'
POSTING = 'https://fixture.myworkdayjobs.com/en-US/Jobs/job/US---Bay-City%2C-CA/Synthetic-Intern_REQ088'
DEGREE = 'Bachelor’s or Equivalent First-Degree (ISCED 6: BA, BSc, BEng, LLB, etc.)'
DISCLOSURE = ['Yes, I have a disability, or have had one in the past',
              'No, I do not have a disability and have not had one in the past', 'I do not want to answer']


def save(config, profile):
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')


@pytest.fixture
def semantic_profile(config, dashboard, monkeypatch):
    profile = config.profile
    profile['education'].update(school='Synthetic University', degree='Bachelor', graduation_date='2030-05-26')
    profile.update(skills=['Python'], employment=[
        dict(company='Freelance Fixture', title='Developer', location='Remote', current=True),
        dict(company='Tea Fixture', title='Crew Member', location='College Park, MD', current=True)],
        locations=dict(ordered_preferences=['San Francisco, CA', 'San Jose, CA', 'New York, NY'],
                       preferred_states=['CA'], any_us_fallback=True, allow_non_us=False),
        demographics=dict(military_spouse=False, national_guard_reserves=False),
        application_preferences=dict(disability='Yes', employer_ai_review_opt_out=False))
    save(config, profile)
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: pytest.fail('Unexpected provider request'))
    return profile


def field(key='q', label='Anticipated graduation date', **changes):
    return dict(dict(key=key, label=label, kind='text', required=True, options=[]), **changes)


def resolve(dashboard, fields, **changes):
    return extension_api.resolve_answers(dashboard, dict(dict(url=POSTING, fields=fields, allow_ai=False), **changes))['answers']


def public_page(url=POSTING, locations=None):
    return dict(posting_url=url, context_source='JobPosting', description='Synthetic public posting.',
                job_locations=locations if locations is not None else [dict(city='Bay City', region='CA', country='US')])


@pytest.mark.parametrize('changes,expected', [
    ({}, '05/26/2030'), ({'kind':'date'}, '2030-05-26'),
    ({'placeholder':'MM/YYYY'}, '05/2030'), ({'kind':'number','label':'Expected graduation year'}, '2030'),
    ({'kind':'select','options':['May 2030','June 2030']}, 'May 2030')])
def test_exact_graduation_formats(dashboard, semantic_profile, changes, expected):
    assert resolve(dashboard, [field(**changes)])[0]['value'] == expected


def test_bound_graduation_parts_and_unbound_dates(dashboard, semantic_profile):
    rows = resolve(dashboard, [field(part, 'Anticipated graduation date '+part, date_context='graduation')
                               for part in ('month','day','year')] + [field('generic', 'Day'), field('birth', 'Date of birth month')])
    assert [r['value'] for r in rows] == ['05','26','2030',None,None]


@pytest.mark.parametrize('today', [date(2031,1,9), date(2031,11,24)])
def test_signing_date_is_recomputed_and_us_ordered(dashboard, semantic_profile, monkeypatch, today):
    class LocalDate(date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr(extension_api, 'date', LocalDate)
    rows = resolve(dashboard, [field(part, 'CC305 date signed '+part, date_context='cc305_signed')
                               for part in ('month','day','year')])
    assert [r['value'] for r in rows] == [f'{today.month:02}',f'{today.day:02}',str(today.year)]


def test_unknown_day_and_other_program_do_not_borrow_date(dashboard, config, semantic_profile):
    semantic_profile['education']['graduation_date'] = '2030-05'
    save(config, semantic_profile)
    rows = resolve(dashboard, [field('day','Anticipated graduation date day',date_context='graduation'),
                              field('other','Previous qualification graduation date'), field('birth','Date of birth')])
    assert all(r['value'] is None for r in rows)


@pytest.mark.parametrize('raw', ['2030-05', '2030'])
def test_partial_date_never_supplies_a_fabricated_exact_day(dashboard, config, semantic_profile, raw):
    semantic_profile['education']['graduation_date'] = raw
    save(config, semantic_profile)
    rows = resolve(dashboard, [field('full', 'Anticipated graduation date (MM/DD/YYYY)'),
                              field('native', kind='date')], allow_ai=True)
    assert all(r['value'] is None and r['reason_code'] == 'needs_fact' for r in rows)


@pytest.mark.parametrize('options,expected', [([DEGREE,'High school','Master'],DEGREE),
    ([DEGREE,"Bachelor's degree"],None), (['High school','Other'],None)])
def test_unique_bachelor_equivalent(options, expected):
    assert answers.fit_options('Bachelor', options) == expected


@pytest.mark.parametrize('apostrophe', ["'", '’'])
def test_bachelor_typography_and_ordinary_full_alias(apostrophe):
    offered = DEGREE.replace('’', apostrophe)
    assert answers.fit_options('Bachelor', [offered, 'Master']) == offered
    assert answers.fit_options('Bachelor', ['BS', "Bachelor's Degree", 'Bachelor of Science']) == "Bachelor's Degree"
    assert answers.fit_options('Bachelor', [offered, "Bachelor's Degree"]) is None


@pytest.mark.parametrize('label', [
    'How many credit hours towards your degree do you anticipate having completed by the time you would start this position?',
    'How many completed credits towards your degree will you have?'])
def test_completed_credits_are_not_completed_degree(dashboard, config, semantic_profile, label):
    semantic_profile['education']['credit_hours_by_start'] = 142
    save(config, semantic_profile)
    assert resolve(dashboard, [field(label=label, kind='number')])[0]['value'] == '142'


def test_completed_degree_remains_unknown(dashboard, semantic_profile):
    row, = resolve(dashboard, [field(label='Highest degree completed',kind='select',options=[DEGREE,'High school'])])
    assert row['value'] is None and row['reason_code'] == 'needs_fact'


def test_exact_disability_option_and_explicit_boolean_preferences(dashboard, semantic_profile):
    rows = resolve(dashboard, [field('disability','Disability status',kind='radio',options=DISCLOSURE),
        field('spouse','Are you a military spouse?',kind='select',options=['Yes','No']),
        field('guard','Are you currently a member of the National Guard or Reserves?',kind='select',options=['Yes','No']),
        field('out','Would you like to opt out of AI review?',kind='select',options=['Yes','No']),
        field('in','Do you consent to AI review?',kind='select',options=['Yes','No']),
        field('choice','AI review preference',kind='select',options=['Opt in','Opt out'])])
    assert [r['value'] for r in rows] == [DISCLOSURE[0],'No','No','No','Yes','Opt in']


def test_unknown_sensitive_and_generic_checkgroup_do_not_reach_provider(dashboard, config, semantic_profile):
    semantic_profile['demographics'] = {}
    semantic_profile['application_preferences'] = {}
    save(config, semantic_profile)
    rows = resolve(dashboard, [field('generic','Please check one of the boxes below:',kind='checkbox',multiple=True,options=DISCLOSURE),
        field('spouse','Are you a military spouse?',kind='select',options=['Yes','No']),
        field('medical','Do you have a medical condition requiring treatment?',kind='select',options=['Yes','No'])], allow_ai=True)
    assert all(r['value'] is None for r in rows)


def test_single_posting_location_and_any_us_last(dashboard, semantic_profile):
    row, = resolve(dashboard, [field(label='What are your top two preferred office locations?',kind='textarea')],page=public_page())
    assert row['value'] == 'Bay City, CA; Open to any US location'
    assert 'San Francisco' not in row['value']
    assert row['source'] == 'job_context:locations'


def test_actual_options_and_foreign_any_are_constrained(dashboard, semantic_profile):
    rows = resolve(dashboard, [field('first','What is your top location preference?',kind='select',
        options=['London, UK','Any','San Jose, CA','Bay City, CA']),
        field('second','What is your second location preference?',kind='select',options=['London, UK','Any','Bay City, CA']),
        field('us','What is your second location preference?',kind='select',options=['London, UK','Any US location','Bay City, CA'])],page=public_page())
    assert [r['value'] for r in rows] == ['Bay City, CA',None,'Any US location']


def test_foreign_only_posting_does_not_inherit_us_url_location(dashboard, semantic_profile):
    row, = resolve(dashboard, [field(label='What are your top two preferred office locations?',kind='textarea')],
        url=POSTING.replace('US---Bay-City%2C-CA','GB---London'),
        page=public_page(POSTING.replace('US---Bay-City%2C-CA','GB---London'),[dict(city='London',region='',country='GB')]))
    assert row['value'] is None


@pytest.mark.parametrize('stored', [False, True])
def test_explicit_foreign_metadata_overrides_us_route_and_stored_location(dashboard, config, db, listing, semantic_profile, stored):
    binding = {}
    if stored:
        job, _ = db.ingest(replace(listing, url=POSTING, location='Bay City, CA'), config)
        binding['application_id'] = db.one('SELECT id FROM applications WHERE job_id=?', (job,))['id']
    rows = resolve(dashboard, [field(label='What are your top two preferred office locations?', kind='textarea'),
                              field('choice', 'What is your top location preference?', kind='select', options=['Bay City, CA', 'Any US location'])],
        page=public_page(locations=[dict(city='London', region='', country='GB')]), allow_ai=True, **binding)
    assert all(row['value'] is None for row in rows)


def test_stale_graduation_memory_and_fact_do_not_override_explicit_day(dashboard, config, db, semantic_profile):
    extension_api.remember(dashboard, dict(url=POSTING,key='q',label='Anticipated graduation date',kind='text',value='05/01/2029'))
    db.set_setting('verified_fact:education.graduation_date',dict(value='2029-05-01',source='USER_PROVIDED',canonical_profile_revision='old'))
    extension_api.remember(dashboard,dict(url=POSTING,key='other',label='Synthetic personal response',kind='text',value='Keep this answer'))
    rows = resolve(dashboard,[field(),field('other','Synthetic personal response')])
    assert [r['value'] for r in rows] == ['05/26/2030','Keep this answer']


def test_stale_location_memory_and_context_changes(dashboard, semantic_profile):
    question=field(label='What are your top 2 office locations?',kind='textarea')
    extension_api.remember(dashboard,dict(url=POSTING,**question,value='San Francisco, CA; San Jose, CA'))
    first = resolve(dashboard,[question],page=public_page())[0]
    second = resolve(dashboard,[question],page=public_page(locations=[dict(city='New York',region='NY',country='US')]))[0]
    assert first['value'] == 'Bay City, CA; Open to any US location'
    assert second['value'] == 'New York, NY; Open to any US location'


def test_verified_short_url_binding_and_wrong_application_rejected(dashboard, config, db, listing, semantic_profile):
    job,_=db.ingest(replace(listing,url='https://short.example.test/verified',location='Bay City, CA'),config)
    app=db.one('SELECT id FROM applications WHERE job_id=?',(job,))['id']
    with db.transaction():
        db.bind_url(app,POSTING.replace('/en-US','').replace('%2C',''))
    assert extension_api.match(dashboard,dict(url=POSTING+'/apply'))['application_id'] == app
    row, = resolve(dashboard,[field(label='What are your top 2 office locations?',kind='textarea')],application_id=app)
    assert row['value'] == 'Bay City, CA; Open to any US location'
    with pytest.raises(SettingsRejected,match='does not match'):
        resolve(dashboard,[field()],application_id=app,url=POSTING.replace('REQ088','REQ089'))


def test_form_description_cannot_replace_public_posting(dashboard, config, db, listing, semantic_profile, monkeypatch):
    job,_=db.ingest(replace(listing,url=POSTING,description='Actual public job description.',location='Bay City, CA'),config)
    app=db.one('SELECT id FROM applications WHERE job_id=?',(job,))['id']
    cached=[]
    monkeypatch.setattr(dashboard,'cache_extension_description',lambda *args:cached.append(args))
    resolve(dashboard,[],application_id=app,page=dict(description='Application field labels. '*100,
        context_source='JobPosting',posting_url=POSTING.replace('REQ088','REQ089'),job_locations=[dict(city='London',country='GB')]))
    assert not cached
    assert db.application(app)['description'] == 'Actual public job description.'


def test_work_locations_bind_matching_anchors_in_reversed_order(dashboard, semantic_profile):
    fields=[field('tea','Location',section='Work Experience',section_entry=dict(company='Tea Fixture',title='Crew Member')),
            field('freelance','Location',section='Work Experience',section_entry=dict(company='Freelance Fixture',title='Developer')),
            field('unknown','Location',section='Work Experience'),
            field('conflict','Location',section='Work Experience',section_entry=dict(company='Tea Fixture',title='Developer'))]
    assert [r['value'] for r in resolve(dashboard,fields)] == ['College Park, MD','Remote',None,None]


def test_grounded_program_and_tools_share_one_minimal_cached_batch(dashboard, semantic_profile, monkeypatch):
    calls=[]
    def writer(request):
        calls.append(request)
        return dict(answers=[dict(key='program',value=DEGREE,fact_ids=['education.degree'],needs_input=False,reason='mapped'),
                             dict(key='tools',value='Python (programming)',fact_ids=['skills'],needs_input=False,reason='mapped')])
    monkeypatch.setattr(dashboard,'run_writing',writer)
    fields=[field('program','Which program are you pursuing?',kind='select',options=[DEGREE,'Master']),
            field('tools','Which programming tools do you use?',kind='select',options=['Python (programming)','Rust'])]
    assert all(r['reason_code']=='reasoning_pending' for r in resolve(dashboard,fields))
    for _ in range(2):
        assert [r['value'] for r in resolve(dashboard,fields,allow_ai=True)] == [DEGREE,'Python (programming)']
    assert len(calls)==1
    assert set(calls[0]['facts']) == {'education.degree','education.major','education.currently_enrolled','skills'}
    assert calls[0]['job']=={} and 'profile' not in calls[0]


def test_public_context_facts_do_not_become_global_preferences(semantic_profile):
    from autoapply.field_mapping import describe
    q=Question('office','Which office locations do you prefer?','textarea',True,[])
    app=dict(id=88,public_job_context=dict(url=POSTING,locations=['Bay City, CA'],source='public_posting'))
    facts=answers.repair_facts(q,describe(q,app,semantic_profile),semantic_profile,app)
    assert facts['locations.ordered_preferences']==[]
    assert facts['job_context.locations']['locations']==['Bay City, CA']
    assert semantic_profile['locations']['ordered_preferences'][0]=='San Francisco, CA'


def test_scoped_syntax():
    for name in ('content.js','background.js'):
        subprocess.run(['node','--check',str(ROOT/'extension'/name)],check=True,capture_output=True,timeout=15)
    script=FIXTURE.read_text(encoding='utf-8').split('<script>')[1].split('</script>')[0]
    subprocess.run(['node','--check','-'],input=script,text=True,check=True,capture_output=True,timeout=15)


def test_public_cache_retains_partial_context_and_replaces_explicit_locations():
    script = r'''
      const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
      const source=fs.readFileSync('extension/background.js','utf8');
      const state={},context=vm.createContext({URL,chrome:{storage:{session:{
        get:async()=>structuredClone(state),set:async value=>Object.assign(state,structuredClone(value))}}}});
      vm.runInContext(source.slice(source.indexOf('function postingKey('),source.indexOf('async function scan(')),context);
      (async()=>{
        const url='https://fixture.myworkdayjobs.com/en-US/Jobs/job/US---Bay-City-CA/Intern_REQ088';
        const us=[{city:'Bay City',region:'CA',country:'US'}],foreign=[{city:'London',region:'',country:'GB'}];
        const page={posting_url:url,context_source:'JobPosting',description:'Public description',job_locations:us};
        await context.publicJobContext(page,url);
        let value=await context.publicJobContext({...page,description:'',job_locations:[]},url+'/apply');
        assert.equal(value.description,'Public description');assert.equal(value.job_locations[0].city,'Bay City');
        value=await context.publicJobContext({...page,job_locations:foreign},url.replace('/en-US',''));
        assert.equal(value.job_locations[0].country,'GB');
        value=await context.publicJobContext({description:'Application labels'},url.replace('REQ088','REQ089')+'/apply');
        assert.equal(value.job_locations,undefined);
        assert.equal(Object.keys(state.jobContexts).length,1);
      })().catch(error=>{console.error(error);process.exitCode=1});
    '''
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def semantic_browser(loaded_extension, backend, semantic_profile, monkeypatch):
    """Real unpacked MV3, local backend and synthetic public surfaces only."""
    context, worker, extension_id = loaded_extension
    service, server, _ = backend
    calls = []
    def writer(request):
        calls.append(request)
        return dict(answers=[dict(key=f['key'], value='Python (programming)', fact_ids=['skills'],
                                 needs_input=False, reason='mapped') for f in request['fields']])
    monkeypatch.setattr(service, 'run_writing', writer)
    worker.evaluate('v=>chrome.storage.local.set(v)', dict(server=server,
        key=service.settings.extension_key(), autoSendStructure=False, autoAccountFill=False))
    blocked, pages = [], []
    def serve(route):
        if route.request.method == 'GET':
            route.fulfill(path=str(FIXTURE), content_type='text/html')
        else:
            blocked.append(route.request.method)
            route.abort()
    context.route('https://fixture.myworkdayjobs.com/**', serve)
    def open_page(view='application', revert=False):
        page = context.new_page()
        pages.append(page)
        page.add_init_script('window.initialView='+json.dumps(view)+';window.revertDate='+json.dumps(revert))
        page.goto(POSTING if view == 'landing' else POSTING+'/apply')
        tab = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
        worker.evaluate('id=>autoApplySidebar(id,"open")', tab)
        page.frame_locator('#autoapply-sidebar iframe').locator('#send-structure').wait_for()
        panel = next(f for f in page.frames if f.url.startswith(f'chrome-extension://{extension_id}/sidepanel.html'))
        def isolated(method, *args):
            return worker.evaluate('''async v=>{
              const [frame]=await chrome.scripting.executeScript({target:{tabId:v.tab},world:'ISOLATED',
                func:(method,args)=>globalThis.autoApplyContent[method](...args),args:[v.method,v.args]});
              return frame.result;
            }''', dict(tab=tab, method=method, args=list(args)))
        def act(method):
            reply = panel.evaluate('v=>chrome.runtime.sendMessage(v)', dict(type=method, tabId=tab))
            assert not reply.get('error'), reply
            return reply
        return page, worker, tab, panel, isolated, act
    yield open_page, calls, writer
    for page in pages:
        assert page.evaluate('submissions') == 0
        page.close()
    assert not blocked


def freeze_local_date(worker, tab, year, month, day):
    worker.evaluate('''async v=>chrome.scripting.executeScript({target:{tabId:v.tab},world:'ISOLATED',
      func:parts=>{const Original=globalThis.fixtureOriginalDate ||= Date;
        globalThis.Date=class extends Original {constructor(...args){super(...(args.length?args:[...parts,12]));}};},
      args:[[v.year,v.month-1,v.day]]})''', dict(tab=tab, year=year, month=month, day=day))


@pytest.mark.browser
@pytest.mark.parametrize('method', ['autofill', 'page-snapshot'])
def test_mv3_semantic_shapes_and_fresh_local_dates(semantic_browser, method):
    open_page, calls, _ = semantic_browser
    page, worker, tab, _, isolated, act = open_page()
    results = []
    for month, day in [(1, 9), (11, 24)]:
        freeze_local_date(worker, tab, 2031, month, day)
        page.locator('#selfIdentifiedDisabilityData--dateSignedOn input').evaluate_all('es=>es.forEach(e=>e.value="")')
        act(method)
        results.append(worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab))
        assert page.locator('#synthetic-grad input').evaluate_all('es=>es.map(e=>e.value)') == ['05', '26', '2030']
        assert page.locator('#selfIdentifiedDisabilityData--dateSignedOn input').evaluate_all('es=>es.map(e=>e.value)') == [f'{month:02}', f'{day:02}', '2031']
    expect(page.locator('#disability-0')).to_be_checked()
    expect(page.locator('#disability-1')).not_to_be_checked()
    expect(page.locator('#disability-2')).not_to_be_checked()
    expect(page.locator('#generic-month')).to_have_value('')
    expect(page.locator('[name=degree]')).to_have_text(DEGREE)
    assert page.locator('[data-company]').evaluate_all('es=>es.map(e=>e.value)') == ['College Park, MD', 'Remote']
    for selector in ('#spouse', '#guard', '#ai'):
        expect(page.locator(selector)).to_have_value('No')
    expect(page.locator('#offices')).to_have_value('Bay City, CA; Open to any US location')
    expect(page.locator('#tools')).to_have_value('Python (programming)')
    assert page.locator('#degree-options').count() == 0
    assert len(calls) == 1
    assert set(calls[0]['facts']) == {'skills'}
    assert list(calls[0]['facts']['skills']) == ['Python']
    assert len(calls[0]['fields']) == 1
    assert calls[0]['fields'][0]['options'] == ['Python (programming)', 'Rust']
    assert calls[0]['job'] == {} and 'profile' not in calls[0]
    for result in results:
        assert result['summary']['errors'] == []
        assert result['summary']['filled'] > 0
        assert not any((f.get('reason_code') or '').startswith('provider_') for f in result['pending'])
    assert any(f['value'] == 'Python (programming)' and f['source'] == 'grounded_ai:answer_repair'
               for f in results[0]['completed'])
    assert not next(f for f in isolated('scan')['fields'] if f['label'] == 'Month')['answered']


@pytest.mark.browser
@pytest.mark.parametrize('method', ['autofill', 'page-snapshot'])
def test_mv3_manual_disclosure_date_and_deferred_day_reversion(semantic_browser, method):
    open_page, _, _ = semantic_browser
    page, _, _, _, isolated, act = open_page(revert=True)
    page.locator('#disability-2').check()
    month = page.locator('#synthetic-grad [aria-label=Month]')
    month.evaluate('e=>e.focus()')
    page.keyboard.insert_text('12')
    act(method)
    expect(page.locator('#disability-2')).to_be_checked()
    expect(page.locator('#disability-0')).not_to_be_checked()
    expect(month).to_have_value('12')
    expect(page.locator('#synthetic-grad [aria-label=Day]')).to_have_value('')
    day = next(f for f in isolated('scan')['fields'] if f.get('date_context') == 'graduation' and f.get('date_part') == 'day')
    assert not day['answered']


@pytest.mark.browser
@pytest.mark.parametrize('change', ['edit', 'navigation'])
def test_mv3_normal_grounded_response_preserves_pending_edits_and_navigation(semantic_browser, backend, monkeypatch, change):
    open_page, _, writer = semantic_browser
    page, _, tab, panel, _, _ = open_page()
    entered, release = threading.Event(), threading.Event()
    def held(request):
        entered.set()
        assert release.wait(15), 'Synthetic repair was not released'
        return writer(request)
    monkeypatch.setattr(backend[0], 'run_writing', held)
    panel.evaluate('''id=>{globalThis.pendingOutcome=null;
      globalThis.pending=chrome.runtime.sendMessage({type:'autofill',tabId:id})
        .then(v=>(pendingOutcome=v),e=>(pendingOutcome={error:String(e)}));}''', tab)
    try:
        deadline = time.monotonic()+12
        while not entered.is_set() and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert entered.is_set(), panel.evaluate('pendingOutcome')
        # Grounded repair starts only after native known facts have settled.
        expect(page.locator('#spouse')).to_have_value('No')
        expect(page.locator('#disability-0')).to_be_checked()
        if change == 'edit':
            page.locator('#tools').select_option('Rust')
        else:
            page.evaluate("history.pushState({},'',location.pathname.replace('REQ088','REQ089'))")
    finally:
        release.set()
    panel.evaluate('pending')
    expect(page.locator('#tools')).to_have_value('Rust' if change == 'edit' else '')


@pytest.mark.browser
@pytest.mark.parametrize('method', ['autofill', 'page-snapshot'])
def test_mv3_bound_jsonld_and_passive_context_survive_apply_navigation(semantic_browser, method):
    open_page, _, _ = semantic_browser
    page, worker, tab, _, isolated, act = open_page('landing')
    page.evaluate('''()=>{
      const current=JSON.parse(posting.textContent);current.url=location.href;
      const other={...current,url:location.href.replace('REQ088','REQ999'),
        jobLocation:{address:{addressLocality:'London',addressCountry:'GB'}}};
      posting.textContent=JSON.stringify({'@graph':[other,current]});
    }''')
    info = isolated('skillsPage')
    assert info['posting_url'] == POSTING
    assert info['job_locations'] == [dict(city='Bay City', region='CA', country='US')]
    # Wait for the passive content notification, without Scan or Analyze first.
    page.wait_for_timeout(500)
    saved = worker.evaluate('async()=> (await chrome.storage.session.get("jobContexts")).jobContexts')
    assert any(v.get('posting_url') == POSTING for v in saved.values())
    page.locator('#apply').click()
    expect(page.locator('#offices')).to_be_visible()
    act(method)
    expect(page.locator('#offices')).to_have_value('Bay City, CA; Open to any US location')
    # Another same-host job cannot consume the previous job's location cache.
    page.evaluate("history.pushState({},'',location.pathname.replace('REQ088','REQ089').replace('US---Bay-City%2C-CA','GB---London'))")
    page.locator('#offices').evaluate('e=>e.value=""')
    act(method)
    expect(page.locator('#offices')).to_have_value('')
    result = worker.evaluate('async id=>(await chrome.storage.session.get("tabResults")).tabResults[id]', tab)
    assert result['url'] == page.url
    assert not result['page'].get('job_locations')
    # A lone explicitly different JobPosting must not be relabelled as this job.
    page.evaluate('''()=>{const meta=document.createElement('script');meta.type='application/ld+json';
      meta.textContent=JSON.stringify({'@type':'JobPosting',url:location.href.replace('REQ089','REQ999'),
        jobLocation:{address:{addressLocality:'Bay City',addressRegion:'CA',addressCountry:'US'}}});document.head.append(meta);}''')
    assert not isolated('skillsPage').get('job_locations')
    act(method)
    expect(page.locator('#offices')).to_have_value('')
    saved = worker.evaluate('async()=> (await chrome.storage.session.get("jobContexts")).jobContexts')
    assert all(v.get('posting_url') == POSTING for v in saved.values())
