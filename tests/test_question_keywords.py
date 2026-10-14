import json

import pytest
import yaml

from autoapply.answers import AnswerResolver, fit_options
from autoapply.concepts import canonical_key
from autoapply.models import Answer, Question
from autoapply.question_keywords import (AUTHORIZATION_STATEMENT, CONCEPTS, authorization_answer,
                                         match, normalize)

ZIPLINE_LABEL = 'Please provide details on your current work authorization status in the United States: *'
ZIPLINE_OPTIONS = [
    'I am legally authorized to work for any employer in the United States',
    'I require, or in the future will require sponsorship to obtain work authorization in the country in which this position is based (for example, F-1, TN, H-1B, etc.)',
    'I am not currently authorized to work in the United States.',
]
AUTH_LABELS = [ZIPLINE_LABEL,
    'Are you legally authorized to work in the United States?',
    'Are you legally work authorized to work in the US?',
    'Are you authorized to work in the US?',
    'Are you legally authorized to work in the country where the job is located?',
    'Are you eligible to work in the country where this vacancy is posted?',
    'Do you have the right to work in the United States?',
    'Work authorization status',
]
SEPARATE_LABELS = [
    ('Will you now or in the future require company sponsorship for a visa or work authorization to work in the country where this role is based?', 'sponsorship_combined'),
    ('Will you now require immigration sponsorship by our company to attain or maintain your employment eligibility (e.g., H-1B, ...)', 'work_authorization.sponsorship_now'),
    ('Will you require immigration sponsorship in the future?', 'work_authorization.sponsorship_future'),
    ('Do you currently have temporary work authorization (such as F-1, OPT, CPT, H-1B or similar)?', 'work_authorization.temporary_authorization'),
    ('Are you a U.S. citizen?', 'citizenship.us_citizen'),
    ('What is your current visa status?', 'visa.status'),
    ('Do you have security clearance?', 'security.clearance'),
]


def test_normalization_and_word_boundaries():
    assert normalize('  U.S. / H-1B!  ') == 'u s h 1b'
    assert match('Which sponsorships do you hold?') is None
    assert match('Employment eligibility verification') is None


@pytest.mark.parametrize('label', AUTH_LABELS)
def test_authorization_concepts(label):
    assert match(label) == 'work_authorization.us_authorized'
    assert canonical_key(label) == 'work_authorization.us_authorized'


@pytest.mark.parametrize('label,key', SEPARATE_LABELS)
def test_separate_concepts(label, key):
    assert canonical_key(label) == key
    assert match(label) != 'work_authorization.us_authorized'


@pytest.mark.parametrize('label', AUTH_LABELS)
@pytest.mark.parametrize('kind', ['text', 'textarea'])
def test_exact_authorization_statement(config, db, label, kind):
    answer = AnswerResolver(config, db).resolve(Question('auth', label, kind, True), {'id': 1})
    assert answer.value == AUTHORIZATION_STATEMENT


@pytest.mark.parametrize('kind', ['checkbox', 'radio', 'select', 'combobox', 'multiselect'])
def test_zipline_choices(config, db, kind):
    q = Question('auth', ZIPLINE_LABEL, kind, True, ZIPLINE_OPTIONS)
    answer = AnswerResolver(config, db).resolve(q, {'id': 1})
    expected = [ZIPLINE_OPTIONS[0]] if kind == 'multiselect' else ZIPLINE_OPTIONS[0]
    assert answer.value == expected


@pytest.mark.parametrize('kind', ['select', 'radio', 'checkbox', 'combobox'])
def test_yes_and_unsafe_options(config, db, kind):
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(Question('a', AUTH_LABELS[1], kind, True, ['No', 'Yes']), {'id': 1}).value == 'Yes'
    assert resolver.resolve(Question('a', AUTH_LABELS[1], kind, True, ZIPLINE_OPTIONS[1:]), {'id': 1}) is None
    assert authorization_answer(kind, ['Yes, I require sponsorship', 'Not authorized to work']) is None


