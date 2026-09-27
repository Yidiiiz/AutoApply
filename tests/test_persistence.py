"""Phase 4 durability and work budgets; all storage is synthetic tmp_path data."""
import ast
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autoapply import archive, history_statistics
from autoapply.archive import ApplicationHistory, archive_application, read_json
from autoapply.database import Database
from autoapply.control import Controller
from autoapply.engine import Engine
from autoapply.models import Answer, Question, State, now
from autoapply.submission_probe import SubmissionProbe


@pytest.fixture
def application(db, config, listing):
    db.ingest(listing, config)
    return 1


def count_work(db, monkeypatch):
    sync = Mock(wraps=db.history.sync)
    stats = Mock(wraps=history_statistics.calculate_statistics)
    monkeypatch.setattr(db.history, 'sync', sync)
    monkeypatch.setattr(history_statistics, 'calculate_statistics', stats)
    return sync, stats


def test_logical_batch_commits_once_then_exports(db, application, monkeypatch):
    sync, stats = count_work(db, monkeypatch)
    trace = []
    db.conn.set_trace_callback(trace.append)
    with db.transaction():
        for n in range(10):
            db.event(1, 'observation', str(n))
        assert sync.call_count == 0
    db.conn.set_trace_callback(None)
    # One mutation commit plus the existing export/dirty-ack commit.
    assert trace.count('COMMIT') == 2
    assert sync.call_count == stats.call_count == 1
    assert len([e for e in read_json(db.history.paths['1'] / 'events.json') if e['kind'] == 'observation']) == 10


def test_failure_before_commit_rolls_back_evidence_and_dirty(db, application, monkeypatch):
    sync, _ = count_work(db, monkeypatch)
    before = db.application(1)
    events = db.rows('SELECT * FROM events')
    with pytest.raises(RuntimeError, match='abort'):
        with db.transaction():
            db.transition(1, State.SUBMITTING, submit_intent_at=now())
            db.event(1, 'supporting', 'evidence')
            raise RuntimeError('abort')
    assert db.application(1) == before
    assert db.rows('SELECT * FROM events') == events
    assert not db.rows('SELECT * FROM history_dirty')
    assert sync.call_count == 0


def test_unchanged_security_observations_do_no_writes(db, application, monkeypatch):
    fields = dict(security_state='NONE', ats_type='lever', current_url='https://example.test/form')
    db.update_security(1, **fields, diagnostics_at='first')
    before = db.application(1)
    changes = db.conn.total_changes
    sync, stats = count_work(db, monkeypatch)
    flush = Mock(wraps=db.flush_history)
    monkeypatch.setattr(db, 'flush_history', flush)
    for _ in range(10):
        db.update_security(1, **fields, diagnostics_at=now())
    assert db.conn.total_changes == changes
    assert db.application(1) == before
    assert sync.call_count == stats.call_count == flush.call_count == 0


@pytest.mark.parametrize('fields', [
    dict(security_state='INTERACTIVE_CHALLENGE', security_provider='test'),
    dict(verification_state='PASSED'), dict(last_http_status=403),
    dict(last_security_message='New challenge evidence'),
    dict(current_url='https://example.test/verification'),
    dict(manual_action_required=1, retry_allowed=0),
    dict(diagnostics_at='explicit capture'),
])
def test_changed_security_evidence_exports_immediately(db, application, monkeypatch, fields):
    sync, _ = count_work(db, monkeypatch)
    db.update_security(1, **fields)
    assert sync.call_count == 1
    record = read_json(db.history.paths['1'] / 'application.json')
    assert all(record[k] == v for k, v in fields.items())


