"""Phase 5: factual purity, compatibility, invalidation, and explicit use."""
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import yaml

from autoapply.answers import AnswerResolver, answer_from_row, from_row, is_writing_question, validate_answer
from autoapply.config import fact
from autoapply.control import Controller
from autoapply.field_mapping import FieldMapper, describe, REJECTED_FIELD, UNKNOWN_FIELD
from autoapply.models import Answer, Question
from autoapply.profile_snapshot import ProfileSnapshot


def write_profile(config, value):
    (config.private / 'profile.yaml').write_text(yaml.safe_dump(value), encoding='utf-8')


def test_snapshot_null_false_original_and_immutable(config):
    profile = config.profile
    profile['work_authorization'].update(sponsorship_now=None, sponsorship_future=False)
    write_profile(config, profile)
    snapshot = config.profile_snapshot()
    assert fact(snapshot.facts, 'work_authorization.sponsorship_now') is None
    assert fact(snapshot.facts, 'work_authorization.sponsorship_future') is False
    with pytest.raises(TypeError):
        snapshot.facts['identity']['first_name'] = 'Changed'
    with pytest.raises(Exception):
        snapshot.revision = 'changed'
    assert snapshot == config.profile_snapshot()


@pytest.mark.parametrize('value', ['false', 0, 1])
def test_snapshot_does_not_coerce_malformed_boolean(value):
    with pytest.raises(ValueError):
        ProfileSnapshot.create({'work_authorization': {'sponsorship_now': value}})


def test_snapshot_reparse_once_and_pure_cache(config, db, monkeypatch):
    import autoapply.config as module
    reads = []
    original = module.read_yaml
    monkeypatch.setattr(module, 'read_yaml', lambda path: (reads.append(path.name), original(path))[1])
    resolver = AnswerResolver(config, db)
    changes = db.conn.total_changes
    for _ in range(20):
        assert resolver.resolve(Question('n', 'First name'), {'id': 1}).value == 'Test'
    assert reads.count('profile.yaml') == 1
    assert resolver.computations == 1 and db.conn.total_changes == changes


@pytest.mark.parametrize('path,initial,changed,label,kind,options', [
    ('education.graduation_date', '2028-05-01', '2029-05-01', 'Graduation date', 'date', []),
    ('contact.city', 'Boston', 'Austin', 'City', 'text', []),
    ('work_authorization.sponsorship_future', False, True, 'Will you require sponsorship in the future?', 'radio', ['Yes', 'No']),
    ('dropdown_preferences.technical_interests', ['Python', 'Go'], ['Go', 'Python'], 'Technical interests', 'select', ['Go', 'Python']),
])
def test_profile_revision_invalidates_answer(config, db, path, initial, changed, label, kind, options):
    profile = config.profile
    section, name = path.split('.')
    profile.setdefault(section, {})[name] = initial
    write_profile(config, profile)
    resolver = AnswerResolver(config, db)
    q = Question('x', label, kind, True, options)
    first = resolver.resolve(q, {'id': 1})
    revision = resolver.snapshot.revision
    profile[section][name] = changed
    write_profile(config, profile)
    second = resolver.resolve(q, {'id': 1})
    assert first.value != second.value and resolver.snapshot.revision != revision


async def test_descriptor_unicode_alias_and_conflict(config, db):
    mapper = FieldMapper('fixture', fallback=AsyncMock())
    mapping = await mapper.map({'label': ' Ｆｉｒｓｔ\u00a0name * ', 'autocomplete': 'given-name'})
    q = Question('x', ' Ｆｉｒｓｔ\u00a0name * ', semantic_key=mapping['semantic_key'])
    resolver = AnswerResolver(config, db)
    result = resolver.resolve_result(q, {'id': 1})
    assert result.answer.value == 'Test'
    assert result.descriptor.semantic_key == 'contact.first_name'
    assert (await mapper.map({'label': 'Email', 'autocomplete': 'given-name'}))['semantic_key'] == REJECTED_FIELD
    mapper.fallback.assert_not_called()


@pytest.mark.parametrize('key,required,status', [(UNKNOWN_FIELD, True, 'USER_INPUT_REQUIRED'),
                                               (UNKNOWN_FIELD, False, 'OPTIONAL_SKIP'),
                                               (REJECTED_FIELD, True, 'REJECTED')])
def test_abstention_is_structured(config, db, key, required, status):
    q = Question('x', 'Unfamiliar field', required=required, semantic_key=key)
    result = AnswerResolver(config, db).resolve_result(q, {'id': 1})
    assert not result.accepted and result.status == status


