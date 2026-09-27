"""Local fixture checks of Phase 4's final-interaction durability boundary."""
import sqlite3

import pytest

from autoapply.submission_probe import SubmissionProbe
from test_browser import browser, site, prepare

pytestmark = pytest.mark.browser


@pytest.mark.parametrize('fail_intent_export', [False, True])
async def test_intent_is_committed_before_final_interaction(config, db, browser, listing, site, monkeypatch, fail_intent_export):
    engine = await prepare(config, db, browser, listing, site)
    original_click = SubmissionProbe.physical_click
    original_sync = db.history.sync
    clicks, failures = [], []

    async def checked_click(probe):
        assert not db.conn.in_transaction
        with sqlite3.connect(config.private / 'test.sqlite3') as reader:
            state, intent, retry = reader.execute('SELECT status,submit_intent_at,retry_allowed FROM applications WHERE id=1').fetchone()
        assert state == 'SUBMITTING' and intent and not retry
        clicks.append(True)
        await original_click(probe)

    def sync(*args):
        if fail_intent_export and db.application(1)['status'] == 'SUBMITTING' and not failures:
            failures.append(True)
            raise OSError('Interrupted intent export')
        return original_sync(*args)

    monkeypatch.setattr(SubmissionProbe, 'physical_click', checked_click)
    monkeypatch.setattr(db.history, 'sync', sync)
    await engine.resume_manual(1)
    assert db.application(1)['submit_intent_at']
    assert not db.application(1)['retry_allowed']
    if fail_intent_export:
        assert failures and not clicks and not site[1]
        assert db.application(1)['application_state'] == 'UNKNOWN'
    else:
        assert len(clicks) == len(site[1]) == 1
        assert db.application(1)['submission_confirmation_seen']
