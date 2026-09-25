"""Read-only production isolation and submission audit for the bounded run."""
import json
import sqlite3

from controlled_once import ROOT, Config, snapshot

config = Config(ROOT)
folder = config.private / 'smartrecruiters-5310'
before = json.loads((folder / 'before.json').read_text(encoding='utf-8'))
after = snapshot(config)
with sqlite3.connect(f'file:{(config.private / "autoapply.sqlite3").as_posix()}?mode=ro', uri=True) as db:
    db.row_factory = sqlite3.Row
    app = dict(db.execute('SELECT * FROM applications WHERE id=5310').fetchone())
    kinds = {r['kind']: r['n'] for r in db.execute(
        'SELECT kind,count(*) AS n FROM events WHERE application_id=5310 GROUP BY kind')}
    ready = db.execute("SELECT value FROM settings WHERE key='upload_result:5310'").fetchone()
    session = db.execute("SELECT value FROM settings WHERE key='manual_session:5310'").fetchone()
changed = [key for key, value in before['applications'].items() if after['applications'].get(key) != value]
result = {
    'application_id':5310,
    'changed_applications':changed,
    'protected':{key:after['applications'][key] == before['applications'][key] for key in ('6401','6415','6416')},
    'unrelated_applications_changed':sum(key != '5310' for key in changed),
    'changed_prior_submitted_history':[key for key,value in before['submitted_history'].items() if after['submitted_history'].get(key)!=value],
    'state':{key:app[key] for key in ('status','security_state','security_provider','error_category','session_preserved',
                                    'manual_resume_allowed','submit_intent_at','submission_confirmation_seen','failure_reason')},
    'upload':json.loads(ready['value']) if ready else None,
    'manual_session':{key:value for key,value in json.loads(session['value']).items() if key != 'token'} if session and session['value']!='null' else None,
    'final_submit_calls':kinds.get('SUBMIT_CLICK_CALL_EXECUTED',0),
    'final_submit_clicks_observed':kinds.get('SUBMIT_CLICK_DISPATCHED',0),
    'submission_requests':kinds.get('SUBMIT_NETWORK_REQUEST_OBSERVED',0),
    'step_next_clicks':kinds.get('STEP_NEXT_CLICK_DELIVERED',0),
    'step_transitions':kinds.get('STEP_TRANSITION_OBSERVED',0),
    'resume_selections':kinds.get('UPLOAD_FILE_SELECTED',0),
}
(folder / 'audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
