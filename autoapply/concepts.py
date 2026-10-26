"""Canonical ordinary applicant concepts; special legal grammars remain scoped."""
import re
from dataclasses import dataclass
from .jobs import normalize

LABELS = {
    "first name": "identity.first_name", "given name": "identity.first_name",
    "last name": "identity.last_name", "family name": "identity.last_name",
    "full name": "identity.full_name", "name": "identity.full_name",
    "email": "contact.email", "email address": "contact.email",
    "phone": "contact.phone", "phone number": "contact.phone", "mobile phone": "contact.phone",
    "address": "contact.address", "street address": "contact.address",
    "city": "contact.city", "state": "contact.state", "state province": "contact.state", "zip code": "contact.postal_code",
    "postal code": "contact.postal_code", "country": "contact.country",
    "skills": "skills", "gender": "demographics.gender", "gender identity": "demographics.gender",
    "race": "demographics.race", "race ethnicity": "demographics.race",
    "disability status": "demographics.disability", "veteran status": "demographics.veteran_status",
    "school": "education.school", "university": "education.school", "college university": "education.school",
    "degree": "education.degree", "degree level": "education.degree",
    "major": "education.major", "discipline": "education.major",
    "gpa": "education.gpa", "cumulative gpa": "education.gpa",
    "expected graduation date": "education.graduation_date", "graduation date": "education.graduation_date",
    "linkedin": "links.linkedin", "linkedin profile": "links.linkedin", "linkedin url": "links.linkedin",
    "github": "links.github", "github url": "links.github",
    "git hub": "links.github", "git hub url": "links.github",
    "linked in": "links.linkedin", "linked in profile": "links.linkedin", "linked in url": "links.linkedin",
    "website": "links.personal_website", "personal website": "links.personal_website", "portfolio": "links.personal_website",
}


def question_text(label):
    return normalize(re.sub(r"\s*\((?:required|optional)\)\s*$", "", label, flags=re.I))


def concept(label):
    from .question_keywords import unusable_label
    if unusable_label(label):
        return None
    text = question_text(label)
    from .disclosures import employment_restriction
    if employment_restriction(label):
        return 'employment_restrictions'
    from .dropdowns import location_ordinal
    if location_ordinal(label):
        return 'locations.ordered_preferences'
    if re.search(r'\bperman(?:ent|ant) address\b', text):
        return 'contact.permanent_address'
    if text == 'i agree':
        return 'application_preferences.terms_acknowledgement'
    if re.fullmatch(r'if you would like us to view your linkedin profile provide your profile url i e https www linkedin com in yourname', text):
        return 'links.linkedin'
    if re.fullmatch(r'please confirm which internship cohort you prefer to join based on your school timeline', text):
        return 'application_preferences.internship_cohort'
    if re.fullmatch(r'if applying for a software development or mobile development stack role please select your role preferences preference does not determine final stack placement', text):
        return 'application_preferences.role_preferences'
    if re.fullmatch(r'are you proficient in any other languages below select all that apply', text):
        return 'languages.other_proficient'
    if re.fullmatch(r'what is the country of residence for the university you anticipate graduating from|(?:what is )?(?:the )?country (?:of|for) (?:your |my )?(?:current )?(?:university|school)(?: of residence)?', text):
        return 'education.country'
    text = re.sub(r'\bgit hub\b', 'github', re.sub(r'\blinked in\b', 'linkedin', text))
    link = re.fullmatch(r"(?:please )?(?:(?:include|provide|enter|share) (?:your )?|(?:what is )?your )?(linkedin|github|portfolio|personal website)(?: profile)?(?: url| link)?", text)
    if link:
        return {"linkedin":"links.linkedin", "github":"links.github", "portfolio":"links.personal_website", "personal website":"links.personal_website"}[link[1]]
    if text in LABELS:
        return LABELS[text]
    if text in {'u s work authorization', 'us work authorization'}:
        return 'work_authorization.us_authorized'
    from .question_keywords import export_status_label
    if export_status_label(label):
        return 'export_status'
    exact = {
        'are you currently enrolled': 'education.currently_enrolled',
        'are you currently a student': 'education.currently_enrolled',
        'are you enrolled in a degree program': 'education.currently_enrolled',
        'are you 18 years or older': 'qualifications.age_18_or_older',
        'are you at least 18': 'qualifications.age_18_or_older',
        'are you 18 years of age or older': 'qualifications.age_18_or_older',
        'are you in good academic standing': 'education.good_academic_standing',
        'sponsorship now': 'work_authorization.sponsorship_now',
        'sponsorship future': 'work_authorization.sponsorship_future',
        'do you require sponsorship now': 'work_authorization.sponsorship_now',
        'will you require sponsorship in the future': 'work_authorization.sponsorship_future',
        'location city': 'contact.city',
        'prior government employment': 'government_employment',
    }
    if text in exact:
        return exact[text]
    exact_saved_concepts = {
        'are you legally work authorized to work in the us': 'work_authorization.us_authorized',
        'will you now or in the future require visa sponsorship in order to work in the us': 'sponsorship_combined',
        'what is your gender identity': 'demographics.gender_identity',
        'what is your race or ethnicity': 'demographics.racial_ethnic_background',
        'what is your disability status': 'demographics.disability',
    }
    if text in exact_saved_concepts:
        return exact_saved_concepts[text]
    # Full-clause variants of the combined now/future sponsorship question.
    if re.fullmatch(r'will you now or in the future require (?:company sponsorship for a visa or work authorization to work in the country where this role is based|visa sponsorship in order to work in the us|([a-z0-9 ]+) to sponsor an employment visa or other employer sponsored immigration benefit in order for you to begin or continue working for \1)', text):
        return 'sponsorship_combined'
    if text in {"how did you hear about us", "how did you first hear about us", "how did you first learn about us",
                "how did you hear about this job", "how did you hear about this position", "how did you find this job"}:
        return "application_preferences.discovery_source"
    if not re.search(r"\b(not|unable|cannot|and|or|relocat\w*|expense|visa|sponsor\w*)\b", text):
        if re.fullmatch(r"(?:are you (?:able|willing) to|can you) (?:come into|attend|work (?:in|from)) (?:the |our )?(?:[a-z]+ ){0,5}office (?:5|five) days (?:a|per) week", text):
            return "application_preferences.office_five_days"
        if re.fullmatch(r"are you willing to work (?:in|at|from) any (?:office|location)", text):
            return "application_preferences.willing_to_work_any_location"
    # Deliberately narrow equivalences. Negation or additional clauses never use fuzzy similarity.
    patterns = [
        (r"(?:are you|are you currently|will you be) (?:legally )?authorized to work in (?:the )?(?:united states|u s|us)(?: of america)?", "work_authorization.us_authorized"),
        (r"(?:are you|are you a) (?:u s|us|united states) citizen", "citizenship.us_citizen"),
        (r"(?:are you|have you been) (?:a )?(?:u s|us|united states) citizen since birth", "citizenship.citizen_since_birth"),
        (r"(?:will you|do you|do you anticipate) (?:now or in the future )?(?:require|requiring|need) (?:employer |visa |employment |immigration )*sponsorship(?: now or in the future)?(?: for employment(?: visa status)?)?(?: e g h 1b visa status)?(?: to (?:be employed|work) (?:at|by|with|for) (?:[a-z0-9]+ ){0,5}[a-z0-9]+)?", "sponsorship_combined"),
        (r"(?:are you|are you currently) (?:enrolled in|pursuing) a bachelor s degree", "education.currently_enrolled"),
        (r"are you willing to relocate", "locations.willing_to_relocate"),
    ]
    for pattern, path in patterns:
        if re.fullmatch(pattern, text):
            return path
    from .question_keywords import match
    return match(label)


