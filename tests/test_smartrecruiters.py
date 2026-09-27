import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoapply.applications import adapter_for
from autoapply.field_mapping import FieldMapper, UNKNOWN_FIELD, REJECTED_FIELD
from autoapply.models import Answer
from autoapply.smartrecruiters import SmartRecruitersAdapter, CapabilityFailure
from autoapply.uploads import UploadTracker
from test_browser import browser  # noqa: F401 -- shared isolated browser fixture


def test_recognition():
    assert isinstance(adapter_for(SimpleNamespace(url='https://jobs.smartrecruiters.com/oneclick-ui/company/example/publication/id')), SmartRecruitersAdapter)


@pytest.mark.parametrize('ashby', [True,False])
def test_zero_selection_never_ready(ashby):
    tracker=UploadTracker()
    ui=dict(attached=True,busy=False,disabled=False,warning=False,replace_usable=True,file_entry=True)
    assert not tracker.verdict(ui,ashby=ashby)['ready']
    tracker.select()
    assert tracker.verdict(ui,ashby=False)['ready']


async def test_mapping_sanitization_and_cache():
    received=[]
    async def fallback(meta):
        received.append(meta)
        return {'semantic_key':'education.school','confidence':.99}
    cache={}
    mapper=FieldMapper('smartrecruiters',cache,fallback,{'education.school'})
    field={'label':'Institution attended','kind':'text','required':True,'value':'PRIVATE','cookies':'SECRET'}
    assert (await mapper.map(field))['semantic_key']=='education.school'
    assert 'PRIVATE' not in json.dumps(received) and 'SECRET' not in json.dumps(received)
    await mapper.map(field)
    assert len(received)==1
    field['options']=['A','B']
    await mapper.map(field)
    assert len(received)==2
    assert (await FieldMapper('x').map({'label':'Ｌａｓｔ name *'}))['semantic_key']=='contact.last_name'
    assert (await FieldMapper('x').map({'label':'Email','autocomplete':'given-name'}))['semantic_key']==REJECTED_FIELD


async def test_conflicting_mapping_abstains_but_unambiguous_facts_resolve(config, db):
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    resolver = AnswerResolver(config, db)
    for label, autocomplete, expected in [('Email', 'given-name', None),
                                          ('Email', 'email', 'student@example.test'),
                                          ('First name', 'given-name', 'Test')]:
        mapping = await FieldMapper('smartrecruiters').map({'label':label, 'autocomplete':autocomplete})
        q = Question('field', label, semantic_key=mapping['semantic_key'])
        answer = resolver.resolve(q, {'id':1})
        assert (answer.value if answer else None) == expected


async def test_conflict_rejects_cached_mapping_and_never_calls_fallback():
    from unittest.mock import AsyncMock
    from autoapply.field_mapping import signature
    field = {'label':'Email', 'autocomplete':'given-name'}
    fallback = AsyncMock(return_value={'semantic_key':'contact.email', 'confidence':1})
    cache = {signature('smartrecruiters', field): {'semantic_key':'contact.email', 'confidence':1}}
    result = await FieldMapper('smartrecruiters', cache, fallback, {'contact.email'}).map(field)
    assert result['semantic_key'] == REJECTED_FIELD and result['confidence'] == 0
    fallback.assert_not_called()
    assert (await FieldMapper('smartrecruiters').map({'label':'Unfamiliar field'}))['semantic_key'] == UNKNOWN_FIELD


def test_rejected_mapping_preserves_only_exact_verified_answers(config, db, listing):
    from autoapply.answers import AnswerResolver, is_writing_question
    from autoapply.models import Question
    db.ingest(listing, config)
    q = Question('conflict', 'Email', 'email', True, scope='global', semantic_key=REJECTED_FIELD)
    row = db.question(1, q)
    db.save_answer(row['id'], Answer('confirmed@example.test', 'user_confirmed'), verified=True)
    resolver = AnswerResolver(config, db)
    answer = resolver.resolve(q, db.application(1))
    assert answer.value == 'confirmed@example.test' and answer.source == 'verified_memory'
    q.options = ['different@example.test']
    assert resolver.resolve(q, db.application(1)) is None
    assert not is_writing_question(Question('conflict', 'Describe your experience', 'textarea', semantic_key=REJECTED_FIELD))


