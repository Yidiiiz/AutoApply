import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from autoapply.ai import AIManager, BrowserAIProvider, ProviderUnavailable
from autoapply.answers import AnswerResolver
from autoapply.codex_writer import CodexWritingProvider
from autoapply.control import Controller
from autoapply.field_mapping import describe, REJECTED_FIELD
from autoapply.models import Question, State
from autoapply import narratives as n
from test_narratives import grounded
from test_browser import browser


@pytest.fixture
def writing(config, db, listing, grounded):
    db.ingest(listing, config)
    return AIManager(config, db, None), Question('why', 'Why this company?', 'textarea'), db.application(1), grounded[1]


async def test_cache_retries_restart_and_pure_consideration(config, db, writing):
    manager, q, app, calls = writing
    first = await manager.draft(q, app)
    changes = db.conn.total_changes
    for _ in range(5):
        again = await manager.draft(replace(q, key='rerendered-control'), app)
        assert again.value == first.value and again.verified is False
        assert again.provenance['narrative_signature'] == first.provenance['narrative_signature']
    assert db.conn.total_changes == changes
    assert len(calls) == 2 and manager.metrics['cache_hits'] == 5
    assert not db.rows("SELECT * FROM events WHERE kind='grounded_narrative'")
    assert not db.rows('SELECT * FROM known_answers')
    again = await AIManager(config, db, None).draft(q, app)
    assert again.verified is False and len(calls) == 2
    row = db.question(1, q)
    with db.transaction():
        db.save_answer(row['id'], again)
    assert len(db.rows("SELECT * FROM events WHERE kind='grounded_narrative'")) == 1
    assert json.loads(db.one('SELECT answer_provenance FROM questions')['answer_provenance'])['verified'] is False


@pytest.mark.parametrize('change', ['profile', 'graduation', 'project', 'skills', 'employer', 'requisition',
    'role', 'description', 'wording', 'characters', 'words', 'policy_version', 'writing_policy',
    'model', 'provider', 'credential_revision', 'sample', 'verified_fact', 'writing_bank'])
