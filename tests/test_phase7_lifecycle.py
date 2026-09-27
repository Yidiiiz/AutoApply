import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from autoapply.control import Controller
from autoapply.engine import Engine
from autoapply.manual import ManualCommand, ManualCommands
from autoapply.models import State, now
from autoapply.retry import Delivery, ErrorCategory as E, Failure, RetryPolicy
from autoapply.status import snapshot


@pytest.fixture
def app(config, db, listing):
    db.ingest(listing, config)
    return 1


@pytest.mark.parametrize('start,end', [('QUEUED','CHECKING'), ('CHECKING','APPLYING'),
    ('APPLYING','NEEDS_INPUT'), ('APPLYING','READY'), ('READY','SUBMITTING'),
    ('SUBMITTING','SUBMITTED'), ('SUBMITTING','MANUAL_REVIEW'), ('FAILED','RETRY'), ('RETRY','CHECKING')])
def test_transition_policy(db, app, start, end):
    db.transition(app, start, **({'submit_intent_at':now()} if start=='SUBMITTING' else {}))
    fields = {'confirmation_text':'Employer receipt'} if end=='SUBMITTED' else {'submit_intent_at':now()} if end=='SUBMITTING' else {}
    db.lifecycle.transition(app, end, **fields)
    assert db.application(app)['status'] == end
    assert db.one('SELECT status FROM jobs WHERE id=1')['status'] == end


@pytest.mark.parametrize('start,end', [('QUEUED','APPLYING'), ('FAILED','APPLYING'),
    ('SUBMITTED','QUEUED'), ('SUBMITTED','APPLYING'), ('INELIGIBLE','RETRY')])
def test_forbidden_transitions(db, app, start, end):
    db.transition(app, start, **({'confirmation_text':'Employer receipt'} if start=='SUBMITTED' else {}))
    before = db.application(app)
    with pytest.raises(ValueError):
        db.lifecycle.transition(app, end)
    assert db.application(app) == before


@pytest.mark.parametrize('delivery', list(Delivery))
@pytest.mark.parametrize('category', [E.NETWORK_ERROR, E.SITE_ERROR, E.RATE_LIMIT, E.STEP_TRANSITION_UNKNOWN])
def test_typed_delivery_decision(delivery, category):
    result = RetryPolicy().decide(Failure(category, 'fixture', operation='next', delivery=delivery), 1, 3)
    assert result.allowed == (delivery == Delivery.NOT_STARTED and category != E.STEP_TRANSITION_UNKNOWN)
    assert not RetryPolicy().decide(Failure(category, 'fixture', delivery=delivery), 1, 3, True).allowed


@pytest.mark.parametrize('hold', ['manual','security','controlled','retired','intent','confirmed'])
def test_holds_override_retry(db, app, hold):
    db.claim(app)
    if hold=='manual': db.update_security(app, manual_action_required=1)
    elif hold=='security': db.update_security(app, security_state='INTERACTIVE_CHALLENGE')
    elif hold=='controlled': db.set_setting('controlled_application_id', app)
    elif hold=='retired': db.set_setting('duplicate_submission_guard:1', True)
    elif hold=='intent': db.transition(app, 'SUBMITTING', submit_intent_at=now())
    else: db.transition(app, 'SUBMITTED', confirmation_text='Employer receipt')
    db.fail(app, Failure(E.NETWORK_ERROR, 'timeout'), 3)
    assert db.claim(app) is None
    with pytest.raises(ValueError): db.retry(app)


def test_restart_budget_counts_work_not_startups(db, app):
    for _ in range(6):
        assert db.claim(app)
        db.recover()
        assert db.application(app)['attempts'] == 0
    for attempt in range(1, 4):
        assert db.claim(app)
        db.lifecycle.begin_attempt(app, 2)
        db.lifecycle.begin_attempt(app, 2)
        assert db.application(app)['attempts'] == attempt
        db.recover()
        assert db.application(app)['status'] == ('FAILED' if attempt==3 else 'RETRY')
        db.execute('UPDATE applications SET retry_at=NULL WHERE id=?', (app,))
    db.recover()
    assert db.claim(app) is None
    assert db.application(app)['attempts'] == 3


@pytest.mark.parametrize('intent', [False, True])
def test_crash_after_possible_delivery_holds(db, app, intent):
    db.claim(app)
    db.lifecycle.begin_attempt(app, 3)
    db.lifecycle.transition(app, 'APPLYING')
    if intent: db.lifecycle.record_submission_intent(app)
    else: db.lifecycle.delivery(app, Delivery.POSSIBLY_DELIVERED)
    db.recover()
    row = db.application(app)
    assert row['application_state']=='UNKNOWN' and not row['retry_allowed']
    assert row['error_category'] == ('SUBMISSION_UNKNOWN' if intent else 'STEP_TRANSITION_UNKNOWN')
    assert db.claim(app) is None


