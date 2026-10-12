import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autoapply.applications import GenericApplicationAdapter
from autoapply.eligibility_questions import assess_eligibility, unresolved_questions, request_unresolved
from autoapply.jobs import eligibility
from autoapply.models import Answer
from autoapply.profile_snapshot import ProfileSnapshot
from autoapply.requirements import extract_requirements
from autoapply.standing import MEANINGS, resolve_standing


def job(description):
    return dict(id=1, company='Example Employer', title='Engineering Intern Summer 2027',
                location='Georgia, USA', canonical_url='https://example.test/intern', description=description)


def synthetic_profile(base):
    profile = copy.deepcopy(base)
    profile['education']['school_year'] = 'Junior'
    profile['contact']['country'] = 'United States'
    profile['availability'] = {'summer_2027': {'start_date': '2027-05-24', 'end_date': None}}
    profile['skills'] = ['Python', 'RESTful APIs']
    profile['verified_facts'] = {
        'fixture.polling': 'I built a cross-platform tray application that polls an example course service in real time to monitor seat availability, using Python, Beautiful Soup, Requests, and PyInstaller.',
        'fixture.integration': 'I architected server-client communication and replication systems for real-time multiplayer interaction.',
    }
    profile['fact_sources'] = {key: dict(file='synthetic-resume.pdf', sha256='0' * 64, page=1,
                                        basis='Synthetic user-supplied resume; manually checked against rendered page')
                               for key in profile['verified_facts']}
    profile['eligibility_assertions'] = [dict(id=key, meaning=MEANINGS[key], source='USER_PROVIDED',
                                             scope='standing', answer=True)
                                        for key in ('communication', 'independent_team_initiative', 'office_familiarity')]
    return profile


@pytest.fixture
async def sanitized_listing():
    html = (Path(__file__).parent / 'fixtures/eligibility-4615-sanitized.html').read_text(encoding='utf-8')
    page = SimpleNamespace(content=AsyncMock(return_value=html))
    inspected = await GenericApplicationAdapter(page).inspect()
    return job(inspected['description'])


async def test_saved_public_evidence_four_unknowns(config, sanitized_listing):
    profile = synthetic_profile(config.profile)
    before = copy.deepcopy(profile)
    check = eligibility(sanitized_listing, profile)
    assert profile == before
    assert check.status == 'UNKNOWN_REQUIRES_USER'
    assert len(check.uncertainties) == 4, check.uncertainties
    for text in ('Familiarity with AI', 'Proficiency in Windows and Microsoft Office',
                 'Ability to work with limited direction', 'June – August 2027 (10-week program)'):
        assert sum(text in u for u in check.uncertainties) == 1, check.uncertainties
    required = [r.text for r in extract_requirements(sanitized_listing['description']) if not r.preferred]
    preferred = [r.text for r in extract_requirements(sanitized_listing['description']) if r.preferred]
    assert len(preferred) == 2
    assert any('Python preferred' in r for r in preferred)
    assert any('preferably in Computer Science' in r for r in preferred)
    assert any('Rising Junior' in r for r in required)
    assert any('primary residence' in r for r in required)
    for excluded in ('Gain new exposure', 'rewarding internship', 'pay range posted',
                     'equal opportunity', 'Personal data collected', 'best of your knowledge',
                     'file types', 'talent community', 'self-identification survey'):
        assert all(excluded not in r for r in required)
    questions = list(unresolved_questions(check, sanitized_listing))
    assert len(questions) == 4 and len({q.key for q in questions}) == 4
    assert all(q.required and q.policy == 'REQUIRE_USER' and q.scope.startswith('application:1:eligibility:') for q in questions)
    assert all('APIs' not in q.label and 'independently' not in q.label for q in questions)