@pytest.mark.parametrize('verified,scope,value,expected', [
    (True, 'global', 'confirmed@example.test', 'confirmed@example.test'),
    (False, 'global', 'confirmed@example.test', None),
    (True, 'application:2', 'confirmed@example.test', None),
    (True, 'global', None, None),
])
def test_rejected_mapping_exact_common_answer(config, db, verified, scope, value, expected):
    import yaml
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    profile = config.profile
    profile['common_answers'] = {'Email': {'verified':verified, 'scope':scope, 'answer':value}}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile))
    answer = AnswerResolver(config, db).resolve(Question('email', 'Email', 'email', semantic_key=REJECTED_FIELD), {'id':1})
    assert (answer.value if answer else None) == expected


def test_unmapped_legacy_compatibility_and_null(config, db):
    import yaml
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(Question('email', 'Email', 'email', semantic_key=UNKNOWN_FIELD), {'id':1}).value == 'student@example.test'
    assert resolver.resolve(Question('unknown', 'Unfamiliar field', semantic_key=UNKNOWN_FIELD), {'id':1}) is None
    profile = config.profile
    profile['contact']['email'] = None
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile))
    assert resolver.resolve(Question('email', 'Email', 'email', semantic_key='contact.email'), {'id':1}) is None


async def test_discovery_fill_and_transition(browser, config):
    page=await browser.new_page()
    await page.set_content((Path(__file__).parent/'fixtures/smartrecruiters-controls.html').read_text())
    adapter=SmartRecruitersAdapter(page)
    events=[]
    adapter.emit=lambda kind,data:events.append(kind)
    qs=await adapter.get_questions({'id':1})
    assert {q.kind for q in qs}=={'text','email','radio','checkbox','select','combobox','textarea','file'}
    assert len(qs)==9 and adapter.counts['visible_candidates']==10
    values={'First name':'Test','Email':'student@example.test','Work authorization':'Yes','Consent':'Yes',
            'Country':'United States','City':'Boston','Message':'Hello','Last name':'Student'}
    for q in qs:
        if q.kind=='file':
            await adapter.upload_documents(q,config.resume)
        else:
            await adapter.answer_question(q,Answer(values[q.label],'fixture'))
    assert await browser.uploads_ready(page,adapter.uploads,timeout_seconds=1)
    assert not await adapter.validate()
    assert adapter.support_report(qs)['autonomous']
    assert (await adapter.action())[0]=='next'
    # Replace the control, then resolve freshly; hidden duplicate stays first.
    await page.locator('#next').evaluate('e=>e.outerHTML=e.outerHTML')
    await adapter.advance()
    assert events.count('STEP_NEXT_CLICK_DELIVERED')==1
    assert events.count('STEP_TRANSITION_OBSERVED')==1
    assert not any(event.startswith('SUBMIT_') for event in events)
    qs=await adapter.get_questions({'id':1})
    assert [q.label for q in qs]==['Gender']
    assert not adapter.live_committed
    await adapter.answer_question(qs[0], Answer('Decline to self-identify', 'fixture'))
    assert set(adapter.live_committed) == {qs[0].key}
    assert await browser.uploads_ready(page,adapter.uploads,timeout_seconds=1)
    assert not await adapter.validate()
    assert (await adapter.action())[0]=='submit'


async def test_unsupported_required_and_validation(browser):
    page=await browser.new_page()
    await page.set_content('<form><label>Color *<input type=color required></label></form>')
    adapter=SmartRecruitersAdapter(page)
    with pytest.raises(CapabilityFailure,match='UNSUPPORTED_REQUIRED'):
        await adapter.get_questions({'id':1})
    await page.set_content('<form><label>Email<input type=email required></label></form>')
    assert await adapter.validate()


async def test_unobserved_transition_stops(browser):
    page=await browser.new_page()
    await page.set_content('<label>Name<input></label><button>Next</button>')
    adapter=SmartRecruitersAdapter(page)
    q, = await adapter.get_questions({'id':1})
    await adapter.answer_question(q, Answer('Test', 'fixture'))
    receipts = dict(adapter.live_committed)
    with pytest.raises(CapabilityFailure,match='STEP_TRANSITION_UNCONFIRMED'):
        await adapter.advance()
    assert adapter.live_committed == receipts


