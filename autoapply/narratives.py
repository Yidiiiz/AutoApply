"""Pure narrative inputs and deterministic checks. Generated text is never fact."""
from dataclasses import asdict, dataclass
import json
import re
import unicodedata

from .answers import validate_answer, writing_topic
from .jobs import normalize
from .profile_snapshot import fingerprint

POLICY_VERSION = 'narrative-v1'
MAX_CACHE_ENTRIES = 256
WRITING_POLICY = (
    'Use 60–120 words unless a smaller field limit applies. Natural undergraduate-professional voice. '
    'Answer the question using only relevant supplied verified facts. '
    'Only use company mission/technology/team facts explicitly in job_context. '
    'Do not invent personal interests or repeat the listing verbatim. Style samples are not factual sources. '
    'No generic praise, I am thrilled, I have always dreamed, prestigious company, perfect fit, or cutting-edge.')


def category(label):
    text = normalize(label)
    for name, pattern in [('location', r'\b(location|city|relocat\w*)\b'),
                          ('projects', r'\b(project|built|created)\b'),
                          ('education', r'\b(education|academic|degree)\b'),
                          ('skills', r'\bskills\b'),
                          ('experience', r'\b(experience|leadership|teamwork|challenge)\b')]:
        if re.search(pattern, text):
            return name
    return writing_topic(label)


def word_limit(label):
    match = re.search(r'(?:maximum|max(?:imum)? of|up to|limit(?: of)?)\s*(\d+)\s*words|\b(\d+)\s*words?\s*(?:max(?:imum)?|limit)', label, re.I)
    return int(next(v for v in match.groups() if v)) if match else None


def template(q, profile):
    """Exact factual summary grammars only; no subjective motivation templates."""
    text = normalize(q.label)
    paths = {
        'describe your education': ('education',),
        'describe your skills': ('skills',),
        'describe your project names': ('projects',),
        'describe your availability': ('availability',),
        'describe your links': ('links',),
    }
    selected = paths.get(text)
    if not selected:
        return None
    key = selected[0]
    value = profile.get(key)
    if key == 'projects' and isinstance(value, (list, tuple)):
        value = [p['name'] for p in value if isinstance(p, dict) and p.get('name')]
    if not value:
        return ('', [])
    if isinstance(value, dict):
        # Education summaries deliberately exclude grades and unrelated assertions.
        keys = ('degree', 'major', 'school', 'graduation_date') if key == 'education' else tuple(value)
        parts = [f'{k.replace("_", " ")}: {value[k]}' for k in keys if value.get(k) is not None]
    elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        parts = list(value)
    else:
        return ('', [])
    return ('; '.join(parts), ['profile.'+key]) if parts else ('', [])


def build_context(q, app, snapshot):
    """Select relevant user-entered facts; listing text is separate task context."""
    profile, kind = snapshot.facts, category(q.label)
    blocks = profile.get('verified_facts', {})
    patterns = {
        'projects': r'project|built|created',
        'education': r'education|academic|school|degree',
        'skills': r'skill|project|technical',
        'location': r'location|city|relocat|interest',
        'experience': r'experience|work|project|leader|team|challenge',
    }
    pattern = patterns.get(kind, r'project|experience|work|skill|interest|education|leader')
    facts = {key: value for key, value in blocks.items()
             if isinstance(value, str) and value.strip() and re.search(pattern, key, re.I)}
    # Unclassified blocks are not guessed into an unrelated category. Identifiers
    # name the factual category; sensitive/legal blocks never enter writing context.
    facts = {k: v for k, v in facts.items()
             if not re.search(r'citizen|sponsor|visa|medical|disab|gender|ethnic|salary|legal', k, re.I)}
    sections = {
        'projects': ('projects', 'skills'), 'education': ('education',),
        'skills': ('skills', 'projects'), 'location': ('locations', 'career_interests'),
        'experience': ('projects', 'employment', 'experience', 'leadership', 'skills', 'education'),
    }.get(kind, ('education', 'skills', 'projects', 'employment', 'experience', 'career_interests'))
    for key in sections:
        if profile.get(key):
            facts['profile.'+key] = json.dumps(profile[key], ensure_ascii=False)
    context = {'job.company': app.get('company', ''), 'job.role': app.get('title', '')}
    if kind in {'general', 'location', 'experience', 'FREE_RESPONSE_COMPANY_INTEREST', 'FREE_RESPONSE_ROLE_INTEREST'}:
        context['job.description'] = app.get('description', '')[:18000]
    if kind == 'location':
        context['job.location'] = app.get('location', '')
    return facts, context


@dataclass(frozen=True)
class NarrativeRequest:
    normalized_question: str
    field_signature: str
    category: str
    employer: str
    requisition: str
    role_title: str
    max_characters: int | None
    max_words: int | None
    profile_revision: str
    verified_fact_revision: int
    writing_revision: int
    policy_version: str
    provider_policy: str
    context_revision: str

    @property
    def signature(self):
        return fingerprint(asdict(self))

    @classmethod
    def create(cls, q, app, descriptor, snapshot, fact_revision, writing_revision,
               provider_policy, facts, context, samples):
        question = ' '.join(unicodedata.normalize('NFKC', q.label).casefold().split())
        return cls(question, descriptor.signature, category(q.label), descriptor.employer,
                   descriptor.requisition, app.get('title', ''), q.max_length, word_limit(q.label),
                   snapshot.revision, fact_revision, writing_revision, POLICY_VERSION,
                   provider_policy, fingerprint([facts, context, samples, WRITING_POLICY]))


def validate_proposal(q, answer, facts, snapshot):
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('Empty narrative response')
    if q.kind not in {'text', 'textarea'} or q.options:
        raise ValueError('Narrative requires an ordinary text field')
    validate_answer(q, answer)
    if q.max_length is not None and len(answer) > q.max_length:
        raise ValueError('Draft exceeds the field\'s character limit')
    limit = word_limit(q.label)
    if limit is not None and len(answer.split()) > limit:
        raise ValueError('Draft exceeds the field\'s word limit')
    if re.search(r'\[(?:insert|your|company|name|placeholder)\b|\b(?:lorem ipsum|TBD|TODO)\b|I am thrilled|I have always dreamed|prestigious company|perfect fit|cutting.edge', answer, re.I):
        raise ValueError('Draft contains placeholder text or violates writing style')
    # Novel numbers (dates, durations, counts, grades) are unsupported applicant claims.
    source_text = ' '.join(str(v) for v in facts.values())
    if set(re.findall(r'\b\d+(?:[./-]\d+)*\b', answer)) - set(re.findall(r'\b\d+(?:[./-]\d+)*\b', source_text)):
        raise ValueError('Draft contains a number absent from supplied verified facts')
    graduation = snapshot.facts.get('education', {}).get('graduation_date')
    claimed = re.search(r'\bgraduat\w*(?:\s+\w+){0,4}\s+(20\d{2})\b', answer, re.I)
    if graduation and claimed and claimed[1] != str(graduation)[:4]:
        raise ValueError('Draft contradicts verified graduation date')


def select_providers(config, tier):
    """Preserve configured order, model, billing and minimum capability tier."""
    return tuple(settings for settings in config['ai']['providers']
                 if config['ai']['enabled'] and settings.get('tier', 0) >= tier)
