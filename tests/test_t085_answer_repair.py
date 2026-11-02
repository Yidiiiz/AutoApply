"""Offline answer repair with synthetic facts and a stub provider only."""
from contextlib import contextmanager
from threading import Event, Thread

import pytest
import yaml

from autoapply.extension_api import resolve_answers
from autoapply.dashboard import AppliedRejected
from test_dashboard import dashboard


@pytest.fixture
def repair_profile(config, dashboard, monkeypatch):
    profile = config.profile
    profile.update(skills=['Python'], locations={'ordered_preferences': ['City A, NY', 'City B, TX', 'City C, CA']},
                   application_preferences={'ordinary_policy_acknowledgement': True,
                                            'recruiting_sms_whatsapp': True},
                   demographics={'veteran_status': 'Not a veteran', 'hispanic_latino': 'decline'})
    save_profile(config, profile)
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: pytest.fail('Unexpected provider request'))
    return profile


def save_profile(config, profile):
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')


def field(key='tools', **changes):
    return dict(dict(key=key, label='Which programming tools do you use?', kind='select',
                     required=True, options=['Python (programming)', 'Rust']), **changes)


def proposal(key='tools', value='Python (programming)', fact_ids=None, **changes):
    return dict(dict(key=key, value=value, fact_ids=fact_ids or ['skills'], needs_input=False, reason='mapped'), **changes)


def resolve(dashboard, fields, url='https://fixture.test/job'):
    return resolve_answers(dashboard, dict(url=url, fields=fields))['answers']


def test_one_batch_outside_all_locks_and_no_persistence(dashboard, config, db, repair_profile, monkeypatch):
    connections, reads, calls = [], [], []
    original_connection = dashboard.connection

    @contextmanager
    def tracked_connection(**kwargs):
        assert not kwargs.get('writable')
        with original_connection(**kwargs) as reader:
            connections.append(reader)
            try:
                yield reader
            finally:
                connections.remove(reader)

    monkeypatch.setattr(dashboard, 'connection', tracked_connection)
    before = db.answer_revision()

    def writer(request):
        assert not connections
        ready = Event()

        def settings_read():
            reads.append(dashboard.settings.get())
            ready.set()

        thread = Thread(target=settings_read, daemon=True)
        thread.start()
        assert ready.wait(2), 'Provider must not hold the Settings mutex'
        thread.join(2)
        calls.append(request)
        return {'answers': [proposal(), proposal('school', 'Example University (Main Campus)', ['education.school']),
                            proposal('text', 'Python')]}

    monkeypatch.setattr(dashboard, 'run_writing', writer)
    fields = [field(), field('school', label='School', options=['Example University (Main Campus)', 'Other']),
              field('text', kind='textarea', options=[], max_length=20)]
    for _ in range(2):
        rows = resolve(dashboard, fields)
        assert [r['status'] for r in rows] == ['RESOLVED'] * 3
        assert all(r['reason_code'] == 'answer_available' and r['evidence'] for r in rows)
        assert all(r['source'] == 'grounded_ai:answer_repair' for r in rows)
    assert len(calls) == len(reads) == 1
    assert calls[0]['task'] == 'answer_repair'
    assert set(calls[0]['facts']) == {'skills', 'education.school'}
    assert 'profile' not in calls[0] and calls[0]['job'] == {}
    assert calls[0]['fields'][1]['fact_ids'] == ['education.school']
    assert db.answer_revision() == before
    assert db.one('SELECT COUNT(*) AS n FROM known_answers')['n'] == 0


@pytest.mark.parametrize('change', ['profile', 'facts'])
def test_revision_change_during_provider_discards_answers(dashboard, config, repair_profile, monkeypatch, change):
    def writer(request):
        if change == 'profile':
            repair_profile['skills'] = ['Rust']
            save_profile(config, repair_profile)
        else:
            with dashboard.connection(writable=True) as db:
                db.set_setting('verified_fact:education.school', {'value': 'Another school', 'source': 'USER_PROVIDED'})
        return {'answers': [proposal()]}

    monkeypatch.setattr(dashboard, 'run_writing', writer)
    row, = resolve(dashboard, [field()])
    assert row['value'] is None and row['reason_code'] == 'stale_data'


