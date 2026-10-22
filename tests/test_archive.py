"""Protected-history snapshots: temporary synthetic records only."""
from pathlib import Path

import pytest

from autoapply.archive import (ApplicationHistory, PROTECTED_INDEX, atomic_json,
                               protected_record, read_json)


def seed_history(root, count=80):
    history = ApplicationHistory(root)
    with history.locked():
        history._invalidate_readers()
        for i in range(count):
            state = ['DISCOVERED', 'FAILED', 'SUBMITTED', 'UNKNOWN', 'MANUAL_REQUIRED',
                     'SUBMITTING', 'ALREADY_APPLIED', 'READY_TO_SUBMIT'][i % 8]
            history._write(dict(application_id=f'archive-{i}', application_state=state,
                                company=f'Company {i}', title='Engineering Intern',
                                canonical_url=f'https://jobs.lever.co/fixture/{i}',
                                submit_intent_at='2026-10-01' if i % 17 == 0 else None))
        history._statistics()
    return history


def json_reads(monkeypatch):
    original = Path.read_text
    reads = []
    def read(path, *args, **kwargs):
        if path.name == 'application.json':
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    return reads


def test_cold_protected_snapshot_skips_record_reads(tmp_path, monkeypatch):
    writer = seed_history(tmp_path / 'history')
    baseline = [r for r in ApplicationHistory(writer.root).list_applications() if protected_record(r)]
    reads = json_reads(monkeypatch)
    reader = ApplicationHistory(writer.root)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'glob', lambda *args, **kwargs: pytest.fail('valid snapshot walked history'))
        assert reader.list_protected_applications() == baseline
    assert reads == [] and reader.records == {}
    assert [r for r in reader.list_applications() if protected_record(r)] == baseline
    assert len(reads) == 80


@pytest.mark.parametrize('damage', ['missing', 'json', 'version', 'incomplete', 'revision', 'digest'])
def test_bad_snapshot_falls_back_without_repair(tmp_path, monkeypatch, damage):
    writer = seed_history(tmp_path / 'history', 24)
    path = writer.root / PROTECTED_INDEX
    value = read_json(path)
    if damage == 'missing':
        path.unlink()
    elif damage == 'json':
        path.write_text('{unfinished', encoding='utf-8')
    else:
        if damage == 'version':
            value['version'] += 1
        elif damage == 'incomplete':
            value['records'].pop()
        elif damage == 'revision':
            value['revision'][0] = 'older'
        else:
            value['sha256'] = 'bad'
        atomic_json(path, value)
    before = {p: p.read_bytes() for p in writer.root.rglob('*') if p.is_file()}
    reads = json_reads(monkeypatch)
    reader = ApplicationHistory(writer.root)
    result = reader.list_protected_applications()
    assert result == [r for r in reader.records.values() if protected_record(r)]
    assert len(reads) == 24
    assert {p: p.read_bytes() for p in writer.root.rglob('*') if p.is_file()} == before


def test_cross_reader_revision_and_interrupted_export(tmp_path, monkeypatch):
    writer = seed_history(tmp_path / 'history', 24)
    reader = ApplicationHistory(writer.root)
    reads = json_reads(monkeypatch)
    assert reader.list_protected_applications()
    assert reads == []
    with writer.locked():
        writer._invalidate_readers()
        writer._write(dict(application_id='external', application_state='FAILED',
                           company='Legacy', job_title='Intern', submit_intent_at='intent'))
        # Simulate interruption before statistics/snapshot publication. The old
        # complete index remains on disk; even a fresh reader must reject it.
    for current in (reader, ApplicationHistory(writer.root)):
        result = current.list_protected_applications()
        assert any(r['application_id'] == 'external' for r in result)
    assert len(reads) == 50
    reads.clear()
    with writer.locked():
        writer._statistics()
    assert any(r['application_id'] == 'external' for r in ApplicationHistory(writer.root).list_protected_applications())
    assert reads == []
    # External record edits participate through the existing statistics revision.
    path = writer.paths['archive-1'] / 'application.json'
    record = read_json(path)
    atomic_json(path, dict(record, submission_confirmation_seen=1))
    stats = writer.root / 'statistics.json'
    atomic_json(stats, dict(read_json(stats), external_revision='changed'))
    reads.clear()
    assert any(r['application_id'] == 'archive-1' for r in reader.list_protected_applications())
    assert len(reads) == 25


def test_index_publish_failure_keeps_old_snapshot_invalid(tmp_path, monkeypatch):
    import autoapply.archive as archive
    writer = seed_history(tmp_path / 'history', 8)
    original = archive.atomic_json
    def fail(path, payload):
        if path.name == PROTECTED_INDEX:
            raise OSError('interrupted snapshot replacement')
        return original(path, payload)
    monkeypatch.setattr(archive, 'atomic_json', fail)
    with writer.locked():
        writer._invalidate_readers()
        writer._write(dict(application_id='new', application_state='UNKNOWN', company='External'))
        with pytest.raises(OSError):
            writer._statistics()
    reader = ApplicationHistory(writer.root)
    assert any(r['application_id'] == 'new' for r in reader.list_protected_applications())


def test_validate_publishes_external_legacy_and_excludes_index(tmp_path):
    writer = seed_history(tmp_path / 'history', 8)
    atomic_json(writer.root / 'legacy.json', dict(id='legacy', company='Legacy',
                                                job_title='Intern', manual_action_required=True))
    writer.validate()
    first = ApplicationHistory(writer.root).list_protected_applications()
    assert any(r['application_id'] == 'legacy' for r in first)
    assert writer.validate()['inspected'] == 9
    assert ApplicationHistory(writer.root).list_protected_applications() == first
