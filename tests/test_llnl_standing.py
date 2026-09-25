import pytest
from autoapply.standing import MEANINGS, resolve_standing


@pytest.mark.parametrize('ident,text', [
    ('age_18_or_older', 'Age 18 or older at the start of the internship.'),
    ('undergraduate_good_standing', 'Continuing undergraduate student in good academic standing.'),
    ('office_familiarity', 'Experience working with personal computers and familiarity with Word, Excel and PowerPoint.'),
    ('adaptability_resilience', 'Proven ability to adapt and thrive in challenging situations, demonstrating resilience and problem-solving skills to achieve goals while overcoming setbacks.'),
    ('communication', 'Demonstrated effective communication skills.'),
    ('communication', 'Ability to present and communicate concepts and ideas.'),
    ('independent_team_initiative', 'Ability to work in a team environment.'),
])
def test_exact_verified_requirements_only(ident, text):
    profile = {'eligibility_assertions': [dict(id=ident, meaning=MEANINGS[ident],
                answer=True, source='USER_PROVIDED', scope='standing')]}
    assert resolve_standing(text, profile)
    assert not resolve_standing(text, {})
    assert not resolve_standing(text + ' and hold an active security clearance', profile)
    profile['eligibility_assertions'][0]['answer'] = False
    assert not resolve_standing(text, profile)