@pytest.mark.parametrize('confirmed', [False, True])
def test_critical_commit_survives_export_failure_and_restart(config, db, application, monkeypatch, confirmed):
    path = config.private / 'test.sqlite3'
    if confirmed:
        db.transition(1, State.SUBMITTING, submit_intent_at=now())
    with monkeypatch.context() as patch:
        patch.setattr(db.history, 'sync', Mock(side_effect=OSError('export failed')))
        with pytest.raises(OSError):
            if confirmed:
                db.transition(1, State.SUBMITTED, confirmation_text='Employer receipt', submitted_at=now())
            else:
                db.transition(1, State.SUBMITTING, submit_intent_at=now())
        # A second raw connection observes the committed evidence, independent
        # of the failing exporter and its rolled-back acknowledgement.
        with sqlite3.connect(path) as reader:
            row = reader.execute('SELECT submit_intent_at,submission_confirmation_seen FROM applications').fetchone()
            assert row[0] and bool(row[1]) == confirmed
            assert reader.execute('SELECT * FROM history_dirty').fetchall()
        # Simulate abrupt exit: intentionally bypass graceful close/export.
        db.conn.close()
        db._closed = True
    restarted = Database(path, startup_maintenance=False)
    try:
        assert not restarted.rows('SELECT * FROM history_dirty')
        restarted.recover()
        assert restarted.application(1)['submit_intent_at']
        assert not restarted.application(1)['retry_allowed']
        if confirmed:
            assert restarted.application(1)['status'] == 'SUBMITTED'
            with pytest.raises(ValueError):
                restarted.update_security(1, submission_confirmation_seen=0)
        else:
            assert restarted.application(1)['application_state'] == 'UNKNOWN'
            assert restarted.claim() is None
    finally:
        restarted.close()


@pytest.mark.parametrize('filename', ['events.json', 'job_description.txt', 'statistics.json'])
def test_atomic_replace_failure_keeps_old_file_and_pending_work(db, application, monkeypatch, filename):
    path = db.history.root / filename if filename == 'statistics.json' else db.history.paths['1'] / filename
    before = path.read_bytes()
    replace = archive.os.replace
    def fail(source, target):
        if Path(target) == path:
            raise PermissionError('simulated Windows replacement lock')
        return replace(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(archive.os, 'replace', fail)
        with pytest.raises(PermissionError):
            if filename == 'events.json':
                db.event(1, 'durable', 'evidence')
            elif filename == 'job_description.txt':
                db.execute("UPDATE jobs SET description='Changed description' WHERE id=1")
            else:
                db.update_security(1, ats_type='new provider')
        assert path.read_bytes() == before
        assert db.rows('SELECT * FROM history_dirty')
        assert not list(db.history.root.rglob('*.tmp'))
    db.flush_history()
    assert path.read_bytes() != before
    assert not db.rows('SELECT * FROM history_dirty')


def test_event_only_export_preserves_unchanged_files(db, application):
    files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in db.history.paths['1'].iterdir() if p.is_file()}
    statistics = db.history.root / 'statistics.json'
    before = statistics.read_bytes(), statistics.stat().st_mtime_ns
    db.event(1, 'extra', 'evidence')
    for path, value in files.items():
        if path.name != 'events.json':
            assert (path.read_bytes(), path.stat().st_mtime_ns) == value
    assert (statistics.read_bytes(), statistics.stat().st_mtime_ns) == before


def test_stats_day_rollover_is_not_suppressed(db, application, monkeypatch):
    from datetime import datetime, timedelta, timezone
    calculate = history_statistics.calculate_statistics
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    monkeypatch.setattr(history_statistics, 'calculate_statistics', lambda records: calculate(records, tomorrow))
    db.event(1, 'next day', 'evidence')
    assert read_json(db.history.root / 'statistics.json')['activity']['today']['discovered'] == 0


def test_other_history_reader_refreshes_when_aggregates_unchanged(db, application):
    reader = ApplicationHistory(db.history.root)
    before = reader.get_application(1)
    stats = (db.history.root / 'statistics.json').read_bytes()
    db.update_security(1, last_security_message='Additional evidence')
    assert (db.history.root / 'statistics.json').read_bytes() == stats
    assert reader.get_application(1)['last_security_message'] != before['last_security_message']
    assert reader.get_application(1)['last_security_message'] == 'Additional evidence'


def test_imported_unknown_fields_and_evidence_survive_optimized_export(db, application):
    folder = db.history.paths['1']
    record = read_json(folder / 'application.json')
    record['imported_metadata'] = {'opaque': ['legacy', {'attachment': 'evidence.bin'}]}
    archive.atomic_json(folder / 'application.json', record)
    (folder / 'evidence.bin').write_bytes(b'protected\x00evidence')
    db.history.validate()
    db.event(1, 'reconciliation', 'user evidence')
    db.transition(1, State.SUBMITTED, confirmation_text='Receipt')
    folder = db.history.paths['1']
    assert read_json(folder / 'application.json')['imported_metadata'] == record['imported_metadata']
    assert (folder / 'evidence.bin').read_bytes() == b'protected\x00evidence'
    assert any(e['detail'] == 'user evidence' for e in read_json(folder / 'events.json'))


