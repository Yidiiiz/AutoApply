from dataclasses import replace
from datetime import datetime, timezone

import pytest

from autoapply.archive import ApplicationHistory, atomic_json, read_json
from autoapply.database import Database
from autoapply.history_statistics import calculate_statistics
from autoapply.models import State


def bundle(root, name, record):
    path = root / name
    atomic_json(path / 'application.json', record)
    return path


def assert_single(history, app_id, state):
    records = [p for p in history.root.glob('*/*/application.json')
               if read_json(p)['application_id'] == str(app_id)]
    assert len(records) == 1
    assert records[0].parent.parent.name == state.lower()
    assert read_json(records[0])['application_state'] == state
    return records[0].parent


def test_validate_preserves_submitted_record_bytes_with_empty_fields(config, db, listing):
    db.ingest(listing, config)
    db.transition(1, State.SUBMITTED, confirmation_text='Your application was submitted')
    path = assert_single(db.history, 1, 'SUBMITTED') / 'application.json'
    before = path.read_bytes()
    assert read_json(path)['failure_reason'] == ''
    db.history.validate()
    assert path.read_bytes() == before


def test_creation_transitions_and_query(config, db, listing):
    db.ingest(listing, config)
    folder = assert_single(db.history, 1, 'DISCOVERED')
    (folder / 'screenshots').mkdir()
    (folder / 'screenshots/test.png').write_bytes(b'preserve')
    db.update_security(1, screenshot_path=str(folder / 'screenshots/test.png'))
    db.transition(1, State.APPLYING)
    filling = assert_single(db.history, 1, 'FILLING')
    assert not folder.exists()
    assert (filling / 'screenshots/test.png').read_bytes() == b'preserve'
    assert db.application(1)['screenshot_path'] == str(filling / 'screenshots/test.png')
    db.transition(1, State.SUBMITTED, confirmation_text='Confirmed', submitted_at='2026-09-21T12:00:00+00:00')
    assert_single(db.history, 1, 'SUBMITTED')
    with pytest.raises(ValueError):
        db.transition(1, State.FAILED)
    assert_single(db.history, 1, 'SUBMITTED')
    assert db.history.get_application(1)['application_id'] == '1'
    assert len(db.history.find_by_job_id(1)) == len(db.history.find_by_url(listing.url)) == 1
    assert len(db.history.list_applications('SUBMITTED')) == 1
    states = [h['status'] for h in db.history.get_application(1)['status_history']]
    assert states == ['DISCOVERED', 'FILLING', 'SUBMITTED']
    stats = read_json(db.history.root / 'statistics.json')
    assert stats['total_applications'] == stats['status_counts']['SUBMITTED'] == 1


@pytest.mark.parametrize('state', ['MANUAL_REQUIRED', 'FAILED', 'UNKNOWN', 'RATE_LIMITED'])
def test_additional_states(config, db, listing, state):
    db.ingest(listing, config)
    db.update_security(1, application_state=state)
    assert_single(db.history, 1, state)


@pytest.mark.parametrize('record,state', [
    ({'submitted': True}, 'SUBMITTED'), ({'status': 'FAILED'}, 'FAILED'),
    ({'company': 'Ambiguous'}, 'UNKNOWN'), ({'manual_action_required': True}, 'MANUAL_REQUIRED'),
    ({'status': 'CLOSED', 'application_state': 'FAILED'}, 'CLOSED'),
    ({'application_state': 'gibberish', 'status': 'SUBMITTED'}, 'UNKNOWN'),
])
def test_legacy_migration(tmp_path, record, state):
    root = tmp_path / 'application_history'
    old = bundle(root, 'old', dict(id=7, **record))
    (old / 'answers.json').write_text('["existing answer"]')
    history = ApplicationHistory(root)
    report = history.migrate()
    target = assert_single(history, 7, state)
    assert not old.exists()
    assert read_json(target / 'answers.json') == ['existing answer']
    assert report['inspected'] == report['moved'] == 1
    assert (tmp_path / 'application_history_migration_backup/old/application.json').exists()
    assert history.validate()['moved'] == 0


def test_duplicate_reconciliation_and_assets(tmp_path):
    root = tmp_path / 'history'
    a = bundle(root, 'first', dict(id=4, application_state='FILLING', updated_at='2026-09-20', company='Acme',
        status_history=[dict(status='FILLING', timestamp='2026-09-20')]))
    b = bundle(root, 'second', dict(id=4, application_state='FAILED', updated_at='2026-09-21', title='Intern',
        status_history=[dict(status='FAILED', timestamp='2026-09-21')]))
    (a / 'answers.json').write_text('[1]')
    (b / 'answers.json').write_text('[2]')
    history = ApplicationHistory(root)
    report = history.validate()
    target = assert_single(history, 4, 'FAILED')
    assert report['duplicates_merged'] == 1
    record = history.get_application(4)
    assert record['company'] == 'Acme' and record['title'] == 'Intern'
    assert len(record['status_history']) == 2
    assert list((target / 'preserved').rglob('answers.json'))
    assert read_json(root / 'statistics.json')['total_applications'] == 1


