"""Checkpoint recovery rejects replay authority at every protected boundary."""
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from autoapply.archive import atomic_json
from autoapply.checkpoint_recovery import ACTION, REASON, prepare, claim, evidence
from autoapply.engine import Engine
from autoapply.models import Question


def qualified(config, db, listing, *, url='https://job-boards.eu.greenhouse.io/example/jobs/123'):
    db.ingest(replace(listing, url=url), config)
    db.claim(1)
    db.lifecycle.begin_attempt(1, 3)
    db.lifecycle.metadata(1, stage='form page 1')
    db.event(1, 'BROWSER_TRANSITION_READINESS', json.dumps({'interaction_at':None}))
    db.lifecycle.record_hold(1, REASON, 'ADAPTER_CAPABILITY_FAILURE', manual_resume_allowed=0)
    db.question(1, Question('resume', 'Resume', 'file', True))
    record = dict(application_id=1, url=db.application(1)['canonical_url'], schema_version=2,
        blocker=REASON, state='MANUAL_REQUIRED', progress_kind='UNFILLED', committed_field_count=0,
        readiness='SAVED_PROGRESS_CHECKPOINT', form_filled=False, upload_ready=False,
        upload=None, resume_sha256=None, reconstruction_verified=False, frames=[])
    path=config.private/'fill-only-checkpoints/1.json'
    atomic_json(path, record)
    db.set_setting('fill_checkpoint:1', {k:record[k] for k in ('schema_version','progress_kind','committed_field_count','readiness','reconstruction_verified','resume_sha256','form_filled','upload_ready')})
    db.event(1,'FILL_ONLY_CHECKPOINT',json.dumps(dict(path=str(path),state=record['state'],readiness=record['readiness'])))
    db.set_setting('auto_submit',False)
    db.set_setting('paused',True)
    return path


@pytest.mark.parametrize('boundary', ['intent','confirmation','delivery','security','verification',
    'session','token','exhausted','lower_budget','wrong_hold','wrong_stage','stale','inactive',
    'document','unknown_fact','retired','conflict','pending_command','auto_submit','controlled',
    'checkpoint_missing','checkpoint_count','checkpoint_url','checkpoint_upload','checkpoint_summary',
    'attempt_event','hold_event','checkpoint_event','field_commit','upload_event','next_event','exception',
    'populated','selected_file','checked','protected'])
def test_recovery_rejection_boundaries(config,db,listing,boundary,monkeypatch):
    path=qualified(config,db,listing)
    updates={'intent':"submit_intent_at='uncertain'",'confirmation':'submission_confirmation_seen=1',
        'delivery':"delivery_state='POSSIBLY_DELIVERED'",'security':"security_state='INTERACTIVE_CHALLENGE'",
        'verification':"verification_state='PENDING'",'session':'session_preserved=1',
        'exhausted':'attempts=4','lower_budget':'max_retries=0',
        'wrong_hold':"error_category='ENGINEERING_FAILURE'",'wrong_stage':"stage='form page 2'"}
    if boundary in updates: db.execute('UPDATE applications SET '+updates[boundary]+' WHERE id=1')
    elif boundary=='token': db.set_setting('manual_session:1',{'token':'stale'})
    elif boundary=='stale': db.execute("UPDATE jobs SET posted_at='2000-01-01' WHERE id=1")
    elif boundary=='inactive': db.execute("UPDATE jobs SET listing_active=0 WHERE id=1")
    elif boundary=='document': config.resume.write_bytes(b'not a pdf')
    elif boundary=='unknown_fact': db.question(1,Question('years','Years of experience','number',True))
    elif boundary=='retired': monkeypatch.setattr(db,'automation_retired',lambda _:True)
    elif boundary=='conflict': monkeypatch.setattr(db,'submission_conflict',lambda _:{'id':2})
    elif boundary=='pending_command': db.execute("INSERT INTO manual_requests VALUES (1,'inspect','now')")
    elif boundary=='auto_submit': db.set_setting('auto_submit',True)
    elif boundary=='controlled': db.set_setting('controlled_application_id',1)
    elif boundary=='checkpoint_missing': path.unlink()
    elif boundary.startswith('checkpoint_') and boundary not in {'checkpoint_summary','checkpoint_event'}:
        saved=json.loads(path.read_text())
        key,value={'checkpoint_count':('committed_field_count',1),'checkpoint_url':('url','https://other.test'),
                   'checkpoint_upload':('upload',{'ready':True})}[boundary]
        saved[key]=value; atomic_json(path,saved)
    elif boundary=='checkpoint_summary': db.set_setting('fill_checkpoint:1',{'progress_kind':'PARTIAL'})
    elif boundary in {'populated','selected_file','checked'}:
        saved=json.loads(path.read_text())
        field={'id':'first_name','type':'text','value':'Changed'} if boundary=='populated' else {'files':['resume.pdf']} if boundary=='selected_file' else {'type':'checkbox','checked':True}
        saved['frames']=[{'fields':[field]}]; atomic_json(path,saved)
    elif boundary=='protected':
        original=db.application
        monkeypatch.setattr(db,'application',lambda _:dict(original(1),id=3556))
        with pytest.raises(ValueError): prepare(db,config,3556,source='Explicit authority')
        return
    elif boundary in {'attempt_event','hold_event','checkpoint_event'}:
        kind={'attempt_event':'APPLICATION_ATTEMPT_STARTED','hold_event':'MANUAL_REVIEW','checkpoint_event':'FILL_ONLY_CHECKPOINT'}[boundary]
        db.execute('DELETE FROM events WHERE application_id=1 AND kind=?',(kind,))
    else: db.event(1,{'field_commit':'LIVE_CONTROL_COMMITTED','upload_event':'UPLOAD_SELECTED','next_event':'STEP_NEXT_INTENT','exception':'APPLICATION_EXCEPTION'}[boundary],'{}')
    before=db.application(1)
    with pytest.raises((ValueError,RuntimeError,OSError)):
        prepare(db,config,1,source='User authorized qualified recovery')
    assert db.application(1)==before
    assert not db.rows('SELECT * FROM manual_commands')