@pytest.mark.parametrize('change', ['unchanged', 'removed', 'changed'])
async def test_control_receipts_survive_same_step_rediscovery(browser, change):
    page = await browser.new_page()
    await page.set_content('<label>First name<input id="first"></label><label>Email<input type=email></label>')
    adapter = SmartRecruitersAdapter(page)
    q = (await adapter.get_questions({'id':1}))[0]
    await adapter.answer_question(q, Answer('Test', 'fixture'))
    if change == 'removed':
        await page.locator('#first').evaluate('e=>e.remove()')
    elif change == 'changed':
        await page.locator('#first').fill('Unexpected')
    await adapter.get_questions({'id':1})
    assert q.key in adapter.live_committed
    if change == 'removed':
        with pytest.raises(CapabilityFailure, match='disappeared within the current step'):
            await adapter.validate()
    else:
        issues = await adapter.validate()
        assert bool(issues) is (change == 'changed')


async def test_security_transition_does_not_retire_control_receipts(browser):
    page = await browser.new_page()
    await page.set_content('''<label>First name<input></label><button onclick="document.body.innerHTML=
      '<h1>Verify you are human</h1><input aria-label=CAPTCHA>'">Next</button>''')
    adapter = SmartRecruitersAdapter(page)
    q, = await adapter.get_questions({'id':1})
    await adapter.answer_question(q, Answer('Test', 'fixture'))
    with pytest.raises(CapabilityFailure, match='SECURITY_CONTROL'):
        await adapter.advance()
    assert q.key in adapter.live_committed


async def test_late_upload_failure_after_transition_blocks_readiness(browser, config):
    page = await browser.new_page()
    await page.set_content('''<div id="step"><label>Resume<input id="resume" type=file
      onchange="this.parentElement.insertAdjacentHTML('afterend','<span>resume.pdf</span>')"></label></div>
      <button onclick="document.querySelector('#step').innerHTML='<label>Email<input id=email type=email></label>'">Next</button>''')
    adapter = SmartRecruitersAdapter(page)
    q, = await adapter.get_questions({'id':1})
    await adapter.upload_documents(q, config.resume)
    assert await browser.uploads_ready(page, adapter.uploads, 1)
    await adapter.advance()
    assert await browser.uploads_ready(page, adapter.uploads, 1)
    # Tracker keys have the identity semantics of Playwright Request objects.
    class Request:
        url = 'https://fixture.smartrecruiters.com/upload'
        method = 'POST'
        failure = 'net::ERR_FAILED'
    late = Request()
    page._autoapply_uploads.started(late)
    page._autoapply_uploads.failed(late)
    assert not await browser.uploads_ready(page, adapter.uploads, 1)


@pytest.mark.parametrize('label,key,value', [
    ('Gender Identity','gender_identity',"I don't wish to answer"),
    ('Racial/Ethnic background','racial_ethnic_background',"I don't wish to answer"),
    ('Sexual orientation','sexual_orientation',"I don't wish to answer"),
    ('Transgender','transgender','No'),('Disability','disability',"I don't wish to answer"),
    ('Veteran or active member of U.S. Armed Forces','armed_forces','No'),
    ('Gender','gender','Decline to self-identify'),('Hispanic/Latino','hispanic_latino','No'),
    ('Race','race','Decline to self-identify'),('Veteran status','veteran_status','Not a protected veteran')])
def test_demographic_categories_are_separate(config,db,label,key,value):
    import yaml
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    from autoapply.field_mapping import semantic_key
    profile=config.profile
    profile['demographics']={key:value}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile))
    q=Question('demographic',label,'select',False,[value],semantic_key=semantic_key(label))
    answer=AnswerResolver(config,db).resolve(q,{'id':1})
    assert answer.value==value
    assert answer.source=='profile:demographics.'+key
    assert AnswerResolver(config,db).resolve(Question('other','Unspecified sensitive attribute','text'),{'id':1}) is None


def test_policy_does_not_answer(config,db):
    import yaml
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    profile=config.profile
    profile['autofill_policies']={'contact.email':'DO_NOT_ANSWER'}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile))
    assert AnswerResolver(config,db).resolve(Question('email','Email','email'),{'id':1}) is None


async def test_missing_mandatory_capability_disables_autonomy(browser):
    page=await browser.new_page()
    adapter=SmartRecruitersAdapter(page)
    adapter.capabilities=adapter.capabilities-{'FILE_UPLOAD'}
    assert not adapter.support_report([])['autonomous']
    assert adapter.support_report([])['missing']==['FILE_UPLOAD']