def test_missing_id_stable_and_duplicate_url(tmp_path):
    root = tmp_path / 'history'
    url = 'https://example.test/jobs/abc'
    bundle(root, 'first', dict(application_state='FAILED', canonical_url=url))
    bundle(root, 'second', dict(application_state='FAILED', canonical_url=url))
    history = ApplicationHistory(root)
    assert history.migrate()['duplicates_merged'] == 1
    identifier = history.list_applications()[0]['application_id']
    history.validate()
    assert history.list_applications()[0]['application_id'] == identifier


def test_wrong_folder_and_malformed_preserved(tmp_path):
    root = tmp_path / 'history'
    bundle(root, 'submitted/wrong', dict(id=1, application_state='FAILED'))
    bad = root / 'broken.json'
    bad.write_bytes(b'{not json')
    history = ApplicationHistory(root)
    report = history.validate()
    assert_single(history, 1, 'FAILED')
    unknown = history.list_applications('UNKNOWN')
    assert len(unknown) == 1
    assert report['warnings']
    assert any(p.read_bytes() == b'{not json' for p in root.glob('unknown/*/original.json'))
    assert history.validate()['duplicates_merged'] == 0


def test_rebuild_deleted_or_corrupt_stats(tmp_path):
    root = tmp_path / 'history'
    bundle(root, 'old', dict(id=3, application_state='DISCOVERED'))
    history = ApplicationHistory(root)
    history.migrate()
    stats = root / 'statistics.json'
    stats.unlink()
    assert history.rebuild_statistics()['total_applications'] == 1
    stats.write_text('{bad')
    assert history.rebuild_statistics()['status_counts']['DISCOVERED'] == 1


def test_stats_ats_security_activity_and_timings():
    record = dict(application_id='1', application_state='SUBMITTED', ats='ashby', company='Acme',
        location='Remote', sources=[dict(source_name='fixture')],
        discovered_at='2026-09-21T10:00:00+00:00', started_at='2026-09-21T10:01:00+00:00',
        submitted_at='2026-09-21T10:03:00+00:00', submit_intent_at='2026-09-21T10:02:00+00:00',
        status_history=[dict(status='FILLING', timestamp='2026-09-21T10:01:00+00:00'),
            dict(status='MANUAL_REQUIRED', timestamp='2026-09-21T10:01:10+00:00', security_state='INTERACTIVE_CHALLENGE', security_provider='hCaptcha'),
            dict(status='FILLING', timestamp='2026-09-21T10:01:30+00:00'),
            dict(status='SUBMITTING', timestamp='2026-09-21T10:02:00+00:00')])
    stats = calculate_statistics([record], datetime(2026, 9, 21, 12, tzinfo=timezone.utc))
    assert stats['by_ats']['ashby']['submitted'] == 1
    assert stats['security_events'] == dict(total=1, by_type={'INTERACTIVE_CHALLENGE': 1})
    assert stats['security_providers'] == {'hCaptcha': 1}
    assert stats['activity']['today'] == dict(discovered=1, started=1, submitted=1)
    assert stats['total_submission_attempts'] == 1
    assert stats['total_manual_interventions'] == 1
    assert stats['average_time_discovered_to_submitted'] == 180
    assert stats['average_time_filling_to_submitted'] == 120
    assert stats['average_time_in_manual_required'] == 20
    assert stats['submission_rate'] == stats['completed_attempt_success_rate'] == 1


def test_security_change_exported_without_status_change(config, db, listing):
    db.ingest(listing, config)
    db.update_security(1, security_state='SPAM_REJECTED', security_provider='UNKNOWN')
    stats = read_json(db.history.root / 'statistics.json')
    assert stats['security_events']['by_type']['SPAM_REJECTED'] == 1
    db.update_security(1, last_security_message='same event')
    assert read_json(db.history.root / 'statistics.json')['security_events']['total'] == 1


def test_restart_repairs_and_deletion_updates(config, db, listing):
    db.ingest(listing, config)
    path = db.history.paths['1']
    wrong = db.history.root / 'failed' / path.name
    path.rename(wrong)
    other = Database(config.private / 'test.sqlite3')
    try:
        assert_single(other.history, 1, 'DISCOVERED')
        with other.transaction():
            other.execute('DELETE FROM events WHERE application_id=1')
            other.execute('DELETE FROM applications WHERE id=1')
        assert other.history.list_applications() == []
        assert read_json(other.history.root / 'statistics.json')['total_applications'] == 0
        assert list((config.private / 'application_history_deleted').glob('*/application.json'))
    finally:
        other.close()


