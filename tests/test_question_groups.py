import pytest
from autoapply.question_groups import normalize, similarity, group, categorize

@pytest.mark.parametrize('questions', [
    ['are you authorized to work in the us', 'are you legally work authorized to work in the us', 'are you eligible to work in the country where this vacancy is posted'],
    ['will you now or in the future require visa sponsorship in order to work in the us', 'will you now or in the future require sponsorship to be employed at rave aerospace', 'will you now or in the future require immuta to sponsor an employment visa or other employer sponsored immigration benefit in order for you to begin or continue working for immuta'],
    ['what is your gender identity', 'gender'],
    ['race please select one option that best describes how you identify', 'what is your race or ethnicity'],
    ['protected veteran status', 'what is your military status'],
    ['proficiency in windows and microsoft office', 'windows and microsoft office proficiency'],
    ['have you verified that you meet all the listed requirements and this is a u s opportunity'] * 2,
])
def test_review_groups(questions):
    assert len(group(questions)) == 1

@pytest.mark.parametrize('questions', [
    ['will you now require immigration sponsorship in order to work', 'will you in the future require immigration sponsorship in order to work'],
    ['experience with sql and database management systems', 'proficient in sql and familiar with data visualization tools tableau power bi'],
    ['are you willing to relocate', 'are you able to work onsite in laramie wy'],
])
def test_distinct_claims(questions):
    assert len(group(questions)) == 2

def test_kind_scope_and_boilerplate():
    q = 'Have you verified that you meet all the listed requirements and this is a U.S. opportunity?'
    assert len(group([dict(question=q, scope='global', answer='Yes'), dict(question=q, scope='application:2', answer='Yes')])) == 1
    assert len(group([dict(question='Gender', answer_kind='text'), dict(question='Gender', answer_kind='select'), dict(question='Gender', answer_kind='boolean')])) == 3
    assert normalize('Can you confirm this requirement verify qualification: Please select your gender identity. Note that this is optional.') == 'gender'
    assert 'example' not in normalize('Your Example Company gender', 'Example Company')
    assert similarity('visa', 'sponsor') == 1

def test_categories_and_overrides():
    assert categorize('Are you currently enrolled?') == ('Education', .95)
    assert categorize('Skills')[0] == 'Skills'
    assert categorize('What is your military status?')[0] == 'Demographics'
    assert categorize('An unknown question') == ('Other', 0)
    q = 'Are you currently enrolled?'
    assert categorize(q, {'question_category:' + normalize(q): 'Other'}) == ('Other', 1)


def test_sponsorship_concepts_and_authorization():
    from autoapply.concepts import canonical_key
    questions = [
        'will you now or in the future require company sponsorship for a visa or work authorization to work in the country where this role is based',
        'will you now or in the future require visa sponsorship in order to work in the us',
        'will you now or in the future require immuta to sponsor an employment visa or other employer sponsored immigration benefit in order for you to begin or continue working for immuta',
        'will you now or in the future require sponsorship to be employed at rave aerospace',
        'will you now or in the future require sponsorship to be employed at example company',
    ]
    assert {canonical_key(q) for q in questions} == {'sponsorship_combined'}
    assert len(group([dict(question=q, answer='No') for q in questions])) == 1
    assert normalize(questions[0]) == normalize(questions[2])
    assert len(group([dict(question=q, answer='Yes') for q in [
        'are you authorized to work in the us',
        'are you legally work authorized to work in the us',
        'are you eligible to work in the country where this vacancy is posted']])) == 1


@pytest.mark.parametrize('answer', ['Yes', 'No', ' y ', 'N', 'TRUE', 'false', True, False])
def test_boolean_values_group_across_control_types(answer):
    records = [
        dict(question='Are you authorized to work in the US?', answer_kind='select', answer=answer),
        dict(question='Are you legally work authorized to work in the US?', answer_kind='boolean', answer='No'),
    ]
    assert group(records) == [records]


def test_nonboolean_control_kinds_are_preserved():
    from autoapply.question_groups import answer_kind
    assert answer_kind(dict(answer_kind='multiselect', answer=['No'])) == 'select'
    assert answer_kind(dict(answer_kind='text', answer='A statement ending in no')) == 'text'
    assert answer_kind(dict(answer_kind='mixed:1', answer='Female')) == 'mixed:1'