async def test_invalidation(config, db, writing, monkeypatch, change):
    manager, q, app, calls = writing
    first = await manager.draft(q, app)
    if change in {'profile', 'graduation', 'project', 'skills'}:
        profile = config.profile
        if change == 'profile':
            profile['identity']['first_name'] = 'Changed'
        elif change == 'graduation':
            profile['education']['graduation_date'] = '2029-05-01'
        elif change == 'project':
            profile['projects'] = [{'name': 'New project', 'description': 'A calendar.'}]
        else:
            profile['skills'] = ['Python', 'SQL']
        (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    elif change in {'employer', 'requisition', 'role', 'description'}:
        key = {'employer': 'company', 'requisition': 'canonical_url', 'role': 'title', 'description': 'description'}[change]
        app = {**app, key: app[key]+' changed'}
    elif change == 'wording':
        q = replace(q, label='Why this company and its engineering team?')
    elif change == 'characters':
        q = replace(q, max_length=1000)
    elif change == 'words':
        q = replace(q, label=q.label+' Maximum 200 words')
    elif change == 'policy_version':
        monkeypatch.setattr(n, 'POLICY_VERSION', 'test-v2')
    elif change == 'writing_policy':
        monkeypatch.setattr(n, 'WRITING_POLICY', n.WRITING_POLICY+' Be concise.')
    elif change in {'model', 'provider', 'credential_revision'}:
        config.data['ai']['providers'][0][{'provider': 'name'}.get(change, change)] = 'changed'
    elif change == 'sample':
        folder = config.private/'writing_samples'
        folder.mkdir()
        (folder/'sample.txt').write_text('A concise style example.')
    elif change == 'verified_fact':
        db.set_setting('verified_fact:education.school', dict(source='USER_PROVIDED', value='New University'))
    else:
        db.execute("INSERT INTO written_responses(question,topic,answer,company,job_title,verified,created_at) VALUES ('other','general','User text','Example','Intern',1,'today')")
    second = await manager.draft(q, app)
    assert len(calls) == 4
    assert second.provenance['narrative_signature'] != first.provenance['narrative_signature']
    assert second.verified is False


async def test_unrelated_event_and_use_do_not_invalidate(db, writing):
    manager, q, app, calls = writing
    await manager.draft(q, app)
    db.event(1, 'unrelated_application_event', 'unchanged writing')
    db.execute("UPDATE written_responses SET last_used_at='today'")
    await manager.draft(q, {**app, 'updated_at': 'later', 'status': 'APPLYING'})
    assert len(calls) == 2


@pytest.mark.parametrize('guard', ['REQUIRE_USER', 'DO_NOT_ANSWER', 'rejected', 'profile_policy', 'disabled'])
async def test_cache_never_bypasses_admission(config, db, writing, guard):
    manager, q, app, calls = writing
    await manager.draft(q, app)
    if guard == 'rejected':
        q = replace(q, semantic_key=REJECTED_FIELD)
    elif guard == 'profile_policy':
        profile = config.profile
        profile['autofill_policies'] = {describe(q, app).semantic_key: 'DO_NOT_ANSWER'}
        (config.private/'profile.yaml').write_text(yaml.safe_dump(profile))
    elif guard == 'disabled':
        config.data['ai']['enabled'] = False
    else:
        q = replace(q, policy=guard)
    with pytest.raises(ProviderUnavailable):
        await manager.draft(q, app)
    assert len(calls) == 2


@pytest.mark.parametrize('label', ['First name', 'Email', 'Phone number', 'Degree', 'School', 'GPA',
    'Graduation date', 'GitHub', 'Sponsorship now', 'Sponsorship future', 'Are you a US citizen?',
    'Do you require sponsorship now?', 'Gender', 'Privacy consent', 'Describe your legal name',
    'Tell us your social security number', 'Describe your date of birth', 'Describe your email',
    'Why do you need visa sponsorship?', 'Describe your disability', 'Describe years of experience',
    'Describe your legal history', 'Describe employee number', 'Tell us your address', 'Describe your age',
    'Describe criminal background', 'Describe marital status'])
async def test_factual_provider_calls_are_zero(config, db, monkeypatch, label):
    provider = AsyncMock(side_effect=AssertionError('Factual provider call'))
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', provider)
    with pytest.raises(ProviderUnavailable):
        await AIManager(config, db, None).draft(Question('fact', label, 'textarea'), {'id': 1})
    provider.assert_not_awaited()


@pytest.mark.parametrize('label,section,value', [
    ('Describe your education', 'education', {'school': 'Test School', 'major': 'CS'}),
    ('Describe your skills', 'skills', ['Python', 'SQL']),
    ('Describe your project names', 'projects', [{'name': 'Course Tracker'}]),
    ('Describe your availability', 'availability', {'start_date': '2027-06-01'}),
    ('Describe your links', 'links', {'github': 'https://github.com/example'})])
async def test_templates_precede_provider(config, db, monkeypatch, label, section, value):
    profile = config.profile
    profile[section] = value
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile))
    provider = AsyncMock(side_effect=AssertionError('Template used AI'))
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', provider)
    answer = await AIManager(config, db, None).draft(Question('t', label, 'textarea'), {'id': 1, 'company': 'Example', 'title': 'Intern'})
    assert answer.source == 'narrative_template' and answer.verified is False
    assert answer.evidence == ['profile.'+section]
    provider.assert_not_awaited()


async def test_missing_template_facts_do_not_fall_back_to_inference(config, db, writing):
    manager, q, app, calls = writing
    with pytest.raises(ProviderUnavailable):
        await manager.draft(replace(q, label='Describe your skills'), app)
    assert not calls
    profile = config.profile
    profile['education'] = {'school': None}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile))
    with pytest.raises(ProviderUnavailable):
        await manager.draft(replace(q, label='Describe your education'), app)
    assert not calls


@pytest.mark.parametrize('bad', ['', '[Your name] loves this company.', 'TODO', 'I built 500 projects.',
                                  'I graduate in 2035.', 'I am thrilled to join.'])
async def test_deterministic_output_validation(writing, monkeypatch, bad):
    manager, q, app, calls = writing
    async def generate(self, request):
        return dict(answer=bad, fact_ids=['project'], needs_input=False)
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', generate)
    with pytest.raises(ProviderUnavailable) as error:
        await manager.draft(q, app)
    assert error.value.category in {'EMPTY_RESPONSE', 'CONTENT_VALIDATION'}
    assert not manager.db.rows('SELECT * FROM narrative_cache')


