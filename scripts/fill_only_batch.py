"""Sequential fill-only runner. Discovery never touches the browser."""
import asyncio
import argparse
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from autoapply.config import Config, setup_logging
from autoapply.database import Database
from autoapply.engine import Engine
from autoapply.discord_bot import DiscordBot, chunks
from autoapply.runtime import ProcessLock
from autoapply.models import now
from autoapply.submission_probe import SubmissionProbe
from autoapply.fill_batch import EXCLUDED, MAX_ACTIVE_APPLICATION_TABS, BatchReport, discover, verify_safety, BatchPolicy, finish_preparation
from autoapply.jobs import eligibility
from autoapply.batch_approval import ApprovedDestinations

class FillDatabase(Database):
    def claim(self, application_id=None):
        if application_id is None or application_id in EXCLUDED:
            raise RuntimeError('Fill-only requires one explicitly selected, unprotected application')
        return super().claim(application_id)
    def update_security(self, app_id, **fields):
        if app_id in EXCLUDED:
            raise RuntimeError('Protected application excluded')
        return super().update_security(app_id, **fields)
    def set_setting(self,key,value):
        if key=='auto_submit' and value is not False:
            raise RuntimeError('Fill-only forbids enabling auto_submit')
        return super().set_setting(key,value)
    def transition(self,app_id,status,reason='',**fields):
        if str(status) in {'SUBMITTING','SUBMITTED'} or fields.get('submit_intent_at'):
            raise RuntimeError('Fill-only forbids submission intent')
        if app_id in EXCLUDED:
            raise RuntimeError('Protected application excluded')
        return super().transition(app_id,status,reason,**fields)

async def forbidden_submit(*args,**kwargs):
    raise RuntimeError('Final submission disabled for entire fill-only process')

class BatchBot(DiscordBot):
    async def setup_hook(self):
        pass
    async def on_message(self,message):
        # This process cannot dispatch submit, verification, or unrelated commands.
        if not self.authorized(message.author) or message.guild is not None:
            return
        parts=message.content.lstrip('!/').split(maxsplit=2)
        if len(parts)!=3 or parts[0]!='answer': return
        try:
            q=self.controller.db.one('SELECT application_id FROM questions WHERE id=?',(int(parts[1]),))
            if not q or q['application_id'] not in self.batch_ids: return
            result=self.controller.answer(int(parts[1]),parts[2])
            await message.channel.send(str(result or 'Answer saved.'))
        except ValueError as exc:
            await message.channel.send(str(exc))