def test_archive_lookup_does_not_force_export_but_recovers_missing_bundle(config, db, application, monkeypatch):
    sync, stats = count_work(db, monkeypatch)
    folder = archive_application(config, db, 1)
    assert sync.call_count == stats.call_count == 0
    (folder / 'application.json').unlink()
    assert archive_application(config, db, 1) == folder
    assert (folder / 'application.json').exists()
    assert sync.call_count == stats.call_count == 1


def test_clean_setting_write_does_not_open_export_transaction(db, application):
    trace = []
    db.conn.set_trace_callback(trace.append)
    db.set_setting('synthetic_setting', True)
    db.conn.set_trace_callback(None)
    assert 'BEGIN IMMEDIATE' not in trace and 'COMMIT' not in trace


def test_user_answer_commits_and_exports_once(config, db, application, monkeypatch):
    question = db.question(1, Question('custom', 'A specific question', required=True))
    db.transition(1, State.NEEDS_INPUT)
    sync, stats = count_work(db, monkeypatch)
    Controller(config, db).answer(question['id'], 'Confirmed answer')
    assert sync.call_count == stats.call_count == 1
    with sqlite3.connect(config.private / 'test.sqlite3') as reader:
        assert json.loads(reader.execute('SELECT answer FROM questions WHERE id=?', (question['id'],)).fetchone()[0]) == 'Confirmed answer'
    assert read_json(db.history.paths['1'] / 'answers.json')[0]['answer_source'] == 'user_confirmed'


def test_hold_and_supporting_event_export_as_one_immediate_operation(config, db, application, monkeypatch):
    sync, stats = count_work(db, monkeypatch)
    Engine(config, db).hold(1, State.MANUAL_REVIEW, 'Required operator review')
    assert sync.call_count == stats.call_count == 1
    assert db.application(1)['retry_allowed'] == 0
    assert read_json(db.history.paths['1'] / 'events.json')[-1]['kind'] == 'hold'


def test_draft_setting_round_trip_is_dirty_without_forced_archive(db, application):
    question = db.question(1, Question('why', 'Why this company?', 'textarea'))
    key = f"draft:{question['id']}"
    path = db.history.paths['1'] / 'generated_responses.json'
    db.set_setting(key, 'First draft')
    assert read_json(path)[0]['draft'] == 'First draft'
    db.set_setting(key, 'Revised draft')
    assert read_json(path)[0]['draft'] == 'Revised draft'
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.set_setting(key, 'Uncommitted')
            raise RuntimeError('abort')
    assert db.setting(key) == 'Revised draft'
    assert read_json(path)[0]['draft'] == 'Revised draft'
    db.execute('DELETE FROM settings WHERE key=?', (key,))
    assert read_json(path) == []


@pytest.mark.asyncio
async def test_draft_and_supporting_event_share_one_export(config, db, application, monkeypatch):
    question = db.question(1, Question('why', 'Why are you interested in this company?', 'textarea', True))
    async def draft(*args):
        assert not db.conn.in_transaction
        return Answer('Synthetic draft', 'synthetic_provider')
    sync, stats = count_work(db, monkeypatch)
    result = await Controller(config, db, SimpleNamespace(draft=draft)).draft(question['id'])
    assert result == 'Synthetic draft'
    assert sync.call_count == stats.call_count == 1
    assert read_json(db.history.paths['1'] / 'generated_responses.json')[0]['draft'] == result


def test_history_lock_cannot_acquire_database_lock(db, application):
    with db.history.locked():
        with pytest.raises(RuntimeError, match='database transaction before'):
            with db.transaction():
                pytest.fail('must reject inversion')
        with pytest.raises(RuntimeError, match='database transaction before'):
            db.event(1, 'invalid order', '')
    with db.transaction():
        with db.history.locked():
            assert db.application(1)['id'] == 1
    with pytest.raises(RuntimeError, match='owned by Database.flush_history'):
        db.history.sync(db, [1])


def test_concurrent_writer_cannot_be_acknowledged_by_older_export(config, db, application, monkeypatch):
    other = sqlite3.connect(config.private / 'test.sqlite3', timeout=0, isolation_level=None)
    write = db.history._write
    attempted = []
    def while_exporting(*args, **kwargs):
        assert db.conn.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            other.execute("INSERT INTO events(application_id,kind,detail,created_at) VALUES(1,'concurrent','new',?)", (now(),))
        attempted.append(True)
        return write(*args, **kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db.history, '_write', while_exporting)
            db.event(1, 'first', 'old')
        assert attempted
        other.execute("INSERT INTO events(application_id,kind,detail,created_at) VALUES(1,'concurrent','new',?)", (now(),))
        assert db.rows('SELECT * FROM history_dirty')
        db.flush_history()
        assert read_json(db.history.paths['1'] / 'events.json')[-1]['detail'] == 'new'
    finally:
        other.close()