@pytest.mark.parametrize('change',['checkpoint','event','resume','profile','attempts','mode','limit','paused','startup_command','listing'])
def test_claim_revalidates_prepared_binding(config,db,listing,change):
    path=qualified(config,db,listing)
    command=prepare(db,config,1,source='Explicit human authorization')
    db.lifecycle.fill_only=True; db.set_setting('paused',False)
    if change=='checkpoint': path.write_bytes(path.read_bytes()+b' ')
    elif change=='event': db.event(1,'inspection','New evidence')
    elif change=='resume': config.resume.write_bytes(b'%PDF-1.4\nchanged\n%%EOF')
    elif change=='profile': (config.private/'profile.yaml').write_text('identity: {first_name: Changed}')
    elif change=='attempts': db.execute('UPDATE applications SET attempts=2 WHERE id=1')
    elif change=='mode': db.lifecycle.fill_only=False
    elif change=='paused': db.set_setting('paused',True)
    elif change=='listing': db.execute("UPDATE jobs SET description=description || ' Changed listing' WHERE id=1")
    else:
        payload=json.loads(command.encode())
        if change=='limit': payload['parameters']['maximum_executions']=True
        else: payload['action']='fill-only-continuation'
        db.execute('UPDATE manual_commands SET command=? WHERE id=?',(json.dumps(payload),command.id))
    before=db.application(1)
    with pytest.raises(ValueError): claim(db,config,command.id,1)
    assert db.application(1)==before
    assert db.one('SELECT state FROM manual_commands')['state']=='PREPARED'


def test_authorization_is_one_shot_and_never_refunds_attempts(config,db,listing):
    qualified(config,db,listing)
    before=db.application(1)
    command=prepare(db,config,1,source='User authorization')
    assert command.action==ACTION and db.application(1)==before
    db.lifecycle.fill_only=True; db.set_setting('paused',False)
    claim(db,config,command.id,1)
    app=db.application(1)
    assert app['attempts']==1 and app['max_retries']==3
    assert app['retry_allowed']==app['manual_resume_allowed']==0
    db.lifecycle.begin_attempt(1,3)
    assert db.application(1)['attempts']==2
    with pytest.raises(ValueError): claim(db,config,command.id,1)
    assert db.one("SELECT status FROM questions WHERE field_key='resume'")['status']=='PENDING'
    assert not db.application(1)['submit_intent_at']


async def test_engine_rejects_ownership_failure_without_consuming(config,db,listing,monkeypatch):
    qualified(config,db,listing)
    command=prepare(db,config,1,source='User authorization')
    db.set_setting('paused',False)
    engine=Engine(config,db,fill_only=True)
    engine._process_one=AsyncMock()
    from autoapply.browser_resources import DedicatedResources
    def fail(self): raise RuntimeError('Unproven profile owner')
    monkeypatch.setattr(DedicatedResources,'inspect',fail)
    with pytest.raises(RuntimeError): await engine.process_one(1,recovery_id=command.id)
    engine._process_one.assert_not_awaited()
    assert db.application(1)['manual_action_required']==1
    assert db.one('SELECT state FROM manual_commands')['state']=='PREPARED'


