"""T092 offline recovery: synthetic facts, in-process API, stub provider only."""
import json
from pathlib import Path
import subprocess
from threading import Event, Thread

import pytest

from autoapply.answers import AnswerResolver, fit_options, repair_facts
from autoapply.concepts import equivalent_answer
from autoapply.extension_api import resolve_answers, remember
from autoapply.field_mapping import describe
from autoapply.models import Question
from autoapply.repair_queue import RepairQueue, evidence
from test_dashboard import dashboard
from test_t085_answer_repair import field, proposal, repair_profile, save_profile


URL = 'https://fixture.test/job'


def test_repair_capability_behavioral_regression_against_prechange_commit():
    root = Path(__file__).resolve().parents[1]
    harness = root / 'tests/fixtures/extension/answer-recovery/capability.cjs'
    baseline = subprocess.run(['git', 'show', '69ad62e:extension/content.js'], cwd=root,
                              capture_output=True, text=True, check=True, timeout=15).stdout
    # A real before/after guard reproduction, separate from syntax/source checks.
    for mode, source in [('baseline', baseline), ('current', (root / 'extension/content.js').read_text(encoding='utf-8'))]:
        result = subprocess.run(['node', str(harness), mode], input=source, text=True,
                                capture_output=True, cwd=root, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr


def test_automatic_retry_scheduler_executes_bounded_recovery():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/fixtures/extension/answer-recovery/automatic.cjs')],
                            text=True, capture_output=True, cwd=root, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_shipped_combobox_selection_requires_retained_exact_commit():
    root = Path(__file__).resolve().parents[1]
    harness = root / 'tests/fixtures/extension/answer-recovery/selection.cjs'
    baseline = subprocess.run(['git', 'show', '69ad62e:extension/content.js'], cwd=root,
                              capture_output=True, text=True, check=True, timeout=15).stdout
    for mode in ('baseline', 'current'):
        result = subprocess.run(['node', str(harness), mode], input=baseline if mode == 'baseline' else None,
                                text=True, capture_output=True, cwd=root, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr


def test_successful_field_results_survive_reconciliation_to_subset(dashboard, repair_profile, monkeypatch):
    calls = []
    def writer(request):
        calls.append(request)
        return {'answers': [proposal(f['key']) for f in request['fields']]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    a, b = unfamiliar(), unfamiliar(key='other')
    assert len(recover(dashboard, [a, b])) == 2
    assert recover(dashboard, [b])[0]['value'] == 'Python (programming)'
    assert len(calls) == 1


def test_missing_fact_reused_when_delayed_options_change_batch(dashboard, config, repair_profile, monkeypatch):
    calls = []
    def writer(request):
        calls.append(request)
        return {'answers': [proposal(f['key']) if f['fact_ids'] else
                            dict(proposal(f['key'], value=None, needs_input=True, reason='needs_fact'), fact_ids=[])
                            for f in request['fields']]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    missing = field('missing', label='Which bicycle model do you ride?', options=['Road', 'Touring'])
    recover(dashboard, [missing])
    rows = recover(dashboard, [dict(missing, key='rebound-missing'), unfamiliar()])
    assert [row['reason_code'] for row in rows] == ['needs_fact', 'answer_available']
    assert [[f['key'] for f in request['fields']] for request in calls] == [['missing'], ['tools']]


def test_rebound_cache_keeps_exact_option_spelling(dashboard, repair_profile, monkeypatch):
    calls = []
    def writer(request):
        calls.append(request)
        f, = request['fields']
        return {'answers': [proposal(f['key'], value=f['options'][0])]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    assert recover(dashboard, [unfamiliar()])[0]['value'] == 'Python (programming)'
    assert recover(dashboard, [unfamiliar(key='rebound', options=['PYTHON (PROGRAMMING)', 'Rust'])])[0]['value'] == 'PYTHON (PROGRAMMING)'
    assert len(calls) == 2


@pytest.mark.parametrize('decline', [
    'Do not wish to identify', 'DO NOT WISH TO IDENTIFY.', 'I do not wish to identify.',
    "I don't wish to identify", 'I don’t wish to self-identify.',
    'Prefer not to identify', 'I prefer not to self-identify.', 'Choose not to identify',
    'I choose not to self-identify.', 'Do not wish to identify (United States)',
])
def test_observed_race_decline_resolves_exact_checkbox_without_ai(dashboard, config, repair_profile, monkeypatch, decline):
    repair_profile['demographics'] = {'race': 'decline'}
    save_profile(config, repair_profile)
    calls = []
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: calls.append(request) or {'answers': []})
    fields = [field('race', label='Race/Ethnicity: Please select any/all groups that you identify with',
                    kind='checkbox', multiple=True, options=['American Indian or Alaskan Native', 'Asian',
                    'Black or African American', 'Hispanic or Latino', 'Native Hawaiian or Other Pacific Islander',
                    'White (Not of Hispanic Origin)', decline]),
              field('conflict', label='Do you or your spouse have a business interest in a competitor?', options=['Yes', 'No'])]
    race, conflict = recover(dashboard, fields)
    assert race['status'] == 'RESOLVED' and race['value'] == [decline]
    assert race['source'] == 'profile:demographics.race'
    assert race['evidence'] == ['demographics.race']
    assert conflict['value'] is None and conflict['reason_code'] == 'needs_fact'
    assert not calls, calls


@pytest.mark.parametrize('option', [
    'Asian', 'I do not identify as a veteran', 'I am not a veteran',
    'Do not identify with any groups', 'I prefer not to identify as a veteran',
    'Do not wish to identify (I am a protected veteran)',
    'Do not wish to identify (except gender)', 'Do not wish to identify and disclose',
])
def test_decline_never_matches_substantive_options_or_qualifiers(dashboard, config, repair_profile, option):
    repair_profile['demographics'] = {'race': 'decline'}
    save_profile(config, repair_profile)
    assert equivalent_answer(option) == option
    assert fit_options('decline', [option]) is None
    # The repair_profile fixture rejects any provider call, even with AI enabled.
    required, optional = recover(dashboard, [
        field('required', label='Race/Ethnicity', kind='checkbox', multiple=True, options=[option, 'White']),
        field('optional', label='Race/Ethnicity', kind='checkbox', multiple=True, required=False, options=[option, 'White']),
    ])
    assert required['value'] is None and required['status'] == 'NEEDS_INPUT'
    assert required['reason_code'] == 'needs_fact'
    assert optional['value'] is None and optional['status'] == 'OPTIONAL_SKIP'
    assert optional['reason_code'] == 'optional_skip'


def test_multiple_decline_offers_still_require_unambiguous_choice():
    assert fit_options('decline', ['Do not wish to identify', 'Prefer not to identify']) is None


def recover(dashboard, fields, **kwargs):
    return resolve_answers(dashboard, dict(url=URL, fields=fields, **kwargs))['answers']


def unfamiliar(**changes):
    return field(label='Which implementation ecosystem is familiar to you?', **changes)


def test_unknown_wording_uses_minimal_grounding_and_exact_current_offers(dashboard, repair_profile, monkeypatch):
    calls = []
    def writer(request):
        calls.append(request)
        assert set(request['facts']) == {'skills'} and list(request['facts']['skills']) == ['Python']
        assert request['fields'][0]['fact_ids'] == ['skills']
        return {'answers': [proposal()]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    q = unfamiliar(kind='combobox', options=[])
    row, = recover(dashboard, [q], allow_ai=False)
    assert row['reason_code'] == 'no_offered_options' and not calls
    q.update(options=['Python (programming)', 'Rust'], options_observed=True)
    row, = recover(dashboard, [q], allow_ai=False)
    assert row['reason_code'] == 'reasoning_pending' and not calls
    for _ in range(2):
        row, = recover(dashboard, [q])
        assert row['value'] == 'Python (programming)'
        assert row['source'] == 'grounded_ai:answer_repair' and row['evidence'] == ['skills']
        assert row['profile_revision'] and isinstance(row['facts_revision'], int)
    assert len(calls) == 1
    assert 'profile' not in calls[0] and calls[0]['job'] == {}


def test_ordinary_missing_fact_attempts_reasoning_and_policy_is_separate(dashboard, config, repair_profile, monkeypatch):
    calls = []
    def writer(request):
        calls.append(request)
        assert request['facts'] == {}  # No unrelated profile/secrets as filler.
        return {'answers': [dict(proposal(value=None, needs_input=True, reason='needs_fact'), fact_ids=[])]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    row, = recover(dashboard, [field(label='Which bicycle model do you ride?', options=['Road', 'Touring'])])
    assert row['reason_code'] == 'needs_fact' and row['value'] is None and len(calls) == 1
    repair_profile['autofill_policies'] = {'UNKNOWN_FIELD': 'REQUIRE_USER'}
    save_profile(config, repair_profile)
    row, = recover(dashboard, [unfamiliar()])
    assert row['reason_code'] == 'policy_requires_user' and len(calls) == 1
    repair_profile['autofill_policies']['UNKNOWN_FIELD'] = 'DO_NOT_ANSWER'
    save_profile(config, repair_profile)
    row, = recover(dashboard, [unfamiliar()])
    assert row['reason_code'] == 'optional_skip' and len(calls) == 1


@pytest.mark.parametrize('empty_skills', [[], {}], ids=['frozen-list', 'frozen-mapping'])
def test_empty_saved_skills_abstains_then_new_fact_invalidates_abstention(dashboard, config, repair_profile, monkeypatch, empty_skills):
    repair_profile['skills'] = empty_skills
    save_profile(config, repair_profile)
    snapshot = config.profile_snapshot()
    assert snapshot.facts['skills'] == (() if isinstance(empty_skills, list) else {})
    # Exercise the real frozen config path, including its tuple representation.
    calls = []

    def writer(request):
        calls.append(request)
        if len(calls) == 1:
            return {'answers': [dict(proposal(value=None, needs_input=True, reason='needs_fact'), fact_ids=[])]}
        assert list(request['facts']['skills']) == ['Python']
        assert request['fields'][0]['fact_ids'] == ['skills']
        return {'answers': [proposal()]}

    monkeypatch.setattr(dashboard, 'run_writing', writer)
    for _ in range(2):
        response = resolve_answers(dashboard, dict(url=URL, fields=[unfamiliar()]))
        row, = response['answers']
        assert calls[0]['facts'] == {}
        assert all(f['fact_ids'] == [] for f in calls[0]['fields'])
        assert row['status'] == 'NEEDS_INPUT' and row['reason_code'] == 'needs_fact'
        assert row['value'] is None and not row.get('evidence')
        assert response['profile_revision'] == snapshot.revision
    assert len(calls) == 1

    repair_profile['skills'] = ['Python']
    save_profile(config, repair_profile)
    for _ in range(2):
        row, = recover(dashboard, [unfamiliar()])
        assert row['value'] == 'Python (programming)'
        assert row['source'] == 'grounded_ai:answer_repair' and row['evidence'] == ['skills']
        assert row['profile_revision'] != snapshot.revision
    assert len(calls) == 2


def test_mapped_repair_facts_omit_empty_containers_but_keep_false_and_zero(config, repair_profile):
    repair_profile['education'].update(degree=[], major={}, currently_enrolled=False, gpa=0)
    repair_profile['skills'] = {}
    save_profile(config, repair_profile)
    profile = config.profile_snapshot().facts
    app = dict(id=URL, canonical_url=URL, company='')
    for label, expected in [
        ('Are you currently enrolled?', {'education.currently_enrolled': False}),
        ('GPA', {'education.gpa': 0}),
        ('Which degree program are you studying?', None),
        ('What is your major?', None),
        ('Which programming tools do you use?', None),
    ]:
        q = Question('test', label, 'select', True, [])
        assert repair_facts(q, describe(q, app, profile), profile) == expected


@pytest.mark.parametrize('label', [
    'Do you or your spouse have a role on a board or a business interest in a competitor?',
    'Are you a previous employee of Fixture?', 'Have you been a government official?',
    'Please provide details of the relationship.', 'I agree to the Nondisclosure Agreement',
    'Password for your programming tools account', 'Which immigration status do you hold?',
])
def test_no_absence_based_sensitive_assertions(dashboard, repair_profile, label):
    row, = recover(dashboard, [field(label=label, options=['Yes', 'No'])])
    assert row['value'] is None


def test_government_and_auditor_history_need_explicit_facts(dashboard, config, repair_profile):
    fields = [field('government', label='Are you a CURRENT U.S. federal government civilian or military (active duty or reserves) employee?', options=['Yes', 'No']),
              field('auditor', label="Are you a CURRENT or FORMER employee of RTX's Independent Auditor, PriceWaterhouseCoopers (PwC)?", options=['Yes', 'No'])]
    assert all(row['value'] is None for row in recover(dashboard, fields))
    repair_profile['application_preferences'].update(government_employment=False, independent_auditor_employment=True)
    save_profile(config, repair_profile)
    assert [row['value'] for row in recover(dashboard, fields)] == ['No', 'Yes']


def test_general_unknown_rejects_fabricated_evidence_and_stale_facts(dashboard, config, repair_profile, monkeypatch):
    monkeypatch.setattr(dashboard, 'run_writing', lambda request: {'answers': [proposal(fact_ids=['contact.email'])]})
    row, = recover(dashboard, [unfamiliar()])
    assert row['value'] is None and row['reason_code'] == 'provider_invalid'
    dashboard._ext_answer_repairs = {}
    def writer(request):
        repair_profile['skills'] = ['Rust']
        save_profile(config, repair_profile)
        return {'answers': [proposal()]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    row, = recover(dashboard, [unfamiliar()])
    assert row['value'] is None and row['reason_code'] == 'stale_data'


def test_simultaneous_batches_share_one_provider_call(dashboard, repair_profile, monkeypatch):
    entered, release = Event(), Event()
    calls, results, errors = [], [], []
    def writer(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return {'answers': [proposal()]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    def run():
        try:
            results.append(recover(dashboard, [unfamiliar()]))
        except Exception as error:
            errors.append(error)
    first, second = Thread(target=run), Thread(target=run)
    first.start()
    assert entered.wait(5)
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert not errors and not first.is_alive() and not second.is_alive()
    assert len(calls) == 1 and len(results) == 2
    assert all(result[0]['value'] == 'Python (programming)' for result in results)


def test_transient_provider_failure_expires_then_success_is_reused(dashboard, repair_profile, monkeypatch):
    calls = []
    clock = [100.0]
    monkeypatch.setattr('autoapply.extension_api.time.monotonic', lambda: clock[0])
    def writer(request):
        calls.append(request)
        if len(calls) == 1:
            raise TimeoutError('synthetic timeout')
        return {'answers': [proposal()]}
    monkeypatch.setattr(dashboard, 'run_writing', writer)
    for _ in range(2):
        assert recover(dashboard, [unfamiliar()])[0]['reason_code'] == 'provider_unavailable'
    assert len(calls) == 1
    clock[0] += 16
    for _ in range(3):
        assert recover(dashboard, [unfamiliar()])[0]['value'] == 'Python (programming)'
    assert len(calls) == 2


def test_verified_memory_retrieval_binds_scope_revision_source_and_options(dashboard, config, repair_profile):
    old = field(label='Which bicycle model do you ride?', options=['Road', 'Touring'])
    remember(dashboard, dict(old, url=URL, value='Road'))
    q = Question('new', 'Select the bicycle model you ride most often', 'select', True, ['ROAD', 'Touring'])
    app = dict(id=URL, canonical_url=URL, company='')
    with dashboard.connection() as db:
        resolver = AnswerResolver(config, db)
        resolver.refresh()
        facts = resolver.repair_memory_facts(q, app, describe(q, app, resolver.snapshot.facts))
        assert len(facts) == 1 and next(iter(facts.values()))['answer'] == 'Road'
        other = dict(app, id=URL + '/other', canonical_url=URL + '/other')
        assert resolver.repair_memory_facts(q, other, describe(q, other, resolver.snapshot.facts)) == {}
        assert resolver.repair_memory_facts(Question('new', q.label, 'select', True, ['Track']), app,
                                            describe(q, app, resolver.snapshot.facts)) == {}
    repair_profile['skills'] = ['Rust']
    save_profile(config, repair_profile)
    with dashboard.connection() as db:
        resolver = AnswerResolver(config, db)
        resolver.refresh()
        assert resolver.repair_memory_facts(q, app, describe(q, app, resolver.snapshot.facts)) == {}


def test_structural_fault_queue_is_value_free_idempotent_and_pauseable(tmp_path):
    # No runner, service or process starts. Only the existing durable queue is exercised.
    saved = dict(host='fixture.test', nodes=[], diagnostic=dict(fields=[dict(
        label='Which bicycle model do you ride?', kind='combobox', required=True,
        active=True, detected=True, answered=False, reasons=[], repair_status='verification_failed',
        value='PRIVATE_SENTINEL', options=['PRIVATE_SENTINEL'],
        control=dict(tag='input', role='combobox', type='text', id='PRIVATE_SENTINEL'), ancestors=[], menu={})]))
    queue = RepairQueue(tmp_path / 'queue')
    try:
        assert queue.enqueue(saved)['status'] == 'disabled'
        queue.configure(dict(enabled=True, paused=True))
        first = queue.enqueue(saved)
        assert first['status'] == 'queued' and first['reason'] == 'paused'
        assert queue.enqueue(saved)['id'] == first['id']
        fixture, _ = evidence(saved)
        assert 'PRIVATE_SENTINEL' not in json.dumps(fixture)
        assert fixture['controls'][0]['causes'] == ['verification_failed']
        saved['diagnostic']['fields'][0].update(repair_status='unresolved', reason_code='needs_fact')
        assert evidence(saved)[0] is None
        saved['diagnostic']['fields'][0].update(detected=False, reasons=['omitted_by_scanner'])
        assert evidence(saved)[0]['controls'][0]['causes'] == ['omitted_by_scanner']
    finally:
        queue.close()