@pytest.mark.affirmative
@pytest.mark.parametrize('label,key', SEPARATE_LABELS)
def test_authorization_never_supplies_separate_facts(config, db, label, key):
    profile = {'work_authorization': {'us_authorized': True}}
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    assert AnswerResolver(config, db).resolve(Question('other', label, 'radio', True, ['Yes', 'No']), {'id': 1}) is None


def test_false_or_missing_authorization_has_no_fixed_statement(config, db):
    for value in (False, None):
        (config.private / 'profile.yaml').write_text(yaml.safe_dump({'work_authorization': {'us_authorized': value}}), encoding='utf-8')
        answer = AnswerResolver(config, db).resolve(Question('a', ZIPLINE_LABEL, 'textarea', True), {'id': 1})
        assert answer is None or answer.value != AUTHORIZATION_STATEMENT


def test_long_legal_text_and_specificity(monkeypatch):
    gallup = 'I acknowledge that only an authorized Gallup official can change these terms. ' + 'These legal terms apply to my application. ' * 10
    assert match(gallup) is None
    assert canonical_key(gallup) != 'work_authorization.us_authorized'
    assert match('Please provide your LinkedIn profile. ' + 'Additional legal text. ' * 25) is None
    monkeypatch.setattr('autoapply.question_keywords.CONCEPTS', [
        dict(key='broad', any=['work'], all=[], exclude=[]),
        dict(key='specific', any=['work authorization status'], all=[], exclude=[]),
        dict(key='long', any=['agreement'], all=['work authorization status', 'agreement'], exclude=[]),
    ])
    assert match('work authorization status') == 'specific'
    assert match('work authorization status agreement ' + 'legal terms ' * 35) == 'long'


def save(db, config, listing, label, value='Fox', kind='text', options=None):
    db.ingest(listing, config)
    q = Question('saved', label, kind, True, options or [])
    row = db.question(1, q)
    db.save_answer(row['id'], Answer(value, 'user_confirmed'), verified=True)
    return db.application(1)


def test_fuzzy_unclassified_answer(config, db, listing):
    app = save(db, config, listing, 'What is your favorite local animal?')
    answer = AnswerResolver(config, db).resolve(Question('new', 'Please provide your favorite local animal', 'text', True), app)
    assert answer.value == 'Fox' and answer.source == 'verified_fuzzy_memory'
    assert answer.provenance['similarity'] >= .6


def test_fuzzy_classified_answer(config, db, listing):
    app = save(db, config, listing, 'Please provide your LinkedIn profile URL', 'https://example.test/me')
    q = Question('new', 'Please enter your LinkedIn profile URL', 'text', True)
    assert AnswerResolver(config, db).resolve(q, app).source == 'verified_fuzzy_memory'


@pytest.mark.parametrize('label,kind,options', [
    ('Please provide your favorite local animal', 'number', []),
    ('Please provide your favorite local animal', 'select', ['Dog']),
    ('What is not your favorite local animal?', 'text', []),
])
def test_fuzzy_incompatible_answers(config, db, listing, label, kind, options):
    app = save(db, config, listing, 'What is your favorite local animal?')
    assert AnswerResolver(config, db).resolve(Question('new', label, kind, True, options), app) is None


def test_fuzzy_choices_must_fit(config, db, listing):
    app = save(db, config, listing, 'Select your favorite local animal', 'Fox', 'select', ['Fox', 'Dog'])
    resolver = AnswerResolver(config, db)
    q = Question('new', 'Please select your favorite local animal', 'select', True, ['Fox', 'Cat'])
    assert resolver.resolve(q, app).value == 'Fox'
    q.options = ['Cat', 'Dog']
    assert resolver.resolve(q, app) is None


def test_fuzzy_never_crosses_concepts(config, db, listing):
    app = save(db, config, listing, 'Please provide your work authorization status details', 'Yes')
    (config.private / 'profile.yaml').write_text(yaml.safe_dump({'work_authorization': {'us_authorized': True}}), encoding='utf-8')
    resolver = AnswerResolver(config, db)
    for label in ['Please provide your work authorization status details requiring sponsorship',
                  'Please provide your work authorization status details and visa status',
                  'Please provide your work authorization status details and U.S. citizenship']:
        assert resolver.resolve(Question('new', label, 'text', True), app) is None


