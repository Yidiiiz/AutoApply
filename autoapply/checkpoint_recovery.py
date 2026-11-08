"""One-shot recovery of the qualified Country inventory failure, before filling.

Saved progress is evidence, never implicit permission to retry. This operation
uses the ordinary remaining attempt budget and cannot use startup continuations.
"""
import hashlib
import json

from .manual import ManualCommand, ManualCommands
from .models import now

ACTION = 'checkpoint-adapter-recovery'
REASON = 'Saved answer is not an available exact dropdown option: Country'
PROTECTED = frozenset({3556, 4522, 6397})
INTERACTIONS = frozenset({'LIVE_CONTROL_COMMITTED', 'STEP_FILLED', 'UPLOAD_SELECTED',
    'UPLOAD_READY', 'ATS_UPLOAD_READY', 'UPLOAD_READINESS', 'STEP_NEXT_INTENT',
    'STEP_NEXT_CLICK_DELIVERED', 'STEP_NEXT_FAILURE', 'STEP_TRANSITION_OBSERVED',
    'STEP_ADVANCED', 'SUBMITTING', 'APPLICATION_EXCEPTION'})


def evidence(db, config, app_id):
    from .candidate_policy import preflight
    from .control import Controller
    from .fill_batch import EXCLUDED, verify_safety
    from .freshness import freshness_state
    from .operations import FileSnapshot
    verify_safety(db)
    app = db.application(app_id)
    if app_id in EXCLUDED | PROTECTED:
        raise ValueError('Protected application')
    if (app['status'] != 'MANUAL_REVIEW' or not app['manual_action_required']
            or app['error_category'] != 'ADAPTER_CAPABILITY_FAILURE'
            or app['manual_action_reason'] != REASON or app['failure_reason'] != REASON
            or app['stage'] != 'form page 1' or app['retry_allowed']
            or app['submit_intent_at'] or app['submission_confirmation_seen']
            or app['submitted_at'] or app['confirmation_text']
            or app['delivery_state'] != 'NOT_STARTED'
            or app['security_state'] not in {'NONE', 'PASSIVE_PROTECTION_DETECTED'}
            or app['verification_state'] != 'NOT_REQUIRED'
            or app['session_preserved'] or db.setting(f'manual_session:{app_id}')
            or not app['attempt_started'] or app['attempts'] < 1
            or app['attempts'] > min(app['max_retries'], config['processing']['max_retries'])
            or app['resume_sha256'] or app['resume_used']):
        raise ValueError('Hold is not an eligible pre-input adapter failure')
    if (app['listing_status'] != 'ACTIVE' or not app['listing_active']
            or freshness_state(app['posted_at'], config['jobs']['max_listing_age_days']) != 'FRESH'
            or not preflight(db, config, app, mode='fill_only',
                             historical_exclusions=EXCLUDED | PROTECTED).proceed):
        raise ValueError('Listing/history preflight rejected recovery')
    config.profile_snapshot(reload=True)
    document = FileSnapshot.read(config.resume, force=True)
    if not document.is_pdf or Controller(config, db).pending_input_blocker(app_id):
        raise ValueError('Canonical resume or personal answers unavailable')
    path = config.private / 'fill-only-checkpoints' / f'{app_id}.json'
    raw = path.read_bytes()
    saved = json.loads(raw)
    summary = db.setting(f'fill_checkpoint:{app_id}')
    if (saved.get('application_id') != app_id or saved.get('url') != app['canonical_url']
            or saved.get('schema_version') != 2 or saved.get('blocker') != REASON
            or saved.get('state') != 'MANUAL_REQUIRED'
            or saved.get('progress_kind') != 'UNFILLED' or saved.get('committed_field_count') != 0
            or saved.get('readiness') != 'SAVED_PROGRESS_CHECKPOINT'
            or saved.get('form_filled') is not False or saved.get('upload_ready') is not False
            or saved.get('upload') or saved.get('resume_sha256')
            or saved.get('reconstruction_verified') is not False
            or not summary or any(summary.get(k) != saved.get(k) for k in (
                'schema_version', 'progress_kind', 'committed_field_count', 'readiness',
                'reconstruction_verified', 'resume_sha256', 'form_filled', 'upload_ready'))):
        raise ValueError('Checkpoint is absent, inconsistent, or populated')
    if any(f.get('files') for frame in saved.get('frames', []) for f in frame.get('fields', [])):
        raise ValueError('Checkpoint has document selections')
    for frame in saved.get('frames', []):
        for field in frame.get('fields', []):
            # Country's inspection search text is not an applicant-field commit.
            # Unchecked checkbox/radio values are their fixed HTML values.
            if (field.get('checked') or field.get('selected')
                    or field.get('value') and field.get('type') not in {'checkbox', 'radio'}
                    and not (field.get('id') == 'country'
                             and field.get('value') in {'United States', 'United States +1'})):
                raise ValueError('Checkpoint contains applicant input')
    events = db.rows('SELECT * FROM events WHERE application_id=? ORDER BY id', (app_id,))
    starts = [e for e in events if e['kind'] == 'APPLICATION_ATTEMPT_STARTED']
    if not starts or json.loads(starts[-1]['detail']).get('application_attempt_count') != app['attempts']:
        raise ValueError('Attempt evidence missing or changed')
    recent = [e for e in events if e['id'] > starts[-1]['id']]
    holds = [e for e in recent if e['kind'] == 'MANUAL_REVIEW' and e['detail'] == REASON]
    checks = [e for e in recent if e['kind'] == 'FILL_ONLY_CHECKPOINT']
    readiness = [e for e in recent if e['kind'] == 'BROWSER_TRANSITION_READINESS'
                 and json.loads(e['detail']).get('interaction_at') is None]
    if (not holds or not checks or not readiness
            or not readiness[-1]['id'] < holds[-1]['id'] < checks[-1]['id']
            or any(e['kind'] in INTERACTIONS for e in recent)):
        raise ValueError('Checkpoint/event evidence does not prove pre-input failure')
    checkpoint_event = json.loads(checks[-1]['detail'])
    if (checkpoint_event.get('path') != str(path)
            or checkpoint_event.get('state') != saved['state']
            or checkpoint_event.get('readiness') != saved['readiness']):
        raise ValueError('Checkpoint event identity mismatch')
    return dict(application_id=app_id, attempts=app['attempts'], max_retries=app['max_retries'],
        configured_max_retries=config['processing']['max_retries'], canonical_url=app['canonical_url'],
        job_id=app['job_id'], posted_at=app['posted_at'], ats=app['ats'], location=app['location'],
        listing_sha256=hashlib.sha256(app['description'].encode()).hexdigest(),
        checkpoint_sha256=hashlib.sha256(raw).hexdigest(), checkpoint_event_id=checks[-1]['id'],
        attempt_event_id=starts[-1]['id'], hold_event_id=holds[-1]['id'],
        event_sha256=hashlib.sha256(json.dumps(recent, sort_keys=True).encode()).hexdigest(),
        resume_path=str(config.resume.resolve()), resume_sha256=document.sha256,
        profile_revision=config.profile_snapshot().revision)