@pytest.mark.parametrize('bad', [
    {'answers': [proposal(value='Unavailable')]},
    {'answers': [proposal(fact_ids=['education.school'])]},
    {'answers': [proposal(fact_ids=['invented'])]},
    {'answers': [proposal(value=['Python (programming)', 'Rust'])]},
    {'answers': [proposal(needs_input=True)]},
    {'answers': [proposal(key='wrong')]},
    {'answers': [proposal(), proposal()]},
    {'answers': [dict(proposal(), unexpected=True)]},
    {'answers': 'invalid'},
])
def test_invalid_output_never_fills(dashboard, repair_profile, monkeypatch, bad):
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: bad)
    row, = resolve(dashboard, [field()])
    assert row['value'] is None and row['status'] == 'NEEDS_INPUT'
    assert row['reason_code'] == 'provider_invalid'


@pytest.mark.parametrize('value', ['Select One', 'Choose an option'])
def test_offered_placeholder_is_invalid(dashboard, repair_profile, monkeypatch, value):
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: {'answers': [proposal(value=value)]})
    row, = resolve(dashboard, [field(options=[value, 'Python (programming)', 'Rust'])])
    assert row['value'] is None and row['reason_code'] == 'provider_invalid'


def test_multiselect_cardinality_none_and_text_limits(dashboard, repair_profile, monkeypatch):
    for value in (['Python', 'None of the Above'], ['Python', 'Python'], ['Python', 'Rust']):
        dashboard._ext_answer_repairs = {}
        monkeypatch.setattr(dashboard, 'run_writing', lambda request: {'answers': [proposal(value=value)]})
        row, = resolve(dashboard, [field(kind='checkbox', multiple=True,
                                       options=['Python', 'Rust', 'None of the Above'], max_selections=1)])
        assert row['reason_code'] == 'provider_invalid'
    for limit in (0, 3):
        monkeypatch.setattr(dashboard, 'run_writing', lambda request: {'answers': [proposal(value='Python')]})
        row, = resolve(dashboard, [field(kind='textarea', options=[], max_length=limit)])
        assert row['value'] is None and row['reason_code'] == 'provider_invalid'
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: {'answers': [proposal(value=['Python'])]})
    row, = resolve(dashboard, [field(kind='checkbox', multiple=True, options=['Python', 'Rust'], max_selections=1)])
    assert row['value'] == ['Python'] and row['status'] == 'RESOLVED'


@pytest.mark.parametrize('error,code', [(TimeoutError('fixture failure'), 'provider_unavailable'),
                                      (AppliedRejected(400, 'fixture malformed JSON'), 'provider_invalid')])
def test_failure_and_honest_abstention_are_cached(dashboard, repair_profile, monkeypatch, error, code):
    calls = []

    def unavailable(request):
        calls.append(request)
        raise error

    monkeypatch.setattr(dashboard, 'run_writing', unavailable)
    for _ in range(2):
        row, = resolve(dashboard, [field()])
        assert row['value'] is None and row['reason_code'] == code
    assert len(calls) == 1
    dashboard._ext_answer_repairs = {}
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: {'answers': [
        proposal(value=None, needs_input=True, reason='needs_fact')]})
    row, = resolve(dashboard, [field()])
    assert row['reason_code'] == 'needs_fact' and row['value'] is None


