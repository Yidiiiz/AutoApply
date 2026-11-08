"""Primary label-only question concepts and conservative saved-question similarity.

Add a CONCEPTS row with a canonical key, alternative whole-word phrases (any),
required phrases (all), and unsafe phrases (exclude). Add its profile path in
concepts.REGISTRY and formatting in answers when needed. More specific matches
win; options never classify a question. Narrow tested concepts.py overrides win.
"""
import re


def normalize(text):
    text = re.sub(r"['’]s\b", '', str(text or '').lower())
    words = re.sub(r'[^a-z0-9]', ' ', text).split()
    def stem(word):
        if len(word) > 4 and word.endswith(('sses', 'shes', 'ches', 'xes', 'zes')):
            return word[:-2]
        if len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us')) and word not in {'this', 'was'}:
            return word[:-1]
        return word
    return ' '.join(stem(word) for word in words)


def unusable_label(label):
    """Control boilerplate and machine identifiers are never applicant questions."""
    raw = str(label or '').strip()
    if raw in {'GitHub', 'LinkedIn'}:
        return False  # Public profile-link brands, not camelCase identifiers.
    text = normalize(raw)
    boilerplate = set('select one required optional please choose a an option search type here value enter selection answer item'.split())
    if not text or text in {'application question', 'unlabelled field'} or set(text.split()) <= boilerplate:
        return True
    return bool(re.fullmatch(r'[\w.-]+', raw) and (
        re.search(r'[_\d]|[a-z][A-Z]', raw)
        or re.match(r'(?:formField|input|field|prompt|question)[.-]', raw, re.I)
        or '.' in raw))


def date_part_label(label):
    """Month/year controls describe a date component, never job availability."""
    text = normalize(label)
    return bool(re.fullmatch(r'month|day|year', text) or re.search(r'\b(?:start|end|from|to|graduation|availability|available)(?: date)? (?:month|day|year)\b'
                          r'|\bdate (?:month|day|year)\b'
                          r'|\b(?:month|day|year)(?: of)?(?: the)? (?:start|end|from|to|graduation)(?: date)?\b', text))


def export_status_label(label):
    """Captured status heading; never classify arbitrary export/legal agreements."""
    return normalize(label) == normalize(
        'EXPORT CONTROLS - This position requires access to information and technology '
        'that is subject to U.S. export controls. Your responses to the questions below '
        'will be used solely to determine your eligibility under U.S. law to receive '
        'information and materials subject to U.S. export controls.')


def contains(text, phrase):
    return (' ' + normalize(phrase) + ' ') in (' ' + text + ' ')


AUTH_EXCLUDE = ['sponsor', 'sponsorship', 'visa', 'citizen', 'citizenship',
                'temporary work authorization', 'f 1', 'opt', 'cpt', 'h 1b', 'tn', 'clearance',
                'not', 'unable', 'cannot', 'without']
