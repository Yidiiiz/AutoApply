import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import yaml

from autoapply.ai import AIManager, BrowserAIProvider, ProviderUnavailable
from autoapply.answers import is_writing_question, writing_topic
from autoapply.engine import Engine
from autoapply.models import Question
from autoapply.applications import GenericApplicationAdapter
from test_browser import browser, site


@pytest.fixture
def grounded(config, db, listing, monkeypatch):
    profile = config.profile
    profile['verified_facts'] = {'project': 'I built a Python course availability tracker.'}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    config.data['ai'].update(enabled=True, providers=[dict(name='fixture', billing='included', tier=3,
        url='https://example.test', input_selector='input', send_selector='button',
        response_selector='article', model_selector='label', model_text='Fixture')])
    answer = ('The software engineering internship at Example Company would let me contribute to '
              'the work described in this role while developing my engineering skills. I built a Python '
              'course availability tracker, and I would bring that experience to the team. As a Computer '
              'Science undergraduate at Example University, I would welcome the opportunity to apply '
              'my project experience to this internship.')
    requests = []
    async def generate(self, request):
        requests.append(request)
        if request.get('task') == 'verify_grounding':
            return dict(supported=True, needs_input=False, unsupported_claims=[], evidence=[
                dict(source_id='project', quote=profile['verified_facts']['project']),
                dict(source_id='job.description', quote=request['sources']['job.description'])])
        return dict(answer=answer, fact_ids=['project'], needs_input=False)
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', generate)
    return answer, requests


@pytest.mark.parametrize('label,required,topic', [
    ('Why Example Company?', True, 'FREE_RESPONSE_COMPANY_INTEREST'),
    ('Why this company?', False, 'FREE_RESPONSE_COMPANY_INTEREST'),
    ('Why are you interested in this role?', False, 'FREE_RESPONSE_ROLE_INTEREST'),
    ('What interests you about this opportunity?', True, 'FREE_RESPONSE_ROLE_INTEREST')])
async def test_generate_grounded_narratives(config, db, listing, grounded, label, required, topic):
    db.ingest(listing, config)
    answer = await AIManager(config, db, None).draft(Question('why', label, 'textarea', required), db.application(1))
    assert answer.value == grounded[0] and answer.confidence == 1
    assert writing_topic(label) == topic
    request = grounded[1][0]
    assert request['verified_facts']['project'].rstrip('.') in answer.value
    assert request['job_description'] == listing.description
    assert request['company'] in answer.value
    audit = json.loads(db.one("SELECT detail FROM events WHERE kind='grounded_narrative'")['detail'])
    assert audit['supporting_facts']['job.description'] == listing.description
    assert not db.rows('SELECT * FROM notifications')


@pytest.mark.parametrize('failure', ['unknown_id', 'unsupported', 'false_quote', 'character_limit', 'word_limit', 'unavailable'])
async def test_generation_failures_never_supply_text(config, db, listing, grounded, monkeypatch, failure):
    db.ingest(listing, config)
    original = BrowserAIProvider.generate_response
    async def invalid(self, request):
        if failure == 'unavailable':
            raise ProviderUnavailable('Fixture offline')
        value = await original(self, request)
        if request.get('task') == 'verify_grounding':
            if failure == 'unsupported':
                value.update(supported=False, unsupported_claims=['invented achievement'])
            if failure == 'false_quote':
                value['evidence'][0]['quote'] = 'I led NASA missions.'
        elif failure == 'unknown_id':
            value['fact_ids'] = ['invented']
        return value
    monkeypatch.setattr(BrowserAIProvider, 'generate_response', invalid)
    q = Question('why', 'Why this company? Maximum 5 words' if failure == 'word_limit' else 'Why this company?',
                 'textarea', False, max_length=20 if failure == 'character_limit' else None)
    with pytest.raises(ProviderUnavailable):
        await AIManager(config, db, None).draft(q, db.application(1))
    assert not db.rows('SELECT * FROM written_responses')


async def test_character_limit_is_passed_and_accepted(config, db, listing, grounded):
    db.ingest(listing, config)
    q = Question('why', 'Why this company?', 'textarea', max_length=len(grounded[0]))
    result = await AIManager(config, db, None).draft(q, db.application(1))
    assert len(result.value) == q.max_length
    assert grounded[1][0]['max_characters'] == q.max_length


@pytest.mark.parametrize('label', ['Describe your disability', 'Tell us your ethnicity', 'Why do you need visa sponsorship?'])
def test_sensitive_narratives_excluded(label):
    assert not is_writing_question(Question('x', label, 'textarea', False))


@pytest.mark.browser
@pytest.mark.parametrize('required', [True, False])
async def test_production_fills_narrative_and_skips_optional_demographic(config, db, browser, listing, site, grounded, required):
    db.ingest(replace(listing, url=site[0]), config)
    engine = Engine(config, db, browser)
    original = browser.navigate
    async def navigate(page, url):
        result = await original(page, url)
        await page.locator('textarea').evaluate("(e, required) => { e.previousElementSibling && (e.previousElementSibling.textContent='Why this company?'); e.setAttribute('aria-label','Why this company?'); e.removeAttribute('maxlength'); e.required=required; }", required)
        await page.locator('form').evaluate("e=>e.insertAdjacentHTML('beforeend','<label>Gender<input name=gender></label>')")
        return result
    browser.navigate = navigate
    await engine.process_one(1)
    questions = db.rows('SELECT * FROM questions WHERE application_id=1')
    narrative = next(q for q in questions if 'Why this company' in q['raw_question'])
    assert narrative['status'] == 'ANSWERED', questions
    assert json.loads(narrative['answer']) == grounded[0]
    assert next(q for q in questions if q['raw_question']=='Gender')['status'] == 'SKIPPED'
    assert len(site[1]) == 1
    assert db.application(1)['status'] == 'SUBMITTED'


@pytest.mark.browser
async def test_optional_generation_failure_blocks_submission(config, db, browser, listing, site):
    db.ingest(replace(listing, url=site[0]), config)
    engine = Engine(config, db, browser)
    engine.ai.draft = AsyncMock(side_effect=ProviderUnavailable('No configured provider'))
    original = browser.navigate
    async def navigate(page,url):
        result = await original(page,url)
        await page.locator('textarea').evaluate("e=>{e.required=false;e.setAttribute('aria-label','Why this company?')}")
        return result
    browser.navigate=navigate
    await engine.process_one(1)
    assert not site[1]
    assert not db.application(1)['submit_intent_at']


@pytest.mark.browser
async def test_limits_from_accessible_help_text(browser):
    page=await browser.new_page()
    await page.set_content('<label>Why us?<textarea aria-describedby="hint"></textarea></label><p id=hint>Maximum 80 words</p>'
                           '<label>Why this role?<textarea aria-describedby="chars" maxlength=500></textarea></label><p id=chars>Maximum 200 characters</p>')
    questions=await GenericApplicationAdapter(page).get_questions({'id':1})
    assert 'Maximum 80 words' in questions[0].label
    assert questions[1].max_length == 200