@pytest.mark.parametrize('heading', [
    'Benefits', 'BENEFITS:', 'Our Benefits & Perks', 'Compensation & Benefits', 'Pay Range:',
    'Salary', 'Salary Range', 'What We Offer', 'Perks', 'Equal Opportunity',
    'Equal Employment Opportunity Statement', 'EEO', 'EEO Policy', 'Privacy',
    'Recruiting Privacy Notice', 'Application Information', 'Application Questions',
    'Voluntary Self-Identification', 'Job Alerts', 'Create a Job Alert', 'Talent Community',
])
def test_section_boundaries(config, heading):
    desc = 'Requirements\nAbility to work independently\n' + heading + '\nGain new exposure and experience.\nRewarding experience.\nKnowledge of our benefit options.\nRequired Qualifications\nComfort with APIs'
    parsed = extract_requirements(desc)
    assert [r.text for r in parsed] == ['Ability to work independently', 'Comfort with APIs']
    assert eligibility(job(desc), config.profile).eligible is None


@pytest.mark.parametrize('text', [
    'Knowledge of compensation systems', 'Experience with privacy software',
    'Ability to explain benefits to customers', 'Skills in salary analysis',
    'Comfort with APIs', 'Interest in LLMs', 'Work independently',
    'Able to work with limited direction in a fast-paced environment',
])
def test_ordinary_requirement_sentences_are_not_headings(config, text):
    check = eligibility(job('Requirements\n' + text), config.profile)
    assert check.eligible is None and any(text in u for u in check.uncertainties)


@pytest.mark.parametrize('text', [
    'Gain new exposure and experience to multiple new tools', 'A rewarding internship experience',
    'Pay may depend on candidate experience', 'Complete and accurate to the best of your knowledge',
    'Our team values knowledge and skills',
])
def test_generic_words_outside_qualification_context(config, text):
    assert eligibility(job(text), config.profile).eligible is True


@pytest.mark.parametrize('heading', ['Preferred', 'Preferred qualifications', 'Nice-to-have', 'Bonus', 'Bonus Qualifications'])
def test_preferred_is_nonblocking_even_with_required_word(config, heading):
    assert eligibility(job(heading + '\nMinimum GPA 4.0 required\nMust have Python experience'), config.profile).eligible is True


def test_required_degree_survives_inline_preferred_major(config):
    desc = 'Requirements\nRising Junior or Senior pursuing a Bachelor’s degree or a graduate student in an accredited college or university, preferably in Computer Science, Software Engineering, Data Science, or a related technical field'
    profile = synthetic_profile(config.profile)
    profile['education']['major'] = 'Other'
    assert eligibility(job(desc), profile).eligible is True
    profile['education']['currently_enrolled'] = None
    assert eligibility(job(desc), profile).eligible is None


@pytest.mark.parametrize('missing', ['skills', 'verified_facts', 'fact_sources'])
async def test_api_requirement_needs_complete_verified_evidence(config, sanitized_listing, missing):
    profile = synthetic_profile(config.profile)
    profile.pop(missing)
    check = eligibility(sanitized_listing, profile)
    assert len(check.uncertainties) == 5
    assert any('APIs, scripting, and connecting systems' in u for u in check.uncertainties)


def test_api_evidence_never_satisfies_stronger_or_negative_claim(config):
    profile = synthetic_profile(config.profile)
    for line in ('Comfort working with APIs, scripting, and connecting systems together with five years of experience',
                 'Expert proficiency working with APIs, scripting, and connecting systems together'):
        assert eligibility(job('Required\n' + line), profile).eligible is None
    profile['verified_facts']['fixture.integration'] = 'I have not architected server-client communication and replication systems for real-time multiplayer interaction.'
    assert eligibility(job('Required\nComfort working with APIs, scripting, and connecting systems together'), profile).eligible is None


@pytest.mark.parametrize('text', ['Excellent written, verbal and communication skills',
                                'Excellent written, verbal, and communication skills',
                                'Excellent written, verbal and communication skills and ability to work independently'])
