import json

import pytest

from autoapply.engine import Engine
from autoapply.models import Question
from autoapply.retry import ErrorCategory, RetryPolicy


@pytest.mark.parametrize('category', [
    ErrorCategory.EXTERNAL_EXECUTION_APPROVAL_REQUIRED,
    ErrorCategory.EXECUTION_APPROVAL_BLOCKED,
])
async def test_execution_approval_hold_does_not_request_applicant_answers(config, db, listing, category):
    db.ingest(listing, config)
    db.claim(1)
    # Even an older pending question must not turn this hold into an input request.
    db.question(1, Question('existing', 'Existing factual question', 'text', True), 'Missing fact')
    before = db.rows('SELECT * FROM questions')
    await Engine(config, db).handoff.request(1, None, 'External execution environment requires approval', category)
    app = db.application(1)
    assert app['error_category'] == category
    assert not app['retry_allowed'] and not app['manual_resume_allowed']
    assert not app['submit_intent_at']
    assert db.rows('SELECT * FROM questions') == before
    payload = json.loads(db.rows('SELECT payload FROM notifications')[-1]['payload'])
    assert payload['kind'] == 'execution_approval'
    assert 'No new applicant answer is requested' in payload['message']
    assert 'question_ids' not in payload
    assert not RetryPolicy().decide(category, 1, 3).allowed