CONCEPTS = [
    dict(key='work_authorization.us_authorized',
         any=['work authorization status', 'legally authorized to work', 'authorized to work',
              'work authorized', 'eligible to work', 'eligibility to work', 'legally able to work', 'right to work'],
         all=[], exclude=AUTH_EXCLUDE),
    dict(key='work_authorization.sponsorship_future', any=['sponsor', 'sponsorship'],
         all=['future'], exclude=[]),
    dict(key='work_authorization.sponsorship_now', any=['sponsor', 'sponsorship'],
         all=[], exclude=['future', 'hold']),
    dict(key='citizenship.us_citizen', any=['us citizen', 'u s citizen', 'united states citizen',
         'us citizenship', 'u s citizenship', 'united states citizenship'], all=[], exclude=['since birth']),
    dict(key='work_authorization.temporary_authorization', any=['temporary work authorization',
         'f 1', 'opt', 'cpt', 'h 1b', 'tn'], all=[], exclude=['sponsor', 'sponsorship']),
    dict(key='visa.status', any=['visa status', 'current visa', 'type of visa'], all=[], exclude=['sponsor', 'sponsorship']),
    dict(key='security.clearance', any=['security clearance', 'clearance'], all=[], exclude=[]),
    dict(key='locations.willing_to_relocate', any=['willing to relocate', 'relocation'], all=[], exclude=['not', 'expense']),
    dict(key='application_preferences.availability_dates', any=['dates and hours', 'availability dates'],
         all=[], exclude=[]),
    dict(key='application_preferences.default_salary_range', any=['salary', 'compensation', 'desired pay', 'hourly rate', 'pay rate'],
         all=[], exclude=['salary history', 'previous salary']),
    dict(key='application_preferences.relatives_at_company', any=['relatives', 'relative', 'family member'],
         all=['work'], exclude=['direct interest', 'conflict', 'not', 'no']),
    dict(key='application_preferences.family_conflict_of_interest', any=['competes', 'competitor', 'supplier', 'customer'],
         all=['family', 'direct interest'], exclude=['not', 'no']),
    dict(key='application_preferences.family_conflict_of_interest', any=['conflict of interest'],
         all=[], exclude=['not', 'no']),
    dict(key='application_preferences.schedule_commitment', any=['schedule', 'program', 'hours', 'available', 'work'],
         all=['commit'], exclude=[]),
    dict(key='application_preferences.schedule_commitment', any=['are you able', 'are you available', 'can you work', 'able to work'],
         all=['schedule'], exclude=[]),
    dict(key='application_preferences.schedule_commitment', any=['are you able', 'are you available'],
         all=['program', 'hours'], exclude=[]),
    dict(key='application_preferences.terms_acknowledgement', any=['agree', 'acknowledge', 'certify', 'consent'],
         all=['term', 'condition'], exclude=['signature', 'sign', 'name', 'not', 'no', 'cannot']),
    dict(key='application_preferences.terms_acknowledgement', any=['acknowledge', 'certify', 'agree'],
         all=['read', 'understand'], exclude=['signature', 'sign', 'name', 'not', 'no', 'cannot']),
    dict(key='identity.full_name', any=['signature', 'type your name', 'typed name', 'electronic signature'],
         all=[], exclude=[]),
    dict(key='demographics.hispanic_latino', any=['hispanic', 'latino', 'hispanic latino', 'hispanic or latino'], all=[], exclude=[]),
    dict(key='start_date', any=['desired start date', 'expected start date', 'earliest start date',
                                'available start date', 'when can you start', 'when are you available to start',
                                'date available to start'], all=[], exclude=['education', 'employment history']),
    dict(key='education.graduation_date', any=['graduation date', 'anticipated graduation', 'expected graduation',
         'projected graduation', 'when will you graduate', 'when are you expected to graduate'], all=[], exclude=['month', 'year']),
    dict(key='education.gpa', any=['gpa', 'grade point average'], all=[], exclude=[]),
    dict(key='education.school', any=['school', 'university', 'college'], all=[], exclude=['high school', 'degree', 'major', 'gpa', 'graduation', 'year']),
    dict(key='education.credit_hours_by_start', any=['credit hours', 'credits completed', 'completed credits'], all=[], exclude=[]),
    dict(key='education.credit_hours_by_start', any=['credits'], all=['completed'], exclude=[]),
    dict(key='contact.phone_device_type', any=['phone device type', 'phone type'], all=[], exclude=[]),
    dict(key='contact.permanent_address', any=['permanent address', 'permanant address'], all=[], exclude=[]),
    dict(key='government_employment', any=['federal government'], all=['employee'], exclude=[]),
    dict(key='independent_auditor_employment', any=['independent auditor'], all=['employee'], exclude=[]),
    dict(key='education.degree', any=['degree', 'degree level'], all=[], exclude=['enrolled', 'pursuing', 'major', 'graduation']),
    dict(key='education.major', any=['major', 'field of study', 'discipline'], all=[], exclude=[]),
    dict(key='application_preferences.discovery_source', any=['how did you hear', 'how did you learn', 'how did you find', 'where did you hear'], all=[], exclude=[]),
    dict(key='prior_employment', any=['previously work', 'previously worked', 'previously employed', 'ever worked', 'former employee', 'worked here before'], all=[], exclude=[]),
    dict(key='qualifications.age_18_or_older', any=['over 18', 'at least 18', '18 years or older', '18 years of age or older'], all=[], exclude=['under', 'not']),
    dict(key='links.linkedin', any=['linkedin'], all=[], exclude=[]),
    dict(key='links.github', any=['github'], all=[], exclude=[]),
    dict(key='links.portfolio', any=['website', 'portfolio'], all=[], exclude=[]),
    dict(key='demographics.pronouns', any=['pronouns'], all=[], exclude=[]),
    dict(key='demographics.gender', any=['gender', 'gender identity', 'sex'], all=[], exclude=[]),
    dict(key='demographics.race', any=['race', 'ethnicity', 'racial ethnic background'], all=[], exclude=[]),
    dict(key='demographics.veteran_status', any=['veteran'], all=['protected'], exclude=[]),
    dict(key='demographics.veteran_status', any=['veteran', 'veteran status'], all=[], exclude=[]),
    dict(key='demographics.disability', any=['disability', 'disability status'], all=[], exclude=[]),
]


