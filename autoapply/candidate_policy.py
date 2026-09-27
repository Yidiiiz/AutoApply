"""Shared data-only preflight. ALLOW never bypasses any live safety boundary."""
from dataclasses import dataclass
from enum import StrEnum
import re

from .config import fact
from .freshness import freshness_state
from .jobs import normalize, us_location


class CandidateStatus(StrEnum):
    ALLOW = 'ALLOW'
    REJECT = 'REJECT'
    HOLD = 'HOLD'
    NEEDS_ENRICHMENT = 'NEEDS_ENRICHMENT'


@dataclass(frozen=True)
class CandidateDecision:
    status: CandidateStatus
    reason: str
    evidence: tuple[str, ...] = ()
    profile_revision: str = ''

    @property
    def proceed(self):
        return self.status in {CandidateStatus.ALLOW, CandidateStatus.NEEDS_ENRICHMENT}


def required_clauses(description):
    """Preserve section context; no substring-based inference of hard requirements."""
    required = False
    preferred = False
    for line in re.split(r'[\n;]+', description):
        text = normalize(line)
        if text in {'preferred qualifications', 'preferred skills', 'preferred skills experience',
                    'preferred skills and experience', 'preferred experience', 'nice to have', 'bonus qualifications'}:
            preferred, required = True, False
            continue
        if text in {'requirements', 'minimum qualifications', 'required qualifications', 'basic qualifications'}:
            preferred, required = False, True
            continue
        if text in {'responsibilities', 'benefits', 'additional information', 'job description'}:
            preferred, required = False, False
            continue
        if preferred or re.search(r'\b(preferred|a plus|nice to have)\b', text):
            continue
        if required or re.match(r'(?:candidates |applicants )?(?:must|are required to)\b', text):
            yield line, re.sub(r'^(?:candidates |applicants )?(?:must |are required to )', '', text)
        elif re.fullmatch(r'(?:no (?:visa )?sponsorship|(?:we |the employer )?(?:cannot|can not|will not|does not) (?:provide (?:visa )?sponsorship|sponsor))', text):
            yield line, text