def test_rejected_field_never_uses_profile_or_narrative(config, db):
    resolver = AnswerResolver(config, db)
    for label in ('Email', 'Describe a project'):
        q = Question('x', label, 'textarea', True, semantic_key=REJECTED_FIELD)
        assert resolver.resolve(q, {'id': 1}) is None
        assert not is_writing_question(q)


def save_verified(db, app_id, q, value):
    row = db.question(app_id, q)
    db.save_answer(row['id'], Answer(value, 'user_confirmed'), verified=True)
    return row


def test_exact_memory_pure_then_one_use(config, db, listing):
    db.ingest(listing, config)
    q = Question('x', 'Email', 'email', True)
    row = save_verified(db, 1, q, 'confirmed@example.test')
    resolver = AnswerResolver(config, db)
    for _ in range(3):
        answer = resolver.resolve(q, db.application(1))
        assert answer.source == 'verified_memory'
    assert db.one('SELECT usage_count FROM known_answers')['usage_count'] == 0
    db.save_answer(row['id'], answer)
    assert db.one('SELECT usage_count FROM known_answers')['usage_count'] == 1
    assert answer.verified is True


@pytest.mark.parametrize('change', ['options', 'kind', 'limits', 'scope', 'semantic', 'policy_version'])
def test_signature_invalidates_memory(config, db, listing, change, monkeypatch):
    import autoapply.field_mapping as module
    db.ingest(listing, config)
    q = Question('x', 'Unknown exact question', 'select', True, ['Yes', 'No'])
    save_verified(db, 1, q, 'Yes')
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(q, db.application(1)).value == 'Yes'
    if change == 'options': q.options = ['Yes', 'No', 'Maybe']
    if change == 'kind': q.kind = 'radio'
    if change == 'limits': q.max_length = 3
    if change == 'scope': q.scope = 'employer:other'
    if change == 'semantic': q.semantic_key = REJECTED_FIELD
    if change == 'policy_version': monkeypatch.setattr(module, 'POLICY_VERSION', 'next-policy')
    assert resolver.resolve(q, db.application(1)) is None


@pytest.mark.parametrize('policy', ['DO_NOT_ANSWER', 'REQUIRE_USER'])
def test_policy_applies_before_profile_and_replay(config, db, listing, policy):
    db.ingest(listing, config)
    q = Question('x', 'Email', 'email', True)
    resolver = AnswerResolver(config, db)
    answer = resolver.resolve(q, db.application(1))
    row = db.question(1, q)
    db.save_answer(row['id'], answer)
    profile = config.profile
    profile['autofill_policies'] = {'contact.email': policy}
    write_profile(config, profile)
    assert resolver.resolve(q, db.application(1)) is None
    assert resolver.replay(db.one('SELECT * FROM questions WHERE id=?', (row['id'],)), q, db.application(1)) is None


def test_user_correction_replaces_unknown_and_cached_fact(config, db, listing):
    db.ingest(listing, config)
    db.claim(1)
    db.transition(1, 'NEEDS_INPUT')
    q = Question('unknown', 'Your exact internal preference', required=True)
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(q, db.application(1)) is None
    row = db.question(1, q)
    Controller(config, db).answer(row['id'], 'Confirmed')
    assert resolver.resolve(q, db.application(1)).value == 'Confirmed'
    email = Question('email', 'Email', 'email', True)
    assert resolver.resolve(email, db.application(1)).value == 'student@example.test'
    db.set_setting('verified_fact:contact.email', {'source': 'USER_PROVIDED', 'value': 'corrected@example.test', 'question_id': row['id']})
    result = resolver.resolve(email, db.application(1))
    assert result.value == 'corrected@example.test' and result.source == 'USER_PROVIDED'
    assert resolver.snapshot.original_facts['contact']['email'] == 'student@example.test'


def test_override_null_and_combined_sponsorship(config, db):
    resolver = AnswerResolver(config, db)
    app = {'id': 1}
    db.set_setting('verified_fact:work_authorization.sponsorship_future', {'source': 'USER_PROVIDED', 'value': 'Yes'})
    q = Question('s', 'Will you now or in the future require sponsorship?', options=['Yes', 'No'])
    assert resolver.resolve(q, app).value == 'Yes'
    db.set_setting('verified_fact:contact.email', {'source': 'USER_PROVIDED', 'value': None})
    assert resolver.resolve(Question('e', 'Email'), app) is None