@pytest.mark.parametrize('failure', ['AUTHENTICATION', 'RATE_LIMIT', 'TRANSPORT_TIMEOUT', 'UNAVAILABLE', 'MALFORMED_RESPONSE'])
async def test_provider_failure_is_typed_and_pool_invalidated(writing, monkeypatch, failure):
    manager, q, app, _ = writing
    fail = AsyncMock(side_effect=ProviderUnavailable('fixture failure', failure))
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', fail)
    with pytest.raises(ProviderUnavailable) as error:
        await manager.draft(q, app)
    assert error.value.category == failure and not manager._providers
    assert not manager.db.rows('SELECT * FROM narrative_cache')


async def test_malformed_object_and_timeout(writing, monkeypatch):
    manager, q, app, _ = writing
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', AsyncMock(return_value=[]))
    with pytest.raises(ProviderUnavailable) as error:
        await manager.draft(q, app)
    assert error.value.category == 'MALFORMED_RESPONSE'
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(ProviderUnavailable) as error:
        await manager.draft(q, app)
    assert error.value.category == 'TRANSPORT_TIMEOUT'


async def test_user_correction_outranks_cache_and_preserves_proposal(config, db, writing):
    manager, q, app, calls = writing
    row = db.question(1, q)
    db.transition(1, State.NEEDS_INPUT)
    controller = Controller(config, db, manager)
    generated = await controller.draft(row['id'])
    proposal = db.one('SELECT * FROM written_responses WHERE verified=0')
    controller.answer(row['id'], 'My corrected application answer.')
    answer = await manager.draft(q, db.application(1))
    assert answer.verified is True and answer.value == 'My corrected application answer.'
    assert len(calls) == 2
    assert db.one('SELECT * FROM written_responses WHERE id=?', (proposal['id'],)) == proposal
    saved = json.loads(db.one('SELECT answer_provenance FROM questions')['answer_provenance'])
    assert saved['provenance']['replaces_proposal']['proposal_id'] == proposal['id']
    assert generated != answer.value


async def test_verified_bank_preferred_even_after_cache(config, db, writing):
    manager, q, app, calls = writing
    await manager.draft(q, app)
    db.execute("""INSERT INTO written_responses(question,topic,answer,company,job_title,verified,created_at,question_signature,profile_revision)
        VALUES (?,?,?,?,?,1,'today',?,?)""", (q.label, 'general', 'Verified writing.', app['company'], app['title'],
            describe(q, app).signature, config.profile_snapshot().revision))
    answer = await manager.draft(q, app)
    assert answer.source == 'verified_writing_bank' and answer.verified is True
    assert len(calls) == 2


async def test_bounded_cache_preserves_audit(db, writing, monkeypatch):
    manager, q, app, calls = writing
    monkeypatch.setattr(n, 'MAX_CACHE_ENTRIES', 2)
    for i in range(4):
        await manager.draft(replace(q, label=f'Why this company? Topic {i}'), app)
    assert len(db.rows('SELECT * FROM narrative_cache')) == 2
    assert len(db.rows('SELECT * FROM written_responses WHERE verified=0')) == 4
    for row in db.rows('SELECT * FROM written_responses WHERE verified=0'):
        provenance = json.loads(row['narrative_provenance'])
        assert provenance['narrative_signature'] and provenance['context_revision']
        assert provenance['provider'] == 'fixture' and provenance['model'] == 'Fixture'
    await manager.draft(replace(q, label='Why this company? Topic 3'), app)
    assert len(calls) == 8


def test_context_is_minimal_and_descriptor_immutable(config, db, listing, grounded):
    q = Question('project', 'Describe a project', 'textarea')
    app = {'id': 1, 'company': 'Example', 'title': 'Intern', 'description': 'Listing'}
    snapshot = config.profile_snapshot()
    facts, context = n.build_context(q, app, snapshot)
    assert set(facts) == {'project'}
    assert 'job.description' not in context
    assert not any('email' in k or 'citizen' in k or 'authorization' in k for k in facts)
    args = (q, app, describe(q, app), snapshot, 0, 0, 'fixture', facts, context, ())
    request = n.NarrativeRequest.create(*args)
    assert request.signature == n.NarrativeRequest.create(*args).signature
    with pytest.raises(FrozenInstanceError):
        request.employer = 'Other'


