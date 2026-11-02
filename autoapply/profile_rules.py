"""Answer common factual questions directly from the canonical profile.

Wordings vary per employer ("Country of residence", "ZIP code of your primary
residence", "End date month"); each rule maps a label pattern to a profile
fact and then fits the value to the live options. Only facts the profile
holds are used; nothing here guesses a value the profile lacks.
"""
import calendar
import re
from datetime import date

SOURCE = 'profile_rule'

STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California', 'CO': 'Colorado',
    'CT': 'Connecticut', 'DE': 'Delaware', 'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas',
    'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts',
    'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana',
    'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico',
    'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma',
    'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota',
    'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
}
US = {'united states', 'united states of america', 'usa', 'us', 'u s', 'u s a', 'america'}


def _norm(text):
    return re.sub(r'[^a-z0-9+]+', ' ', str(text or '').lower()).strip()


def _get(profile, path):
    node = profile
    for part in path.split('.'):
        if not hasattr(node, 'get'):
            return None
        node = node.get(part)
    return node


def completed_education(label):
    text = _norm(label)
    return bool(re.search(r'\b(?:degree|qualifications?|level of education)\b', text)
                and re.search(r'\b(?:highest|achieved|completed|awarded|earned)\b', text))


def preference_boolean(label):
    """Return an explicit preference path and question polarity, never a default."""
    text = _norm(label)
    if (re.search(r'\bprivacy (?:policy|notice)\b', text)
            and re.search(r'\bread\b', text) and re.search(r'\bunderstand\b', text)
            and not re.search(r'\b(?:not|disagree|waive|release|certify|accurate)\b', text)):
        return 'application_preferences.ordinary_policy_acknowledgement', False
    if re.search(r'\b(?:sms|text messages?|whatsapp)\b', text):
        # A future right to withdraw is boilerplate, not the requested action.
        text = re.sub(r'\b(?:i|you) (?:can|may) opt out at any time\b', '', text)
        if re.search(r'\b(?:consent|receive|contact|opt|agree|subscribe)\b', text):
            remaining, count = re.subn(
                r'\b(?:opt out|unsubscribe|(?:do not|don t) (?:contact|receive)|'
                r'agree not to receive|decline(?: to receive)?)\b', '', text)
            # Mixed requests and unsupported negation need the user's interpretation.
            if (count and re.search(r'\b(?:receive|contact|reach|subscribe|opt in)\b', remaining)
                    or re.search(r'\b(?:not|never|no|cannot)\b', remaining)):
                return 'application_preferences.recruiting_sms_whatsapp', None
            negative = bool(count)
            return 'application_preferences.recruiting_sms_whatsapp', negative
        return 'application_preferences.recruiting_sms_whatsapp', None
    return None, None


def primary_graduation_matches(label, profile):
    """A primary program date cannot establish another qualification's date."""
    text = _norm(label)
    if not re.search(r'\bgraduat\w*\b', text):
        return True
    if re.search(r'\b(?:previous|prior|past|other|another)\b', text):
        return False
    primary = _norm(str(_get(profile, 'education.degree') or '') + ' ' +
                    str(_get(profile, 'education.school') or ''))
    for qualification in (r'high school|secondary school', r'bachelor\w*|undergraduate',
                          r'master\w*', r'doctor\w*|phd|ph d', r'associate\w*'):
        if re.search(r'\b(?:' + qualification + r')\b', text) and not re.search(
                r'\b(?:' + qualification + r')\b', primary):
            return False
    return True


def fit(value, options):
    """Fit a profile value to one live option, tolerant of phone codes and abbreviations."""
    if not options:
        return value
    want = _norm(value)
    norm = {o: _norm(o) for o in options}
    exact = [o for o, n in norm.items() if n == want]
    if len(exact) == 1:
        return exact[0]
    if want in US:
        us = [o for o, n in norm.items() if re.sub(r'\s*\+\s*1$', '', n).strip() in US]
        if len(us) == 1:
            return us[0]
    # "United States +1", "Virginia (VA)": option begins with the value as whole words.
    prefix = [o for o, n in norm.items() if re.match(re.escape(want) + r'\b', n)]
    if len(prefix) == 1:
        return prefix[0]
    abbrev = {k.lower(): v for k, v in STATES.items()}
    full = {v.lower(): k for k, v in STATES.items()}
    alt = abbrev.get(want) or full.get(want)
    if alt:
        alt = _norm(alt)
        hits = [o for o, n in norm.items() if n == alt or re.match(re.escape(alt) + r'\b', n)]
        if len(hits) == 1:
            return hits[0]
    return None


def _yes_no(flag, options):
    if flag is None:
        return None
    return fit('Yes' if flag else 'No', options or ['Yes', 'No'])


def parse_profile_date(value):
    """Parse YYYY, YYYY-MM or YYYY-MM-DD, defaulting missing parts to 01."""
    match = re.fullmatch(r'(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?', str(value or ''))
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2] or '01'), int(match[3] or '01'))
    except ValueError:
        return None