def test_saved_wording_does_not_override_verified_authorization(config, db, listing):
    app = save(db, config, listing, ZIPLINE_LABEL, 'Yes')
    assert AnswerResolver(config, db).resolve(Question('a', ZIPLINE_LABEL, 'text', True), app).value == AUTHORIZATION_STATEMENT


@pytest.mark.parametrize('value', ['United States', 'USA', 'US', 'U.S.', 'United States of America'])
@pytest.mark.parametrize('suffix', [' +1', ' (+1)', '(+1)'])
def test_fit_options_phone_country_aliases(value, suffix):
    options = ['United Arab Emirates +971', 'United Kingdom +44',
               'United States Minor Outlying Islands +1', 'United States' + suffix]
    assert fit_options(value, options) == options[-1]


@pytest.mark.parametrize('value,option', [
    ('United Kingdom', 'United Kingdom +44'),
    ('United Arab Emirates', 'United Arab Emirates(+971)'),
    ('Afghanistan', 'Afghanistan (+93)'),
    ('Albania', 'Albania +355'),
])
def test_fit_options_trailing_dial_code(value, option):
    assert fit_options(value, ['United States +1', option]) == option


@pytest.mark.parametrize('value', ['United States', 'USA', 'US', 'U.S.', 'United States of America'])
@pytest.mark.parametrize('options', [
    ['United Arab Emirates +971', 'United Kingdom +44', 'United States Minor Outlying Islands +1'],
    ['United States +1 extra'], ['+1 United States'], ['United States 1'],
    ['United States (+1'], ['United States +1)'],
    ['United States +1', 'United States (+1)'],
])
def test_fit_options_phone_country_abstains(value, options):
    assert fit_options(value, options) is None


def test_fit_options_preserves_exact_and_other_matching():
    assert fit_options('United States', ['United States +1', 'United States']) == 'United States'
    assert fit_options('US', ['United States +1', 'US']) == 'US'
    assert fit_options('United States', ['United States', 'United States']) is None
    assert fit_options('US', ['United States']) is None
    assert fit_options('United', ['United States +1', 'United Kingdom +44']) is None
    assert fit_options('Yes', ['Yes, I require sponsorship']) is None
    assert fit_options('I am a protected veteran', ['Protected veteran']) == 'Protected veteran'
    assert fit_options('decline', ['Prefer not to say']) == 'Prefer not to say'
    assert fit_options('US', []) == 'US'


@pytest.mark.parametrize('option', ["Bachelor's Degree", 'Bachelors', 'BS', 'B.S.', 'Bachelor of Science'])
def test_bachelor_degree_aliases(option):
    assert fit_options("Bachelor's", [option, 'Masters']) == option


def test_bachelor_degree_prefers_full_degree_and_preserves_other_answers():
    assert fit_options("Bachelor's", ['BS', "Bachelor's Degree", 'Bachelor of Science']) == "Bachelor's Degree"
    assert fit_options('Master', ["Bachelor's Degree"]) is None
    assert fit_options("Bachelor's", ['Bachelor of Science', 'BS']) is None
    assert fit_options('Computer Science', ['Computer Science', 'Math']) == 'Computer Science'


SCHEDULE = ('Our Fall Co-op program is 40 hours per week from July 6th. 2026 - December 18th. 2026. '
            'The hours are 8am – 5pm with a one - hour unpaid lunch from Monday to Friday. '
            'Are you able to commit to this schedule?*')
