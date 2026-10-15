"""Sanitized Expedia labels with synthetic profile facts; no live providers/services."""
import json

import pytest
import yaml

from autoapply.answers import AnswerResolver
from autoapply.models import Question
from autoapply.dropdowns import ORDERED_LOCATIONS_SETTING
from test_dashboard import dashboard, http_dashboard
from test_extension_api import ext

ROLE = "If applying for a Software Development or Mobile Development stack/role, please select your role preferences. (Preference does not determine final stack placement.)"
LANGUAGE = "Are you proficient in any other languages below? Select All that apply?"
RESTRICTION = 'Are you subject to any non-competition, non-disclosure or other agreement that would preclude or restrict your employment at Expedia Group?'
LINKEDIN = 'If you would like us to view your LinkedIn profile, provide your profile URL (i.e. https://www.linkedin.com/in/YOURNAME).'
ROLES = ['Front-End', 'Back-End', 'Full Stack', 'Machine Learning Engineer', 'Mobile (iOS)', 'Mobile (Android)']
LOCATIONS = ['San Francisco, CA', 'San Jose, CA', 'New York City, New York', 'Seattle, WA', 'Austin, TX']
APP = dict(id=42, company='Expedia Group')


@pytest.fixture
def expedia_profile(config):
    profile = dict(config.profile)
    profile['contact'].update(permanent_address='123 Fixture Lane', permanent_city='Example City',
                              permanent_state='Maryland', country='United States', state='Maryland')
    profile['education']['country'] = 'United States'
    profile['links'] = dict(linkedin='https://www.linkedin.com/in/fixture-student')
    profile['locations'] = dict(ordered_preferences=LOCATIONS)
    profile['application_preferences'] = dict(role_preferences=ROLES, internship_cohort='No preference',
        terms_acknowledgement=True, ordinary_policy_acknowledgement=True)
    profile['languages'] = [dict(language='English', fluent=True, proficiency='highest')]
    profile['skills'] = ['Python', 'C/C++', 'C#']
    profile['demographics'] = dict(race='decline', gender='decline')
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    return profile


def test_snapshot_labels_resolve_synthetic_facts(config, db, expedia_profile):
    resolver = AnswerResolver(config, db)
    cases = [
        ('Please enter your permanant address, including city and state.', 'textarea', [], '123 Fixture Lane, Example City, Maryland, United States'),
        ('Please enter your permanent address, including city and state.', 'textarea', [], '123 Fixture Lane, Example City, Maryland, United States'),
        (LINKEDIN, 'text', [], expedia_profile['links']['linkedin']),
        (ROLE, 'multiselect', ROLES + ['Data Engineer', 'I do not have a preference'], ROLES),
        (LANGUAGE, 'multiselect', ['Spanish', 'French', 'None of the Above'], ['None of the Above']),
        ('Please confirm which Internship cohort you prefer to join based on your school timeline', 'select', ['May 2027 - July 2027', 'June 2027 - August 2027', 'No preference - I can join any cohort'], 'No preference - I can join any cohort'),
        ('What is the country of residence for the university you anticipate graduating from?', 'select', ['United States', 'Canada', 'Other'], 'United States'),
        ('I agree', 'checkbox', ['I agree'], 'I agree'),
    ]
    for label, kind, options, expected in cases:
        answer = resolver.resolve(Question(label, label, kind, True, options), APP)
        assert answer and answer.value == expected, label
    answer = resolver.resolve(Question('language', LANGUAGE, 'multiselect', True, ['Spanish', 'French', 'None of the Above']), APP)
    assert answer.evidence == ['languages'] and answer.provenance['rule'] == 'other-languages-english-only'