async def run(config,db,report, candidate_ids=None, approval=None, *, policy=None):
    policy = policy or BatchPolicy()
    verify_safety(db)
    if approval is None:
        raise RuntimeError('Destination-specific approval evidence required')
    engine=Engine(config,db,fill_only=True)
    engine.browser.network_policy=approval
    ids=[]
    retired_path=config.private/'failed-batch-retirement.json'
    retired_ids={entry['application']['id'] for entry in json.loads(retired_path.read_text(encoding='utf-8'))['applications']} if retired_path.exists() else set()
    baseline={i:db.application(i) for i in EXCLUDED}
    bot=BatchBot(engine.control); bot.batch_ids=ids
    connection=None
    def save(result):
        rows=[]
        for i in ids:
            a=db.application(i)
            row = {k:a[k] for k in ('id','company','title','ats','status','application_state','failure_reason','error_category','canonical_url','current_url','resume_sha256','eligibility_json')}
            row['snapshot'] = engine.control.status_snapshot(i)
            rows.append(row)
        report.save(result=result,applications=rows,
            ready_count=sum(a['snapshot']['readiness']=='LIVE_READY_FOR_MANUAL_SUBMIT' for a in rows),
            prepared_count=sum(a['snapshot']['checkpoint'].get('readiness') in {'RECONSTRUCTABLE_CHECKPOINT','RECONSTRUCTION_VERIFIED'} for a in rows),
            reconstruction_verified_count=sum(a['snapshot']['checkpoint'].get('readiness')=='RECONSTRUCTION_VERIFIED' for a in rows),
            **engine.fill_invariant.snapshot(),
            applications_opened=engine.browser.application_tabs_opened,
            applications_filled=sum(bool(db.one("SELECT id FROM events WHERE application_id=? AND kind='LIVE_CONTROL_COMMITTED' AND created_at>=?",(i,report.data['started_at']))) for i in ids),
            skipped_ineligible=sum(a['status']=='INELIGIBLE' for a in rows),skipped_closed=sum(a['status']=='CLOSED' for a in rows),
            blocked_questions=sum(bool(db.one("SELECT id FROM questions WHERE application_id=? AND status='PENDING'",(i,))) for i in ids),
            active_tab_count=sum(not p.is_closed() for p in engine.browser.leases),
            unexpected_popup_violations=engine.browser.unexpected_popup_violations,
            max_active_tabs_observed=engine.browser.max_tabs_observed,auto_submit=db.setting('auto_submit'),
            browser_pid=engine.browser.browser_pid,
            blocked_network_requests=approval.blocked,final_submission_requests_blocked=approval.final_requests_blocked,
            protected_records_unchanged=all(db.application(i)==a for i,a in baseline.items()))
    try:
        connection=asyncio.create_task(bot.start(os.environ['DISCORD_BOT_TOKEN']))
        await bot.wait_for_delivery(connection)
        user=await bot.fetch_user(bot.owner_id)
        db.set_setting('paused',False)
        while report.data.get('prepared_count', 0)<policy.target_count:
            verify_safety(db)
            engine.fill_invariant.check()
            engine.browser.check_tab_limit()
            if db.setting('paused') or engine.stop_event.is_set():
                save('GLOBAL_WORKER_PAUSED'); break
            candidates=discover(db,config,set(ids)|retired_ids)
            if candidate_ids is not None:
                candidates=[i for i in candidates if i in candidate_ids]
            if not candidates:
                save('QUEUE_EXHAUSTED'); break
            app_id=candidates[0]; ids.append(app_id)
            report.save(current_application_id=app_id,listings_considered=len(ids))
            app=db.application(app_id)
            approval.select(app)
            # Shared preflight preserves sparse candidates for live enrichment.
            from autoapply.candidate_policy import preflight
            decision = preflight(db, config, app, mode='fill_only', historical_exclusions=EXCLUDED)
            if not decision.proceed:
                save('PROCESSING'); continue
            print(json.dumps({'selected':app_id,'company':app['company'],'role':app['title']}),flush=True)
            await engine.process_one(app_id)
            engine.browser.check_tab_limit()
            engine.fill_invariant.check()
            app=db.application(app_id)
            if app['submit_intent_at']: raise RuntimeError('UNEXPECTED_SUBMISSION_INTENT')
            save('PROCESSING')
            print(json.dumps({'id':app_id,'state':app['application_state'],'reason':app['failure_reason']}),flush=True)
            pending=db.rows("SELECT * FROM questions WHERE application_id=? AND status='PENDING'",(app_id,))
            if pending:
                message=f"AutoApply needs input — #{app_id} {app['company']} {app['title']}\n"
                for q in pending:
                    message+=f"\nQuestion #{q['id']}: {q['raw_question']}\nOptions: {q['options']}\nReason: No verified semantically equivalent answer is available.\nReply: !answer {q['id']} <answer>\n"
                for piece in chunks(message): await user.send(piece)
                db.event(app_id,'FILL_ONLY_DISCORD_INPUT_SENT','Exact unknown questions sent to configured owner')
            if app['application_state']=='READY_FOR_MANUAL_SUBMIT':
                await finish_preparation(engine,app_id,verify=policy.verify_reconstruction)
                engine.fill_invariant.check()
                report.save(smoke_test='PASSED')
                save('PROCESSING')
                continue
            if app['error_category'] in {'EXTERNAL_EXECUTION_APPROVAL_REQUIRED','EXECUTION_APPROVAL_BLOCKED'}:
                if report.data['smoke_test']=='NOT_STARTED': report.save(smoke_test='FAILED')
                save('GLOBAL_EXECUTION_BLOCKER'); break
            if report.data['smoke_test']=='NOT_STARTED':
                report.save(smoke_test='FAILED')
                save('SMOKE_TEST_FAILED'); break
        else:
            save('TARGET_CHECKPOINTS_PREPARED')
    except Exception as exc:
        save('GLOBAL_FAILURE: '+str(exc))
        raise
    finally:
        db.set_setting('paused',True)
        await bot.close()
        if connection:
            connection.cancel()
            await asyncio.gather(connection,return_exceptions=True)
        # Closing a ready form invalidates live readiness unless reconstruction was verified.
        for i in engine.retained_pages:
            if db.application(i)['application_state']=='READY_FOR_MANUAL_SUBMIT':
                db.update_security(i,application_state='MANUAL_REQUIRED',session_preserved=0,
                    manual_action_required=1,manual_action_reason='Saved filled checkpoint; live session ended, reconstruction needs verification')
        await engine.close()
        save(report.data['result'])
        report.save(current_application_id=None,worker_finished_at=now())

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-reconstruction',action='store_true',help='Explicitly replay each prepared checkpoint once')
    parser.add_argument('--target-count',type=int,default=5)
    parser.add_argument('--candidate-ids',type=int,nargs='+',help='Restrict this run to explicitly approved employer destinations')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    config=Config(ROOT)
    config.data['application'].update(auto_submit=False,delay_seconds=0)
    config.data['ai']['enabled']=False
    approval=ApprovedDestinations(config.private/'fill-only-network-approval.json')
    if args.candidate_ids is None:
        args.candidate_ids=list(approval.destinations)
    if not set(args.candidate_ids)<=approval.destinations.keys():
        raise ValueError('Candidate IDs exceed explicitly approved destinations')
    approval.record(config.private/'fill-only-explicit-approval.json',os.environ['DISCORD_USER_ID'])
    setup_logging(config)
    report=None
    try:
        with ProcessLock(config.private/'worker.lock'):
            db=FillDatabase(config.private/'autoapply.sqlite3',config['jobs']['max_listing_age_days'],startup_maintenance=False)
            report=BatchReport(config.private/'fill-only-batch-result.json')
            try:
                db.set_setting('auto_submit',False)
                verify_safety(db)
                report.save(result='PREFLIGHT_PASSED')
                report.save(approval_evidence='data/private/fill-only-explicit-approval.json',approval_sha256=approval.source_sha256)
                asyncio.run(run(config,db,report,args.candidate_ids,approval,policy=BatchPolicy(args.target_count,args.verify_reconstruction)))
            finally:
                db.close()
    finally:
        if report:
            report.save(lock_released=True)