INSULET = [
    ('What is your desired start date?', 'start_date', '05/17/2027', 'text', []),
    ('Are you legally authorized to work in the United States?*', 'work_authorization.us_authorized', 'Yes', 'radio', ['Yes', 'No']),
    ('Will you now or in the future require sponsorship for employment visa status?*', 'sponsorship_combined', 'No', 'radio', ['Yes', 'No']),
    ('What is your desired Annual Salary?', 'application_preferences.default_salary_range', '$70,000 - $95,000', 'text', []),
    ('Do you have any relatives that also work at Insulet Corporation?*', 'application_preferences.relatives_at_company', 'No', 'radio', ['Yes', 'No']),
    ('Do you or any member(s) of your Immediate Family have a Direct Interest in any business that Competes with or is a Supplier or customer of Insulet?*', 'application_preferences.family_conflict_of_interest', 'No', 'radio', ['Yes', 'No']),
    ('Please specify the dates and hours that you can work.*', 'application_preferences.availability_dates', '05/17/2027 - 8/20/2027', 'text', []),
    (SCHEDULE, 'application_preferences.schedule_commitment', 'Yes', 'radio', ['Yes', 'No']),
    ('When are you expected to graduate?*', 'education.graduation_date', 'May-28', 'text', []),
    ('If you believe you belong to any of the categories of protected veterans please indicate by selecting the appropriate drop-down below.*', 'demographics.veteran_status', 'I am not a protected veteran', 'select', ['I am a protected veteran', 'I am not a protected veteran', 'Prefer not to say']),
    ('Please select the race which most accurately describes yourself*', 'demographics.race', 'Prefer not to say', 'select', ['White', 'Prefer not to say']),
    ('Please select your gender.*', 'demographics.gender', 'Prefer not to say', 'select', ['Male', 'Female', 'Prefer not to say']),
    ('Please indicate if you are Hispanic or Latino?', 'demographics.hispanic_latino', 'Prefer not to say', 'select', ['Yes', 'No', 'Prefer not to say']),
    ('I have read, understand, and agree to the recruitment Terms & Conditions.*', 'application_preferences.terms_acknowledgement', 'Yes', 'checkbox', []),
    ('Disability status', 'demographics.disability', 'Yes', 'select', ['Yes', 'No', 'Prefer not to say']),
]


@pytest.fixture
def standard_profile(config):
    path = config.private / 'profile.yaml'
    profile = yaml.safe_load(path.read_text(encoding='utf-8'))
    profile['application_preferences'] = dict(desired_start_date='05/17/2027',
        availability_dates='05/17/2027 - 8/20/2027', default_salary_range='$70,000 - $95,000',
        relatives_at_company=False, family_conflict_of_interest=False, schedule_commitment=True,
        terms_acknowledgement=True, race='decline', gender='decline', veteran='not a protected veteran', disability='Yes')
    path.write_text(yaml.safe_dump(profile), encoding='utf-8')
    return profile


@pytest.mark.parametrize('label,key,value,kind,options', INSULET)
def test_insulet_standard_answers(config, db, standard_profile, label, key, value, kind, options):
    assert canonical_key(label) == key
    answer = AnswerResolver(config, db).resolve(Question('q', label, kind, True, options), {'id': 1})
    assert answer and answer.value == value


def test_stems_and_whole_word_phrases():
    assert normalize("Veterans veterans' terms conditions applicant's classes") == 'veteran veteran term condition applicant class'
    assert match('Protected veterans') == 'demographics.veteran_status'
    assert match('I agree to the terms and conditions') == 'application_preferences.terms_acknowledgement'
    assert match('Degendered coursework') is None


@pytest.mark.parametrize('description,label,expected', [
    ('Pay: $25 - $35 per hour', 'Desired annual salary', '$52,000 - $72,800'),
    ('Pay: $25 - $35 per hour', 'Desired hourly compensation', '$25 - $35 per hour'),
    ('Pay range: $70,000 - $95,000', 'Expected compensation', '$70,000 - $95,000'),
    ('40 hours per week, 2026 - 2027 program', 'Desired annual salary', '$70,000 - $95,000'),
    ('', 'Desired salary', '$70,000 - $95,000'),
])
def test_advertised_salary(config, db, standard_profile, description, label, expected):
    answer = AnswerResolver(config, db).resolve(Question('salary', label, 'text', True), {'id': 1, 'description': description})
    assert answer.value == expected


