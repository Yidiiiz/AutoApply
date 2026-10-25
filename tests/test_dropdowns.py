"""Synthetic explicit state/city preference and answer-editor checks."""
import pytest
import yaml

from autoapply.answers import AnswerResolver
from autoapply.dropdowns import location_identity, location_ordinal, ranked_location
from autoapply.models import Question
from test_dashboard_answers import dashboard, request

CITIES = ['San Francisco, CA', 'San Jose, CA', 'New York, NY', 'Seattle, WA', 'Austin, TX']
TOP = 'What is your top location preference?'


@pytest.mark.parametrize('word,rank', [('top', 1), ('primary', 1), ('1st', 1), ('first', 1),
                                      ('2nd', 2), ('second', 2), ('3rd', 3), ('third', 3)])
def test_location_ordinals(word, rank):
    assert location_ordinal(f'What is your {word} location preference?') == rank


@pytest.mark.parametrize('value,expected', [
    ('Costa Mesa, CA (HQ)', ('costa mesa', 'CA')),
    ('Costa Mesa, California (headquarters)', ('costa mesa', 'CA')),
    ('Costa Mesa, CA, United States', ('costa mesa', 'CA')),
    ('New York City, New York', ('new york', 'NY')),
    ('Toronto, Ontario, CA', None), ('Toronto, CA, Canada', None),
    ('Toronto, Ontario (CA)', None), ('Costa Mesa, CA (Canada)', None),
    ('Costa Mesa, CA (Ontario)', None), ('Canada', None), ('CA', None),
    ('', None), ('Costa Mesa, CA, Ontario', None), ('Costa Mesa, Unknown', None)])
def test_strict_us_location_identity(value, expected):
    assert location_identity(value) == expected


@pytest.mark.parametrize('options,states,expected', [
    (['Costa Mesa, CA (HQ)', 'Ashville, OH'], ['CA'], 'Costa Mesa, CA (HQ)'),
    (['New York, NY', 'Costa Mesa, CA (HQ)'], ['California'], 'Costa Mesa, CA (HQ)'),
    (['Costa Mesa, CA (HQ)', 'San Jose, CA', 'San Francisco, California'], ['CA'], 'San Francisco, California'),
    (['Costa Mesa, CA (HQ)', 'San Jose, CA'], ['CA'], 'San Jose, CA'),
    (['New York, NY', 'Costa Mesa, CA (HQ)'], [], 'New York, NY'),
    (['Costa Mesa, CA (HQ)', 'Ashville, OH'], [], None),
    (['Toronto, CA, Canada', 'Ashville, OH'], ['CA'], None),
    (['Toronto, Ontario, CA', 'New York, NY'], ['CA'], 'New York, NY'),
    ([], ['CA'], None), (['Unknown', 'None of the above'], ['CA'], None),
    (['Costa Mesa, CA', 'Los Angeles, CA'], ['CA'], None),
    (['San Jose, CA', 'San Jose, California', 'New York, NY'], ['CA'], None),
])
def test_rank_state_then_city(options, states, expected):
    assert ranked_location(TOP, options, CITIES, states) == expected


def test_distinct_ranks_and_nonpreferred_order():
    options = ['Austin, TX', 'Seattle, WA', 'Costa Mesa, CA (HQ)', 'New York, NY', 'San Jose, CA']
    assert [ranked_location(f'What is your {rank} location preference?', options, CITIES, ['CA'])
            for rank in ['1st', '2nd', '3rd']] == ['San Jose, CA', 'Costa Mesa, CA (HQ)', 'New York, NY']
    assert ranked_location('What is your 2nd location preference?', ['San Jose, CA'], CITIES, ['CA']) is None


def test_real_resolver_explicit_state_only(config, db):
    profile = config.profile
    profile['locations'] = dict(preferred_states=['CA'], ordered_preferences=CITIES)
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    answer = AnswerResolver(config, db).resolve(Question('top', TOP, 'select', True,
        ['Costa Mesa, CA (HQ)', 'New York, NY']), dict(id=1, company='Fixture'))
    assert answer.value == 'Costa Mesa, CA (HQ)'
    assert 'locations.preferred_states' in answer.evidence


def test_answer_api_state_tags_normalize_and_preserve_cities(dashboard, config):
    status, model = request(dashboard, 'GET', '/api/answers')
    fields = {f['path']: f for section in model['sections'] for f in section['fields']}
    assert status == 200 and fields['locations.preferred_states']['type'] == 'tags'
    assert fields['locations.ordered_preferences']['type'] == 'tags'
    status, _ = request(dashboard, 'PUT', '/api/answers', {'changes': {'locations.ordered_preferences': CITIES}})
    assert status == 200
    status, model = request(dashboard, 'PUT', '/api/answers', {'changes': {'locations.preferred_states': ['California', 'ca', 'New York']}})
    assert status == 200
    assert config.profile['locations']['preferred_states'] == ['CA', 'NY']
    assert config.profile['locations']['ordered_preferences'] == CITIES
    status, _ = request(dashboard, 'PUT', '/api/answers', {'changes': {'locations.preferred_states': []}})
    assert status == 200 and config.profile['locations']['preferred_states'] == []


@pytest.mark.parametrize('path,value', [
    ('locations.preferred_states', ['Ontario']), ('locations.preferred_states', ['Canada']),
    ('locations.preferred_states', ['CA'] * 52), ('locations.preferred_states', ['']),
    ('locations.preferred_states', 'CA'), ('locations.preferred_states', [None]),
    ('locations.ordered_preferences', ['Toronto, CA, Canada']),
    ('locations.ordered_preferences', ['San Jose, CA'] * 101)])
def test_answer_api_rejects_invalid_location_tags(dashboard, config, path, value):
    before = config.profile
    status, _ = request(dashboard, 'PUT', '/api/answers', {'changes': {path: value}})
    assert status == 400
    assert config.profile == before