def test_provenance_roundtrip_and_legacy_unknown(config, db, listing):
    db.ingest(listing, config)
    resolver = AnswerResolver(config, db)
    q = Question('x', 'Email', 'email', True)
    answer = resolver.resolve(q, db.application(1))
    row = db.question(1, q)
    db.save_answer(row['id'], answer)
    stored = db.one('SELECT * FROM questions WHERE id=?', (row['id'],))
    restored = answer_from_row(stored)
    assert restored == answer
    assert from_row(stored).semantic_key == 'contact.email'
    db.execute('UPDATE questions SET answer_provenance=NULL WHERE id=?', (row['id'],))
    legacy = answer_from_row(db.one('SELECT * FROM questions WHERE id=?', (row['id'],)))
    assert legacy.verified is None and legacy.evidence == []


def test_scope_requisition_employer_and_global_fact(config, db, listing):
    db.ingest(listing, config)
    db.ingest(replace(listing, url='https://jobs.lever.co/example/second'), config)
    a, b = db.application(1), db.application(2)
    assert describe(Question('e', 'Email'), a).signature == describe(Question('e', 'Email'), b).signature
    q = Question('p', 'Have you ever worked for us?', 'radio', True, ['Yes', 'No'], scope='global')
    save_verified(db, 1, q, 'Yes')
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(q, a).value == 'Yes'
    assert resolver.resolve(q, b) is None
    assert resolver.resolve(q, dict(a, company='Another employer')) is None


@pytest.mark.parametrize('label,kind', [('First name', 'text'), ('Last name', 'text'), ('Email', 'email'),
    ('Phone', 'text'), ('School', 'text'), ('Degree', 'text'), ('Major', 'text'), ('Graduation date', 'date'),
    ('City', 'text'), ('Are you authorized to work in the US?', 'radio'),
    ('Do you require sponsorship now?', 'radio'), ('Have you ever worked for us?', 'radio'),
    ('Do you have any conflicts of interest with us?', 'radio'), ('Privacy acknowledgement', 'checkbox'),
    ('SMS recruiting preference', 'radio'), ('Gender', 'select'), ('Technical interests', 'select')])
async def test_factual_fields_never_enter_provider(config, db, label, kind, monkeypatch):
    from autoapply.ai import AIManager, BrowserAIProvider, ProviderUnavailable
    provider = AsyncMock(side_effect=AssertionError('Factual question reached provider'))
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', provider)
    q = Question('x', label, kind, True)
    AnswerResolver(config, db).resolve(q, {'id': 1, 'company': 'Example'})
    assert not is_writing_question(q)
    with pytest.raises(ProviderUnavailable):
        await AIManager(config, db, None).draft(q, {'id': 1})
    provider.assert_not_called()


def test_multiselect_limits_in_signature_and_validation():
    q = Question('x', 'Technical interests', 'multiselect', True, ['Go', 'Python'], max_selections=1)
    with pytest.raises(ValueError): validate_answer(q, ['Go', 'Python'])
    with pytest.raises(ValueError): validate_answer(q, ['Go', 'Go'])
    assert describe(q, {'id': 1}).signature != describe(replace(q, max_selections=2), {'id': 1}).signature


def test_null_authorization_and_sponsorship_not_derived_from_citizenship(config, db):
    profile = config.profile
    profile['work_authorization'] = {'us_authorized': None, 'sponsorship_now': None, 'sponsorship_future': None}
    write_profile(config, profile)
    resolver = AnswerResolver(config, db)
    for label in ('Are you authorized to work in the US?', 'Will you now or in the future require sponsorship?'):
        assert resolver.resolve(Question('x', label, options=['Yes', 'No']), {'id': 1}) is None


def test_exact_memory_precedes_standing_and_policy_stops_both(config, db, listing):
    from autoapply.standing import MEANINGS
    profile = config.profile
    profile['eligibility_assertions'] = [{'id': 'communication', 'meaning': MEANINGS['communication'],
                                         'answer': True, 'source': 'USER_PROVIDED', 'scope': 'standing'}]
    write_profile(config, profile)
    db.ingest(listing, config)
    q = Question('x', 'Do you have strong communication skills?', 'radio', True, ['Yes', 'No'])
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(q, db.application(1)).value == 'Yes'
    save_verified(db, 1, q, 'No')
    assert resolver.resolve(q, db.application(1)).source == 'verified_memory'
    assert resolver.resolve(q, db.application(1)).value == 'No'
    profile['autofill_policies'] = {UNKNOWN_FIELD: 'DO_NOT_ANSWER'}
    write_profile(config, profile)
    assert resolver.resolve(q, db.application(1)) is None


