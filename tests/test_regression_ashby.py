"""Regressions recovered from the first real Ashby application attempt."""
from dataclasses import replace

from autoapply.jobs import job_identity


def test_ashby_application_tab_is_same_requisition():
    base = 'https://jobs.ashbyhq.com/persona/eb77c97c-fa9d-4bf0-9566-e5ba4453b7d3'
    assert job_identity(base) == job_identity(base + '/application?embed=true')
    assert job_identity(base) != job_identity('https://jobs.ashbyhq.com/persona/another-job/application')


def test_ashby_discovery_deduplicates_application_tab(config, db, listing):
    base = 'https://jobs.ashbyhq.com/persona/eb77c97c-fa9d-4bf0-9566-e5ba4453b7d3'
    first, created = db.ingest(replace(listing, url=base), config)
    assert created
    second, created = db.ingest(replace(listing, url=base + '/application?embed=true', source='second-source'), config)
    assert not created
    assert second == first
    assert db.one('SELECT count(*) n FROM applications')['n'] == 1


def test_ashby_prior_submit_blocks_alias_with_changed_title(config, db, listing):
    base = 'https://jobs.ashbyhq.com/persona/eb77c97c-fa9d-4bf0-9566-e5ba4453b7d3'
    first, _ = db.ingest(replace(listing, url=base), config)
    db.transition(first, 'SUBMITTING', submit_intent_at='2026-09-21T03:12:29+00:00')
    db.transition(first, 'MANUAL_REVIEW', 'Possible spam')
    other, _ = db.ingest(replace(listing, url='https://example.test/legacy', title='Changed title'), config)
    db.execute('UPDATE jobs SET canonical_url=? WHERE id=?', (base + '/application?embed=true', other))
    assert db.submission_conflict(other)['id'] == first
