import pytest

from autoapply.candidate_policy import evaluate
from autoapply.jobs import eligibility, phd_rejection
from autoapply.models import now


@pytest.mark.parametrize('title,description,reason', [
    ('PhD Research Intern', '', 'phd_title'),
    ('Ph.D. Software Intern', '', 'phd_title'),
    ('Machine Learning Intern (PHD)', '', 'phd_title'),
    ('Ph.D Software Intern', '', 'phd_title'),
    ('Ph D Intern', '', 'phd_title'),
    ('Bachelor or PhD Intern', 'Bachelor accepted', 'phd_title'),
    ('Software Intern', 'Currently pursuing a PhD in CS', 'phd_without_bachelor'),
    ('Software Intern', 'Preferred: Ph.D.', 'phd_without_bachelor'),
    ('Software Intern', "Pursuing a Bachelor's, Master's or PhD", ''),
    ('Software Intern', 'Pursuing bachelors or ph d', ''),
    ('graphd Intern', 'Use aphdx tools', ''),
    ('Software Intern', 'Undergraduate internship.', ''),
])
def test_phd_policy(config, title, description, reason):
    job = dict(id=1, title=title, description=description, location='New York, NY', posted_at=now())
    decision = evaluate(job, config.profile_snapshot())
    assert phd_rejection(title, description) == reason
    if reason:
        assert decision.status == 'REJECT' and decision.reason == reason and not decision.proceed
        check = eligibility(job, {})
        assert check.eligible is False and check.reasons == [reason]
    else:
        assert decision.proceed
        assert eligibility(job, config.profile).eligible is True


def test_phd_precedes_missing_evidence(config):
    job = dict(id=1, title='Intern', description='PhD in CS', location='', posted_at=None)
    assert evaluate(job, config.profile_snapshot()).reason == 'phd_without_bachelor'


@pytest.mark.parametrize('changes,reason', [
    ({'listing_status': 'CLOSED'}, 'confirmed_closed'),
    ({'posted_at': '2020-01-01'}, 'stale_listing'),
    ({'status': 'SUBMITTED'}, 'protected_application'),
    ({'status': 'ALREADY_APPLIED'}, 'protected_application'),
    ({'submit_intent_at': now()}, 'protected_application'),
])
def test_existing_boundaries_precede_phd(config, changes, reason):
    job = dict(id=1, title='PhD Intern', description='', location='', posted_at=None) | changes
    assert evaluate(job, config.profile_snapshot()).reason == reason


def heterogeneous_candidates(reference, count=720):
    """Unique, realistic descriptions with shared clauses and unknown qualifiers."""
    from datetime import timedelta
    clauses = [
        'Must be authorized to work in the United States.',
        'Must be a US citizen.', 'No visa sponsorship.',
        "Currently pursuing a Bachelor's degree in Computer Science.",
        'Must have a graduate degree.', 'Must graduate in 2028.',
        'Minimum GPA of 3.5 required.', 'Strong communication skills.',
        'Ability to work independently and in a team.',
        'Strong analytical and problem-solving skills.',
        'Must have five years of Kubernetes experience.',
        'Must be located in Canada.',
        'Ability to work independently with limited supervision.',
        'Internship runs from June through August, Monday through Friday.',
        'Comfort working with APIs, scripting, and connecting systems together.',
        'Must be enrolled in a doctoral degree.',
    ]
    locations = ['New York, NY', 'Remote, United States', 'Toronto, Canada', 'London, UK',
                 'Seattle, WA', 'Remote', 'Austin, Texas', 'Boston, MA | Toronto, Canada']
    domains = ['payments', 'robotics', 'distributed systems', 'data platforms', 'developer tools', 'security']
    for i in range(count):
        required = '\n'.join(clauses[(i * 7 + j) % len(clauses)] for j in range(1 + i % 7))
        description = (f'Engineering internship on the {domains[i % len(domains)]} team, requisition {i}.\n'
                       'Responsibilities\nBuild reliable tools with engineers, test changes, and document results.\n'
                       f'Required qualifications\n{required}\n'
                       'Preferred qualifications\nExperience with Python or Go preferred.\n'
                       'Compensation and benefits\nCompetitive hourly pay and mentoring.\n'
                       'Equal opportunity employer. Upload your resume to apply.')
        yield dict(id=i + 1, title='PhD Research Intern' if i % 37 == 0 else 'Software Engineering Intern Summer 2027',
                   description=description, location=locations[i % len(locations)], company=f'Company {i}',
                   canonical_url=f'https://jobs.lever.co/fixture/{i}',
                   posted_at=(reference - timedelta(days=i % 35)).isoformat() if i % 31 else None)