def prepare(db, config, app_id, *, source):
    if not source.strip():
        raise ValueError('Explicit recovery authorization required')
    with db.lifecycle.atomic():
        binding = evidence(db, config, app_id)
        if db.rows("SELECT id FROM manual_commands WHERE application_id=? AND state IN ('PREPARED','PENDING','IN_PROGRESS')", (app_id,)):
            raise ValueError('Outstanding command exists')
        command = ManualCommand(ACTION, app_id, parameters=dict(binding=binding,
            source=source, auto_submit=False, maximum_executions=1))
        db.execute('INSERT INTO manual_commands VALUES (?,?,?,?,?,?,?)',
                   (command.id, app_id, command.encode(), 'PREPARED', now(), now(), None))
        # Keep the bound attempt's event history immutable until claim.
    return command


def claim(db, config, command_id, app_id):
    """Called only after fresh OS ownership inspection by the fill-only Engine."""
    with db.lifecycle.atomic():
        row = db.one('SELECT * FROM manual_commands WHERE id=?', (command_id,))
        if not row or row['application_id'] != app_id or row['state'] != 'PREPARED':
            raise ValueError('Recovery unavailable or already consumed')
        command = ManualCommand.decode(app_id, row['command'])
        p = command.parameters
        if (not db.lifecycle.fill_only or db.setting('paused', True)
                or command.action != ACTION or command.session_token is not None
                or p.get('auto_submit') is not False or type(p.get('maximum_executions')) is not int
                or p['maximum_executions'] != 1 or not str(p.get('source') or '').strip()
                or evidence(db, config, app_id) != p.get('binding')):
            raise ValueError('Recovery constraints or evidence changed')
        db.execute("UPDATE manual_commands SET state='IN_PROGRESS',updated_at=? WHERE id=? AND state='PREPARED'", (now(), command_id))
        db.event(app_id, 'CHECKPOINT_RECOVERY_CLAIMED', command.encode())
        db.lifecycle.transition(app_id, 'RETRY', 'Qualified checkpoint-bound pre-input adapter recovery',
            attempt_started=0, manual_action_required=0, manual_action_reason='',
            manual_resume_allowed=0, retry_allowed=0, retry_at=None, stage='checking')
        db.lifecycle.transition(app_id, 'CHECKING', 'Fresh recovery reservation; ordinary attempt budget', retry_allowed=0)
    return command


def finish(db, command, outcome):
    app = db.application(command.application_id)
    if app['retry_allowed']:
        db.lifecycle.record_hold(app['id'], 'Checkpoint recovery ended; fresh review required',
            app['error_category'] or 'CHECKPOINT_RECOVERY_STOPPED', manual_resume_allowed=0)
        app = db.application(command.application_id)
    ManualCommands(db).finish(command, outcome, json.dumps(dict(status=app['status'],
        attempts=app['attempts'], replay_permitted=False)))


def current_binding(db, config, app_id):
    """Recheck document/profile binding after startup, before employer I/O."""
    from .operations import FileSnapshot
    command_id = getattr(db.lifecycle, 'checkpoint_recovery_id', None)
    if not command_id:
        return None
    row = db.one("SELECT * FROM manual_commands WHERE id=? AND application_id=? AND state='IN_PROGRESS'", (command_id, app_id))
    if not row:
        raise ValueError('Recovery claim unavailable')
    binding = ManualCommand.decode(app_id, row['command']).parameters['binding']
    document = FileSnapshot.read(config.resume, force=True)
    if (not document.is_pdf or document.sha256 != binding['resume_sha256']
            or str(config.resume.resolve()) != binding['resume_path']
            or config.profile_snapshot(reload=True).revision != binding['profile_revision']):
        raise ValueError('Canonical recovery document/profile changed')
    return binding
