import json
import re
import copy
from dataclasses import replace
from datetime import date

from .config import fact
from .jobs import location_rank, normalize, us_location
from .models import Answer, AnswerResolution, Question, now

from .concepts import LABELS, question_text, concept


def scope_for(label, app):
    return "global" if concept(label) else f"application:{app['id']}"


def fit_options(value, options):
    if not options:
        return str(value)
    expected = normalize(value)
    exact = [option for option in options if normalize(option) == expected]
    if len(exact) == 1:
        return exact[0]
    if expected == "decline":
        matches = [option for option in options if re.search(r"decline|prefer not|do not wish|don t wish", option, re.I)]
        return matches[0] if len(matches) == 1 else None
    return None


def validate_answer(q, value):
    if q.kind == "multiselect":
        if not isinstance(value, list) or not value and q.required:
            raise ValueError("Select one or more exact options as a JSON list")
        if any(v not in q.options for v in value):
            raise ValueError("Answer contains an unavailable option")
        if len(set(value)) != len(value) or (q.min_selections is not None and len(value) < q.min_selections) or (q.max_selections is not None and len(value) > q.max_selections):
            raise ValueError('Answer violates selection limits')
        return
    if not isinstance(value, str) or not value.strip() and q.required:
        raise ValueError("A nonempty text answer is required")
    if q.options and value not in q.options:
        raise ValueError("Choose an exact listed option")
    if q.max_length and len(value) > q.max_length:
        raise ValueError(f"Answer exceeds {q.max_length} characters")
    if q.kind == "number":
        try:
            number = float(value)
            if not __import__("math").isfinite(number):
                raise ValueError()
        except ValueError:
            raise ValueError("A finite numeric answer is required") from None
    if q.kind == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Enter a valid email address")
    if q.kind == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("Use a complete date: YYYY-MM-DD") from None


def from_row(row):
    return Question(row["field_key"], row["raw_question"], row["field_type"], bool(row["required"]),
                    json.loads(row["options"]), row["max_length"], scope=row["scope"],
                    semantic_key=row.get("semantic_key") or "", signature=row.get("question_signature") or "",
                    policy=row.get("field_policy") or "",
                    min_selections=row.get('min_selections'), max_selections=row.get('max_selections'))