def test_pending_export_recovers_after_failure(config, db, listing, monkeypatch):
    original = db.history.sync
    def fail(*args):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(db.history, 'sync', fail)
    with pytest.raises(OSError):
        db.ingest(listing, config)
    assert db.application(1)['status'] == 'QUEUED'
    assert db.rows('SELECT * FROM history_dirty')
    monkeypatch.setattr(db.history, 'sync', original)
    db.flush_history()
    assert_single(db.history, 1, 'DISCOVERED')
    assert not db.rows('SELECT * FROM history_dirty')


def test_transaction_rollback_does_not_export(config, db, listing):
    db.ingest(listing, config)
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.transition(1, State.FAILED)
            raise RuntimeError('rollback')
    assert_single(db.history, 1, 'DISCOVERED')


def test_closed_not_failed_and_multiple_sources(config, db, listing):
    db.ingest(listing, config)
    db.ingest(replace(listing, source='another', closed=True), config)
    db.transition(1, State.CLOSED, "LISTING_CLOSED")
    stats = read_json(db.history.root / 'statistics.json')
    assert stats['status_counts']['CLOSED'] == 1
    assert stats['failure_rate'] == 0
    assert stats['applications_by_source'] == {'fixture': 1, 'another': 1}


def test_empty_directory_is_not_an_application(tmp_path):
    root = tmp_path / 'history'
    (root / 'empty').mkdir(parents=True)
    history = ApplicationHistory(root)
    report = history.validate()
    assert report['empty_directories'] == 1
    assert history.list_applications() == []


def test_imported_submission_blocks_duplicate(config, db, listing):
    db.ingest(listing, config)
    bundle(db.history.root, 'imported', dict(id='old-application', canonical_url=listing.url, submitted=True))
    db.history.validate()
    assert db.submission_conflict(1)['id'] == 'old-application'


def test_move_failure_preserves_record_for_repair(tmp_path, monkeypatch):
    from pathlib import Path
    root = tmp_path / 'history'
    old = bundle(root, 'legacy', dict(id=5, status='SUBMITTED'))
    history = ApplicationHistory(root)
    original = Path.rename
    def fail_move(self, target):
        if self == old:
            raise OSError('simulated rename failure')
        return original(self, target)
    monkeypatch.setattr(Path, 'rename', fail_move)
    with pytest.raises(OSError):
        history.migrate()
    assert read_json(old / 'application.json')['application_id'] == '5'
    monkeypatch.setattr(Path, 'rename', original)
    history.validate()
    assert_single(history, 5, 'SUBMITTED')


def test_malformed_bundle_bytes_survive_repeated_validation(tmp_path):
    root = tmp_path / 'history'
    path = root / 'broken' / 'application.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'\xff\x00bad')
    history = ApplicationHistory(root)
    history.validate()
    history.validate()
    preserved = list(root.glob('unknown/*/preserved/malformed-application.json'))
    assert len(preserved) == 1 and preserved[0].read_bytes() == b'\xff\x00bad'


def test_temporary_windows_rename_lock_recovers(tmp_path, monkeypatch):
    from pathlib import Path
    root = tmp_path / 'history'
    old = bundle(root, 'legacy', dict(id=5, status='FAILED'))
    original = Path.rename
    attempts = []
    def briefly_locked(self, target):
        if self == old:
            attempts.append(target)
            if len(attempts) < 3:
                raise PermissionError('temporary scanner lock')
        return original(self, target)
    monkeypatch.setattr(Path, 'rename', briefly_locked)
    history = ApplicationHistory(root)
    history.validate()
    assert len(attempts) == 3
    assert_single(history, 5, 'FAILED')


def test_failed_deletion_move_can_retry(config, db, listing, monkeypatch):
    import autoapply.archive as archive
    db.ingest(listing, config)
    old = db.history.paths['1']
    original = archive.rename_with_retry
    def fail(source, target):
        raise PermissionError('persistent folder lock')
    monkeypatch.setattr(archive, 'rename_with_retry', fail)
    with pytest.raises(PermissionError):
        with db.transaction():
            db.execute('DELETE FROM events WHERE application_id=1')
            db.execute('DELETE FROM applications WHERE id=1')
    assert old.exists() and db.rows('SELECT * FROM history_dirty')
    monkeypatch.setattr(archive, 'rename_with_retry', original)
    db.flush_history()
    assert not old.exists()
    assert db.history.list_applications() == []
    assert read_json(db.history.root / 'statistics.json')['total_applications'] == 0