def test_explicit_factual_choice_and_visa_brand_location_reach_provider(dashboard, config, repair_profile, monkeypatch):
    calls = []

    def writer(request):
        calls.append(request)
        return {'answers': [proposal('sponsor', 'I do not need employer sponsorship', ['work_authorization.sponsorship_now']),
                            proposal('office', 'City A, NY (HQ)', ['locations.ordered_preferences'])]}

    monkeypatch.setattr(dashboard, 'run_writing', writer)
    fields = [field('sponsor', label='Do you require sponsorship now?',
                    options=['I need employer sponsorship', 'I do not need employer sponsorship']),
              field('office', label='Visa operates in various locations. Which office location do you prefer?',
                    options=['City A, NY (HQ)', 'City B, TX (HQ)'])]
    rows = resolve(dashboard, fields)
    assert [r['value'] for r in rows] == ['I do not need employer sponsorship', 'City A, NY (HQ)']
    assert len(calls) == 1
    assert calls[0]['facts']['work_authorization.sponsorship_now'] is False
    assert set(calls[0]['facts']) == {'work_authorization.sponsorship_now', 'locations.ordered_preferences'}
    # No immigration status fact is established by authorization or citizenship.
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: pytest.fail('Unknown legal fact reached provider'))
    row, = resolve(dashboard, [field('visa_status', label='What is your current visa status?', options=['F-1', 'H-1B'])])
    assert row['value'] is None and row['reason_code'] == 'needs_fact'


def test_sensitive_unknown_optional_decline_and_protected_policies(dashboard, config, repair_profile):
    repair_profile['autofill_policies'] = {'education.school': 'DO_NOT_ANSWER', 'skills': 'REQUIRE_USER'}
    save_profile(config, repair_profile)
    fields = [field('spouse', label='Are you a military spouse?', options=['Yes', 'No']),
              field('guard', label='Are you currently a member of the National Guard or Reserves?', required=False, options=['Yes', 'No']),
              field('ai', label='Would you like to opt out of AI review?', required=False, options=['Yes', 'No']),
              field('achieved', label='Highest level of education ACHIEVED', options=["Bachelor's degree", 'High school']),
              field('race', label='Are you Hispanic or Latino?', required=False, options=['Yes', 'No']),
              field('race_required', label='Are you Hispanic or Latino?', options=['Yes', 'No']),
              field('school', label='School', options=['Example University (Main Campus)']),
              field('skills', label='Skills', options=['Python (programming)']),
              field('trap', label='Do not fill this if you are human'),
              field('credential', label='Password for your programming tools account', kind='text', options=[]),
              field('empty', options=[]), field('file', kind='file', label='Unknown attachment', options=[])]
    rows = {r['key']: r for r in resolve(dashboard, fields)}
    assert all(r['value'] is None for r in rows.values())
    assert rows['achieved']['reason_code'] == 'needs_fact'
    assert rows['race']['reason_code'] == 'optional_skip'
    assert rows['guard']['status'] == rows['ai']['status'] == 'OPTIONAL_SKIP'
    assert rows['empty']['reason_code'] == 'no_offered_options'
    assert rows['file']['reason_code'] == 'unsupported_field'


def test_captured_wordings_resolve_without_provider(dashboard, repair_profile):
    rows = resolve(dashboard, [
        field('graduation', label='Anticipated graduation from current program', kind='combobox', options=['2027', '2028', '2029']),
        field('year', label='Expected graduation year', options=['2026 - 2027', '2028 - 2029']),
        field('date', label='Anticipated graduation from current program', kind='date', options=[]),
        field('locations', label='Visa operates in various locations. What are your top 2 office locations?', kind='textarea', options=[]),
        field('locations_choice', label='What are your top 2 office locations?', kind='checkbox', multiple=True,
              options=['City C, CA', 'City B, TX', 'City A, NY']),
        field('privacy', label='By checking this box, you will declare that you read and understand the privacy policy of Visa.', kind='checkbox', options=[]),
        field('sms', label='Do you consent to receive recruiting SMS text messages?', options=['Yes', 'No']),
        field('sms_out', label='Would you like to opt out of recruiting SMS text messages?', options=['Yes', 'No']),
    ])
    assert [r['value'] for r in rows] == ['2028', '2028 - 2029', '2028-05-01', 'City A, NY; City B, TX',
                                         ['City A, NY', 'City B, TX'], True, 'Yes', 'No']


