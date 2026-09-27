"""Immutable views over the existing YAML schema (no additional profile file)."""
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json


class FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError('ProfileSnapshot is immutable')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __deepcopy__(self, memo):
        return self


def freeze(value):
    if isinstance(value, dict):
        return FrozenDict({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ProfileSnapshot:
    facts: FrozenDict
    normalized: FrozenDict
    revision: str
    source: str
    original_facts: FrozenDict | None = None
    references: FrozenDict | None = None

    @classmethod
    def create(cls, profile, source='profile.yaml'):
        from .jobs import normalize
        from .config import fact
        # Reject malformed known facts instead of coercing strings/numbers to booleans.
        for path in ('citizenship.us_citizen', 'citizenship.citizen_since_birth',
                     'work_authorization.us_authorized', 'work_authorization.sponsorship_now',
                     'work_authorization.sponsorship_future', 'education.currently_enrolled'):
            value = fact(profile, path)
            if value is not None and type(value) is not bool:
                raise ValueError(f'{path} must be a boolean or null')
        def normalized(value):
            if isinstance(value, dict):
                return {k: normalized(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [normalized(v) for v in value]
            return normalize(value) if isinstance(value, str) else value
        facts = freeze(profile)
        return cls(facts, freeze(normalized(profile)), fingerprint(facts), str(source))

    def with_overrides(self, settings):
        from .concepts import REGISTRY
        from .jobs import normalize
        values = json.loads(json.dumps(self.facts))
        references = {}
        by_path = {c.profile_path: c for c in REGISTRY.values() if c.profile_path}
        for key, record in settings.items():
            path = key.removeprefix('verified_fact:')
            spec = by_path.get(path)
            if not key.startswith('verified_fact:') or not spec or not isinstance(record, dict) or record.get('source') != 'USER_PROVIDED':
                continue
            value = record.get('value')
            if spec.answer_type == 'boolean' and value is not None and type(value) is not bool:
                value = {'yes': True, 'no': False}.get(normalize(value))
            target = values
            parts = path.split('.')
            for part in parts[:-1]:
                if not isinstance(target.get(part), dict):
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value
            references[path] = record.get('question_id')
        result = self.create(values, self.source)
        return ProfileSnapshot(result.facts, result.normalized, result.revision, self.source,
                               self.facts, freeze(references))
