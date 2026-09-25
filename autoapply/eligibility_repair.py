"""One incident repair; never a general terminal-state override."""
import hashlib
import json
from dataclasses import asdict

from .jobs import eligibility
from .models import now

REASON = 'Graduation year outside stated window; Graduation year outside stated window'
DESCRIPTION_SHA256 = '4b58ff6354c3628a78b930267cd612474ea23610c8ac35da4d74fe2fb2317952'


def repair_5310(db, profile, application_id=5310):
    """Recompute the pinned defective input, preserve evidence, and queue once.

    Caller must hold the worker lock. The normal worker then fetches and checks
    the current listing; this operation grants no eligibility override.
    Unknown events fail closed, including any form/upload/submit activity.
    """
    with db.transaction():
        if application_id != 5310:
            raise ValueError('Repair is restricted to application 5310')
        app = db.application(application_id)
        events = db.rows('SELECT * FROM events WHERE application_id=? ORDER BY id', (application_id,))
        if (app['status'] != 'INELIGIBLE' or app['application_state'] != 'INELIGIBLE'
                or app['failure_reason'] != REASON or app['stage'] != 'checking'
                or app['attempts'] != 1 or app['eligibility_override']
                or app['canonical_url'] != 'https://jobs.smartrecruiters.com/LLNL/3743990015289136'
                or hashlib.sha256(app['description'].encode()).hexdigest() != DESCRIPTION_SHA256
                or db.automation_retired(application_id)):
            raise ValueError('Incident identity/state does not match')
        if any(app[k] for k in ('submit_intent_at', 'submitted_at', 'resume_used', 'resume_sha256',
                'confirmation_text', 'confirmation_url', 'submission_confirmation_seen',
                'submission_confirmation_reason', 'url_before_submit', 'url_after_submit', 'session_preserved')):
            raise ValueError('Employer activity or live session evidence prohibits repair')
        if ([e['kind'] for e in events] != ['discovered', 'CHECKING', 'INELIGIBLE', 'hold']
                or any(e['detail'] != REASON for e in events[2:])
                or db.one('SELECT id FROM questions WHERE application_id=?', (application_id,))):
            raise ValueError('Unexpected activity prohibits repair')
        old_check = json.loads(app['eligibility_json'])
        if old_check['eligible'] is not False or old_check['reasons'] != REASON.split('; '):
            raise ValueError('Recorded parser defect does not match')
        corrected = asdict(eligibility(app, profile))
        if corrected['eligible'] is False:
            raise ValueError('Corrected parser still finds ineligibility')
        evidence = dict(application_id=application_id, old_classification='INELIGIBLE',
                        defect_category='DEGREE_PROGRAM_YEAR_AS_GRADUATION_DEADLINE',
                        old_parser_result=old_check, corrected_parser_result=corrected,
                        repair_timestamp=now(), description_sha256=DESCRIPTION_SHA256,
                        prior_event_ids=[e['id'] for e in events], employer_activity_seen=False)
        db.event(application_id, 'ELIGIBILITY_REPAIR_APPLIED', json.dumps(evidence))
        db.execute("UPDATE applications SET status='QUEUED',application_state='DISCOVERED',"
                   "retry_allowed=1,failure_reason='',updated_at=? WHERE id=?", (now(), application_id))
        db.execute("UPDATE jobs SET status='QUEUED',reason='' WHERE id=?", (app['job_id'],))
    return evidence