@pytest.mark.parametrize('label', ['Privacy acknowledgement', 'SMS recruiting preference'])
def test_signed_common_policy_answer_does_not_cross_options_or_employer(config, db, label):
    profile = config.profile
    app = {'id': 1, 'company': 'Example'}
    q = Question('x', label, 'radio', True, ['Yes', 'No'])
    descriptor = describe(q, app, profile)
    profile['common_answers'] = {label: {'verified': True, 'scope': descriptor.scope,
                                       'signature': descriptor.signature, 'answer': 'No'}}
    write_profile(config, profile)
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(q, app).value == 'No'
    assert resolver.resolve(replace(q, options=['Yes', 'No', 'Other']), app) is None
    assert resolver.resolve(q, dict(app, company='Other')) is None


def test_cross_connection_correction_and_rollback(config, db):
    import sqlite3
    resolver = AnswerResolver(config, db)
    q = Question('x', 'Email')
    assert resolver.resolve(q, {'id': 1}).value == 'student@example.test'
    path = db.conn.execute('PRAGMA database_list').fetchone()['file']
    with sqlite3.connect(path) as other:
        other.execute('INSERT INTO settings VALUES (?,?)', ('verified_fact:contact.email',
                      json.dumps({'source': 'USER_PROVIDED', 'value': 'external@example.test'})))
    assert resolver.resolve(q, {'id': 1}).value == 'external@example.test'
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.set_setting('verified_fact:contact.email', {'source': 'USER_PROVIDED', 'value': 'rollback@example.test'})
            assert resolver.resolve(q, {'id': 1}).value == 'rollback@example.test'
            raise RuntimeError('Rollback')
    assert resolver.resolve(q, {'id': 1}).value == 'external@example.test'


def test_preflight_result_revalidated_after_correction(config, db):
    resolver = AnswerResolver(config, db)
    q = Question('x', 'Email')
    prior = resolver.resolve_result(q, {'id': 1})
    db.set_setting('verified_fact:contact.email', {'source': 'USER_PROVIDED', 'value': 'new@example.test'})
    assert resolver.current_result(prior, q, {'id': 1}).answer.value == 'new@example.test'


def test_generated_provenance_stays_unverified(config, db, listing):
    db.ingest(listing, config)
    q = Question('x', 'Describe a project', 'textarea', True)
    resolver = AnswerResolver(config, db)
    result = resolver.resolve_result(q, db.application(1))
    answer = resolver.stamp(Answer('A proposal', 'grounded_ai:fixture', 1.0, ['project'], verified=False), result.descriptor)
    row = db.question(1, q)
    db.save_answer(row['id'], answer)
    restored = answer_from_row(db.one('SELECT * FROM questions WHERE id=?', (row['id'],)))
    assert restored.verified is False and restored.confidence == 1.0
    assert db.rows('SELECT * FROM known_answers') == []
    assert resolver.resolve(q, db.application(1)) is None


@pytest.mark.parametrize('label,path', [
    ('Are you willing to work four days per week in our San Francisco office?', 'application_preferences.willing_to_work_any_location'),
    ('How did you hear about us?', 'application_preferences.discovery_source'),
])
def test_user_local_preference_does_not_become_global_fact(config, db, listing, label, path):
    db.ingest(listing, config)
    db.ingest(replace(listing, url='https://jobs.lever.co/other/two', company='Other'), config)
    db.claim(1)
    db.transition(1, 'NEEDS_INPUT')
    q = Question('x', label, 'text', True)
    row = db.question(1, q)
    Controller(config, db).answer(row['id'], 'Yes' if 'office' in label else 'LinkedIn')
    assert db.setting('verified_fact:' + path) is None
    assert AnswerResolver(config, db).resolve(q, db.application(2)) is None


def test_combined_sponsorship_correction_beats_old_memory(config, db, listing):
    db.ingest(listing, config)
    q = Question('x', 'Will you now or in the future require sponsorship?', 'radio', True, ['Yes', 'No'])
    save_verified(db, 1, q, 'No')
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(q, db.application(1)).value == 'No'
    db.set_setting('verified_fact:sponsorship_combined', {'source': 'USER_PROVIDED', 'value': 'Yes'})
    assert resolver.resolve(q, db.application(1)).value == 'Yes'