def _graduation(profile):
    parsed = parse_profile_date(_get(profile, 'education.graduation_date'))
    return (parsed.year, parsed.month) if parsed else (None, None)


def _declines(profile, key):
    from .concepts import demographic_value
    value = demographic_value(profile, 'demographics.' + ('veteran_status' if key == 'veteran' else key))
    from .concepts import demographic_values
    if value is None and demographic_values(profile, 'demographics.' + ('veteran_status' if key == 'veteran' else key)):
        return False
    return value is None or bool(re.search(r'decline|prefer not|not wish|don ?t wish', str(value), re.I))


def _decline(options):
    hits = [o for o in options or [] if re.search(r'decline|prefer not|not wish|don.?t wish|choose not', o, re.I)]
    return hits[0] if len(hits) == 1 else None


def _veteran(options, profile):
    from .concepts import demographic_value
    value = demographic_value(profile, 'demographics.veteran_status')
    if value and re.search(r'not a (?:protected )?veteran', str(value), re.I):
        from .answers import fit_options
        return fit_options(value, options or [])
    return _decline(options) if _declines(profile, 'veteran') else None


def _career_plans(profile):
    goal = str(_get(profile, 'application_preferences.career_goal') or '').strip().rstrip('.')
    if not goal:
        return None
    major, school = _get(profile, 'education.major'), _get(profile, 'education.school')
    study = f' as a {major} student at {school}' if major and school else ''
    return (f'My plan is to {goal[0].lower() + goal[1:]}. In an internship I want to apply what I have learned{study} '
            'to real production software, learn how experienced teams design, test and ship systems, and earn the '
            'trust to take on larger pieces of work. After converting the internship into a full-time role, I intend '
            'to keep deepening my technical skills and ownership over time, with the long-term aim of growing into a '
            'senior software engineer who can lead projects and mentor others.')


def _skill_level(text, options, profile):
    if not options:
        return None
    skills = ' '.join(str(s) for s in (_get(profile, 'skills') or ())).lower()
    subject = re.search(r'proficiency (?:with|in) (.+?)(?: software)?$', text)
    known = bool(subject and subject[1].strip() and subject[1].strip() in skills)
    ranked = [o for o in options if not re.match(r'\s*(?:none|no experience|n/?a)\b', o, re.I)]
    if not ranked:
        return None
    middle = [o for o in ranked if re.search(r'intermediate|proficient|moderate|competent', o, re.I)]
    if known and middle:
        return middle[0]
    return ranked[0]