def test_communication_compound_only_known_clauses(config, text):
    assert resolve_standing(text, synthetic_profile(config.profile))


@pytest.mark.parametrize('suffix', [' in German', ', Python proficiency', ' and security clearance',
                                  ' and five years of professional experience'])
def test_communication_unsupported_compounds(config, suffix):
    assert resolve_standing('Excellent written, verbal and communication skills' + suffix, synthetic_profile(config.profile)) is None


@pytest.mark.parametrize('text', [
    'Must be available for the full ten-week internship from June–August 2027',
    'Availability for the entire program duration required',
    'The program takes place from June – August 2027 (10-week program).',
])
def test_start_date_does_not_prove_duration(config, text):
    profile = synthetic_profile(config.profile)
    result = eligibility(job(text), profile)
    assert result.status == 'UNKNOWN_REQUIRES_USER'
    assert result.uncertainties == ['Confirm full-program availability: ' + text]
    assert '2027-06-01' not in result.uncertainties[0]


def test_residence_is_separate_from_job_location_and_citizenship(config):
    desc = 'Application Questions\nTo be eligible for this position, your primary residence must be located in the United States. Please indicate the state in which you currently reside.'
    profile = config.profile
    assert eligibility(job(desc), profile).eligible is None
    profile['contact']['country'] = 'United States'
    assert eligibility(job(desc), profile).eligible is True
    profile['contact']['country'] = 'Canada'
    assert eligibility(job(desc), profile).eligible is False


async def test_inline_html_preserves_preferred_context():
    page = SimpleNamespace(content=AsyncMock(return_value='<main><h2>Requirements</h2><!-- Preferred --><p>Python experience <strong>\npreferred\n</strong></p><p>Familiarity with <a>LLMs</a> required</p><h2>Benefits</h2><p>Rewarding experience</p></main>'))
    listing = await GenericApplicationAdapter(page).inspect()
    parsed = extract_requirements(listing['description'])
    assert len(parsed) == 2 and parsed[0].preferred and not parsed[1].preferred


def test_focused_answers_scoped_and_no_blanket_override(config, db, listing):
    from dataclasses import replace
    from autoapply.control import Controller
    app_id, _ = db.ingest(replace(listing, description='Requirements\nFamiliarity with LLMs\nComfort with APIs'), config)
    db.claim(app_id)
    db.transition(app_id, 'NEEDS_INPUT')
    app = db.application(app_id)
    snapshot = config.profile_snapshot()
    check = assess_eligibility(app, snapshot, db)
    questions = list(unresolved_questions(check, app))
    rows = [db.question(app_id, q) for q in questions]
    control = Controller(config, db)
    control.answer(rows[0]['id'], 'Yes')
    assert db.application(app_id)['status'] == 'NEEDS_INPUT'
    assert len(assess_eligibility(app, snapshot, db).uncertainties) == 1
    control.answer(rows[1]['id'], 'Yes')
    assert db.application(app_id)['status'] == 'RETRY'
    assert db.application(app_id)['eligibility_override'] == 0
    assert assess_eligibility(app, snapshot, db).eligible is True
    assert eligibility(app, snapshot.facts).eligible is None  # profile not invented
    changed = dict(app, description=app['description'] + '\nKnowledge of robotics')
    assert len(assess_eligibility(changed, snapshot, db).uncertainties) == 3
    revised = ProfileSnapshot.create(dict(snapshot.facts, extra_fact='changed'))
    assert len(assess_eligibility(app, revised, db).uncertainties) == 2


def test_unverified_saved_answer_does_not_clear_requirement(config, db, listing):
    from dataclasses import replace
    app_id, _ = db.ingest(replace(listing, description='Requirements\nFamiliarity with LLMs'), config)
    app = db.application(app_id)
    snapshot = config.profile_snapshot()
    check = assess_eligibility(app, snapshot, db)
    row = db.question(app_id, next(unresolved_questions(check, app)))
    db.save_answer(row['id'], Answer('Yes', 'guessed'))
    assert assess_eligibility(app, snapshot, db).eligible is None
    assert json.loads(db.one('SELECT answer FROM questions WHERE id=?', (row['id'],))['answer']) == 'Yes'


