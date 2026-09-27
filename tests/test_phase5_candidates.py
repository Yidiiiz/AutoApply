"""Conclusive data-only rejects and protected live boundaries."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from autoapply.candidate_policy import evaluate, preflight
from autoapply.engine import Engine
from autoapply.fill_batch import discover, EXCLUDED
from autoapply.models import now
from autoapply.profile_snapshot import ProfileSnapshot


def candidate(**changes):
    return dict({'id': 1, 'title': 'Software Intern', 'company': 'Example', 'posted_at': now(),
                 'location': 'New York, NY', 'description': 'Undergraduate internship.',
                 'canonical_url': 'https://jobs.lever.co/example/one'}, **changes)


@pytest.mark.parametrize('changes,reason', [
    ({'listing_status': 'CLOSED'}, 'confirmed_closed'),
    ({'listing_status': 'REMOVED'}, 'confirmed_closed'),
    ({'posted_at': (datetime.now(timezone.utc)-timedelta(days=31)).isoformat()}, 'stale_listing'),
    ({'location': 'Toronto, Canada'}, 'foreign_only_location'),
    ({'location': 'Berlin, Germany; Paris, France'}, 'foreign_only_location'),
    ({'description': 'Must have a masters degree'}, 'incompatible_degree'),
    ({'description': 'Must graduate in 2027'}, 'incompatible_graduation'),
    ({'description': 'Required qualifications\nGraduate between 2026 and 2027'}, 'incompatible_graduation'),
])
def test_conclusive_rejects(config, changes, reason):
    result = evaluate(candidate(**changes), config.profile_snapshot())
    assert result.status == 'REJECT' and result.reason == reason and result.evidence


@pytest.mark.parametrize('description,path,value,reason', [
    ('Must be authorized to work in the US', 'work_authorization.us_authorized', False, 'incompatible_authorization'),
    ('Must be a U.S. citizen', 'citizenship.us_citizen', False, 'incompatible_citizenship'),
    ('We cannot provide visa sponsorship', 'work_authorization.sponsorship_future', True, 'incompatible_sponsorship'),
])
def test_verified_legal_incompatibility(config, description, path, value, reason):
    profile = config.profile
    section, name = path.split('.')
    profile[section][name] = value
    result = evaluate(candidate(description=description), ProfileSnapshot.create(profile))
    assert result.reason == reason and not result.proceed
    profile[section][name] = None
    assert evaluate(candidate(description=description), ProfileSnapshot.create(profile)).proceed


@pytest.mark.parametrize('changes', [
    {'description': '', 'title': 'Senior scientist PhD'},
    {'description': 'A growing team with excellent benefits.', 'title': 'Engineer'},
    {'location': ''}, {'location': 'Remote'}, {'posted_at': None},
    {'location': 'London'}, {'location': 'Canada or Remote'},
    {'location': 'New York, NY; Toronto, Canada'},
])
def test_incomplete_listing_remains_for_enrichment(config, changes):
    result = evaluate(candidate(**changes), config.profile_snapshot())
    assert result.status == 'NEEDS_ENRICHMENT' and result.proceed


@pytest.mark.parametrize('description', [
    'Preferred qualifications\nMust have a masters degree',
    'Must have a masters degree or equivalent experience',
    'Must graduate before 2027',
    'Graduation in 2027 preferred',
    'Must graduate in 2027 or 2028',
    'Must be authorized to work in the US or Canada',
    'Must not be a U.S. citizen',
    'Our last cohort graduated in 2027',
    'Preferred Skills & Experience:\nMust graduate in 2027',
])
def test_unsupported_or_preferred_requirement_cannot_reject(config, description):
    assert evaluate(candidate(description=description), config.profile_snapshot()).proceed


def test_sparse_applicant_does_not_imply_reject():
    snapshot = ProfileSnapshot.create({})
    job = candidate(description='Must have a masters degree\nMust graduate in 2027\nMust be authorized to work in the US')
    assert evaluate(job, snapshot).proceed


def test_mode_restrictions_and_protection(config):
    snapshot = config.profile_snapshot()
    for kwargs, reason in [({'controlled_target': 2}, 'controlled_target_mismatch'),
                           ({'retired': True}, 'permanently_retired'),
                           ({'mode': 'fill_only', 'historical_exclusions': (1,)}, 'historical_exclusion'),
                           ({'conflict': {'id': 2}}, 'exact_protected_duplicate')]:
        result = evaluate(candidate(), snapshot, **kwargs)
        assert result.status == 'HOLD' and result.reason == reason
    assert evaluate(candidate(submit_intent_at=now()), snapshot).reason == 'protected_application'
    assert evaluate(candidate(), snapshot).status == 'ALLOW'


def test_profile_correction_changes_preflight(config, db, listing):
    db.ingest(replace(listing, description='Must be authorized to work in the US'), config)
    db.set_setting('verified_fact:work_authorization.us_authorized', {'value': 'No', 'source': 'USER_PROVIDED'})
    first = preflight(db, config, db.application(1))
    assert first.reason == 'incompatible_authorization'
    db.set_setting('verified_fact:work_authorization.us_authorized', {'value': 'Yes', 'source': 'USER_PROVIDED'})
    second = preflight(db, config, db.application(1))
    assert second.proceed and first.profile_revision != second.profile_revision


def test_same_title_distinct_requisitions_and_exact_alias(config, db, listing):
    db.ingest(listing, config)
    db.transition(1, 'SUBMITTED', confirmation_text='Fixture confirmation')
    db.ingest(replace(listing, url='https://jobs.lever.co/example/two'), config)
    assert preflight(db, config, db.application(2)).proceed
    db.execute('UPDATE jobs SET canonical_url=? WHERE id=2', (listing.url + '/apply?utm_source=other',))
    result = preflight(db, config, db.application(2))
    assert result.status == 'HOLD' and result.reason == 'exact_protected_duplicate'


def test_legacy_similarity_explicitly_ambiguous(config, db, listing):
    db.ingest(replace(listing, url='https://example.test/one'), config)
    db.transition(1, 'SUBMITTED', confirmation_text='Fixture confirmation')
    db.ingest(replace(listing, url='https://example.test/two'), config)
    assert preflight(db, config, db.application(2)).reason == 'ambiguous_legacy_identity'


async def test_engine_conclusive_preflight_never_opens_browser(config, db, listing):
    db.ingest(replace(listing, description='Must have a masters degree'), config)
    engine = Engine(config, db)
    engine.browser.new_page = AsyncMock(side_effect=AssertionError('Browser must not open'))
    assert await engine.process_one(1)
    engine.browser.new_page.assert_not_called()
    assert db.application(1)['status'] == 'INELIGIBLE'
    assert not db.application(1)['submit_intent_at']


def test_fill_only_shares_policy_keeps_sparse_and_historical_exclusion(config, db, listing):
    db.ingest(replace(listing, description='Must have a masters degree'), config)
    db.ingest(replace(listing, url='https://example.test/sparse', title='Engineer', description=''), config)
    assert discover(db, config) == [2]
    assert EXCLUDED == frozenset({4571, 4535, 6401, 5310, 6415, 6416})
    db.set_setting('controlled_application_id', 1)
    assert discover(db, config) == []
