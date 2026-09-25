"""Sanitized field classification; classifiers never receive applicant answers."""
import hashlib
import json
import re
import unicodedata

from .answers import concept

UNKNOWN_FIELD = 'UNKNOWN_FIELD'
ALIASES = {'identity.first_name': 'contact.first_name', 'identity.last_name': 'contact.last_name',
           'identity.full_name': 'contact.full_name', 'links.personal_website': 'links.portfolio'}
PROFILE_PATHS = {v: k for k, v in ALIASES.items()}
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


def semantic_key(label):
    text = normalize(label)
    if text in DEMOGRAPHICS:
        return 'demographics.' + DEMOGRAPHICS[text]
    if text in {'confirm your email', 'confirm email', 'email confirmation'}:
        return 'contact.email'
    if text in {'resume', 'cv', 'curriculum vitae'}:
        return 'documents.resume'
    result = concept(text)
    return ALIASES.get(result, result)


def answer_signature(label, kind, options):
    return hashlib.sha256(json.dumps([normalize(label), kind, [normalize(v) for v in options]],
                                    sort_keys=True).encode()).hexdigest()


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

    async def map(self, item):
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
        cache_key = signature(self.ats, item)
        cached = self.cache.get(cache_key)
        if cached and cached.get('semantic_key') in self.allowed_keys and cached.get('confidence', 0) >= .98:
            return dict(cached, mapping_source='semantic_fallback')
        if self.fallback and not ranked:
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
