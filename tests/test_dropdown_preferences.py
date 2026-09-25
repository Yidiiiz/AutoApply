from autoapply.dropdowns import choose_option, query_for

PROFILE = {'education': {'school': 'University of Maryland - College Park'},
           'contact': {'city': 'College Park', 'state': 'Maryland'},
           'dropdown_preferences': {'education.school': [
               'University of Maryland College Park', 'University of Maryland - College Park',
               'University of Maryland College']}}


def test_school_preference_is_over_observed_options_only():
    choices = ['University of Maryland - Baltimore', 'University of Maryland - College Park']
    assert choose_option('School', choices, PROFILE) == choices[1]
    choices += ['University of Maryland College Park']
    assert choose_option('School', choices, PROFILE) == choices[2]
    assert choose_option('School', [choices[0]], PROFILE) is None


def test_explicit_fallback_and_no_fuzzy_guess():
    assert choose_option('School', ['University of Maryland College'], PROFILE) == 'University of Maryland College'
    assert choose_option('School', ['University of Maryland University College'], PROFILE) is None


def test_city_requires_correct_state_and_unique_option():
    right = 'College Park, MD, USA'
    assert query_for('Location (City)', PROFILE) == 'College Park'
    assert choose_option('Location (City)', ['College Park, GA, USA', right], PROFILE) == right
    for options in [['College Park'], ['College Park, GA, USA'], [right, right], ['College Park, Maryland, Canada']]:
        assert choose_option('Location (City)', options, PROFILE) is None


def test_city_does_not_resolve_work_location_preferences():
    assert choose_option('Preferred work locations', ['College Park, MD, USA'], PROFILE) is None