@dataclass(frozen=True)
class Concept:
    key: str
    aliases: tuple[str, ...]
    profile_path: str | None
    category: str
    scopes: tuple[str, ...] = ('global', 'application', 'employer')
    answer_type: str = 'text'
    factual_fallback: bool = True
    special_policy: bool = False


ALIASES = {'identity.first_name': 'contact.first_name', 'identity.last_name': 'contact.last_name',
           'identity.full_name': 'contact.full_name', 'links.personal_website': 'links.portfolio',
           'demographics.gender_identity': 'demographics.gender',
           'demographics.racial_ethnic_background': 'demographics.race'}
PROFILE_PATHS = {v: k for k, v in ALIASES.items() if not k.startswith('demographics.')}
DEMOGRAPHIC_PATHS = {
    'demographics.hispanic_latino': ('demographics.hispanic_latino', 'application_preferences.race'),
    'demographics.gender': ('demographics.gender', 'demographics.gender_identity', 'application_preferences.gender'),
    'demographics.race': ('demographics.race', 'demographics.racial_ethnic_background', 'application_preferences.race'),
    'demographics.disability': ('demographics.disability', 'application_preferences.disability'),
    'demographics.veteran_status': ('demographics.veteran_status', 'application_preferences.veteran'),
}


def demographic_values(profile, path):
    from .config import fact
    return [dict(path=p, value=fact(profile, p)) for p in DEMOGRAPHIC_PATHS.get(path, (path,))
            if fact(profile, p) not in (None, '')]


def equivalent_answer(value):
    """Comparison only: decline phrasing and Yes/No casing carry equal meaning."""
    if type(value) is bool:
        return 'yes' if value else 'no'
    if not isinstance(value, str):
        return value
    # Only a known regional suffix is incidental. Arbitrary parenthetical
    # qualifications can change the meaning of an apparent decline.
    comparison = re.sub(r'\s*\((?:United States(?: of America)?|U\.?S\.?A?\.?)\)\s*$', '', value, flags=re.I)
    text = re.sub(r'[^a-z0-9]+', ' ', comparison.casefold()).strip()
    if re.fullmatch(r'not declared|(?:i )?decline(?: to (?:self identify|answer|disclose))?|(?:i )?(?:do not|don t) wish to (?:answer|self identify|disclose|share)|(?:i )?(?:prefer|choose) not to (?:say|answer|disclose|self identify)', text):
        return 'decline'
    return text if text in {'yes', 'no'} else value