async def test_saved_generated_receipt_cannot_bypass_narrative_revision(config, db, writing):
    manager, q, app, _ = writing
    answer = await manager.draft(q, app)
    resolver = AnswerResolver(config, db)
    result = resolver.resolve_result(q, app)
    resolver.stamp(answer, result.descriptor)
    row = db.question(1, q)
    db.save_answer(row['id'], answer)
    row = db.one('SELECT * FROM questions WHERE id=?', (row['id'],))
    assert resolver.replay(row, q, app) is None


async def test_cli_readiness_ttl_revision_failure_and_revalidation(monkeypatch):
    provider = CodexWritingProvider(dict(name='fixture', billing='included', executable='unused'), None, None)
    clock = [100.0]
    monkeypatch.setattr('autoapply.codex_writer.time.monotonic', lambda: clock[0])
    revision = ['first']
    monkeypatch.setattr(provider, 'credential_revision', lambda: revision[0])
    provider.run = AsyncMock(return_value='Logged in using ChatGPT')
    await provider.ensure_ready('unused')
    await provider.ensure_ready('unused')
    assert provider.run.await_count == 1
    clock[0] += 61
    await provider.ensure_ready('unused')
    revision[0] = 'changed'
    await provider.ensure_ready('unused')
    assert provider.run.await_count == 3
    monkeypatch.setattr(provider, '_generate_response', AsyncMock(side_effect=ProviderUnavailable('failure')))
    with pytest.raises(ProviderUnavailable):
        await provider.generate_response({})
    await provider.ensure_ready('unused')
    assert provider.run.await_count == 4


async def test_cli_auth_failure_is_not_cached():
    provider = CodexWritingProvider(dict(name='fixture', billing='included', executable='unused'), None, None)
    provider.run = AsyncMock(return_value='Logged in using API key')
    for _ in range(2):
        with pytest.raises(ProviderUnavailable) as error:
            await provider.ensure_ready('unused')
        assert error.value.category == 'AUTHENTICATION'
    assert provider.run.await_count == 2


async def test_provider_pool_config_change_and_selection(writing):
    manager, q, app, calls = writing
    await manager.draft(q, app)
    original = next(iter(manager._providers.values()))
    await manager.draft(replace(q, label='Why this role?'), app)
    assert next(iter(manager._providers.values())) is original
    manager.config.data['ai']['providers'][0]['model_text'] = 'New model'
    await manager.draft(q, app)
    assert next(iter(manager._providers.values())) is not original
    with pytest.raises(ProviderUnavailable, match='No configured Tier 4'):
        await manager.draft(q, app, tier=4)
    assert len(calls) == 6


async def test_revision_change_during_provider_await_refuses_output(config, db, writing, monkeypatch):
    manager, q, app, calls = writing
    original = BrowserAIProvider.generate_response
    async def changed(self, request):
        result = await original(self, request)
        if request.get('task') == 'verify_grounding':
            db.set_setting('verified_fact:education.school', dict(source='USER_PROVIDED', value='Changed'))
        return result
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', changed)
    with pytest.raises(ProviderUnavailable, match='inputs changed'):
        await manager.draft(q, app)
    assert not db.rows('SELECT * FROM narrative_cache')


async def test_cache_payload_change_requires_new_audit(db, writing):
    manager, q, app, calls = writing
    await manager.draft(q, app)
    db.execute("UPDATE written_responses SET answer='A different proposal.' WHERE verified=0")
    await manager.draft(q, app)
    assert len(calls) == 4


async def test_verified_writing_revision_external_change_and_rollback(db, writing):
    import sqlite3
    manager, q, app, calls = writing
    await manager.draft(q, app)
    before = db.writing_revision()
    try:
        with db.transaction():
            db.execute("INSERT INTO written_responses(question,topic,answer,company,job_title,verified,created_at) VALUES ('other','general','Correction','Example','Intern',1,'today')")
            assert db.writing_revision() > before
            raise ValueError('rollback')
    except ValueError:
        pass
    assert db.writing_revision() == before
    await manager.draft(q, app)
    assert len(calls) == 2
    path = db.conn.execute('PRAGMA database_list').fetchone()[2]
    with sqlite3.connect(path) as other:
        other.execute("INSERT INTO written_responses(question,topic,answer,company,job_title,verified,created_at) VALUES ('other','general','Correction','Example','Intern',1,'today')")
    await manager.draft(q, app)
    assert len(calls) == 4