def test_recovery_failure_cannot_requeue_itself(config,db,listing):
    from autoapply.checkpoint_recovery import finish
    qualified(config,db,listing)
    command=prepare(db,config,1,source='Explicit authority')
    db.lifecycle.fill_only=True; db.set_setting('paused',False)
    claim(db,config,command.id,1)
    db.lifecycle.begin_attempt(1,3)
    db.fail(1,'Observed pre-submit network failure',3)
    assert db.application(1)['retry_allowed']==1
    finish(db,command,'ACKNOWLEDGED')
    app=db.application(1)
    assert app['manual_action_required']==1 and app['retry_allowed']==0
    assert app['manual_resume_allowed']==0 and app['attempts']==2


@pytest.mark.parametrize('change',['resume','profile'])
def test_claimed_binding_stays_current_before_employer_io(config,db,listing,change):
    from autoapply.checkpoint_recovery import current_binding
    qualified(config,db,listing)
    command=prepare(db,config,1,source='Explicit authority')
    db.lifecycle.fill_only=True; db.set_setting('paused',False)
    claim(db,config,command.id,1)
    db.lifecycle.checkpoint_recovery_id=command.id
    if change=='resume': config.resume.write_bytes(b'%PDF-1.4\nchanged')
    else: (config.private/'profile.yaml').write_text('identity: {first_name: Changed}')
    with pytest.raises(ValueError): current_binding(db,config,1)
    assert db.application(1)['attempts']==1


@pytest.mark.parametrize('accepted',[False,True])
async def test_real_browser_recovery_fills_and_requires_fresh_resume_acceptance(config,db,listing,monkeypatch,accepted):
    qualified(config,db,listing,url='http://127.0.0.1:8123/recovery')
    # Local intercepted employer fixture; preserve binding to the canonical URL.
    from autoapply.browser import Browser
    from autoapply.checkpoint_recovery import prepare
    browser=Browser(config)
    command=prepare(db,config,1,source='User authorizes one controlled recovery')
    db.set_setting('paused',False)
    engine=Engine(config,db,browser=browser,fill_only=True,preserve_fill_session=True)
    html='''<h1>Software Engineering Intern Summer 2027</h1><p>Software engineering internship for undergraduate students in New York, NY.</p>
    <form><label>First Name<input name="first_name" required></label><label>Resume<input name="resume" type="file" required></label>
    <button type="submit">Submit application</button></form><script>window.finalClicks=0;
    document.querySelector('button').onclick=()=>window.finalClicks++;
    document.querySelector('form').onsubmit=e=>{e.preventDefault();fetch('/submit',{method:'POST'})}</script>'''
    original=browser._start
    async def start():
        await original()
        context=browser.context
        await context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        return context
    monkeypatch.setattr(browser,'_start',start)
    check=browser.uploads_ready
    async def uploads(page, controls):
        if accepted: return await check(page, controls)
        page._autoapply_uploads.result=dict(ready=False,category='UPLOAD_FAILED')
        return False
    monkeypatch.setattr(browser,'uploads_ready',uploads)
    try:
        await engine.process_one(1,recovery_id=command.id)
        page=engine.control.live_page(1)
        assert page is not None
        assert await page.locator('[name=first_name]').input_value()=='Test'
        assert await page.evaluate('window.finalClicks')==0
        fields=db.rows("SELECT * FROM events WHERE application_id=1 AND kind='LIVE_CONTROL_COMMITTED'")
        assert fields
        resume=db.one("SELECT * FROM questions WHERE application_id=1 AND field_key='resume'")
        # A historical control with a different identity cannot be resolved by another upload.
        assert resume['status']=='PENDING'
        result=db.setting('upload_result:1') or {}
        assert result.get('ready',False)==accepted
        assert db.application(1)['error_category'] != 'ENGINEERING_FAILURE'
        assert not db.application(1)['submit_intent_at']
        assert db.application(1)['attempts']==2
        assert db.one('SELECT state FROM manual_commands')['state']=='ACKNOWLEDGED'
        engine.fill_invariant.check()
    finally: await engine.close()