class AnswerResolver:
    def __init__(self, config, db):
        self.config, self.db = config, db
        self._revision = None
        self._cache = {}
        self.computations = 0

    def refresh(self):
        from .profile_snapshot import fingerprint
        snapshot = self.config.profile_snapshot()
        revision = (snapshot.revision, self.db.answer_revision(), fingerprint(self.config.data))
        if revision != self._revision:
            self._cache.clear()
            self._settings = {r['key']: json.loads(r['value']) for r in self.db.rows(
                "SELECT key,value FROM settings WHERE key LIKE 'verified_fact:%' OR key LIKE 'known_answer_signature:%'")}
            self._memory = {(r['normalized_question'], r['scope']): r for r in self.db.rows(
                'SELECT * FROM known_answers WHERE verified=1')}
            self.snapshot = snapshot.with_overrides(self._settings)
            self._revision = revision
        return self.snapshot

    def resolve_result(self, q, app):
        from .field_mapping import describe, REJECTED_FIELD
        self.refresh()
        descriptor = describe(q, app, self.snapshot.facts)
        # The complete immutable descriptor also distinguishes exact rendered options.
        key = descriptor
        if key not in self._cache:
            self.computations += 1
            canonical = replace(q, scope=descriptor.scope, semantic_key=descriptor.semantic_key)
            answer = self._resolve(canonical, app, descriptor, self.snapshot.facts)
            if answer:
                answer.semantic_key, answer.scope = descriptor.semantic_key, descriptor.scope
                answer.signature, answer.profile_revision = descriptor.signature, self.snapshot.revision
                answer.verified = True
                answer.reason = 'Exact compatible verified answer or explicit deterministic policy'
                answer.provenance.update(profile_revision=self.snapshot.revision, fact_revision=self._revision[1],
                                         policy_version=descriptor.policy_version, evidence=answer.evidence)
            status = ('RESOLVED' if answer else 'REJECTED' if descriptor.semantic_key == REJECTED_FIELD
                      else 'USER_INPUT_REQUIRED' if q.required else 'OPTIONAL_SKIP')
            self._cache[key] = AnswerResolution(answer, status, descriptor, self.snapshot.revision,
                                                context_revision=self._revision)
        return copy.deepcopy(self._cache[key])

    def current_result(self, prior, q, app):
        from .field_mapping import describe
        self.refresh()
        if (prior and prior.context_revision == self._revision and
                prior.descriptor == describe(q, app, self.snapshot.facts)):
            return prior
        return self.resolve_result(q, app)

    def resolve(self, q, app):
        """Compatibility facade; lookup has no persistent side effects."""
        result = self.resolve_result(q, app)
        q.scope, q.signature, q.policy = result.descriptor.scope, result.descriptor.signature, result.descriptor.policy
        return result.answer

    def stamp(self, answer, descriptor):
        """Attach evidence to narrative/receipt answers without caching them."""
        answer.signature, answer.semantic_key, answer.scope = descriptor.signature, descriptor.semantic_key, descriptor.scope
        answer.profile_revision = self.snapshot.revision
        answer.provenance.update(fact_revision=self._revision[1], policy_version=descriptor.policy_version)
        return answer

    def replay(self, row, q, app):
        """Only unchanged, signed saved receipts can bypass deterministic re-resolution."""
        from .field_mapping import describe
        self.refresh()
        descriptor = describe(q, app, self.snapshot.facts)
        data = json.loads(row.get('answer_provenance') or '{}')
        if (not row.get('answer') or descriptor.policy in {'DO_NOT_ANSWER', 'REQUIRE_USER'}
                or data.get('signature') != descriptor.signature
                or data.get('profile_revision') != self.snapshot.revision
                or data.get('provenance', {}).get('fact_revision') != self._revision[1]):
            return None
        answer = answer_from_row(row)
        try:
            validate_answer(q, answer.value)
        except (ValueError, TypeError):
            return None
        return answer

    def _resolve(self, q, app, descriptor, profile):
        from .field_mapping import REJECTED_FIELD
        mapped_key, policy = descriptor.semantic_key, descriptor.policy
        if policy == 'DO_NOT_ANSWER':
            return None
        row = self._memory.get((normalize(q.label), q.scope))
        from .concepts import REGISTRY
        spec = REGISTRY.get(mapped_key)
        memory_path = spec.profile_path if spec and spec.profile_path else 'sponsorship_combined' if mapped_key == 'sponsorship_combined' else None
        stored = self._settings.get('verified_fact:' + memory_path) if memory_path else None
        memory_data = json.loads(row.get('provenance') or '{}') if row else {}
        compatible_profile = (not memory_data.get('provenance', {}).get('canonical_profile_revision') or
                              memory_data['provenance']['canonical_profile_revision'] == self._revision[0])
        compatible_override = (not stored or stored.get('source') != 'USER_PROVIDED' or
                               normalize(stored.get('value')) == normalize(json.loads(row['answer']) if row else None))
        if row and row.get('question_signature') == descriptor.signature and compatible_profile and compatible_override:
            value = json.loads(row['answer'])
            try:
                validate_answer(q, value)
            except (ValueError, TypeError):
                pass
            else:
                return Answer(value, 'verified_memory', provenance={'memory_id': row['id'],
                    'reference': json.loads(row.get('provenance') or '{}')})
        if policy == 'REQUIRE_USER':
            return None
        from .standing import resolve_standing, SOURCE
        match = resolve_standing(descriptor, profile)
        # A boolean assertion never supplies an essay, numeric GPA, or dates.
        if mapped_key != REJECTED_FIELD and match and q.kind in {"radio", "select", "combobox", "checkbox", "text"}:
            value = fit_options("Yes", q.options)
            if value is not None:
                try:
                    validate_answer(q, value)
                except ValueError:
                    pass
                else:
                    return Answer(value, SOURCE, evidence=match["assertion_ids"],
                                  provenance={"event_kind": "standing_answer", "event_detail": match})
        if mapped_key == REJECTED_FIELD:
            # Only an exact, scoped, explicitly verified answer may resolve a
            # rejected field. Weaker label/profile interpretation must abstain.
            item = (fact(profile, 'common_answers') or {}).get(q.label)
            if common_compatible(item, q, descriptor):
                value = item.get('answer')
                try:
                    validate_answer(q, value)
                except (ValueError, TypeError):
                    return None
                return Answer(value, 'profile:common_answers', evidence=['common_answers'])
            return None
        from .dropdowns import choose_option
        from .disclosures import resolve_disclosure, resolve_service_or_survey
        if q.kind in {"radio", "select", "combobox", "checkbox"}:
            standing = resolve_service_or_survey(descriptor, q.options, profile, app.get("company", ""), required=q.required)
            if standing:
                validate_answer(q, standing["value"])
                return Answer(standing["value"], standing["source"], evidence=[standing["intent"]],
                              provenance={"event_kind": "standing_disclosure_answer", "event_detail": standing})
        if q.kind in {"radio", "select", "combobox"}:
            disclosure = resolve_disclosure(descriptor, q.options, profile, app.get("company", ""))
            if disclosure:
                validate_answer(q, disclosure["value"])
                return Answer(disclosure["value"], disclosure["source"], evidence=[disclosure["intent"]],
                              provenance={"event_kind": "standing_disclosure_answer", "event_detail": disclosure})
        from .field_mapping import UNKNOWN_FIELD
        mapped = mapped_key if mapped_key and mapped_key != UNKNOWN_FIELD else None
        spec = REGISTRY.get(mapped)
        path = (spec.profile_path if spec and spec.factual_fallback else
                'sponsorship_combined' if mapped == 'sponsorship_combined' else None)
        citizen = fact(profile, 'citizenship.us_citizen')
        # Export status is distinct from clearance, license eligibility, or agreement
        # to legal conditions. Only the current U.S.-person option is derived.
        if question_text(q.label) in {"export compliance", "export control status", "are you a u s person"}:
            if citizen is True:
                choices = [o for o in q.options if question_text(o) in {"i am currently a u s person", "i am a u s person", "yes"}]
                if len(choices) == 1:
                    return Answer(choices[0], "derived:citizenship.us_citizen", evidence=["citizenship.us_citizen", "22 CFR 120.62"])
            return None
        stored = self._settings.get("verified_fact:" + path) if path else None
        if stored and stored.get("source") == "USER_PROVIDED":
            if stored.get('value') is None:
                return None
            override = fact(profile, path) if spec and spec.answer_type == 'boolean' else stored['value']
            if override is None:
                return None
            if isinstance(override, bool):
                override = 'Yes' if override else 'No'
            reusable = fit_options(override, q.options)
            if reusable is not None:
                try:
                    validate_answer(q, reusable)
                except ValueError:
                    pass
                else:
                    return Answer(reusable, "USER_PROVIDED", evidence=[path],
                                  provenance={'question_id': stored.get('question_id')})
        if q.kind == 'combobox':
            selected = choose_option(descriptor, q.options, profile)
            if selected is not None:
                return Answer(selected, 'USER_PROVIDED_DROPDOWN_PREFERENCE')
        if path == "sponsorship_combined":
            values = [fact(profile, "work_authorization." + key) for key in ["sponsorship_now", "sponsorship_future"]]
            value = None if any(v is None for v in values) else any(values)
        else:
            value = fact(profile, path) if path else None
        if path == "identity.full_name" and not value:
            first, last = fact(profile, "identity.first_name"), fact(profile, "identity.last_name")
            value = f"{first} {last}" if first and last else None
        text = question_text(q.label)
        if path == "application_preferences.discovery_source" and value is not None and q.options:
            selected = fit_options(str(value), q.options)
            if selected is None and normalize(value) == "job board":
                selected = next((o for o in q.options if normalize(o) in {"job board", "online job board", "job boards"}), None)
                selected = selected or next((o for o in q.options if normalize(o) == "other"), None)
            # Never turn a generic job-board answer into a university or named-board claim.
            value = selected
        if value is None and text in {"race", "race ethnicity", "gender", "disability status", "veteran status"}:
            preference = {"race ethnicity": "race", "disability status": "disability", "veteran status": "veteran"}.get(text, text)
            value = fact(profile, "application_preferences." + preference)
            path = "application_preferences." + preference
        if value is None and text in {"salary expectations", "expected compensation", "desired salary"} and q.kind in {"text", "textarea"}:
            value = fact(profile, "application_preferences.compensation_text")
            path = "application_preferences.compensation_text"
        if value is None and "summer 2027" in text and text in {"summer 2027 start date", "available start date for summer 2027"}:
            value = fact(profile, "availability.summer_2027.start_date")
            path = "availability.summer_2027.start_date"
        if value is None and text in {"preferred locations", "preferred location", "office location preference"} and q.options:
            acceptable = [o for o in q.options if us_location(o) is True]
            acceptable.sort(key=lambda v: location_rank(v, self.config["locations"]["preferred_groups"]), reverse=True)
            if acceptable:
                value = acceptable if q.kind == "multiselect" else acceptable[0]
                path = "configured_location_preferences"
        if value is None and descriptor.semantic_key == 'technical_interests' and q.options:
            preferences = fact(profile, 'dropdown_preferences.technical_interests')
            if isinstance(preferences, (tuple, list)):
                choices = [fit_options(v, q.options) for v in preferences]
                choices = list(dict.fromkeys(v for v in choices if v is not None))
                if choices:
                    value = choices[:q.max_selections] if q.kind == 'multiselect' else choices[0]
                    path = 'dropdown_preferences.technical_interests'
        if value is None:
            exact = fact(profile, "common_answers") or {}
            item = exact.get(q.label)
            if common_compatible(item, q, descriptor):
                value, path = item.get("answer"), "common_answers"
        if value is None:
            return None
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            value = fit_options(str(value), q.options)
        if value is None:
            return None
        try:
            validate_answer(q, value)
        except ValueError:
            return None
        return Answer(value, "profile:" + str(path), evidence=[str(path)])


