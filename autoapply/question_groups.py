"""Offline suggestions for saved answers. Never used by the answering cascade."""
from collections import Counter
from functools import lru_cache
import math
import re

from .concepts import canonical_key, REGISTRY

CATEGORIES = ('Personal', 'Contact', 'Education', 'Experience', 'Projects', 'Skills',
              'Links', 'Work authorization', 'Demographics', 'Preferences/Availability',
              'Eligibility & requirements', 'Other')
STOP = set('you your our company currently are is what do does have has the a an to in of for and or with at by this that please select indicate one option best describes how identify will require need order work employed employment legally vacancy posted where country begin continue benefit immigration sponsored'.split())


@lru_cache(maxsize=4096)
def normalize(question, employer=''):
    original = str(question).lower()
    text = original
    text = re.split(r'\bnote that\b', text)[0]
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    text = re.sub(r'^can you confirm this requirement verify qualification\s*', '', text)
    if employer:
        name = re.sub(r'[^a-z0-9]+', ' ', employer.lower()).strip()
        if name:
            text = re.sub(r'\b' + re.escape(name) + r'\b', ' ', text)
    for pattern, replacement in (
        (r'\bunited states\b|\bu s\b', 'us'),
        (r'\bgender identity\b', 'gender'),
        (r'\brace or ethnicity\b|\brace ethnicity\b|\bethnicity\b', 'race'),
        (r'\bauthori[sz]ed\b|\beligible\b', 'authorized'),
        (r'\bsponsorship\b|\bvisa\b|\bsponsor\b', 'sponsorship'),
        (r'\benrolled\b|\bpursuing\b|\bstudent\b', 'enrolled'),
        (r'\bmilitary\b|\bveteran\b', 'veteran')):
        text = re.sub(pattern, replacement, text)
    words = [w for w in text.split() if w not in STOP]
    # Long employer visa boilerplate carries the same review suggestion, while
    # now/future and negation remain distinct. This grants no answer authority.
    if 'sponsorship' in words and re.search(r'\b(require|need|sponsor)\b', str(question).lower()):
        words = list(dict.fromkeys(w for w in words if w in {'sponsorship', 'now', 'future', 'not', 'never'}))
    if 'authorized' in words:
        words = [w for w in words if w != 'us']
    return ' '.join(re.sub(r'(?:ing|ed|s)$', '', w) if len(w) > 6 and w not in {'authorized', 'enrolled', 'sponsorship'} else w for w in words) or original


def _timeframe(text):
    return tuple(w for w in ('now', 'future') if w in text.split())


@lru_cache(maxsize=8192)
def similarity(a, b, employer=''):
    left, right = normalize(a, employer), normalize(b, employer)
    if ('sponsorship' in left or 'sponsorship' in right) and _timeframe(left) != _timeframe(right):
        return 0.0
    if bool(re.search(r'\b(not|never|no)\b', left)) != bool(re.search(r'\b(not|never|no)\b', right)):
        return 0.0
    ka, kb = canonical_key(a, employer), canonical_key(b, employer)
    if ka and kb and ka != kb:
        return 0.0
    if a != b and any(re.fullmatch(r'[a-z_]+(?:\.[a-z_]+)+', str(q)) for q in (a, b)) and not (ka and ka == kb):
        return 0.0
    if ka and ka == kb:
        return 1.0
    wa, wb = set(left.split()), set(right.split())
    overlap = len(wa & wb)
    if not overlap:
        return 0.0
    # Different multi-part SQL claims must not over-merge on the shared tool.
    if 'sql' in wa & wb and ('database' in wa) != ('database' in wb):
        return 0.0
    jaccard = overlap / len(wa | wb)
    ca, cb = Counter(left[i:i+3] for i in range(len(left)-2)), Counter(right[i:i+3] for i in range(len(right)-2))
    denominator = math.sqrt(sum(v*v for v in ca.values()) * sum(v*v for v in cb.values()))
    cosine = sum(v * cb[k] for k, v in ca.items()) / denominator if denominator else 0.0
    return max(jaccard, cosine)


def answer_kind(item):
    if isinstance(item, str):
        return 'unknown'
    value = item.get('answer', '')
    answer = re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip() if isinstance(value, str) else ''
    if type(value) is bool or answer in {'yes', 'no', 'y', 'n', 'true', 'false'} or (item.get('check_answer') and re.search(r'\b(?:yes|no)$', answer)):
        return 'boolean'
    kind = item.get('answer_kind', item.get('kind', ''))
    if kind.startswith('mixed:'):
        return kind
    if kind in {'boolean', 'bool', 'checkbox'}:
        return 'boolean'
    if kind in {'select', 'radio', 'combobox', 'multiselect'}:
        return 'select'
    if kind in {'text', 'textarea', 'email', 'number'}:
        return 'text'
    return 'select' if isinstance(item.get('answer'), list) else 'text'