def evaluate(candidate, snapshot, *, max_days=30, include_international=False,
             retired=False, conflict=None, mode='ordinary', controlled_target=None,
             historical_exclusions=(), reference=None):
    """No writes, no browser, no AI, and no cached eligibility conclusions."""
    def decision(status, reason, *evidence):
        return CandidateDecision(CandidateStatus(status), reason, tuple(str(v) for v in evidence), snapshot.revision)
    app_id = candidate.get('id')
    if controlled_target is not None and app_id != controlled_target:
        return decision('HOLD', 'controlled_target_mismatch', controlled_target)
    if mode == 'fill_only' and app_id in historical_exclusions:
        return decision('HOLD', 'historical_exclusion', app_id)
    if retired:
        return decision('HOLD', 'permanently_retired', app_id)
    if (candidate.get('submit_intent_at') or candidate.get('submission_confirmation_seen')
            or candidate.get('status') in {'SUBMITTED', 'ALREADY_APPLIED', 'SUBMITTING'}):
        return decision('HOLD', 'protected_application', app_id)
    if conflict:
        return decision('HOLD', conflict.get('identity_match', 'exact_protected_duplicate'), conflict.get('id'))
    if candidate.get('listing_status') in {'CLOSED', 'REMOVED'} or candidate.get('closed') is True:
        return decision('REJECT', 'confirmed_closed', candidate.get('listing_status', 'closed'))
    fresh = freshness_state(candidate.get('posted_at'), max_days, reference)
    if fresh == 'STALE':
        return decision('REJECT', 'stale_listing', candidate.get('posted_at'))
    location = candidate.get('location') or ''
    # A city name alone, remote without a country, or mixed options is not foreign-only proof.
    foreign_country = r'\b(canada|united kingdom|uk|india|singapore|germany|france|australia|china|japan|ireland|mexico|switzerland|poland|netherlands|israel|brazil)\b'
    locations = re.split(r';|\||\bor\b', location)
    if (not include_international and locations and all(us_location(v) is False and
            re.search(foreign_country, normalize(v)) and 'remote' not in normalize(v) for v in locations)):
        return decision('REJECT', 'foreign_only_location', location)
    profile = snapshot.facts
    description = candidate.get('description') or ''
    unknown_requirement = False
    for original, clause in required_clauses(description):
        if re.fullmatch(r'be (?:legally )?authorized to work in (?:the )?(?:united states|u s|us)', clause):
            if fact(profile, 'work_authorization.us_authorized') is False:
                return decision('REJECT', 'incompatible_authorization', original, 'work_authorization.us_authorized')
            unknown_requirement |= fact(profile, 'work_authorization.us_authorized') is None
        if re.fullmatch(r'be (?:a )?(?:u s|us|united states) citizen', clause):
            if fact(profile, 'citizenship.us_citizen') is False:
                return decision('REJECT', 'incompatible_citizenship', original, 'citizenship.us_citizen')
            unknown_requirement |= fact(profile, 'citizenship.us_citizen') is None
        if re.fullmatch(r'(?:no (?:visa )?sponsorship|(?:we |the employer )?(?:cannot|can not|will not|does not) (?:provide (?:visa )?sponsorship|sponsor)|not (?:require|need) (?:visa )?sponsorship(?: now or in the future)?)', clause):
            if any(fact(profile, 'work_authorization.' + k) is True for k in ('sponsorship_now', 'sponsorship_future')):
                return decision('REJECT', 'incompatible_sponsorship', original)
            unknown_requirement |= any(fact(profile, 'work_authorization.' + k) is None for k in ('sponsorship_now', 'sponsorship_future'))
        degree = normalize(fact(profile, 'education.degree') or '')
        if (re.fullmatch(r'(?:have|hold|be pursuing|be enrolled in) (?:a |an )?(?:master s|masters|doctoral|phd|ph d|graduate) degree', clause)
                and re.fullmatch(r'bachelor s?|bachelors|undergraduate|bs|ba|b s|b a', degree)):
            return decision('REJECT', 'incompatible_degree', original, 'education.degree')
        graduation = str(fact(profile, 'education.graduation_date') or '')
        match = re.fullmatch(r'(?:graduate|be graduating|have a graduation date) (?:in (20\d{2})|between (20\d{2}) and (20\d{2}))', clause)
        if match and re.fullmatch(r'20\d{2}-\d{2}-\d{2}', graduation):
            years = [int(v) for v in match.groups() if v]
            if not min(years) <= int(graduation[:4]) <= max(years):
                return decision('REJECT', 'incompatible_graduation', original, 'education.graduation_date')
    # Even a long description is not proof that every eligibility dimension was captured.
    if unknown_requirement or fresh != 'FRESH' or us_location(location) is not True or not description.strip():
        return decision('NEEDS_ENRICHMENT', 'incomplete_listing_evidence')
    from .jobs import eligibility
    # Use the existing assessment only to establish positive completeness.
    # Its broader title/absence heuristics never authorize a preflight rejection.
    if eligibility(candidate, profile).eligible is not True:
        return decision('NEEDS_ENRICHMENT', 'eligibility_requires_enrichment')
    return decision('ALLOW', 'no_conclusive_conflict')


def preflight(db, config, candidate, *, snapshot=None, mode='ordinary', historical_exclusions=()):
    if snapshot is None:
        settings = {r['key']: __import__('json').loads(r['value']) for r in db.rows(
            "SELECT key,value FROM settings WHERE key LIKE 'verified_fact:%'")}
        snapshot = config.profile_snapshot().with_overrides(settings)
    return evaluate(candidate, snapshot, max_days=config['jobs']['max_listing_age_days'],
                    include_international=config['jobs'].get('include_international', False),
                    retired=db.automation_retired(candidate['id']), conflict=db.submission_conflict(candidate['id']),
                    controlled_target=db.setting('controlled_application_id'), mode=mode,
                    historical_exclusions=historical_exclusions)