def test_probe_observations_commit_without_export_then_flush(db, config, application, monkeypatch):
    sync, stats = count_work(db, monkeypatch)
    probe = SubmissionProbe(db, 1, None, None)
    for n in range(10):
        probe.record('observation', {'n': n})
        probe.save()
        assert not db.conn.in_transaction
    assert sync.call_count == stats.call_count == 0
    with sqlite3.connect(config.private / 'test.sqlite3') as reader:
        assert reader.execute("SELECT count(*) FROM events WHERE kind='observation'").fetchone()[0] == 10
        assert reader.execute('SELECT count(*) FROM history_dirty').fetchone()[0] == 1
        assert reader.execute('SELECT count(*) FROM settings WHERE key LIKE ?', ('%submit_probe:%',)).fetchone()[0] == 2
    db.flush_history()
    assert sync.call_count == stats.call_count == 1


def test_probe_evidence_recovers_after_abrupt_restart(config, db, application):
    probe = SubmissionProbe(db, 1, None, None)
    probe.record('NETWORK_SUBMIT_RESPONSE_OBSERVED', {'status': 200})
    probe.save()
    db.conn.close()
    db._closed = True
    restarted = Database(config.private / 'test.sqlite3', startup_maintenance=False)
    try:
        assert read_json(restarted.history.paths['1'] / 'events.json')[-1]['kind'] == 'NETWORK_SUBMIT_RESPONSE_OBSERVED'
        assert restarted.setting('latest_submit_probe:1') == probe.key
        assert not restarted.rows('SELECT * FROM history_dirty')
    finally:
        restarted.close()


def test_probe_callback_events_and_settings_never_export(db, application, monkeypatch):
    sync, stats = count_work(db, monkeypatch)
    page = SimpleNamespace(url='https://example.test/form', main_frame=object())
    probe = SubmissionProbe(db, 1, page, None)
    probe.click_started()
    request = SimpleNamespace(method='POST', url='https://example.test/submit')
    probe.on_request(request)
    probe.on_response(SimpleNamespace(request=request, status=200))
    probe.confirmed('Employer receipt')
    assert sync.call_count == stats.call_count == 0
    assert probe.data['confirmation_observed']
    events = db.rows('SELECT kind FROM events')
    assert any(e['kind'] == 'CONFIRMATION_OBSERVED' for e in events)
    assert db.setting(f'submit_probe:1:{probe.key}')['network'][0]['status'] == 200


def test_graceful_close_exports_pending_observations(db, application):
    probe = SubmissionProbe(db, 1, None, None)
    probe.record('observation', {'durable': True})
    folder = db.history.paths['1']
    db.close()
    assert read_json(folder / 'events.json')[-1]['kind'] == 'observation'
    db.close()  # Closing twice remains harmless.


@pytest.mark.asyncio
async def test_upload_evidence_batch_has_one_export_and_no_transaction_during_await(config, db, application, monkeypatch):
    tracker = SimpleNamespace(events=[{'kind': 'upload_event', 'n': n} for n in range(10)], result={'ready': True})
    page = SimpleNamespace(_autoapply_uploads=tracker)
    async def wait(*args, **kwargs):
        assert not db.conn.in_transaction
        return True
    engine = Engine(config, db)
    monkeypatch.setattr(engine.browser, 'uploads_ready', wait)
    sync, stats = count_work(db, monkeypatch)
    assert await engine.check_uploads(1, page, [])
    assert sync.call_count == stats.call_count == 1
    assert not tracker.events
    assert len([e for e in read_json(db.history.paths['1'] / 'events.json') if e['kind'].startswith('upload_') or e['kind'] == 'UPLOAD_READINESS']) == 11


def test_production_explicit_transactions_do_not_contain_await():
    root = Path(__file__).resolve().parents[1] / 'autoapply'
    for name in ('engine.py', 'submission_probe.py', 'control.py', 'database.py'):
        tree = ast.parse((root / name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and any(isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Attribute)
                    and item.context_expr.func.attr == 'transaction' for item in node.items):
                assert not any(isinstance(child, ast.Await) for child in ast.walk(node)), (name, node.lineno)
