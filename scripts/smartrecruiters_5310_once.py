"""Single authorized reconstruction, inspect hold, then normal Engine continuation.

Commands are explicit local files, not executable code. No queue selection.
"""
import asyncio
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controlled_once import snapshot
from autoapply.browser import Browser
from autoapply.config import Config, setup_logging
from autoapply.database import Database
from autoapply.runtime import ProcessLock
from autoapply.security import SecurityDetector

AUDIT = ROOT/'data/private/smartrecruiters-5310'


def guard(db):
    app=db.application(5310)
    if app['submit_intent_at'] or app['submission_confirmation_seen'] or db.automation_retired(5310):
        raise RuntimeError('Submission guard: reconstruction forbidden')
    if db.one("SELECT id FROM events WHERE application_id=5310 AND kind IN ('SUBMIT_INTENT','SUBMIT_CLICKED','SUBMISSION_REQUEST','SUBMIT_INTENT_CREATED','SUBMIT_CLICK_CALL_EXECUTED','SUBMIT_MOUSE_DOWN','SUBMIT_CLICK_DISPATCHED','SUBMIT_NETWORK_REQUEST_OBSERVED')"):
        raise RuntimeError('Submission telemetry exists; inspect only')
    return app


async def inspect(page):
    from autoapply.applications import FIELD_SCRIPT
    from autoapply.smartrecruiters import METADATA
    fields=[]
    for f in await page.locator('input:not([type=hidden]),textarea,select,button,[role=combobox]:not(input)').all():
        item=await f.evaluate(METADATA)
        item.pop('value',None)
        item['visible']=await f.is_visible()
        item['ancestors']=await f.evaluate("e=>{const a=[];for(let n=e.parentElement;n&&a.length<6;n=n.parentElement)a.push(n.tagName+(n.id?'#'+n.id:''));return a}")
        if await f.get_attribute('type')=='hidden':
            continue
        fields.append(item)
    result={'fields':fields,'generic_extracted':len(await page.evaluate(FIELD_SCRIPT)),
            'form_roots':await page.locator('form').evaluate_all("es=>es.map(e=>({id:e.id,classes:e.className,controls:e.querySelectorAll('input,textarea,select').length}))")}
    (AUDIT/'structure.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'inspection':'complete','fields':len(fields),'generic_extracted':result['generic_extracted']}),flush=True)


async def run(config,db,baseline):
    browser=Browser(config)
    engine=None
    page=await browser.new_page()
    db.set_setting('controlled_application_id',5310)
    db.set_setting('paused',True)
    db.set_setting('auto_submit',True)
    try:
        app=guard(db)
        await browser.navigate(page,app['canonical_url'])
        from autoapply.applications import adapter_for
        adapter=adapter_for(page)
        await adapter.inspect()
        security,_=await SecurityDetector().detect(page,browser.observation(page))
        if not security.blocking:
            await adapter.begin()
        security,_=await SecurityDetector().detect(page,browser.observation(page))
        if not security.blocking:
            await inspect(page)
        print(json.dumps({'security':security.state,'provider':security.provider,'stage':'inspection hold'}),flush=True)
        while True:
            command_path=AUDIT/'command.json'
            if command_path.exists():
                command=json.loads(command_path.read_text())
                command_path.unlink()
                if command.get('action')=='inspect':
                    await inspect(page)
                elif command.get('action')=='continue':
                    guard(db)
                    for name in ('autoapply.field_mapping','autoapply.applications','autoapply.smartrecruiters','autoapply.answers','autoapply.engine'):
                        if name in sys.modules:
                            importlib.reload(sys.modules[name])
                    from autoapply.engine import Engine
                    engine=Engine(config,db,browser)
                    engine.handoff.pages[5310]=page
                    await engine.handoff.request(5310,page,'Reconstructed once; adapter ready for normal revalidation','INPUT_REQUIRED')
                    db.set_setting('paused',False)
                    await engine.resume_manual(5310,strict_session=True,session_token=engine.handoff.sessions[5310])
                    print(json.dumps({'status':db.application(5310)['status'],'reason':db.application(5310)['failure_reason']}),flush=True)
                    await engine.wait_for_manual()
                    break
            await asyncio.sleep(.5)
    finally:
        after=snapshot(config)
        result={'changed_applications':[k for k,v in baseline['applications'].items() if after['applications'].get(k)!=v],
                'changed_submitted_history':[k for k,v in baseline['submitted_history'].items() if after['submitted_history'].get(k)!=v]}
        (AUDIT/'isolation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result),flush=True)
        if engine:
            await engine.close()
        else:
            await browser.close()


def main():
    config=Config(ROOT)
    if config['browser']['headless']:
        raise RuntimeError('Headed browser required')
    setup_logging(config)
    AUDIT.mkdir(exist_ok=True)
    with ProcessLock(config.private/'worker.lock'):
        db=Database(config.private/'autoapply.sqlite3',startup_maintenance=False)
        try:
            guard(db)
            marker=AUDIT/'reconstruction.json'
            if marker.exists():
                raise RuntimeError('One reconstruction already used; preserve existing session')
            baseline=snapshot(config)
            (AUDIT/'before.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
            marker.write_text(json.dumps({'application_id':5310,'count':1,'submit_intent':False}),encoding='utf-8')
            asyncio.run(run(config,db,baseline))
        finally:
            db.close()

if __name__=='__main__':
    main()