def current_immigration_status(text):
    """The current-status request, distinct from an export licence or clearance claim."""
    text = normalize(text)
    return bool(re.search(r'\b(?:indicate|select) your current immigration and work authorization status\b', text)
                and not re.search(r'\b(?:hypothetical|if you|would you|clearance|licen[cs]e|since birth)\b', text))


def match(text):
    """Return the most specific concept; options are deliberately never inspected."""
    if unusable_label(text) or date_part_label(text):
        return None
    text = normalize(text)
    if text in {'u s work authorization', 'us work authorization'}:
        return 'work_authorization.us_authorized'
    if current_immigration_status(text):
        return 'citizenship.us_citizen'
    long = len(text.split()) > 60
    matches = []
    for item in CONCEPTS:
        if any(contains(text, p) for p in item['exclude']):
            continue
        required = item['all']
        # Long boilerplate needs explicit required phrases, not a lone keyword.
        if (long and not required) or not all(contains(text, p) for p in required):
            continue
        hits = [p for p in item['any'] if contains(text, p)]
        if hits:
            score = max(len(normalize(p).split()) for p in hits) + len(hits) / 10
            score += sum(len(p.split()) for p in required if contains(text, p))
            matches.append((score, item['key']))
    return max(matches, default=(0, None))[1]


STOPWORDS = set('a an the are is was were you your our their i me my do does did have has had '
                'what which how please provide enter select indicate to for in on at of with '
                'this that it us company position role job'.split())


def similarity(left, right):
    """Jaccard content overlap; preserve negation, timeframes and numeric qualifiers."""
    if unusable_label(left) or unusable_label(right):
        return 0.0
    a, b = normalize(left).split(), normalize(right).split()
    qualifiers = {'not', 'no', 'never', 'without', 'now', 'future', 'temporary'}
    if set(a) & qualifiers != set(b) & qualifiers:
        return 0.0
    if {w for w in a if any(c.isdigit() for c in w)} != {w for w in b if any(c.isdigit() for c in w)}:
        return 0.0
    a, b = set(a) - STOPWORDS, set(b) - STOPWORDS
    return len(a & b) / len(a | b) if a and b else 0.0


AUTHORIZATION_STATEMENT = 'I am legally authorized to work for any employer in the United States.'


def authorization_answer(kind, options):
    if not options:
        return AUTHORIZATION_STATEMENT if kind in {'text', 'textarea'} else 'Yes'
    blocked = ['not', 'no', 'never', 'unable', 'cannot', 'require', 'sponsor', 'sponsorship',
               'visa', 'f 1', 'h 1b', 'opt', 'cpt', 'tn', 'temporary']
    safe = [o for o in options if not any(contains(normalize(o), p) for p in blocked)]
    for phrase in ['legally authorized to work for any employer', 'authorized to work', 'yes']:
        hits = [o for o in safe if (normalize(o) == phrase if phrase == 'yes' else contains(normalize(o), phrase))]
        if len(hits) == 1:
            return [hits[0]] if kind == 'multiselect' else hits[0]
    return None



def robot_only(label):
    """Explicit anti-bot instructions are never applicant website questions."""
    return bool(re.search(r'beecatcher|honey[\s_-]*pot|robots?\s+only|(?:do\s+not|don.t)\s+(?:enter|fill|complete).*\bhuman\b|leave\s+(?:this\s+)?(?:field\s+)?(?:blank|empty).*\bhuman\b', str(label or ''), re.I))