async def test_custom_checkbox_and_zero_discovery(browser):
    page=await browser.new_page()
    await page.set_content('<div role="checkbox" aria-label="Consent" aria-checked="false" onclick="this.setAttribute(\'aria-checked\',\'true\')">Consent</div>')
    adapter=SmartRecruitersAdapter(page)
    qs=await adapter.get_questions({'id':1})
    await adapter.answer_question(qs[0],Answer('Yes','fixture'))
    await page.set_content('<p>Loading form</p>')
    with pytest.raises(CapabilityFailure,match='ADAPTER_DISCOVERY_FAILURE'):
        await adapter.get_questions({'id':1})


async def test_receipt_cannot_hide_same_step_removal_or_new_session(browser):
    from autoapply.uploads import SessionAttachment
    page=await browser.new_page()
    await page.set_content('<label>Resume<input type=file></label><span>resume.pdf</span>')
    page._autoapply_uploads.select()
    await page.locator('input').set_input_files({'name':'resume.pdf','mimeType':'application/pdf','buffer':b'x'})
    receipt=SessionAttachment(page,'input[type=file]')
    controls={'resume':(receipt,'resume.pdf',1)}
    assert await browser.uploads_ready(page,controls,1)
    await page.locator('input').evaluate('e=>e.remove()')
    assert not await browser.uploads_ready(page,controls,1)
    new_page=await browser.new_page()
    fresh=SessionAttachment(new_page,'input[type=file]')
    assert not await browser.uploads_ready(new_page,{'resume':(fresh,'resume.pdf',1)},1)


async def test_declaration_remains_unanswered_required(browser):
    page=await browser.new_page()
    await page.set_content('<p>By submitting I certify that all statements are accurate.</p><label>Name<input></label>')
    qs=await SmartRecruitersAdapter(page).get_questions({'id':1})
    declarations=[q for q in qs if q.kind=='attestation']
    assert len(declarations)==1 and declarations[0].required and not declarations[0].value


async def test_normal_engine_two_steps_and_one_confirmed_submit(browser,config,db,listing):
    from dataclasses import replace
    from autoapply.engine import Engine
    html='''<script type="application/ld+json">{"@type":"JobPosting","title":"Software Engineering Intern Summer 2027",
    "description":"Software engineering internship for undergraduate students.","hiringOrganization":{"name":"Example Company"},
    "jobLocation":{"address":{"addressLocality":"New York","addressRegion":"NY","addressCountry":"US"}}}</script>
    <main><h1>Software Engineering Intern Summer 2027</h1><div id="step">
    <label>First name<input required autocomplete="given-name"></label><label>Email<input type=email required></label>
    <label>Resume<input type=file required onchange="this.parentElement.insertAdjacentHTML('afterend','<span>resume.pdf</span>')"></label></div>
    <button id="action" onclick="advance()">Next</button></main><script>
    async function advance(){const b=document.querySelector('#action');if(b.textContent==='Next'){
    document.querySelector('#step').innerHTML='<label>Last name<input required autocomplete="family-name"></label>';b.textContent='Submit application';
    }else{await fetch('/apply',{method:'POST',body:'fixture'});document.body.innerHTML='Your application has been submitted';}}
    </script>'''
    submissions=[]
    async def route(r):
        if r.request.method=='POST':
            submissions.append(r.request.url)
            await r.fulfill(status=200,body='OK')
        else:
            await r.fulfill(status=200,content_type='text/html',body=html)
    await browser.context.route('https://jobs.smartrecruiters.com/**',route)
    db.ingest(replace(listing,url='https://jobs.smartrecruiters.com/fixture/123'),config)
    db.set_setting('auto_submit',True)
    config.data['application']['confirmation_timeout_seconds']=1
    engine=Engine(config,db,browser)
    await engine.process_one(1)
    assert db.application(1)['status']=='SUBMITTED',db.application(1)['failure_reason']
    assert len(submissions)==1
    progress=db.setting('application_transaction:1')
    assert progress['next_clicks']==1 and progress['step']==2
    assert db.setting('upload_result:1')['ready']
    saved = {row['raw_question']: row for row in db.rows('SELECT * FROM questions WHERE application_id=1')}
    assert json.loads(saved['First name']['answer']) == 'Test'
    assert saved['First name']['answer_source'] == 'profile:identity.first_name'
    assert json.loads(saved['Last name']['answer']) == 'Student'
    assert saved['Last name']['answer_source'] == 'profile:identity.last_name'