def test_ranked_available_distinct_locations(config, db, expedia_profile):
    resolver = AnswerResolver(config, db)
    options = ['Seattle, WA', 'Austin, TX', 'San Jose, California', 'I have no preference for location']
    boilerplate = ' If you do not have a location preference, please select "I have no preference for location" for the next three boxes. If the location listed in the job description is not available below, or if the role you are applying for is based outside of the U.S., please select any applicable option for the following boxes to complete the form.'
    for rank, expected in enumerate(['San Jose, California', 'Seattle, WA', 'Austin, TX'], 1):
        label = f'What is your {rank}{["st", "nd", "rd"][rank-1]} location preference?' + (boilerplate if rank == 1 else '')
        assert resolver.resolve(Question(str(rank), label, 'select', True, options), APP).value == expected
    label = 'What is your 3rd location preference?'
    assert resolver.resolve(Question('3', label, 'select', True, ['Seattle, WA']), APP) is None
    assert resolver.resolve(Question('ny', 'What is your 1st location preference?', 'select', True,
        ['New York, NY', 'Austin, TX']), APP).value == 'New York, NY'


@pytest.mark.affirmative
def test_restrictions_never_inferred_and_country_not_indiscriminate(config, db, expedia_profile):
    resolver = AnswerResolver(config, db)
    for label in [RESTRICTION, 'Do you have an agreement that restricts your employment?',
                  'Are you bound by a non-disclosure agreement?']:
        result = resolver.resolve_result(Question('q', label, 'select', True, ['Yes', 'No']), APP)
        assert result.answer is None and result.descriptor.policy == 'REQUIRE_USER'
    from autoapply.profile_rules import resolve
    for label in ['What country is this job located in?', 'What is your spouse country of residence?', 'Country for future employment']:
        assert resolve(label, 'select', ['United States', 'Canada'], expedia_profile) is None
    assert resolve('Country of residence', 'select', ['United States', 'Canada'], expedia_profile) == 'United States'


def test_extension_checkbox_group_and_optional_decline(http_dashboard, expedia_profile, monkeypatch):
    calls = []
    monkeypatch.setattr(http_dashboard[1], 'run_writing', lambda *args: calls.append(args))
    fields = [dict(key='roles', label=ROLE, kind='checkbox', multiple=True, required=True, options=ROLES + ['Data Engineer']),
              dict(key='race', label='What is your race/ethnicity?', kind='select', required=False, options=['White', 'Asian']),
              dict(key='gender', label='What is your gender?', kind='select', required=False, options=['Female', 'Male']),
              dict(key='restriction', label=RESTRICTION, kind='select', required=True, options=['Yes', 'No'])]
    status, data, _ = ext(http_dashboard, 'POST', '/ext/answers', dict(fields=fields, url='https://fixture.test/job'))
    assert status == 200
    rows = {a['key']: a for a in data['answers']}
    assert rows['roles']['value'] == ROLES
    assert rows['race']['status'] == rows['gender']['status'] == 'OPTIONAL_SKIP'
    assert rows['restriction']['status'] == 'NEEDS_INPUT'
    assert not calls


def test_ordered_preference_remember_pipeline(http_dashboard, config, expedia_profile):
    payload = dict(label='What is your 1st location preference?', kind='select', options=LOCATIONS,
                   value='Austin, TX', url='https://fixture.test/job')
    before = config.profile
    assert ext(http_dashboard, 'POST', '/ext/remember', payload)[0] == 200
    with http_dashboard[1].connection() as db:
        assert db.setting(ORDERED_LOCATIONS_SETTING)['value'][0] == 'Austin, TX'
    other = dict(payload, url='https://other.test/job', fields=[dict(key='first', label=payload['label'], kind='select', required=True, options=LOCATIONS)])
    status, data, _ = ext(http_dashboard, 'POST', '/ext/answers', other)
    assert status == 200 and data['answers'][0]['value'] == 'Austin, TX'
    assert config.profile == before