@pytest.mark.parametrize('submitted', [False, True])
def test_reconciliation_retains_intent_evidence(db, app, config, submitted):
    db.claim(app)
    db.transition(app, 'SUBMITTING', submit_intent_at=now())
    intent = db.application(app)['submit_intent_at']
    db.recover()
    controller = Controller(config, db)
    with pytest.raises(ValueError): controller.reconcile(app, submitted, ' ')
    controller.command(f'reconcile {app} {"submitted" if submitted else "not-submitted"} --evidence "Employer history receipt"')
    row = db.application(app)
    assert row['status'] == ('SUBMITTED' if submitted else 'RETRY')
    evidence = json.loads(db.one("SELECT detail FROM events WHERE kind='submission_intent_reconciled'")['detail'])
    assert evidence['prior_submit_intent_at'] == intent and evidence['evidence']=='Employer history receipt'


def test_atomic_hold_rolls_back_all_dimensions(db, app, monkeypatch):
    db.claim(app)
    before = db.application(app)
    def fail(*args): raise RuntimeError('event failure')
    monkeypatch.setattr(db, 'event', fail)
    with pytest.raises(RuntimeError):
        db.lifecycle.record_hold(app, 'Needs input', E.INPUT_REQUIRED, session_preserved=1)
    assert db.application(app) == before


def test_persisted_flags_do_not_prove_live_readiness(db, app):
    db.transition(app, 'READY')
    db.update_security(app, application_state='READY_FOR_MANUAL_SUBMIT', session_preserved=1)
    db.set_setting('manual_session:1', {'token':'old'})
    assert snapshot(db, app)['readiness'] != 'LIVE_READY_FOR_MANUAL_SUBMIT'
    page = Mock(); page.is_closed.return_value=False
    assert snapshot(db, app, live_page=page, live_browser=True)['readiness']=='LIVE_READY_FOR_MANUAL_SUBMIT'
    db.recover()
    assert not db.application(app)['session_preserved']
    assert db.setting('manual_session:1') is None


@pytest.fixture
def held(app, config, db):
    db.claim(app)
    db.lifecycle.record_hold(app, 'Unknown question', E.INPUT_REQUIRED, manual_resume_allowed=1, session_preserved=1)
    db.set_setting('manual_session:1', {'token':'session-A', 'busy':False})
    return Controller(config, db)


@pytest.mark.parametrize('text', ['resume 1','resume-manual 1','inspect-manual 1'])
def test_cli_discord_same_envelope(held, db, text):
    held.command(text)
    first = ManualCommand.decode(1, db.one('SELECT * FROM manual_requests')['action'])
    ManualCommands(db).cancel(1)
    held.route('!' + text)
    second = ManualCommand.decode(1, db.one('SELECT * FROM manual_requests')['action'])
    assert (first.action, first.application_id, first.session_token, first.parameters) == (second.action, second.application_id, second.session_token, second.parameters)
    assert first.session_token == 'session-A'


@pytest.mark.parametrize('interface', ['command','route'])
@pytest.mark.parametrize('token', [None,'wrong','session-A'])
def test_controlled_token_parity(held, db, interface, token):
    db.set_setting('controlled_application_id', 1)
    text = 'resume 1' + (f' --token {token}' if token else '')
    if token != 'session-A':
        with pytest.raises(ValueError, match='token'): getattr(held, interface)(text)
        assert db.one('SELECT * FROM manual_requests') is None
    else:
        getattr(held, interface)(text)
        assert ManualCommand.decode(1, db.one('SELECT * FROM manual_requests')['action']).session_token == token


def test_claim_crash_has_evidence_and_never_replays(held, db):
    held.command('resume 1')
    request = db.one('SELECT * FROM manual_requests')
    ledger = ManualCommands(db)
    command = ledger.claim(request)
    assert db.one('SELECT * FROM manual_requests') is None
    assert db.one('SELECT state FROM manual_commands')['state'] == 'IN_PROGRESS'
    db.recover()
    assert db.one('SELECT state FROM manual_commands')['state'] == 'FAILED'
    db.execute('INSERT INTO manual_requests VALUES (?,?,?)', tuple(request.values()))
    assert ledger.claim(request) is None
    assert db.setting(f'manual_ack:{command.id}') == 'FAILED'


async def test_stale_command_records_failure_without_browser(held, db, config):
    held.command('resume 1')
    engine = Engine(config, db)
    engine.handoff.sessions[1] = 'different'
    engine.resume_manual = AsyncMock()
    await engine.service_manual_requests()
    engine.resume_manual.assert_not_awaited()
    assert db.one('SELECT state FROM manual_commands')['state'] == 'FAILED'


