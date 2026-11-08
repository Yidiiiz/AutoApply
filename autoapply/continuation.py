"""Explicit, one-shot fill-only continuations using the durable manual ledger.

Preparation never queues work. Claim retires the authorization before startup;
even a zero-page startup failure requires a new human authorization to try again.
"""
import json

from .manual import ManualCommand, ManualCommands
from .models import now
from .retry import Delivery

ACTION = 'fill-only-continuation'


def safety(db, config, app_id):
    from .candidate_policy import preflight
    from .fill_batch import EXCLUDED, verify_safety
    verify_safety(db)
    app = db.application(app_id)
    if not app or app_id in EXCLUDED:
        raise ValueError('Protected or unknown application')
    if (app['security_state'] not in {'NONE', 'PASSIVE_PROTECTION_DETECTED'}
            or app['verification_state'] != 'NOT_REQUIRED'
            or app['submit_intent_at'] or app['submission_confirmation_seen']
            or app['delivery_state'] != Delivery.NOT_STARTED
            or app['session_preserved'] or db.setting(f'manual_session:{app_id}')
            or app['status'] != 'MANUAL_REVIEW' or not app['manual_action_required']
            or app['error_category'] != 'BROWSER_START_FAILED'
            or app['retry_allowed']):
        raise ValueError('Only a proven zero-delivery browser-startup hold may be continued')
    decision = preflight(db, config, app, mode='fill_only', historical_exclusions=EXCLUDED)
    if not decision.proceed:
        raise ValueError('Candidate preflight: ' + decision.reason)
    config.profile_snapshot(reload=True)  # Canonical resume/profile binding remains mandatory.
    return app


def prepare(db, config, app_id, *, source):
    """Audit user authority without changing application/history rows or retry policy."""
    from .operations import FileSnapshot
    if not source.strip():
        raise ValueError('Explicit user authorization source required')
    with db.lifecycle.atomic():
        app = safety(db, config, app_id)
        if db.rows("SELECT id FROM manual_commands WHERE application_id=? AND state IN ('PREPARED','IN_PROGRESS')", (app_id,)):
            raise ValueError('An outstanding authorization already exists')
        command = ManualCommand(ACTION, app_id, parameters=dict(
            mode='fill-only', maximum_executions=1, auto_submit=False,
            historical_attempts=app['attempts'], max_retries=app['max_retries'],
            hold_reason=app['manual_action_reason'], canonical_url=app['canonical_url'],
            resume_sha256=FileSnapshot.read(config.resume, force=True).sha256,
            source=source, consume_at='explicit-command-claim-before-browser-startup'))
        db.execute('INSERT INTO manual_commands VALUES (?,?,?,?,?,?,?)',
                   (command.id, app_id, command.encode(), 'PREPARED', now(), now(), None))
        db.event(app_id, 'FILL_ONLY_CONTINUATION_PREPARED', command.encode())
    return command


def claim(db, config, command_id):
    from .operations import FileSnapshot
    with db.lifecycle.atomic():
        row = db.one('SELECT * FROM manual_commands WHERE id=?', (command_id,))
        if not row or row['state'] != 'PREPARED':
            raise ValueError('Authorization unavailable or already consumed')
        command = ManualCommand.decode(row['application_id'], row['command'])
        app = safety(db, config, command.application_id)
        p = command.parameters
        if (command.action != ACTION or p.get('mode') != 'fill-only'
                or type(p.get('maximum_executions')) is not int or p['maximum_executions'] != 1
                or p.get('auto_submit') is not False or command.session_token is not None
                or p.get('historical_attempts') != app['attempts']
                or p.get('max_retries') != app['max_retries']
                or p.get('hold_reason') != app['manual_action_reason']
                or p.get('canonical_url') != app['canonical_url']
                or p.get('resume_sha256') != FileSnapshot.read(config.resume, force=True).sha256):
            raise ValueError('Authorization constraints or application binding changed')
        if db.setting('paused', False):
            raise ValueError('Explicit execution must unpause under the worker lock')
        changed = db.execute("UPDATE manual_commands SET state='IN_PROGRESS',updated_at=? WHERE id=? AND state='PREPARED'", (now(), command.id))
        if not changed.rowcount:
            raise ValueError('Authorization already consumed')
        db.set_setting(f'manual_ack:{command.id}', 'IN_PROGRESS')
        db.event(app['id'], 'manual_command_claimed', command.id)
        # A fresh reservation resets the per-execution flag, never the count/events.
        db.lifecycle.transition(app['id'], 'RETRY', 'Explicit one-time fill-only continuation reservation',
            attempt_started=0, manual_action_required=0, manual_action_reason='',
            retry_allowed=0, retry_at=None, manual_resume_allowed=0, stage='checking')
        db.lifecycle.transition(app['id'], 'CHECKING', 'Explicit one-time fill-only continuation', retry_allowed=0)
    return command


def budget_authorized(lifecycle, app_id):
    """Only an explicit Engine invocation binds authority; queue work cannot use it."""
    command_id = getattr(lifecycle, 'continuation_id', None)
    if not lifecycle.fill_only or not command_id or lifecycle.db.setting('auto_submit') is not False:
        return False
    row = lifecycle.db.one("SELECT * FROM manual_commands WHERE id=? AND application_id=? AND state='IN_PROGRESS'", (command_id, app_id))
    if not row:
        return False
    command = ManualCommand.decode(app_id, row['command'])
    app = lifecycle.db.application(app_id)
    p = command.parameters
    return (command.action == ACTION and p.get('mode') == 'fill-only'
            and p.get('maximum_executions') == 1 and p.get('auto_submit') is False
            and app['attempts'] == p.get('historical_attempts')
            and not app['attempt_started'] and not app['manual_action_required']
            and not app['submit_intent_at'] and not app['submission_confirmation_seen']
            and app['delivery_state'] == Delivery.NOT_STARTED
            and app['security_state'] in {'NONE', 'PASSIVE_PROTECTION_DETECTED'}
            and app['verification_state'] == 'NOT_REQUIRED'
            and not lifecycle.db.automation_retired(app_id)
            and not lifecycle.db.submission_conflict(app_id))


def finish(db, command, state, result):
    ManualCommands(db).finish(command, state, json.dumps(result, sort_keys=True))
