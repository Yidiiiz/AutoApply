"""T095: temporary databases and synthetic applicant facts; no browser/provider."""
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from autoapply.answers import AnswerResolver
from autoapply.dashboard_settings import SettingsRejected
from autoapply.extension_api import remember, resolve_answers, skip_answer
from autoapply.models import Question
from test_dashboard import dashboard


def test_shipped_edit_queue_and_cover_cancellation_offline():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run(['node','tests/fixtures/extension/interactions/offline.cjs'],cwd=root,
                          text=True,capture_output=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def field(label='Website', kind='text', options=None, **extra):
    return dict(key='answer', label=label, kind=kind, options=options or [], required=True, **extra)


def resolve(dashboard, item, url='https://fixture.test/jobs/one'):
    return resolve_answers(dashboard, dict(url=url, fields=[item], allow_ai=False))['answers'][0]


def save(dashboard, item, value, url='https://fixture.test/jobs/one'):
    return remember(dashboard, dict(item, url=url, value=value))


def test_latest_user_website_overrides_explicit_profile_and_aliases(dashboard, config):
    profile = config.profile
    profile['links'] = {'personal_website': 'https://old.test/'}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    assert resolve(dashboard, field())['value'] == 'https://old.test/'
    assert save(dashboard, field(), 'https://edited.test/')['ok']
    for label in ['Website', 'Personal website', 'Portfolio URL']:
        assert resolve(dashboard, field(label), 'https://other.test/jobs/two')['value'] == 'https://edited.test/'
    save(dashboard, field(), 'https://latest.test/')
    assert resolve(dashboard, field())['value'] == 'https://latest.test/'


@pytest.mark.parametrize('item,value', [
    (field('Contact consent', 'checkbox', ['Contact consent']), False),
    (field('Years using a fixture tool', 'number'), 0),
    (field('Fixture tools', 'checkbox', ['A', 'B'], multiple=True), ['A', 'B']),
    (field('Fixture tools', 'select', ['A', 'B'], multiple=True), ['A', 'B']),
])
def test_false_zero_and_multi_are_answers(dashboard, item, value):
    assert save(dashboard, item, value)['ok']
    assert resolve(dashboard, item)['value'] == ('0' if value == 0 and type(value) is int else value)


def test_textarea_memory_is_posting_scoped_without_profile_promotion(dashboard):
    item = field('Why this company?', 'textarea')
    save(dashboard, item, 'A specific answer for this posting.')
    assert resolve(dashboard, item)['value'] == 'A specific answer for this posting.'
    assert resolve(dashboard, item, 'https://other.test/jobs/two')['value'] is None
    with dashboard.connection() as db:
        assert not db.rows("SELECT key FROM settings WHERE key LIKE 'verified_fact:%'")


def test_skip_survives_fresh_resolver_and_other_posting_then_undo_and_save(dashboard):
    item = field('What is your favorite fixture editor?', 'select', ['A', 'B'])
    skip_answer(dashboard, dict(item, url='https://fixture.test/jobs/one'))
    for url in ['https://fixture.test/jobs/one', 'https://other.test/jobs/two']:
        row = resolve(dashboard, item, url)
        assert row['source'] == 'user_skip' and row['value'] is None
    changed = dict(item, options=['A', 'C'])
    assert resolve(dashboard, changed)['source'] != 'user_skip'
    skip_answer(dashboard, dict(item, url='https://fixture.test/jobs/one', restore=True))
    assert resolve(dashboard, item)['source'] != 'user_skip'
    skip_answer(dashboard, dict(item, url='https://fixture.test/jobs/one'))
    save(dashboard, item, 'B')
    assert resolve(dashboard, item)['value'] == 'B'


def test_sensitive_skip_does_not_cross_posting_and_never_becomes_no(dashboard):
    item = field('Have you worked for this company?', 'select', ['Yes', 'No'])
    skip_answer(dashboard, dict(item, url='https://fixture.test/jobs/one'))
    assert resolve(dashboard, item)['source'] == 'user_skip'
    assert resolve(dashboard, item, 'https://other.test/jobs/two')['source'] != 'user_skip'
    with dashboard.connection() as db:
        assert not db.rows('SELECT * FROM known_answers')


def test_blank_is_not_verified_and_skip_cannot_override_do_not_answer(dashboard, config):
    with pytest.raises(SettingsRejected):
        save(dashboard, field('First name'), '')
    profile = config.profile
    profile['autofill_policies'] = {'contact.email': 'DO_NOT_ANSWER'}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    item = field('Email', 'email')
    skip_answer(dashboard, dict(item, url='https://fixture.test/jobs/one'))
    skip_answer(dashboard, dict(item, url='https://fixture.test/jobs/one', restore=True))
    assert resolve(dashboard, item)['value'] is None


def test_compound_and_employer_answers_do_not_promote_boolean_or_fact(dashboard):
    for item, value in [(field('Email and phone'), 'mixed'),
                        (field('Will you attend our office five days per week?', 'select', ['Yes','No']), 'No'),
                        (field('I agree to all terms', 'checkbox', ['I agree to all terms']), False)]:
        save(dashboard, item, value)
    with dashboard.connection() as db:
        assert not db.rows("SELECT key FROM settings WHERE key LIKE 'verified_fact:%'")