def resolve(label, kind, options, profile):
    """Return a value fitted to the options, or None when no rule applies."""
    text = _norm(label)
    if not text or not primary_graduation_matches(label, profile):
        return None
    if completed_education(label):
        value = _get(profile, 'education.highest_completed_degree')
        return fit(value, options) if value else None
    from .concepts import canonical_key
    from .disclosures import employment_restriction
    if employment_restriction(label):
        return None
    key = canonical_key(label)
    if key == 'contact.permanent_address':
        from .answers import permanent_address
        return permanent_address(profile) if not options else None
    if key == 'education.country':
        value = _get(profile, 'education.country')
        return fit(value, options) if value else None
    # Country facts concern this applicant, never a job destination or another person.
    if re.search(r'\bcountry\b', text) and not re.fullmatch(
            r'(?:what is )?(?:your |my )?(?:current )?country(?: of (?:residence|residency))?|country region', text):
        return None
    year, month = _graduation(profile)
    # Conditional follow-ups ("If yes, please describe ...") when the trigger answer is No.
    if re.match(r'^if (?:yes|so|applicable)\b', text) and kind in {'text', 'textarea'}:
        return 'N/A'
    work = lambda key: _get(profile, 'work_authorization.' + key)
    ack = _get(profile, 'application_preferences.ordinary_policy_acknowledgement')
    rules = [
        # Acknowledgement statements / privacy consent (single "I acknowledge" option or agree box).
        (r'\b(?:i (?:acknowledge|agree|understand|certify)|by (?:applying|submitting|checking)|privacy (?:policy|notice)|application statement)\b',
         lambda: (next((o for o in options if re.search(r'\b(?:acknowledge|agree|accept|understand|yes)\b', o, re.I)), None)
                  if options and kind != 'checkbox' else 'Yes') if ack else None),
        # Work authorization phrasings not covered by exact concepts.
        (r'\bsponsorship\b|\bsponsor\b.{0,40}\b(?:visa|immigration|work authori[sz]ation|employment)\b', lambda: None if work('sponsorship_now') is None or work('sponsorship_future') is None
         else ('Yes' if work('sponsorship_now') or work('sponsorship_future') else 'No')),
        (r'\b(?:government|united nations) employee\b|\bgovernment official\b', lambda: 'No'),
        (r'\b(?:text messages?|sms|whatsapp)\b', lambda: None if _get(profile, 'application_preferences.recruiting_sms_whatsapp') is None
         else ('Yes' if _get(profile, 'application_preferences.recruiting_sms_whatsapp') else 'No')),
        (r'\b(?:completed|submitted) an application\b.{0,60}\bwithin\b|\bapplied (?:to|for|with) .{0,40}(?:before|previously|in the (?:last|past))',
         lambda: 'Yes' if _get(profile, 'application_preferences.previous_employer_work_default') else 'No'),
        # Compensation.
        (r'\b(?:hourly rate|pay rate|salary|compensation) (?:requirement|expectation)s?\b|\bdesired (?:pay|salary|compensation)\b',
         lambda: _get(profile, 'application_preferences.compensation_text')),
        # Address / location.
        (r'^(?:street )?address(?: line)? ?1?\b|^street address\b', lambda: _get(profile, 'contact.address')),
        (r'^current location\b|^location\b', lambda: ', '.join(v for v in (_get(profile, 'contact.city'), _get(profile, 'contact.state')) if v) or None),
        # Education.
        (r'\b(?:projected|expected|anticipated) graduation date\b|^graduation date\b',
         lambda: f'{calendar.month_name[month]} {year}' if year and month else None),
        (r'\blevel of education\b|\bclass standing\b|\bcurrent (?:year|class) in school\b|\bacademic year\b',
         lambda: _get(profile, 'education.school_year')),
        # Skill proficiency ratings: middle level for profile skills, lowest non-zero otherwise.
        (r'\b(?:rate|level of) (?:your )?(?:level of )?proficiency\b|\bproficiency (?:level|with|in)\b',
         lambda: _skill_level(text, options, profile)),
        # Optional demographics follow the profile's decline preference.
        (r'\bhispanic\b|\blatino\b', lambda: _decline(options) if _declines(profile, 'race') else None),
        (r'\bveteran status\b|\bprotected veteran\b', lambda: _veteran(options, profile)),
        # "What led you to apply / how did you hear about us": the profile's chosen source (Other).
        (r'\bwhat led you to apply\b|\bhow did you (?:hear|learn|find out)\b|\b(?:referral|application|candidate|job) source\b|\bwhere did you (?:hear|find|see)\b',
         lambda: _get(profile, 'application_preferences.application_source_answer')),
        # Career plans / goals: composed from the stated goal and education facts.
        (r'\bcareer (?:plans?|goals?|aspirations?)\b|\bwhere do you see yourself\b|\blong[ -]term (?:career )?goals?\b|\bprofessional goals?\b',
         lambda: _career_plans(profile) if kind in {'text', 'textarea'} else None),
        # Location of residence.
        (r'\b(?:zip|postal) code\b', lambda: _get(profile, 'contact.postal_code')),
        (r'\bcountry\b(?! code)', lambda: _get(profile, 'contact.country')),
        (r'\b(?:state|province)\b|primary residence must be located in the united states', lambda: _get(profile, 'contact.state')),
        (r'^(?:current )?city\b|\bcity of residence\b', lambda: _get(profile, 'contact.city')),
        # Education dates.
        (r'\b(?:end|graduation|expected graduation|grad) (?:date )?month\b', lambda: calendar.month_name[month] if month else None),
        (r'\b(?:end|graduation|expected graduation|grad) (?:date )?year\b', lambda: str(year) if year else None),
        # Employment history / outside interests.
        (r'\b(?:previously|ever|formerly) (?:worked|been employed)|\bformer employee\b|\bworked (?:for|at) .{0,40} before\b',
         lambda: 'Yes' if _get(profile, 'application_preferences.previous_employer_work_default') else 'No'),
        (r'\b(?:own|operate|provide services to) .{0,40}\b(?:business|organization)\b', lambda: 'No'),
        (r'\bwilling to relocate\b', lambda: 'Yes' if (_get(profile, 'locations.willing_to_relocate')
                                                        or _get(profile, 'application_preferences.willing_to_work_any_location')) else None),
    ]
    for pattern, getter in rules:
        if re.search(pattern, text):
            value = getter()
            if value in (None, ''):
                return None
            return fit(value, options) if options else str(value)
    return None


def confirm_requirement(requirement, profile):
    """Answer a job-requirement confirmation from profile facts: True/False/None."""
    text = _norm(requirement)
    from .question_keywords import match
    if match(requirement) == 'work_authorization.us_authorized':
        return _get(profile, 'work_authorization.us_authorized')
    if re.search(r'\bsponsor', text):
        now, future = _get(profile, 'work_authorization.sponsorship_now'), _get(profile, 'work_authorization.sponsorship_future')
        if now is None or future is None:
            return None
        needs = now or future
        # "Must not require sponsorship" is met when no sponsorship is needed.
        return not needs if re.search(r'\b(?:not|without|no)\b', text) else needs
    if re.search(r'\b(?:u s|us|united states) citizen', text):
        return _get(profile, 'citizenship.us_citizen')
    if re.search(r'\b(?:enroll\w*|pursuing|current(?:ly)? (?:a )?student)\b.{0,60}\b(?:bachelor|undergraduate|graduate|degree|university|college|program)', text):
        return _get(profile, 'education.currently_enrolled')
    if re.search(r'\bgood (?:academic )?standing\b', text):
        return _get(profile, 'education.good_academic_standing')
    return None
