"""Audit-only cross-phase checks; temporary storage, no browser or real provider.

The Phase 8 baseline stays unchanged. Run with both existing offline plugins.
"""
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from autoapply.ai import AIManager, BrowserAIProvider, ProviderUnavailable
from autoapply.codex_writer import CodexWritingProvider
from autoapply.config import Config
from autoapply.database import Database
from autoapply.fill_batch import checkpoint
from autoapply.manual import ManualCommand, ManualCommands
from autoapply.models import Listing, Question, now
from autoapply.status import snapshot


@pytest.fixture
def isolated(tmp_path):
    config = Config(tmp_path, {'ai': {'enabled': False}, 'discord': {'enabled': False},
                              'gmail': {'enabled': False}, 'application': {'auto_submit': False}})
    path = config.private / 'audit.sqlite3'
    db = Database(path, startup_maintenance=False)
    db.ingest(Listing('Synthetic', 'Software Engineering Intern', 'New York, NY',
                     'https://jobs.lever.co/synthetic/audit', 'fixture', posted_at=now()), config)
    yield config, db, path
    db.close()


@pytest.mark.parametrize('label', [
    'Describe your citizenship', 'Tell us your visa status',
    'Describe your criminal history', 'Explain your disability',
    'Tell us your gender', 'Describe your GPA', 'Tell us your graduation year',
])
async def test_requested_adversarial_labels_never_generate(isolated, monkeypatch, label):
    config, db, _ = isolated
    browser_call = AsyncMock(side_effect=AssertionError('Forbidden factual generation'))
    cli_call = AsyncMock(side_effect=AssertionError('Forbidden factual transport'))
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', browser_call)
    monkeypatch.setattr(CodexWritingProvider, 'run', cli_call)
    # Empty profile: no applicant fact can be supplied by the resolver.
    manager = AIManager(config, db, None)
    question = Question('fact', label, 'textarea', required=True)
    assert manager.resolver.resolve_result(question, db.application(1)).answer is None
    with pytest.raises(ProviderUnavailable, match='requires a verified factual answer'):
        await manager.draft(question, db.application(1))
    browser_call.assert_not_awaited()
    cli_call.assert_not_awaited()
    assert not db.rows('SELECT * FROM written_responses')


@pytest.mark.parametrize('operation', ['intent', 'confirmation', 'manual_command', 'retry', 'checkpoint'])
async def test_lifecycle_commit_export_failure_restart(isolated, monkeypatch, operation):
    config, db, path = isolated
    assert db.claim(1)
    db.lifecycle.begin_attempt(1, 3)
    db.lifecycle.transition(1, 'APPLYING')
    if operation == 'confirmation':
        db.lifecycle.record_submission_intent(1)
    if operation == 'checkpoint':
        db.lifecycle.record_ready(1, live=True, url=db.application(1)['canonical_url'])
    command = ManualCommand('inspect-only', 1, id='audit-command')
    page = SimpleNamespace(frames=[], url='https://jobs.lever.co/synthetic/audit')
    with monkeypatch.context() as patch:
        patch.setattr(db.history, 'sync', Mock(side_effect=OSError('synthetic export failure')))
        with pytest.raises(OSError, match='synthetic export failure'):
            if operation == 'intent':
                db.lifecycle.record_submission_intent(1)
            elif operation == 'confirmation':
                db.lifecycle.record_confirmation(1, 'Synthetic affirmative employer receipt', page.url)
            elif operation == 'manual_command':
                ManualCommands(db).enqueue(command)
            elif operation == 'retry':
                db.fail(1, 'synthetic network timeout', 3)
            else:
                await checkpoint(SimpleNamespace(config=config, db=db), 1, page)
        with sqlite3.connect(path) as reader:
            assert reader.execute('SELECT count(*) FROM history_dirty').fetchone()[0] == 1
            persisted = reader.execute('SELECT status,attempts,submit_intent_at,submission_confirmation_seen FROM applications').fetchone()
            assert persisted[1] == 1
            if operation in {'intent', 'confirmation'}:
                assert persisted[2]
                assert bool(persisted[3]) == (operation == 'confirmation')
            if operation == 'retry':
                assert persisted[0] == 'RETRY'
                retry_deadline = reader.execute('SELECT retry_at FROM applications').fetchone()[0]
            if operation == 'manual_command':
                assert reader.execute('SELECT state FROM manual_commands WHERE id=?', (command.id,)).fetchone()[0] == 'PENDING'
        # Abrupt connection close deliberately bypasses graceful export.
        db.conn.close()
        db._closed = True
    restarted = Database(path, startup_maintenance=False)
    try:
        assert not restarted.rows('SELECT * FROM history_dirty')
        restarted.recover()
        app = restarted.application(1)
        assert app['attempts'] == 1
        if operation == 'intent':
            assert app['application_state'] == 'UNKNOWN'
            assert app['submit_intent_at'] and not app['retry_allowed']
            assert restarted.claim(1) is None
        elif operation == 'confirmation':
            assert app['status'] == 'SUBMITTED' and app['submission_confirmation_seen']
            assert restarted.claim(1) is None
        elif operation == 'manual_command':
            assert restarted.one('SELECT state FROM manual_commands WHERE id=?', (command.id,))['state'] == 'PENDING'
            assert ManualCommands(restarted).enqueue(command) == command.id
            assert len(restarted.rows('SELECT * FROM manual_commands')) == 1
        elif operation == 'retry':
            assert app['status'] == 'RETRY' and app['retry_allowed'] and app['retry_at']
            assert app['retry_at'] == retry_deadline
            assert len(restarted.rows("SELECT * FROM events WHERE kind='retry_decision'")) == 1
        else:
            saved = json.loads((config.private / 'fill-only-checkpoints/1.json').read_text())
            assert saved['readiness'] == 'RECONSTRUCTABLE_CHECKPOINT'
            assert snapshot(restarted, 1)['readiness'] == 'RECONSTRUCTABLE_CHECKPOINT'
            assert not snapshot(restarted, 1)['session']['live_page']
            assert not app['session_preserved']
        archived = json.loads((restarted.history.paths['1'] / 'application.json').read_text())
        assert archived['application_id'] == '1'
        assert not restarted.rows('SELECT * FROM history_dirty')
    finally:
        restarted.close()