def writing_topic(question):
    text = normalize(question)
    if re.search(r"\b(why|interest|interests)\b", text):
        if re.search(r"\b(role|position|internship|opportunity)\b", text):
            return "FREE_RESPONSE_ROLE_INTEREST"
        return "FREE_RESPONSE_COMPANY_INTEREST"
    for topic, pattern in [("company_interest", r"why.*(?:company|us|join|work here)"), ("technical_challenge", r"challenge|debug"),
                           ("teamwork", r"team|conflict"), ("leadership", r"leader|ownership"), ("projects", r"project|built|created"),
                           ("motivation", r"why|interest|goals")]:
        if re.search(pattern, text):
            return topic
    return "general"


def is_writing_question(q):
    """Only narrative prompts; a text box alone does not authorize factual invention."""
    from .field_mapping import REJECTED_FIELD
    from .field_mapping import semantic_key
    from .concepts import REGISTRY
    if q.semantic_key == REJECTED_FIELD or (q.semantic_key or semantic_key(q.label)) in REGISTRY:
        return False
    if q.kind not in {"text", "textarea"} or concept(q.label):
        return False
    text = question_text(q.label)
    if re.search(r"citizen|visa|sponsor|export|salary|gpa|clearance|certif|attest|convict|disab|veteran|years of|license|gender|ethnic|race|religion|medical|prefer not|privacy|sms|consent|authorization|previous employer|prior employ|government connect", text):
        return False
    return bool(re.match(r"why\b|describe\b|tell us\b|what interests you\b|share (?:an example|a project)\b", text))