@pytest.mark.parametrize('supplied', [True, False])
def test_captured_sms_disclaimer_and_actual_negation(dashboard, config, repair_profile, supplied):
    repair_profile['application_preferences']['recruiting_sms_whatsapp'] = supplied
    save_profile(config, repair_profile)
    labels = [
        'I agree that Visa may reach out to me via SMS regarding my application and candidate experience. '
        'Message and data rates may apply. I can opt-out at any time.',
        'Would you like to opt out of recruiting SMS text messages?',
        'Do not contact me via SMS.',
        'I agree not to receive recruiting SMS text messages.',
        'Do you consent to receive recruiting SMS? You may opt out at any time.',
        'Do you agree to receive SMS and opt out of WhatsApp?',
    ]
    rows = resolve(dashboard, [field(str(i), label=label, options=['Yes', 'No'])
                               for i, label in enumerate(labels)])
    yes, no = ('Yes', 'No') if supplied else ('No', 'Yes')
    assert [r['value'] for r in rows] == [yes, no, no, no, yes, None]


def test_graduation_does_not_borrow_current_program_for_other_qualification(dashboard, config, repair_profile):
    from autoapply.answers import AnswerResolver
    from autoapply.models import Question
    labels = ['Anticipated High School graduation date', 'Previous qualification graduation year',
              "Anticipated master's graduation date"]
    rows = resolve(dashboard, [field(str(i), label=label, options=['2028', '2029'])
                               for i, label in enumerate(labels)])
    assert all(r['value'] is None and r['reason_code'] == 'needs_fact' for r in rows)
    with dashboard.connection() as db:
        resolver = AnswerResolver(config, db)
        assert all(resolver.resolve(Question(str(i), label, 'select', True, ['2028', '2029']),
                                    dict(id=42, company='Fixture')) is None for i, label in enumerate(labels))
    repair_profile['education_history'] = [dict(school='Fixture High School', graduation_date='2022-06')]
    save_profile(config, repair_profile)
    row, = resolve(dashboard, [field('section', label='End date', section='Education', kind='date', options=[])])
    assert row['value'] == '2022-06-01'


def test_no_completed_degree_or_unknown_locations_inferred(dashboard, config, repair_profile, monkeypatch):
    repair_profile['education']['highest_completed_degree'] = 'High school'
    repair_profile['application_preferences']['recruiting_sms_whatsapp'] = False
    save_profile(config, repair_profile)
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: {'answers': [
        proposal('locations', value=None, fact_ids=['locations.ordered_preferences'], needs_input=True, reason='needs_fact')]})
    rows = resolve(dashboard, [field('degree', label='Highest level of education completed', options=['High school', "Bachelor's degree"]),
        field('locations', label='What are your top 2 office locations?', kind='checkbox', multiple=True, options=['City A, NY', 'Unknown City, FL']),
        field('sms', label='Do you consent to receive SMS text messages?', options=['Yes', 'No'])])
    assert rows[0]['value'] == 'High school'
    # No second compatible location is known. The provider may only abstain.
    assert rows[1]['value'] is None
    assert rows[2]['value'] == 'No'


def test_batch_bound_and_context_options_and_facts_invalidate_cache(dashboard, config, repair_profile, monkeypatch):
    calls = []

    def writer(request):
        calls.append(request)
        return {'answers': [proposal(f['key'], value=None, needs_input=True, reason='ambiguous') for f in request['fields']]}

    monkeypatch.setattr(dashboard, 'run_writing', writer)
    fields = [field(str(i)) for i in range(22)]
    rows = resolve(dashboard, fields)
    assert len(calls) == 1 and len(calls[0]['fields']) == 20
    assert rows[-1]['reason_code'] == 'unsupported_field'
    resolve(dashboard, fields)
    assert len(calls) == 1
    resolve(dashboard, fields, url='https://fixture.test/another-job')
    assert len(calls) == 2
    fields[0]['options'] = ['Python tooling', 'Rust tooling']
    resolve(dashboard, fields)
    assert len(calls) == 3
    repair_profile['skills'] = ['Python', 'Rust']
    save_profile(config, repair_profile)
    resolve(dashboard, fields)
    assert len(calls) == 4