def clear_text_caches():
    from autoapply import candidate_policy, jobs, requirements, standing
    for module in (candidate_policy, jobs, requirements, standing):
        for value in vars(module).values():
            if callable(getattr(value, 'cache_clear', None)):
                value.cache_clear()


def uncached_policy(patch):
    """Original pure parsing/location path; no cached eligibility conclusions."""
    import re
    import unicodedata
    from autoapply import candidate_policy, jobs, requirements
    def normalize(value):
        return re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', str(value)).casefold()).strip()
    def location(value):
        text = normalize(value)
        foreign = re.search(r'\b(canada|toronto|vancouver|montreal|ontario|london|united kingdom|uk|india|bangalore|bengaluru|singapore|germany|berlin|france|paris|australia|sydney|china|beijing|japan|tokyo|ireland|dublin|mexico|switzerland|poland|netherlands|israel|brazil)\b', text)
        explicit = bool(re.search(r'\b(united states|usa|u s|us)\b', text))
        state = bool(set(re.findall(r'\b[A-Z]{2}\b', value)) & jobs.US_STATES)
        domestic = explicit or state or any(re.search(r'\b' + re.escape(s) + r'\b', text) for s in jobs.US_NAMES) or text in jobs.US_CITIES
        return (None if domestic else False) if foreign else True if domestic else None
    patch.setattr(jobs, 'normalize', normalize)
    patch.setattr(candidate_policy, 'normalize', normalize)
    patch.setattr(jobs, 'us_location', location)
    patch.setattr(candidate_policy, 'us_location', location)
    for name in ('heading_kind', 'administrative', 'duration_requirement', 'explicit_gate', '_extract_requirements'):
        patch.setattr(requirements, name, getattr(requirements, name).__wrapped__)
    patch.setattr(candidate_policy, '_required_clauses', candidate_policy._required_clauses.__wrapped__)


def test_cold_720_candidates_equal_uncached_policy(config, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from time import perf_counter
    from autoapply.profile_snapshot import ProfileSnapshot
    from autoapply.standing import MEANINGS
    reference = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    candidates = list(heterogeneous_candidates(reference))
    profile = dict(config.profile, eligibility_assertions=[dict(id=key, meaning=value, source='USER_PROVIDED',
                      scope='standing', answer=True) for key, value in MEANINGS.items()])
    snapshots = [ProfileSnapshot.create(profile),
                 ProfileSnapshot.create(dict(profile, education=dict(profile['education'], degree='Masters', gpa=2.7),
                                             citizenship=dict(us_citizen=False))),
                 ProfileSnapshot.create(profile).with_overrides({
                     'verified_fact:work_authorization.us_authorized': dict(value=False, source='USER_PROVIDED'),
                     'verified_fact:work_authorization.sponsorship_now': dict(value=True, source='USER_PROVIDED')})]
    clear_text_caches()
    start = perf_counter()
    optimized = [evaluate(c, snapshots[0], reference=reference) for c in candidates]
    optimized_seconds = perf_counter() - start
    clear_text_caches()
    with monkeypatch.context() as patch:
        uncached_policy(patch)
        start = perf_counter()
        baseline = [evaluate(c, snapshots[0], reference=reference) for c in candidates]
        baseline_seconds = perf_counter() - start
    assert optimized == baseline
    # Facts/overrides, content, scope, protection, and date boundaries are always
    # evaluated anew even when every text cache is populated.
    for snapshot in snapshots:
        for boundary in (reference, reference + timedelta(days=31)):
            actual = [evaluate(c, snapshot, reference=boundary) for c in candidates]
            with monkeypatch.context() as patch:
                uncached_policy(patch)
                assert actual == [evaluate(c, snapshot, reference=boundary) for c in candidates]
    changed = dict(candidates[1], description='Required qualifications\nMust be a US citizen.')
    assert evaluate(changed, snapshots[1], reference=reference).reason == 'incompatible_citizenship'
    for options in (dict(retired=True), dict(conflict={'id': 99}), dict(controlled_target=999),
                    dict(mode='fill_only', historical_exclusions={changed['id']})):
        assert evaluate(changed, snapshots[0], reference=reference, **options).status == 'HOLD'
    print(f'T068 CPU cold 720: optimized={optimized_seconds:.4f}s uncached={baseline_seconds:.4f}s')
