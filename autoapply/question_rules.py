"""Small, auditable grammars for reusable answers. Similarity is never authority."""
import re
from functools import lru_cache

from .field_mapping import normalize

VERSION = 'experience-v1'
PREFIX = 'known_answer_signature:reusable:'
SUBJECTS = {
    'git': ('git', 'version control using git', 'git version control'),
    'version_control': ('version control tools such as git', 'version control', 'version control tools'),
    'sql': ('sql',),
    'sql_database': ('sql and database management systems',),
    'python': ('python',),
    'embedded': ('embedded development',),
    'embedded_software': ('embedded software development',),
    'cicd': ('ci cd', 'continuous integration continuous deployment ci cd pipeline',
             'continuous integration and continuous deployment pipelines', 'ci cd pipelines'),
}


@lru_cache(maxsize=2048)
def experience_key(label):
    text = normalize(label)
    prefix = 'can you confirm this requirement verify qualification '
    if text.startswith(prefix):
        text = text[len(prefix):]
    # Full grammars preserve negation, durations, qualifications and extra clauses.
    match = re.fullmatch(r'(?:do you have |have you had |have you got )?experience (?:with |using |in )?(.+)', text)
    if not match:
        return None
    subjects = [key for key, aliases in SUBJECTS.items() if match[1] in aliases]
    return subjects[0] if len(subjects) == 1 else None


def remember(db, config, q, value, question_id):
    key = experience_key(q.label)
    if q.semantic_key not in {'','UNKNOWN_FIELD'}:
        return False
    if not key or q.kind not in {'radio', 'select', 'combobox'} or set(q.options) != {'Yes', 'No'}:
        return False
    if value not in {'Yes', 'No'} or q.policy in {'REQUIRE_USER', 'DO_NOT_ANSWER'}:
        return False
    db.set_setting(PREFIX + key, dict(value=value, source='USER_PROVIDED',
        question_id=question_id, rule_version=VERSION,
        profile_revision=config.profile_snapshot().revision))
    return True


def resolve(settings, q, descriptor, revision):
    key = experience_key(q.label)
    if (not key or descriptor.semantic_key != 'UNKNOWN_FIELD'
            or descriptor.policy in {'REQUIRE_USER', 'DO_NOT_ANSWER'}):
        return None
    if q.kind not in {'radio', 'select', 'combobox'} or set(q.options) != {'Yes', 'No'}:
        return None
    item = settings.get(PREFIX + key)
    if (not item or item.get('source') != 'USER_PROVIDED' or item.get('rule_version') != VERSION
            or item.get('profile_revision') != revision or item.get('value') not in q.options):
        return None
    from .models import Answer
    return Answer(item['value'], 'verified_keyword_memory',
        evidence=[str(item['question_id'])], provenance={'rule':VERSION, 'subject':key})
