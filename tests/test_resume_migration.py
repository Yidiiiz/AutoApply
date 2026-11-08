import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from autoapply.applications import GenericApplicationAdapter, UnsupportedForm
from autoapply.answers import AnswerResolver
from autoapply.engine import Engine
from autoapply.fill_batch import verify_reconstruction
from autoapply.operations import FileSnapshot
from autoapply.profile_snapshot import ProfileSnapshot
from autoapply.resume_migration import refresh_profile, invalidate_current_state
from autoapply.uploads import UploadTracker


def test_file_snapshot_replacement(config):
    before = FileSnapshot.read(config.resume)
    config.resume.write_bytes(b'%PDF-1.4\nchanged content\n%%EOF')
    after = FileSnapshot.read(config.resume, before)
    assert before.sha256 != after.sha256
    assert after.path == str(config.resume.resolve())
    (config.resume.parent / 'obsolete.pdf').write_bytes(b'old')
    assert config.resume.name == 'resume.pdf'


@pytest.mark.parametrize('adapter_name', ['generic', 'smartrecruiters'])
async def test_old_same_session_upload_rejected_even_same_size(config, adapter_name):
    from autoapply.smartrecruiters import SmartRecruitersAdapter
    cls = GenericApplicationAdapter if adapter_name == 'generic' else SmartRecruitersAdapter
    adapter = object.__new__(cls)
    old = FileSnapshot.read(config.resume)
    adapter.page = SimpleNamespace(_autoapply_document_snapshots={'resume': old}, _autoapply_uploads=UploadTracker())
    adapter.uploads = {'resume': ('control', config.resume.name, config.resume.stat().st_size)}
    adapter._upload_documents = AsyncMock()
    config.resume.write_bytes(config.resume.read_bytes().replace(b'local', b'other'))
    with pytest.raises(UnsupportedForm, match='fresh upload'):
        await adapter.upload_documents(SimpleNamespace(key='resume'), config.resume)
    adapter._upload_documents.assert_not_called()
    assert adapter.page._autoapply_uploads.result['ready'] is False


async def test_readiness_rejects_old_hash(config, db, listing):
    db.ingest(listing, config)
    old = FileSnapshot.read(config.resume)
    observation = {}
    browser = SimpleNamespace(uploads_ready=AsyncMock(return_value=True), observation=lambda page: observation)
    engine = Engine(config, db, browser, fill_only=True)
    page = SimpleNamespace(_autoapply_document_snapshots={'resume': old}, _autoapply_uploads=UploadTracker())
    config.resume.write_bytes(b'%PDF-1.4\nnew\n%%EOF')
    assert not await engine.check_uploads(1, page, {'resume': object()})
    browser.uploads_ready.assert_not_called()
    assert db.setting('upload_result:1')['ready'] is False
    assert observation['upload_result'] == db.setting('upload_result:1')
    assert observation['upload_result']['category'] == 'UPLOAD_FAILED'


@pytest.mark.parametrize('protected', [False, True])
def test_checkpoint_invalidation_preserves_attachment_history(config, db, listing, protected):
    db.ingest(listing, config)
    db.lifecycle.metadata(1, resume_used=str(config.resume), resume_sha256='old')
    if protected:
        db.execute("UPDATE applications SET status='SUBMITTED',submitted_at='historical',submit_intent_at='historical' WHERE id=1")
    app_before = db.application(1)
    receipt = dict(ready=True, resume_sha256='old')
    checkpoint = dict(readiness='RECONSTRUCTION_VERIFIED', reconstruction_verified=True, resume_sha256='old')
    db.set_setting('upload_result:1', receipt)
    db.set_setting('fill_checkpoint:1', checkpoint)
    with db.transaction():
        affected, files = invalidate_current_state(db.conn, 'old', 'new', {1: checkpoint})
    assert db.application(1) == app_before
    if protected:
        assert affected == [] and files == {}
        assert db.setting('upload_result:1') == receipt
        assert db.setting('fill_checkpoint:1') == checkpoint
    else:
        assert affected == [1]
        assert files[1]['resume_sha256'] == 'old'
        assert files[1]['required_resume_sha256'] == 'new'
        assert db.setting('fill_checkpoint:1')['readiness'] == 'RESUME_REVALIDATION_REQUIRED'
        assert not db.setting('upload_result:1')['ready']
        assert db.setting('resume_migration_history:old:upload_result:1') == receipt
        with pytest.raises(ValueError, match='Checkpoint restoration'):
            db.lifecycle.restore_checkpoint(1)


async def test_stale_reconstruction_never_navigates(config, db, listing):
    db.ingest(listing, config)
    db.set_setting('auto_submit', False)
    db.lifecycle.metadata(1, resume_sha256='old')
    engine = SimpleNamespace(config=config, db=db, browser=SimpleNamespace(navigate=AsyncMock()))
    with pytest.raises(RuntimeError, match='fresh upload'):
        await verify_reconstruction(engine, 1)
    engine.browser.navigate.assert_not_called()


def test_refresh_removes_old_evidence_preserves_explicit_facts_and_invalidates(config, db):
    profile = config.profile
    profile.update(verified_facts={'old.project': 'old only', 'user.fact': 'explicit'},
                   fact_sources={'old.project': dict(file='resume.pdf', sha256='old'),
                                 'user.fact': dict(source='USER_PROVIDED')})
    before = copy.deepcopy(profile)
    resolver = AnswerResolver(config, db)
    resolver.refresh()
    resolver._cache['old'] = object()
    digest = FileSnapshot.read(config.resume).sha256
    updated, report = refresh_profile(profile, {'new.project': 'new evidence', 'user.fact': 'conflict'},
                                      {'skills': ['Python']}, digest)
    assert profile == before
    assert updated['verified_facts'] == {'new.project': 'new evidence', 'user.fact': 'explicit'}
    assert report['conflicts'] == ['user.fact']
    assert updated['citizenship'] == profile['citizenship']
    assert updated['fact_sources']['new.project']['sha256'] == digest
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(updated), encoding='utf-8')
    old_revision = resolver._revision
    resolver.refresh()
    assert resolver._cache == {} and resolver._revision != old_revision
    assert ProfileSnapshot.create(before).revision != resolver.snapshot.revision
    config.resume.write_bytes(b'%PDF-1.4\nother')
    with pytest.raises(ValueError, match='revision mismatch'):
        config.profile_snapshot()


def test_new_resume_wording_requires_complete_sourced_evidence(config):
    from autoapply.jobs import api_integration_evidence
    facts = {'resume.course_tracker': 'I built a Windows system-tray application that periodically polls UMD Testudo to monitor course-seat availability, using Python, Beautiful Soup, Requests, and PyInstaller.',
             'resume.freelance.networking': 'I implemented client-server communication and state replication to support real-time multiplayer gameplay.'}
    profile, _ = refresh_profile(config.profile, facts, {'skills': ['Python', 'REST APIs']}, 'new')
    requirement = 'Comfort working with APIs, scripting, and connecting systems together'
    assert api_integration_evidence(requirement, profile)
    del profile['fact_sources']['resume.course_tracker']
    assert not api_integration_evidence(requirement, profile)