def demographic_value(profile, path):
    """Legacy fallback is read-only; unresolved conflicting facts require a choice."""
    values = demographic_values(profile, path)
    source = (profile.get('fact_sources') or {}).get(path, {})
    if source.get('basis') == 'Dashboard edit' and values and values[0]['path'] == path:
        return equivalent_answer(values[0]['value'])
    if path == 'demographics.veteran_status' and values:
        negative = {re.sub(r'^i am ', '', str(v['value']).strip().casefold()) for v in values}
        # A plain non-veteran preference is compatible with an older protected-status denial.
        if negative <= {'not a veteran', 'not a protected veteran'}:
            return 'not a veteran' if 'not a veteran' in negative else 'not a protected veteran'
    if len({str(equivalent_answer(v['value'])).strip().casefold() for v in values}) > 1:
        return None
    return equivalent_answer(values[0]['value']) if values else None
REGISTRY = {}
for path in set(LABELS.values()) | {
    'work_authorization.us_authorized', 'work_authorization.sponsorship_now',
    'work_authorization.sponsorship_future', 'citizenship.us_citizen',
    'citizenship.citizen_since_birth', 'education.currently_enrolled',
    'education.good_academic_standing', 'education.credit_hours_by_start',
    'contact.phone_device_type', 'contact.permanent_address', 'qualifications.age_18_or_older',
    'education.country', 'locations.ordered_preferences', 'application_preferences.role_preferences',
    'application_preferences.internship_cohort',
    'application_preferences.discovery_source', 'application_preferences.office_five_days',
    'application_preferences.willing_to_work_any_location', 'locations.willing_to_relocate',
    'demographics.gender', 'demographics.gender_identity', 'demographics.race',
    'demographics.racial_ethnic_background', 'demographics.disability',
    'demographics.veteran_status', 'demographics.armed_forces', 'demographics.pronouns',
    'demographics.sexual_orientation', 'demographics.transgender', 'demographics.hispanic_latino',
}:
    key = ALIASES.get(path, path)
    boolean = path.startswith(('citizenship.', 'work_authorization.')) or path in {
        'education.currently_enrolled', 'education.good_academic_standing',
        'qualifications.age_18_or_older', 'locations.willing_to_relocate',
        'application_preferences.office_five_days', 'application_preferences.willing_to_work_any_location'}
    REGISTRY[key] = Concept(key, tuple(label for label, p in LABELS.items() if p == path),
                            key if path.startswith('demographics.') else path,
                            path.split('.')[0], answer_type='boolean' if boolean else 'text')
for key in ('sponsorship_combined', 'prior_employment', 'conflict_of_interest',
            'government_connections', 'privacy_ack', 'sms_recruiting', 'technical_interests',
            'start_date', 'government_employment', 'independent_auditor_employment',
            'military_service', 'required_demographic_processing_consent',
            'employment_restrictions', 'languages.other_proficient', 'export_status'):
    REGISTRY[key] = Concept(key, (), None, 'policy', scopes=('application', 'employer'),
                            factual_fallback=False, special_policy=True)

# Explicit answers for ordinary application questions.
for key in ('desired_start_date', 'availability_dates', 'default_salary_range',
            'relatives_at_company', 'family_conflict_of_interest',
            'schedule_commitment', 'terms_acknowledgement'):
    path = 'application_preferences.' + key
    REGISTRY[path] = Concept(path, (), path, 'application_preferences',
                            scopes=('application', 'employer'), special_policy=True,
                            answer_type='boolean' if key in {'relatives_at_company', 'family_conflict_of_interest',
                                                            'schedule_commitment', 'terms_acknowledgement'} else 'text')
REGISTRY['start_date'] = Concept('start_date', (), 'application_preferences.desired_start_date',
                                 'application_preferences', scopes=('application', 'employer'), special_policy=True)

# A configured standing preference may apply broadly, but an answer to one
# employer's office/source question must not manufacture that standing preference.
for key in ('application_preferences.discovery_source', 'application_preferences.office_five_days',
            'application_preferences.willing_to_work_any_location'):
    spec = REGISTRY[key]
    REGISTRY[key] = Concept(spec.key, spec.aliases, spec.profile_path, spec.category,
                            scopes=('application', 'employer'), answer_type=spec.answer_type,
                            special_policy=True)


def canonical_key(label, employer=''):
    from .question_keywords import unusable_label
    if unusable_label(label):
        return None
    path = concept(label)
    if path:
        return ALIASES.get(path, path)
    from .disclosures import disclosure_intent
    intent = disclosure_intent(label, employer)
    if intent:
        return intent
    from .question_keywords import match
    return {'privacy acknowledgement': 'privacy_ack', 'sms recruiting preference': 'sms_recruiting',
            'technical interests': 'technical_interests',
            'technical interest preference': 'technical_interests'}.get(question_text(label)) or match(label)