def written_reuse(db, q, app, profile_revision=None):
    if not is_writing_question(q):
        return None
    if 'id' not in app:
        # A draft preview may lack a persisted application. It cannot establish
        # reusable requisition scope, but can still follow the normal draft flow.
        return None
    from .field_mapping import describe
    descriptor = describe(q, app)
    # Signed question and requisition context; unsigned legacy narratives remain readable.
    rows = db.rows("SELECT * FROM written_responses WHERE question=? AND company=? AND job_title=? AND verified=1 ORDER BY id DESC",
                   (q.label, app["company"], app["title"]))
    for row in rows:
        if (profile_revision and row.get('profile_revision') == profile_revision
                and row.get("question_signature") == descriptor.signature
                and (not q.max_length or len(row["answer"]) <= q.max_length)):
            return Answer(row["answer"], "verified_writing_bank", verified=True, provenance={"writing_id": row["id"]})
    return None


def common_compatible(item, q, descriptor):
    if not isinstance(item, dict) or item.get('verified') is not True or item.get('scope') != q.scope:
        return False
    if item.get('signature'):
        return item['signature'] == descriptor.signature
    # Existing exact free-text common answers remain readable; option-bearing
    # and newly scoped legal questions require explicit compatibility evidence.
    return not q.options and descriptor.semantic_key not in {
        'prior_employment', 'conflict_of_interest', 'government_connections', 'privacy_ack', 'sms_recruiting'}


def answer_from_row(row):
    data = json.loads(row.get('answer_provenance') or '{}')
    allowed = set(Answer.__dataclass_fields__) - {'value', 'source', 'confidence'}
    return Answer(json.loads(row['answer']), row['answer_source'], row['confidence'],
                  **{k: v for k, v in data.items() if k in allowed})


def record_answer_use(db, app_id, answer):
    """Call inside the accepted receipt transaction, never during consideration."""
    memory_id = answer.provenance.get('memory_id')
    if memory_id:
        db.execute('UPDATE known_answers SET usage_count=usage_count+1,last_used_at=? WHERE id=?', (now(), memory_id))
    writing_id = answer.provenance.get('writing_id')
    if writing_id:
        db.execute('UPDATE written_responses SET last_used_at=? WHERE id=?', (now(), writing_id))
    if answer.provenance.get('event_kind'):
        db.event(app_id, answer.provenance['event_kind'], json.dumps(answer.provenance['event_detail']))
