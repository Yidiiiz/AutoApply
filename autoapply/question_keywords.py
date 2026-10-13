"""Small label-only concept matcher and conservative saved-question similarity."""
import re


def normalize(text):
    return ' '.join(re.sub(r'[^a-z0-9]', ' ', str(text or '').lower()).split())


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
         all=[], exclude=['future']),
    dict(key='citizenship.us_citizen', any=['us citizen', 'u s citizen', 'united states citizen',
         'us citizenship', 'u s citizenship', 'united states citizenship'], all=[], exclude=['since birth']),
    dict(key='work_authorization.temporary_authorization', any=['temporary work authorization',
         'f 1', 'opt', 'cpt', 'h 1b', 'tn'], all=[], exclude=['sponsor', 'sponsorship']),
    dict(key='visa.status', any=['visa status', 'current visa', 'type of visa'], all=[], exclude=['sponsor', 'sponsorship']),
    dict(key='security.clearance', any=['security clearance', 'clearance'], all=[], exclude=[]),
    dict(key='locations.willing_to_relocate', any=['willing to relocate', 'relocation'], all=[], exclude=['not', 'expense']),
    dict(key='start_date', any=['start date', 'when can you start'], all=[], exclude=['education', 'employment history']),
    dict(key='education.graduation_date', any=['graduation date', 'when will you graduate'], all=[], exclude=['month', 'year']),
    dict(key='education.gpa', any=['gpa', 'grade point average'], all=[], exclude=[]),
    dict(key='education.school', any=['school', 'university', 'college'], all=[], exclude=['high school', 'degree', 'major', 'gpa', 'graduation', 'year']),
    dict(key='education.degree', any=['degree', 'degree level'], all=[], exclude=['enrolled', 'pursuing', 'major', 'graduation']),
    dict(key='education.major', any=['major', 'field of study', 'discipline'], all=[], exclude=[]),
    dict(key='application_preferences.discovery_source', any=['how did you hear', 'how did you learn', 'how did you find', 'where did you hear'], all=[], exclude=[]),
    dict(key='prior_employment', any=['previously worked', 'previously employed', 'ever worked', 'former employee', 'worked here before'], all=[], exclude=[]),
    dict(key='qualifications.age_18_or_older', any=['over 18', 'at least 18', '18 years or older', '18 years of age or older'], all=[], exclude=['under', 'not']),
    dict(key='links.linkedin', any=['linkedin'], all=[], exclude=[]),
    dict(key='links.github', any=['github'], all=[], exclude=[]),
    dict(key='links.portfolio', any=['website', 'portfolio'], all=[], exclude=[]),
    dict(key='demographics.pronouns', any=['pronouns'], all=[], exclude=[]),
    dict(key='demographics.gender', any=['gender', 'gender identity'], all=[], exclude=[]),
    dict(key='demographics.race', any=['race', 'ethnicity', 'racial ethnic background'], all=[], exclude=[]),
    dict(key='demographics.veteran_status', any=['veteran', 'veteran status'], all=[], exclude=[]),
    dict(key='demographics.disability', any=['disability', 'disability status'], all=[], exclude=[]),
]


def match(text):
    """Return the most specific concept; options are deliberately never inspected."""
    text = normalize(text)
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
