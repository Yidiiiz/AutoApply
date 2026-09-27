"""Sanitized field classification; classifiers never receive applicant answers."""
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .concepts import canonical_key, ALIASES, PROFILE_PATHS, REGISTRY

UNKNOWN_FIELD = 'UNKNOWN_FIELD'
REJECTED_FIELD = 'REJECTED_FIELD'
POLICY_VERSION = 'phase5-v1'
AUTOCOMPLETE = {'given-name': 'contact.first_name', 'family-name': 'contact.last_name',
                'name': 'contact.full_name', 'email': 'contact.email', 'tel': 'contact.phone',
                'address-level2': 'contact.city', 'address-level1': 'contact.state',
                'country-name': 'contact.country', 'postal-code': 'contact.postal_code'}
DEMOGRAPHICS = {'gender identity': 'gender_identity', 'racial ethnic background': 'racial_ethnic_background',
                'sexual orientation': 'sexual_orientation', 'transgender': 'transgender',
                'disability': 'disability', 'disability status': 'disability',
                'veteran or active member of u s armed forces': 'armed_forces',
                'gender': 'gender', 'hispanic latino': 'hispanic_latino', 'race': 'race',
                'veteran status': 'veteran_status'}


def normalize(value):
    value = re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',unicodedata.normalize('NFKC', str(value))).casefold().replace('_',' ')
    return re.sub(r'[^\w]+', ' ', value).strip()


@lru_cache(maxsize=2048)
def semantic_key(label):
    text = normalize(label)
    if text in DEMOGRAPHICS:
        return 'demographics.' + DEMOGRAPHICS[text]
    if text in {'confirm your email', 'confirm email', 'email confirmation'}:
        return 'contact.email'
    if text in {'resume', 'cv', 'curriculum vitae'}:
        return 'documents.resume'
    result = canonical_key(text)
    return ALIASES.get(result, result)