def test_sponsorship_control_types_and_echoed_answer_group():
    records = [
        dict(question='Will you now or in the future require company sponsorship for a visa or work authorization to work in the country where this role is based?', answer_kind='select', answer='No'),
        dict(question='Will you now or in the future require visa sponsorship in order to work in the US?', answer_kind='boolean', answer='No'),
        dict(question='Will you now or in the future require Immuta to sponsor an employment visa or other employer sponsored immigration benefit in order for you to begin or continue working for Immuta?', answer_kind='radio', answer='No'),
        dict(question='Will you now or in the future require sponsorship to be employed at RAVE Aerospace?', answer_kind='select', answer='Will you now, or in the future require sponsorship to be employed at RAVE Aerospace? YES', check_answer=True),
    ]
    assert group(records) == [records]
    assert records[-1]['check_answer'] is True


def test_profile_concept_boundaries_and_nonempty_normalization():
    assert len(group(['contact.address', 'contact.city', 'contact.country', 'contact.state'])) == 4
    assert len(group(['custom.field_one', 'custom.field_two'])) == 2
    assert len(group(['contact.city', 'City', 'contact.country', 'Country'])) == 2
    assert len(group(['Sponsorship now', 'Sponsorship future'])) == 2
    assert normalize('country') == 'country'
    assert normalize('Are you?') == 'are you?'
    assert normalize('!!!') == '!!!'


def test_bridge_cannot_merge_distinct_canonical_concepts(monkeypatch):
    monkeypatch.setattr('autoapply.question_groups.similarity', lambda *args: 1.0)
    groups = group(['contact.city', 'A shared wording', 'contact.country'])
    assert len(groups) == 2
    assert not any('contact.city' in g and 'contact.country' in g for g in groups)


@pytest.mark.parametrize('value', ["Decline to self-identify", "I don't wish to answer", 'decline',
    'I decline to self-identify', 'Prefer not to say'])
def test_decline_fits_live_option_phrasings(value):
    from autoapply.answers import fit_options
    assert fit_options(value, ['Male', "I don't wish to answer"]) == "I don't wish to answer"
    assert fit_options(value, ['Asian', 'Prefer not to say']) == 'Prefer not to say'


@pytest.mark.parametrize('subject', ['sql', 'python', 'docker', 'llm', 'nlp', 'database',
    'algorithms', 'excel', 'office', 'git', 'ci cd'])
def test_technical_requirements(subject):
    assert categorize('Can you confirm this requirement verify qualification: Knowledge of ' + subject)[0] == 'Skills'


@pytest.mark.parametrize('question', [
    'Can you confirm this requirement verify qualification: Availability this summer',
    'Can you confirm this requirement verify qualification: Flexible schedule',
    'Can you confirm this requirement verify qualification: Onsite/hybrid work',
    'Can you confirm this requirement verify qualification: Soft skills',
    'Can you confirm this requirement verify qualification: Compensation acknowledgement',
    'Do you consent to processing?', 'Privacy notice', 'By checking this box I agree',
    'Do you have a conflict-of-interest?', 'Outside business activities',
    'Are you a government official?', 'Relationships with employees',
])
def test_eligibility_categories(question):
    assert categorize(question)[0] == 'Eligibility & requirements'


@pytest.mark.parametrize('question', ['Have you worked at Example?', 'Have you ever worked for Example?', 'Previously worked for Example'])
def test_prior_work_categories(question):
    assert categorize(question)[0] == 'Experience'


@pytest.mark.parametrize('question', ['backend', 'frontend', 'mobile ios', 'mobile android',
    'data engineering', 'machine learning', 'foundations', 'money', 'dashos', 'dasher logistics',
    'merchant consumer platform', 'operational excellence', 'new verticals', 'marketplace',
    'ads promotions', 'platform services', 'launchpad ai research'])
def test_team_preferences(question):
    assert categorize(question)[0] == 'Preferences/Availability'


@pytest.mark.parametrize('question', ['End date month', 'End date year'])
def test_education_dates_category(question):
    assert categorize(question)[0] == 'Education'