def group(questions, threshold=0.6):
    """Return lists of the original records (or strings), preserving their order."""
    questions = list(questions)
    parents = list(range(len(questions)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    def label(item):
        return item if isinstance(item, str) else item['question']
    def key(item):
        text = label(item)
        path = item.get('profile_path') if isinstance(item, dict) else None
        return canonical_key(text) or path or (text if re.fullmatch(r'[a-z_]+(?:\.[a-z_]+)+', text) else None)
    concepts = [{key(item)} - {None} for item in questions]
    for i, a in enumerate(questions):
        for j in range(i):
            b = questions[j]
            employer = a.get('employer', '') if isinstance(a, dict) else ''
            if answer_kind(a) == answer_kind(b) and similarity(label(a), label(b), employer) >= threshold:
                left, right = root(i), root(j)
                keys = concepts[left] | concepts[right]
                if len(keys) > 1:
                    continue
                parents[left] = right
                concepts[right] = keys
    groups = {}
    for i, item in enumerate(questions):
        groups.setdefault(root(i), []).append(item)
    return list(groups.values())


def categorize(question, settings=None):
    override = (settings or {}).get('question_category:' + normalize(question))
    if override in CATEGORIES:
        return override, 1.0
    raw = re.sub(r'[^a-z0-9]+', ' ', str(question).lower()).strip()
    technical = r'\b(?:sql|python|docker|llm|nlp|databases?|algorithms?|excel|office|git|ci cd)\b'
    if raw.startswith('can you confirm this requirement verify qualification'):
        return ('Skills' if re.search(technical, raw) else 'Eligibility & requirements'), 0.85
    if re.search(r'\b(?:consent|acknowledg\w*|privacy|by checking this box|conflict of interest|outside business|government official|relationships? with employees)\b', raw):
        return 'Eligibility & requirements', 0.85
    if re.search(r'\b(?:have you (?:ever )?worked (?:at|for)|previously worked for)\b', raw):
        return 'Experience', 0.85
    if re.fullmatch(r'end date (?:month|year)', raw):
        return 'Education', 0.85
    if re.fullmatch(r'(?:backend|frontend|mobile ios|mobile android|data engineering|machine learning|foundations|money|dashos|dasher logistics|merchant consumer platform|operational excellence|new verticals|marketplace|ads promotions|platform services|launchpad ai research)(?: team)?', raw):
        return 'Preferences/Availability', 0.85
    key = canonical_key(question) or question
    spec = REGISTRY.get(key)
    if spec:
        category = {'identity': 'Personal', 'contact': 'Contact', 'education': 'Education',
                    'links': 'Links', 'work_authorization': 'Work authorization', 'citizenship': 'Work authorization',
                    'demographics': 'Demographics', 'qualifications': 'Eligibility & requirements',
                    'application_preferences': 'Preferences/Availability', 'locations': 'Preferences/Availability',
                    'skills': 'Skills'}.get(spec.category)
        if key in {'contact.first_name', 'contact.last_name', 'contact.full_name'}:
            category = 'Personal'
        if key == 'sponsorship_combined':
            category = 'Work authorization'
        if category:
            return category, 0.95
    text = normalize(question)
    rules = [('Work authorization', r'authorized|sponsorship|citizen'),
             ('Demographics', r'gender|race|veteran|disability|hispanic|latino|transgender|orientation'),
             ('Education', r'enrolled|academic|school|degree|graduat|university|gpa'),
             ('Links', r'linkedin|github|portfolio|website'), ('Projects', r'project'),
             ('Skills', r'skill|proficien|sql|python|microsoft|windows|docker|llm|nlp|database|algorithm|excel|\bgit\b|ci cd'),
             ('Experience', r'experience|employer|employment|previously|worked'),
             ('Contact', r'email|phone|address|postal|city'),
             ('Preferences/Availability', r'relocat|onsite|hybrid|availability|start date|salary|office|team interest'),
             ('Eligibility & requirements', r'requirement|qualification|verified|18|eligib|schedule|soft skill|compensation|government|conflict'),
             ('Personal', r'name|yourself')]
    for category, pattern in rules:
        if re.search(pattern, text):
            return category, 0.7
    return 'Other', 0.0