@pytest.mark.parametrize('proposal,expected', [
    (dict(choices=['Python'], fact_ids=['skills'], needs_input=False), ['Python']),
    (dict(choices=['Spanish'], fact_ids=['skills'], needs_input=False), None),
    (dict(choices=['Python', 'None of the Above'], fact_ids=['skills'], needs_input=False), None),
    (dict(choices=['Python'], fact_ids=['made_up'], needs_input=False), None),
    (dict(choices=[], fact_ids=[], needs_input=True), None),
])
def test_unknown_choices_use_grounded_writer_and_validate(http_dashboard, expedia_profile, monkeypatch, proposal, expected):
    requests = []
    def writer(payload):
        requests.append(payload)
        return proposal
    monkeypatch.setattr(http_dashboard[1], 'run_writing', writer)
    payload = dict(url='https://fixture.test/job', fields=[dict(key='unknown', label='Which of these tools do you use?', kind='checkbox', multiple=True, required=True, options=['Python', 'Rust', 'None of the Above'])])
    for _ in range(2):
        status, data, _ = ext(http_dashboard, 'POST', '/ext/answers', payload)
        assert status == 200 and data['answers'][0]['value'] == expected
        assert data['answers'][0]['status'] == ('RESOLVED' if expected else 'NEEDS_INPUT')
    assert len(requests) == 1
    assert requests[0]['task'] == 'choice_interpret'
    assert requests[0]['options'] == payload['fields'][0]['options']
    assert list(requests[0]['profile']['languages']) == expedia_profile['languages']
    assert 'Do not write prose' in requests[0]['instruction']


def test_unknown_choice_provider_unavailable_stays_pending(http_dashboard, expedia_profile, monkeypatch):
    def unavailable(*args):
        raise RuntimeError('offline fixture')
    monkeypatch.setattr(http_dashboard[1], 'run_writing', unavailable)
    payload = dict(fields=[dict(key='unknown', label='Which fixture tracks interest you?', kind='checkbox', multiple=True, required=True, options=['A', 'B'])], url='https://fixture.test/job')
    status, data, _ = ext(http_dashboard, 'POST', '/ext/answers', payload)
    assert status == 200 and data['answers'][0]['status'] == 'NEEDS_INPUT'


@pytest.mark.parametrize('company', ['Expedia Group', 'Another Employer'])
@pytest.mark.parametrize('supplied,expected', [(True,'Yes'),(False,'No')])
def test_t054_explicit_all_employer_restrictions(config, db, expedia_profile, company, supplied, expected):
    expedia_profile['application_preferences']['employment_restrictions']=supplied
    (config.private/'profile.yaml').write_text(yaml.safe_dump(expedia_profile),encoding='utf-8')
    resolver=AnswerResolver(config,db)
    label=RESTRICTION.replace('Expedia Group',company)
    answer=resolver.resolve(Question('q',label,'select',True,['Yes','No']),dict(id=42,company=company))
    assert answer.value==expected
    assert answer.evidence==['application_preferences.employment_restrictions']
    assert answer.provenance['rule']=='explicit-all-employer-restriction'
    for unsupported in [label+' and can you guarantee it will never affect your work?',
                        'Are you not subject to a non-disclosure agreement?',
                        'Will you disclose all confidential information from your previous employer?']:
        assert resolver.resolve(Question('q',unsupported,'select',True,['Yes','No']),dict(id=42,company=company)) is None
    assert resolver.resolve(Question('terms','I agree','checkbox',True,['I agree']),APP).value=='I agree'


def test_t054_current_profile_wins_over_stale_no_memory(config, db, expedia_profile, listing):
    from autoapply.models import Answer
    db.ingest(listing,config)
    question=Question('restriction',RESTRICTION,'select',True,['Yes','No'])
    row=db.question(1,question)
    db.save_answer(row['id'],Answer('No','USER_PROVIDED'),verified=True)
    expedia_profile['application_preferences']['employment_restrictions']=True
    (config.private/'profile.yaml').write_text(yaml.safe_dump(expedia_profile),encoding='utf-8')
    answer=AnswerResolver(config,db).resolve(question,dict(id=1,company='Expedia Group'))
    assert answer.value=='Yes' and answer.source=='profile:application_preferences.employment_restrictions'


# Executed by the configured extension/API coverage wrappers; avoid duplicate collection.
__test__ = False