def test_exact_identity_distinct_tenants_and_requisitions(config, db, listing):
    db.ingest(listing, config)
    db.transition(1, 'SUBMITTED', confirmation_text='Employer receipt')
    for url in ['https://jobs.lever.co/example/other', 'https://jobs.lever.co/other/abc-123']:
        job, _ = db.ingest(replace(listing, url=url), config)
        assert db.submission_conflict(job) is None


def test_normal_resume_never_refreshes_live_classes(held, db, config):
    import inspect
    source = inspect.getsource(Engine.resume_manual)
    assert 'importlib.reload' not in source and '__class__ =' not in source and 'refresh_upload_protocol' not in source


@pytest.mark.parametrize('interface', ['command', 'route'])
@pytest.mark.parametrize('action', ['answer', 'skip', 'stop', 'inspect', 'reconcile'])
def test_other_manual_commands_share_semantics(config, db, app, interface, action):
    from autoapply.models import Question
    controller = Controller(config, db)
    dispatch = getattr(controller, interface)
    db.claim(app)
    db.lifecycle.record_hold(app, 'Input needed', E.INPUT_REQUIRED, manual_resume_allowed=1)
    if action in {'answer', 'skip'}:
        q = db.question(app, Question('custom', 'Exact local fact', 'text', False))
        dispatch(f'{action} {q["id"]}' + (' synthetic fact' if action=='answer' else ''))
        row = db.one('SELECT * FROM questions WHERE id=?', (q['id'],))
        assert row['status'] == ('ANSWERED' if action=='answer' else 'SKIPPED')
        assert (json.loads(row['answer']) if row['answer'] is not None else None) == ('synthetic fact' if action=='answer' else None)
    elif action=='stop':
        command = ManualCommand('inspect', app, parameters={'reconstruct':True})
        ManualCommands(db).enqueue(command)
        dispatch('stop 1')
        assert db.setting('paused') and not db.rows('SELECT * FROM manual_requests')
        assert db.one('SELECT state FROM manual_commands')['state']=='FAILED'
    elif action=='inspect':
        assert 'Live session: UNVERIFIED_OR_UNAVAILABLE' in dispatch('inspect 1')
        view = controller.status_snapshot(1)
        assert view['manual_hold']['required']
        assert not view['session']['live_page']
    else:
        db.execute('UPDATE applications SET submit_intent_at=? WHERE id=1', (now(),))
        dispatch('reconcile 1 not-submitted --evidence "Employer history empty"')
        assert db.application(app)['status']=='RETRY'
        assert db.one("SELECT id FROM events WHERE kind='submission_intent_reconciled'")


@pytest.mark.parametrize("payload", ['{bad-json', '[]', '{"action":"inspect","id":[]}'])
def test_malformed_legacy_request_has_durable_failure(db, app, payload):
    db.execute('INSERT INTO manual_requests VALUES (?,?,?)', (app, payload, now()))
    assert ManualCommands(db).claim(db.one('SELECT * FROM manual_requests')) is None
    assert not db.rows('SELECT * FROM manual_requests')
    assert db.one('SELECT state FROM manual_commands')['state']=='FAILED'


def test_resolved_answers_cannot_manufacture_retry_budget(db, app):
    db.transition(app, 'NEEDS_INPUT', attempts=4, max_retries=3)
    assert not db.lifecycle.resolve_input(app)
    assert db.application(app)['status']=='FAILED'
    assert not db.application(app)['retry_allowed']


def test_controlled_missing_token_does_not_resolve_facts(held, db, monkeypatch):
    db.set_setting('controlled_application_id', 1)
    resolve = Mock()
    monkeypatch.setattr(held, 'resolve_known_pending', resolve)
    with pytest.raises(ValueError): held.command('resume 1')
    resolve.assert_not_called()


@pytest.mark.parametrize('reconstruct', [False, True])
def test_ambiguous_next_cannot_inherit_prior_resume_permission(db, app, reconstruct):
    db.claim(app)
    db.lifecycle.begin_attempt(app, 3)
    db.lifecycle.transition(app, 'APPLYING')
    db.update_security(app, manual_resume_allowed=1)
    db.lifecycle.delivery(app, Delivery.POSSIBLY_DELIVERED)
    db.fail(app, Failure(E.NETWORK_ERROR, 'Next response lost'), 3)  # Durable delivery evidence wins.
    assert not db.application(app)['manual_resume_allowed']
    with pytest.raises(ValueError, match='Hold'):
        db.lifecycle.resume(app, reconstruct=reconstruct)
    assert db.application(app)['application_state']=='UNKNOWN'