def answer_signature(label, kind, options):
    return hashlib.sha256(json.dumps([normalize(label), kind, [normalize(v) for v in options]],
                                    sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class FieldDescriptor:
    label: str
    normalized_label: str
    semantic_key: str
    kind: str
    options: tuple[str, ...]
    required: bool
    max_length: int | None
    min_selections: int | None
    max_selections: int | None
    scope: str
    employer: str
    requisition: str
    mapping_status: str
    policy: str
    policy_version: str
    signature: str


@lru_cache(maxsize=4096)
def _descriptor(label, mapped, kind, options, required, max_length, min_selections, max_selections, scope, employer, requisition, policy, version):
    normalized = normalize(label)
    status = mapped if mapped in {UNKNOWN_FIELD, REJECTED_FIELD} else 'MAPPED'
    payload = [normalized, mapped, kind, [normalize(v) for v in options], required,
               max_length, min_selections, max_selections, scope, employer, requisition, policy, version]
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
    return FieldDescriptor(label, normalized, mapped, kind, options, required, max_length, min_selections, max_selections,
                           scope, employer, requisition, status, policy, version, digest)


def describe(q, app, profile=None):
    from .answers import scope_for
    mapped = q.semantic_key
    if not mapped or mapped == UNKNOWN_FIELD:
        mapped = semantic_key(q.label) or canonical_key(q.label, app.get('company', '')) or UNKNOWN_FIELD
    scope = q.scope or scope_for(q.label, app)
    # Legal/unknown fields retain application identity even when a caller supplies global scope.
    special = REGISTRY.get(mapped)
    scoped = scope != 'global' or not special or special.special_policy
    employer = normalize(app.get('company', '')) if scoped else ''
    requisition = str(app.get('canonical_url') or app.get('id', '')) if scoped else ''
    policy = (q.policy or field_policy({}, mapped, q.kind)) if profile is None else field_policy(profile, mapped, q.kind)
    return _descriptor(q.label, mapped, q.kind, tuple(q.options), q.required, q.max_length, q.min_selections, q.max_selections, scope,
                       employer, requisition, policy, POLICY_VERSION)


def field_policy(profile, key, kind):
    mode = profile.get('autofill_policies', {}).get(key)
    if mode in {'ALWAYS_AUTOFILL','AUTOFILL_IF_EXACT','GENERATE_GROUNDED','REQUIRE_USER','DO_NOT_ANSWER'}:
        return mode
    return 'GENERATE_GROUNDED' if kind == 'textarea' else 'AUTOFILL_IF_EXACT'


def safe_metadata(item):
    # Explicit allowlist: no values, hidden data, files, cookies or tokens.
    return {'label': str(item.get('label', ''))[:1000],
            'options': [str(v)[:300] for v in item.get('options', [])[:100]],
            'section': str(item.get('section', ''))[:300],
            'control_type': str(item.get('kind', ''))[:40], 'required': bool(item.get('required'))}


def signature(ats, item):
    data = safe_metadata(item)
    data['label'] = normalize(data['label'])
    data['options'] = [normalize(v) for v in data['options']]
    return hashlib.sha256(json.dumps([ats, data], sort_keys=True).encode()).hexdigest()


class FieldMapper:
    def __init__(self, ats, cache=None, fallback=None, allowed_keys=()):
        self.ats, self.cache, self.fallback = ats, cache if cache is not None else {}, fallback
        self.allowed_keys = set(allowed_keys)
        self._deterministic = {}

    async def map(self, item):
        # Includes every signal: conflicting autocomplete/name can never hit a label-only cache.
        rule_key = json.dumps([POLICY_VERSION, {k: item.get(k) for k in
            ('label', 'aria_label', 'placeholder', 'name', 'id', 'autocomplete', 'kind', 'options', 'required', 'section')}], sort_keys=True)
        if rule_key in self._deterministic:
            return dict(self._deterministic[rule_key])
        result = await self._map(item)
        if result['mapping_source'] == 'generic_rule':
            if len(self._deterministic) >= 2048:
                self._deterministic.clear()
            self._deterministic[rule_key] = dict(result)
        return result

    async def _map(self, item):
        scores = {}
        for signal, weight in [('label', .99), ('aria_label', .98), ('placeholder', .85),
                               ('name', .65), ('id', .60)]:
            key = semantic_key(item.get(signal, ''))
            if key:
                scores[key] = 1 - (1 - scores.get(key, 0)) * (1 - weight)
        key = AUTOCOMPLETE.get(item.get('autocomplete', ''))
        if key:
            scores[key] = 1 - (1 - scores.get(key, 0)) * .04
        ranked = sorted(scores, key=scores.get, reverse=True)
        if ranked and scores[ranked[0]] >= .95 and (len(ranked) == 1 or scores[ranked[1]] < .6):
            return dict(semantic_key=ranked[0], mapping_source='generic_rule', confidence=round(scores[ranked[0]], 4))
        if len(ranked) > 1 and scores[ranked[1]] >= .6:
            # Conflicting evidence must not be overridden by cached label-only
            # mappings, legacy answer rules, or a semantic fallback provider.
            return dict(semantic_key=REJECTED_FIELD, mapping_source='generic_rule', confidence=0)
        cache_key = signature(self.ats, item)
        cached = self.cache.get(cache_key)
        if cached and cached.get('semantic_key') in self.allowed_keys and cached.get('confidence', 0) >= .98:
            return dict(cached, mapping_source='semantic_fallback')
        # Unknown legal/factual wording needs the applicant, never a classifier.
        factual = re.search(r'\b(sponsor\w*|authoriz\w*|citizen\w*|employ\w*|conflict\w*|government|privacy|sms|consent|school|degree|graduat\w*|gender|disab\w*|veteran|race)\b', normalize(item.get('label', '')))
        if self.fallback and not ranked and not factual:
            import asyncio
            try:
                result = await asyncio.wait_for(self.fallback(safe_metadata(item)), timeout=10)
            except Exception:
                result = None
            if (isinstance(result, dict) and set(result) <= {'semantic_key', 'confidence'}
                    and result.get('semantic_key') in self.allowed_keys
                    and type(result.get('confidence')) in (int, float) and .98 <= result['confidence'] <= 1):
                self.cache[cache_key] = dict(result)
                return dict(result, mapping_source='semantic_fallback')
        return dict(semantic_key=UNKNOWN_FIELD, mapping_source='generic_rule', confidence=0)