@pytest.mark.parametrize('separator', ['; ', ', '])
def test_inline_preference_does_not_erase_required_clause(config, separator):
    desc = 'Requirements\nPython knowledge required' + separator + 'Rust experience preferred'
    check = eligibility(job(desc), config.profile)
    assert len(check.uncertainties) == 1 and 'Python' in check.uncertainties[0]
    assert 'Rust' not in check.uncertainties[0]


def test_candidate_and_live_parser_share_preferred_and_boundary_scope(config):
    from autoapply.candidate_policy import evaluate
    from autoapply.models import now
    for heading in ('Preferred', 'Bonus', 'Nice-to-have'):
        app = job(heading + '\nMust have a masters degree') | {'posted_at': now()}
        assert evaluate(app, config.profile_snapshot()).proceed
        assert eligibility(app, config.profile).eligible is True


@pytest.mark.parametrize('text,path', [
    ('Must be a U.S. citizen', 'citizenship.us_citizen'),
    ('Must be authorized to work in the US', 'work_authorization.us_authorized'),
])
def test_verified_legal_requirements_remain_conservative(config, text, path):
    profile = config.profile
    section, key = path.split('.')
    for value, expected in [(True, True), (False, False), (None, None)]:
        profile[section][key] = value
        assert eligibility(job('Application Questions\n' + text), profile).eligible is expected


def test_sponsorship_and_degree_and_gpa_unknowns(config):
    profile = config.profile
    profile['work_authorization']['sponsorship_now'] = None
    assert eligibility(job('No visa sponsorship'), profile).eligible is None
    profile['work_authorization']['sponsorship_now'] = True
    assert eligibility(job('No visa sponsorship'), profile).eligible is False
    for desc in ('Requirements\nCurrently enrolled in a degree program',
                 'Requirements\nMinimum GPA 3 5', 'Must be at least 18 years old'):
        assert eligibility(job(desc), {}).eligible is None


def test_form_questions_are_not_employer_constraints(config):
    desc = 'Application Questions\nWill you now or in the future require sponsorship?\nAre you authorized to work in the United States?'
    assert extract_requirements(desc) == []
    assert eligibility(job(desc), config.profile).eligible is True


def test_verified_degree_and_graduation_not_asked_again(config):
    desc = 'Requirements\nCurrently enrolled in a Bachelor degree in Computer Science\nMust graduate in 2028'
    assert eligibility(job(desc), config.profile).eligible is True
    assert eligibility(job('Requirements\nCurrently enrolled in a Bachelor degree in Computer Science and hold a security clearance'), config.profile).eligible is None
    assert eligibility(dict(job('Requirements'), title='PhD Intern'), {}).eligible is False


@pytest.mark.parametrize('text', ['Must work independently', 'Must be comfortable with APIs',
                                'Must be located in California', 'Must relocate to New York'])
def test_explicit_unheaded_qualifications_stay_unknown(config, text):
    check = eligibility(job(text), config.profile)
    assert check.eligible is None and len(check.uncertainties) == 1


@pytest.mark.parametrize('text', ['Python is a bonus', 'Python is nice-to-have'])
def test_inline_preference_variants(config, text):
    assert eligibility(job('Requirements\n' + text), config.profile).eligible is True


def test_focused_no_uses_existing_ineligible_transition(config, db, listing):
    from dataclasses import replace
    from autoapply.control import Controller
    app_id, _ = db.ingest(replace(listing, description='Requirements\nFamiliarity with LLMs'), config)
    db.claim(app_id)
    db.transition(app_id, 'NEEDS_INPUT')
    app = db.application(app_id)
    check = assess_eligibility(app, config.profile_snapshot(), db)
    request_unresolved(db, app, check)
    row = db.one('SELECT * FROM questions WHERE application_id=?', (app_id,))
    Controller(config, db).answer(row['id'], 'No')
    assert db.application(app_id)['status'] == 'INELIGIBLE'
    assert db.application(app_id)['eligibility_override'] == 0


