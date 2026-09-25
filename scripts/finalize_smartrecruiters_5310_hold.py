"""Record the observed restriction and verified missing worker; never open a browser."""
import json

from controlled_once import ROOT, Config, Database, ProcessLock
from autoapply.models import State
from autoapply.archive import archive_application

config=Config(ROOT)
folder=config.private/'smartrecruiters-5310'
command=folder/'command.json'
if command.exists():
    command.rename(folder/'unacknowledged-command.cancelled.json')
with ProcessLock(config.private/'worker.lock'):
    db=Database(config.private/'autoapply.sqlite3',startup_maintenance=False)
    try:
        app=db.application(5310)
        if app['submit_intent_at'] or app['submission_confirmation_seen']:
            raise RuntimeError('Unexpected submission evidence; do not overwrite')
        reason='DataDome access restriction observed; reconstructed worker/session unavailable. No further reconstruction or submission performed.'
        with db.transaction():
            db.set_setting('paused',True)
            db.set_setting('manual_session:5310',None)
            db.set_setting('inspect_only_once:5310',False)
            db.set_setting('refresh_upload_protocol:5310',False)
            db.transition(5310,State.MANUAL_REVIEW,reason,stage='security hold; session unavailable')
            db.update_security(5310,security_state='UNKNOWN_SECURITY_FAILURE',security_provider='DataDome',
                               error_category='UNKNOWN_SECURITY_FAILURE',application_state='MANUAL_REQUIRED',
                               manual_action_required=1,manual_action_reason=reason,session_preserved=0,
                               manual_resume_allowed=0,retry_allowed=0,last_http_status=None,screenshot_path=None,
                               last_security_message='Access is temporarily restricted. We detected unusual activity from your device or network.')
            db.event(5310,'SMARTRECRUITERS_SECURITY_HOLD',json.dumps({
                'worker_observed':'UNKNOWN_SECURITY_FAILURE','provider':'DataDome','user_confirmed_restriction':True,
                'worker_and_browser_alive':False,'reconstruction_count':1,'further_reconstruction':False,
                'resume_selections':0,'next_clicks':0,'submit_calls':0,'confirmation':False}))
            db.set_setting('application_transaction:5310',{'stage':'MANUAL_INTERVENTION_REQUIRED',
                'reason':'ACCESS_RESTRICTED_SESSION_UNAVAILABLE','step':0,'next_clicks':0,'upload_ready':False})
        archive_application(config,db,5310)
        print(json.dumps({'application_id':5310,'state':'SECURITY_HOLD_SESSION_UNAVAILABLE','submit_clicks':0}),flush=True)
    finally:
        db.close()