@pytest.mark.browser
async def test_browser_rerender_failure_reuses_proposal(browser, db, writing):
    manager, q, app, calls = writing
    page = await browser.new_page()
    await page.set_content('<textarea id="old"></textarea>')
    proposal = await manager.draft(q, app)
    await page.locator('#old').evaluate("e => e.outerHTML = '<textarea id=\"new\"></textarea>'")
    from playwright.async_api import TimeoutError as BrowserTimeout
    with pytest.raises(BrowserTimeout):
        await page.locator('#old').fill(proposal.value, timeout=100)
    assert not db.rows("SELECT * FROM events WHERE kind='grounded_narrative'")
    again = await manager.draft(replace(q, key='new'), app)
    await page.locator('#new').fill(again.value)
    assert await page.locator('#new').input_value() == proposal.value
    assert len(calls) == 2 and again.verified is False
    await page.close()


async def test_cli_cancellation_invalidates_readiness(monkeypatch):
    provider = CodexWritingProvider(dict(name='fixture', billing='included', executable='unused'), None, None)
    provider._ready_until = float('inf')
    monkeypatch.setattr(provider, '_generate_response', AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await provider.generate_response({})
    assert provider._ready_until == 0


async def test_offline_validation_blocks_external_services_and_providers(monkeypatch, tmp_path):
    import importlib.util
    import socket
    root = Path(__file__).resolve().parents[1]
    for name, path, fixture in [
        ('phase5_guard', root/'docs/phase5-2026-09-25/offline_validation.py', 'offline_only'),
        ('phase8_guard', root/'docs/phase8-2026-09-27/offline_providers.py', 'no_live_writing_provider'),
    ]:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if fixture == 'no_live_writing_provider':
            getattr(module, fixture).__wrapped__(monkeypatch, tmp_path)
        else:
            getattr(module, fixture).__wrapped__(monkeypatch)
    for host in ('api.openai.com', 'employer.example', 'gmail.googleapis.com', 'discord.com'):
        with pytest.raises(AssertionError, match='external DNS'):
            socket.getaddrinfo(host, 443)
    with pytest.raises(AssertionError, match='fake writing provider'):
        await CodexWritingProvider.run(None, [], 'unused')
    with pytest.raises(AssertionError, match='fake writing provider'):
        await BrowserAIProvider.generate_response(None, {})


def test_credential_and_model_change_revision(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    provider = CodexWritingProvider(dict(name='fixture', billing='included', executable='unused'), None, None)
    absent = provider.credential_revision()
    (tmp_path/'auth.json').write_text('{}')
    present = provider.credential_revision()
    assert present != absent
    provider.settings['model'] = 'another'
    assert provider.credential_revision() != present


async def test_persistent_cache_new_connection(config, db, writing):
    from autoapply.database import Database
    manager, q, app, calls = writing
    first = await manager.draft(q, app)
    path = db.conn.execute('PRAGMA database_list').fetchone()[2]
    other = Database(path, startup_maintenance=False)
    try:
        reused = await AIManager(config, other, None).draft(q, app)
        assert reused.value == first.value and reused.verified is False
        assert len(calls) == 2
    finally:
        other.close()


async def test_explicit_employer_scope_still_obeys_phase5_requisition_signature(writing):
    manager, q, app, calls = writing
    q = replace(q, scope='employer:example')
    await manager.draft(q, app)
    await manager.draft(q, {**app, 'id': 99})  # same canonical requisition, explicit employer scope
    assert len(calls) == 2
    await manager.draft(q, {**app, 'canonical_url': app['canonical_url']+'-other'})
    assert len(calls) == 4


async def test_options_and_field_kind_cannot_hit_narrative_cache(writing):
    manager, q, app, calls = writing
    await manager.draft(q, app)
    for changed in (replace(q, options=['Yes', 'No']), replace(q, kind='select')):
        with pytest.raises(ProviderUnavailable):
            await manager.draft(changed, app)
    assert len(calls) == 2
    with pytest.raises(ProviderUnavailable, match='character limit'):
        await manager.draft(replace(q, max_length=0), app)