def test_salary_context_invalidates_cache(config, db, standard_profile):
    resolver = AnswerResolver(config, db)
    q = Question('salary', 'Desired annual salary', 'text', True)
    assert resolver.resolve(q, {'id': 1, 'description': '$25 - $35 per hour'}).value == '$52,000 - $72,800'
    assert resolver.resolve(q, {'id': 1, 'description': '$30 - $40 per hour'}).value == '$62,400 - $83,200'


@pytest.mark.parametrize('label', ['Are you NOT able to commit to this schedule?',
    'Are you unable to commit to this program?', "Can't you commit to these hours?"])
def test_negated_commitment_requires_user(config, db, standard_profile, label):
    assert AnswerResolver(config, db).resolve(Question('schedule', label, 'radio', True, ['Yes', 'No']), {'id': 1}) is None


def test_hispanic_no_decline_requires_user(config, db, standard_profile):
    assert AnswerResolver(config, db).resolve(Question('h', 'Are you Hispanic or Latino?', 'radio', True, ['Yes', 'No']), {'id': 1}) is None


def test_signature_uses_name(config, db, standard_profile):
    q = Question('signature', 'Type your full name as an electronic signature to agree to these terms and conditions', 'text', True)
    assert canonical_key(q.label) == 'contact.full_name'
    assert AnswerResolver(config, db).resolve(q, {'id': 1}).value == 'Test Student'


def test_long_schedule_and_specific_demographic_concept():
    assert match(SCHEDULE + ' Program details. ' * 40) == 'application_preferences.schedule_commitment'
    assert match('Select your race or Hispanic Latino ethnicity') == 'demographics.hispanic_latino'


def test_terms_option_checkbox(config, db, standard_profile):
    label = 'I have read, understand, and agree to the recruitment Terms & Conditions.'
    q = Question('terms', label, 'checkbox', True, [label])
    assert AnswerResolver(config, db).resolve(q, {'id': 1}).value == label


@pytest.mark.affirmative
def test_negated_ability_never_reaches_affirmative_rule(config, db, standard_profile):
    q = Question('schedule', 'Are you NOT able to work these program hours?', 'radio', True, ['Yes', 'No'])
    assert AnswerResolver(config, db).resolve(q, {'id': 1}) is None


def test_long_protected_veteran_prompt():
    assert match('Please indicate your protected veteran status. ' + 'Additional information. ' * 40) == 'demographics.veteran_status'


@pytest.mark.parametrize('hint,expected', [('MM-DD-YYYY', '05-17-2027'), ('YYYY/MM/DD', '2027/05/17')])
def test_other_start_date_hints(hint, expected):
    from autoapply.answers import format_start_date
    assert format_start_date('05/17/2027', placeholder=hint) == expected


@pytest.mark.parametrize('label', ['Can you work this schedule?', 'Are you available for all program hours?'])
def test_schedule_wording_variants(config, db, standard_profile, label):
    assert canonical_key(label) == 'application_preferences.schedule_commitment'
    answer = AnswerResolver(config, db).resolve(Question('schedule', label, 'radio', True, ['Yes', 'No']), {'id': 1})
    assert answer.value == 'Yes'


def test_current_salary_result_uses_updated_description(config, db, standard_profile):
    resolver = AnswerResolver(config, db)
    q = Question('salary', 'Desired annual salary', 'text', True)
    app = {'id': 1, 'description': '$25 - $35 per hour'}
    prior = resolver.resolve_result(q, app)
    app['description'] = '$30 - $40 per hour'
    assert resolver.current_result(prior, q, app).answer.value == '$62,400 - $83,200'


def test_salary_memory_stays_with_its_application(config, db, listing, standard_profile):
    save(db, config, listing, 'Desired annual salary', '$80,000 - $90,000')
    q = Question('salary', 'Desired annual salary', 'text', True)
    answer = AnswerResolver(config, db).resolve(q, {'id': 2, 'description': '$25 - $35 per hour'})
    assert answer.value == '$52,000 - $72,800'