def test_pending_refresh_leaves_verified_facts_and_legacy_questions_alone(config, db, listing):
    from dataclasses import replace
    from autoapply.models import Question
    app_id, _ = db.ingest(replace(listing, description='Requirements\nStrong communication skills\nKnowledge of LLMs'), config)
    app = db.application(app_id)
    db.question(app_id, Question('eligibility', 'Legacy confirmation', required=True))
    request_unresolved(db, app, assess_eligibility(app, config.profile_snapshot(), db))
    verified = ProfileSnapshot.create(synthetic_profile(config.profile))
    request_unresolved(db, app, assess_eligibility(app, verified, db))
    pending = db.rows("SELECT * FROM questions WHERE application_id=? AND status='PENDING'", (app_id,))
    assert len(pending) == 2
    assert any(r['field_key'] == 'eligibility' for r in pending)
    assert any('LLMs' in r['raw_question'] for r in pending)
    assert not any('communication' in r['raw_question'] for r in pending)


def test_changed_listing_cannot_reuse_confirmation_memory(config, db, listing):
    from dataclasses import replace
    from autoapply.answers import AnswerResolver
    from autoapply.control import Controller
    app_id, _ = db.ingest(replace(listing, description='Requirements\nKnowledge of LLMs'), config)
    db.claim(app_id)
    db.transition(app_id, 'NEEDS_INPUT')
    app = db.application(app_id)
    snapshot = config.profile_snapshot()
    request_unresolved(db, app, assess_eligibility(app, snapshot, db))
    row = db.one('SELECT * FROM questions WHERE application_id=?', (app_id,))
    Controller(config, db).answer(row['id'], 'Yes')
    updated = dict(app, description=app['description'] + '\nAbility to work weekends')
    for q in unresolved_questions(assess_eligibility(updated, snapshot, db), updated):
        assert AnswerResolver(config, db).resolve(q, updated) is None
    revised = ProfileSnapshot.create(dict(snapshot.facts, extra='revision'))
    request_unresolved(db, app, assess_eligibility(app, revised, db))
    assert db.one('SELECT status FROM questions WHERE id=?', (row['id'],))['status'] == 'PENDING'


async def test_engine_local_fixture_requests_only_four_unresolved(config, db, listing, sanitized_listing):
    from dataclasses import replace
    from autoapply.engine import Engine
    import yaml
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(synthetic_profile(config.profile)), encoding='utf-8')
    app_id, _ = db.ingest(replace(listing, url='http://127.0.0.1:8123/eligibility'), config)
    db.set_setting('auto_submit', False)
    engine = Engine(config, db, fill_only=True)
    html = (Path(__file__).parent / 'fixtures/eligibility-4615-sanitized.html').read_text(encoding='utf-8')
    try:
        await engine.browser.start()
        await engine.browser.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
        await engine.process_one(app_id)
        app = db.application(app_id)
        assert app['status'] == 'MANUAL_REVIEW' and app['error_category'] == 'INPUT_REQUIRED'
        pending = db.rows("SELECT * FROM questions WHERE application_id=? AND status='PENDING'", (app_id,))
        assert len(pending) == 4
        assert all(row['field_key'].startswith('eligibility:') and row['answer'] is None for row in pending)
        assert not db.one("SELECT * FROM events WHERE kind IN ('FIELD_FILLED','LIVE_CONTROL_COMMITTED')")
        assert app['submit_intent_at'] is None and not app['submission_confirmation_seen']
        engine.fill_invariant.check()
    finally:
        await engine.close()
