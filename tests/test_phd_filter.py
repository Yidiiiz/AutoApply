from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from autoapply.dashboard import Dashboard
from autoapply.engine import Engine
from autoapply.fill_batch import discover


def test_existing_phd_queue_filtered_without_writes(config, db, listing):
    roles = [
        ('PhD Research Intern', ''),
        ('Ph.D. Software Intern', 'Bachelor accepted'),
        ('Machine Learning Intern (PHD)', ''),
        ('Software Intern', 'Currently pursuing a PhD in CS'),
        ('Software Intern', "Pursuing a Bachelor's, Master's or PhD"),
        ('Software Intern', 'Undergraduate internship'),
    ]
    for i, (title, description) in enumerate(roles):
        db.ingest(replace(listing, title=title, description=description,
                          url=f'https://example.test/jobs/{i}'), config)
    before = {table: db.rows(f'SELECT * FROM {table}') for table in ('jobs', 'applications', 'events')}
    assert discover(db, config) == [5, 6]
    assert [row['id'] for row in discover(db, config, details=True)] == [5, 6]
    dashboard = Dashboard(config, db)
    try:
        assert [row['application_id'] for row in dashboard.priority()['rows']] == [5, 6]
    finally:
        dashboard.close()
    assert {table: db.rows(f'SELECT * FROM {table}') for table in before} == before


@pytest.mark.parametrize('fill_only', [False, True])
@pytest.mark.parametrize('title,description', [
    ('PhD Intern', ''), ('Software Intern', 'Currently pursuing a PhD in CS'),
])
async def test_automatic_fill_rejects_before_browser(config, db, listing, monkeypatch, fill_only, title, description):
    db.ingest(replace(listing, title=title, description=description), config)
    # Satisfy the fill-only guard through a synthetic read; do not change settings.
    setting = db.setting
    monkeypatch.setattr(db, 'setting', lambda key, *args: False if key == 'auto_submit' else setting(key, *args))
    engine = Engine(config, db, fill_only=fill_only)
    engine.open_application_page = AsyncMock(side_effect=AssertionError('Must not open a page'))
    assert await engine.process_one(1)
    engine.open_application_page.assert_not_called()
    assert db.application(1)['status'] == 'INELIGIBLE'
    assert db.application(1)['submit_intent_at'] is None
    assert not db.application(1)['submission_confirmation_seen']
