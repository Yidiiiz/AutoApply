import json

import pytest
import yaml

from autoapply.answers import AnswerResolver
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
