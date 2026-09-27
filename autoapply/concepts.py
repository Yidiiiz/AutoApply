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
    "city": "contact.city", "state": "contact.state", "zip code": "contact.postal_code",
    "postal code": "contact.postal_code", "country": "contact.country",
    "school": "education.school", "university": "education.school", "college university": "education.school",
    "degree": "education.degree", "degree level": "education.degree",
    "major": "education.major", "discipline": "education.major",
    "gpa": "education.gpa", "cumulative gpa": "education.gpa",
    "expected graduation date": "education.graduation_date", "graduation date": "education.graduation_date",
    "linkedin": "links.linkedin", "linkedin profile": "links.linkedin", "linkedin url": "links.linkedin",
    "github": "links.github", "github url": "links.github",
    "website": "links.personal_website", "personal website": "links.personal_website", "portfolio": "links.personal_website",
}


def question_text(label):
    return normalize(re.sub(r"\s*\((?:required|optional)\)\s*$", "", label, flags=re.I))


def concept(label):
    text = question_text(label)
    link = re.fullmatch(r"(?:please )?(?:(?:include|provide|enter|share) (?:your )?|(?:what is )?your )?(linkedin|github|portfolio|personal website)(?: profile)?(?: url| link)?", text)
    if link:
        return {"linkedin":"links.linkedin", "github":"links.github", "portfolio":"links.personal_website", "personal website":"links.personal_website"}[link[1]]
    if text in LABELS:
        return LABELS[text]
    exact = {
        'sponsorship now': 'work_authorization.sponsorship_now',
        'sponsorship future': 'work_authorization.sponsorship_future',
        'do you require sponsorship now': 'work_authorization.sponsorship_now',
        'will you require sponsorship in the future': 'work_authorization.sponsorship_future',
        'location city': 'contact.city',
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
    if text in {"how did you hear about us", "how did you first hear about us", "how did you first learn about us",
                "how did you hear about this job", "how did you hear about this position", "how did you find this job"}:
        return "application_preferences.discovery_source"
    if not re.search(r"\b(not|unable|cannot|and|or|relocat\w*|expense|visa|sponsor\w*)\b", text):
        if re.fullmatch(r"(?:are you (?:able|willing) to|can you) (?:come into|attend|work (?:in|from)) (?:the |our )?(?:[a-z]+ ){0,5}office (?:5|five) days (?:a|per) week", text):
            return "application_preferences.office_five_days"
        if re.fullmatch(r"are you willing to work (?:(?:[1-7]|one|two|three|four|five|six|seven) days (?:a|per) week )?(?:in|at|from) (?:the |our )?(?:[a-z]+ ){0,6}(?:office|location)", text):
            return "application_preferences.willing_to_work_any_location"
    # Deliberately narrow equivalences. Negation or additional clauses never use fuzzy similarity.
    patterns = [
        (r"(?:are you|are you currently|will you be) (?:legally )?authorized to work in (?:the )?(?:united states|u s|us)(?: of america)?", "work_authorization.us_authorized"),
        (r"(?:are you|are you a) (?:u s|us|united states) citizen", "citizenship.us_citizen"),
        (r"(?:are you|have you been) (?:a )?(?:u s|us|united states) citizen since birth", "citizenship.citizen_since_birth"),
        (r"(?:will you|do you|do you anticipate) (?:now or in the future )?(?:require|requiring|need) (?:employer |visa |employment |immigration )*sponsorship(?: now or in the future)?(?: for employment(?: visa status)?)?(?: e g h 1b visa status)?", "sponsorship_combined"),
        (r"(?:are you|are you currently) (?:enrolled in|pursuing) a bachelor s degree", "education.currently_enrolled"),
        (r"are you willing to relocate", "locations.willing_to_relocate"),
    ]
    for pattern, path in patterns:
        if re.fullmatch(pattern, text):
            return path
    return None


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
           'identity.full_name': 'contact.full_name', 'links.personal_website': 'links.portfolio'}
PROFILE_PATHS = {v: k for k, v in ALIASES.items()}
REGISTRY = {}
for path in set(LABELS.values()) | {
    'work_authorization.us_authorized', 'work_authorization.sponsorship_now',
    'work_authorization.sponsorship_future', 'citizenship.us_citizen',
    'citizenship.citizen_since_birth', 'education.currently_enrolled',
    'application_preferences.discovery_source', 'application_preferences.office_five_days',
    'application_preferences.willing_to_work_any_location', 'locations.willing_to_relocate',
    'demographics.gender', 'demographics.gender_identity', 'demographics.race',
    'demographics.racial_ethnic_background', 'demographics.disability',
    'demographics.veteran_status', 'demographics.armed_forces',
    'demographics.sexual_orientation', 'demographics.transgender', 'demographics.hispanic_latino',
}:
    key = ALIASES.get(path, path)
    boolean = path.startswith(('citizenship.', 'work_authorization.')) or path in {
        'education.currently_enrolled', 'locations.willing_to_relocate',
        'application_preferences.office_five_days', 'application_preferences.willing_to_work_any_location'}
    REGISTRY[key] = Concept(key, tuple(label for label, p in LABELS.items() if p == path),
                            path, path.split('.')[0], answer_type='boolean' if boolean else 'text')
for key in ('sponsorship_combined', 'prior_employment', 'conflict_of_interest',
            'government_connections', 'privacy_ack', 'sms_recruiting', 'technical_interests',
            'start_date', 'military_service', 'required_demographic_processing_consent'):
    REGISTRY[key] = Concept(key, (), None, 'policy', scopes=('application', 'employer'),
                            factual_fallback=False, special_policy=True)

# A configured standing preference may apply broadly, but an answer to one
# employer's office/source question must not manufacture that standing preference.
for key in ('application_preferences.discovery_source', 'application_preferences.office_five_days',
            'application_preferences.willing_to_work_any_location'):
    spec = REGISTRY[key]
    REGISTRY[key] = Concept(spec.key, spec.aliases, spec.profile_path, spec.category,
                            scopes=('application', 'employer'), answer_type=spec.answer_type,
                            special_policy=True)


def canonical_key(label, employer=''):
    path = concept(label)
    if path:
        return ALIASES.get(path, path)
    from .disclosures import disclosure_intent
    intent = disclosure_intent(label, employer)
    if intent:
        return intent
    return {'privacy acknowledgement': 'privacy_ack', 'sms recruiting preference': 'sms_recruiting',
            'technical interests': 'technical_interests', 'technical interest preference': 'technical_interests',
            'start date': 'start_date'}.get(question_text(label))

