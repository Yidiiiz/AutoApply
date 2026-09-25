import pytest

from autoapply.standing import MEANINGS, resolve_standing


def profile():
    return {'eligibility_assertions': [
        {'id': key, 'meaning': MEANINGS[key], 'answer': True,
         'source': 'USER_PROVIDED', 'scope': 'standing'}
        for key in ('full_time_experience_at_most_two_years',
                    'substantial_equivalent_technical_experience')]}


@pytest.mark.parametrize('text', [
    'Do you have no more than two years of full-time work experience?',
    'Have no more than 2 years of full-time work experience',
    'At most 2 years of full-time employment experience',
    'Two years or less of full-time work experience',
    'Substantial relevant technical experience',
])
def test_explicit_distinction(text):
    assert resolve_standing(text, profile())


@pytest.mark.parametrize('text', [
    'At most 1 year of full-time work experience',
    'At least two years of full-time work experience',
    'Exactly two years of full-time employment experience',
    'No more than two years of relevant experience',
    'No more than two years of full-time work experience including projects',
    'Substantial professional employment experience',
    'Five years of relevant technical experience',
    'No more than two years of full-time work experience and a security clearance',
])
def test_no_duration_or_employment_invention(text):
    assert resolve_standing(text, profile()) is None


def test_explicit_source_required():
    assert resolve_standing('No more than two years of full-time work experience', {}) is None
