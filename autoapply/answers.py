import json
import re
from datetime import date

from .config import fact
from .jobs import location_rank, normalize, us_location
from .models import Answer, Question, now

LABELS = {
    "first name": "identity.first_name", "given name": "identity.first_name",
    "last name": "identity.last_name", "family name": "identity.last_name",
    "full name": "identity.full_name", "name": "identity.full_name",
    "email": "contact.email", "email address": "contact.email",
    "phone": "contact.phone", "phone number": "contact.phone", "mobile phone": "contact.phone",
    "address": "contact.address", "street address": "contact.address",
    "city": "contact.city", "state": "contact.state", "zip code": "contact.postal_code",
    "postal code": "contact.postal_code", "country": "contact.country",
    "school": "education.school", "university": "education.school", "college university": "education.school",
    "degree": "education.degree", "degree level": "education.degree",
    "major": "education.major", "discipline": "education.major",
    "gpa": "education.gpa", "cumulative gpa": "education.gpa",
    "expected graduation date": "education.graduation_date", "graduation date": "education.graduation_date",
    "linkedin": "links.linkedin", "linkedin profile": "links.linkedin", "linkedin url": "links.linkedin",
    "github": "links.github", "github url": "links.github",
    "website": "links.personal_website", "personal website": "links.personal_website", "portfolio": "links.personal_website",
}


def question_text(label):
    return normalize(re.sub(r"\s*\((?:required|optional)\)\s*$", "", label, flags=re.I))


def concept(label):
    text = question_text(label)
    link = re.fullmatch(r"(?:please )?(?:(?:include|provide|enter|share) (?:your )?|(?:what is )?your )?(linkedin|github|portfolio|personal website)(?: profile)?(?: url| link)?", text)
    if link:
        return {"linkedin":"links.linkedin", "github":"links.github", "portfolio":"links.personal_website", "personal website":"links.personal_website"}[link[1]]
    if text in LABELS:
        return LABELS[text]
    exact_saved_concepts = {
        'are you legally work authorized to work in the us': 'work_authorization.us_authorized',
        'will you now or in the future require visa sponsorship in order to work in the us': 'sponsorship_combined',
        'what is your gender identity': 'demographics.gender_identity',
        'what is your race or ethnicity': 'demographics.racial_ethnic_background',
        'what is your disability status': 'demographics.disability',
    }
    if text in exact_saved_concepts:
        return exact_saved_concepts[text]
    if text in {"how did you hear about us", "how did you first hear about us", "how did you first learn about us",
                "how did you hear about this job", "how did you hear about this position", "how did you find this job"}:
        return "application_preferences.discovery_source"
    if not re.search(r"\b(not|unable|cannot|and|or|relocat\w*|expense|visa|sponsor\w*)\b", text):
        if re.fullmatch(r"(?:are you (?:able|willing) to|can you) (?:come into|attend|work (?:in|from)) (?:the |our )?(?:[a-z]+ ){0,5}office (?:5|five) days (?:a|per) week", text):
            return "application_preferences.office_five_days"
        if re.fullmatch(r"are you willing to work (?:(?:[1-7]|one|two|three|four|five|six|seven) days (?:a|per) week )?(?:in|at|from) (?:the |our )?(?:[a-z]+ ){0,6}(?:office|location)", text):
            return "application_preferences.willing_to_work_any_location"
    # Deliberately narrow equivalences. Negation or additional clauses never use fuzzy similarity.
    patterns = [
        (r"(?:are you|are you currently|will you be) (?:legally )?authorized to work in (?:the )?(?:united states|u s|us)(?: of america)?", "work_authorization.us_authorized"),
        (r"(?:are you|are you a) (?:u s|us|united states) citizen", "citizenship.us_citizen"),
        (r"(?:are you|have you been) (?:a )?(?:u s|us|united states) citizen since birth", "citizenship.citizen_since_birth"),
        (r"(?:will you|do you|do you anticipate) (?:now or in the future )?(?:require|requiring|need) (?:employer |visa |employment |immigration )*sponsorship(?: now or in the future)?(?: for employment(?: visa status)?)?(?: e g h 1b visa status)?", "sponsorship_combined"),
        (r"(?:are you|are you currently) (?:enrolled in|pursuing) a bachelor s degree", "education.currently_enrolled"),
        (r"are you willing to relocate", "locations.willing_to_relocate"),
    ]
    for pattern, path in patterns:
        if re.fullmatch(pattern, text):
            return path
    return None


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
                    json.loads(row["options"]), row["max_length"], scope=row["scope"])


class AnswerResolver:
    def __init__(self, config, db):
        self.config, self.db = config, db

    def resolve(self, q, app):
        q.scope = q.scope or scope_for(q.label, app)
        from .field_mapping import semantic_key, field_policy, answer_signature
        mapped_key = q.semantic_key or semantic_key(q.label)
        policy = field_policy(self.config.profile, mapped_key, q.kind)
        if policy == 'DO_NOT_ANSWER':
            return None
        from .standing import resolve_standing, SOURCE
        match = resolve_standing(q.label, self.config.profile)
        # A boolean assertion never supplies an essay, numeric GPA, or dates.
        if match and q.kind in {"radio", "select", "combobox", "checkbox", "text"}:
            value = fit_options("Yes", q.options)
            if value is not None:
                try:
                    validate_answer(q, value)
                except ValueError:
                    pass
                else:
                    self.db.event(app["id"], "standing_answer", json.dumps(match))
                    return Answer(value, SOURCE, evidence=match["assertion_ids"])
        row = self.db.one("SELECT * FROM known_answers WHERE normalized_question=? AND scope=? AND verified=1",
                          (normalize(q.label), q.scope))
        remembered_signature = self.db.setting('known_answer_signature:' + str(row['id'])) if row else None
        if row and (remembered_signature == answer_signature(q.label,q.kind,q.options) or (not q.semantic_key and remembered_signature is None)):
            value = json.loads(row["answer"])
            try:
                validate_answer(q, value)
            except ValueError:
                return None
            self.db.execute("UPDATE known_answers SET usage_count=usage_count+1,last_used_at=? WHERE id=?", (now(), row["id"]))
            return Answer(value, "verified_memory")
        from .dropdowns import choose_option
        from .disclosures import resolve_disclosure, resolve_service_or_survey
        if q.kind in {"radio", "select", "combobox", "checkbox"}:
            standing = resolve_service_or_survey(q.label, q.options, self.config.profile, app.get("company", ""), required=q.required)
            if standing:
                validate_answer(q, standing["value"])
                self.db.event(app["id"], "standing_disclosure_answer", json.dumps(standing))
                return Answer(standing["value"], standing["source"], evidence=[standing["intent"]])
        if q.kind in {"radio", "select", "combobox"}:
            disclosure = resolve_disclosure(q.label, q.options, self.config.profile, app.get("company", ""))
            if disclosure:
                validate_answer(q, disclosure["value"])
                self.db.event(app["id"], "standing_disclosure_answer", json.dumps(disclosure))
                return Answer(disclosure["value"], disclosure["source"], evidence=[disclosure["intent"]])
        if q.kind == 'combobox':
            selected = choose_option(q.label, q.options, self.config.profile)
            if selected is not None:
                return Answer(selected, 'USER_PROVIDED_DROPDOWN_PREFERENCE')
        if policy == 'REQUIRE_USER':
            return None
        from .field_mapping import PROFILE_PATHS, UNKNOWN_FIELD
        mapped = mapped_key if mapped_key and mapped_key != UNKNOWN_FIELD else None
        path, profile = PROFILE_PATHS.get(mapped, mapped) if mapped else concept(q.label), self.config.profile
        citizen = fact(profile, 'citizenship.us_citizen')
        latest_citizen = self.db.setting('verified_fact:citizenship.us_citizen')
        if latest_citizen and latest_citizen.get('source') == 'USER_PROVIDED':
            citizen = {'yes':True, 'no':False}.get(normalize(latest_citizen['value']))
        # Export status is distinct from clearance, license eligibility, or agreement
        # to legal conditions. Only the current U.S.-person option is derived.
        if question_text(q.label) in {"export compliance", "export control status", "are you a u s person"}:
            if citizen is True:
                choices = [o for o in q.options if question_text(o) in {"i am currently a u s person", "i am a u s person", "yes"}]
                if len(choices) == 1:
                    return Answer(choices[0], "derived:citizenship.us_citizen", evidence=["citizenship.us_citizen", "22 CFR 120.62"])
            return None
        stored = self.db.setting("verified_fact:" + path) if path else None
        if stored and stored.get("source") == "USER_PROVIDED":
            reusable = fit_options(stored["value"], q.options)
            if reusable is not None:
                try:
                    validate_answer(q, reusable)
                except ValueError:
                    pass
                else:
                    return Answer(reusable, "USER_PROVIDED", evidence=[path])
        if path == "sponsorship_combined":
            values = [fact(profile, "work_authorization." + key) for key in ["sponsorship_now", "sponsorship_future"]]
            value = None if any(v is None for v in values) else any(values)
        else:
            value = fact(profile, path) if path else None
        if value is None and citizen is True:
            if path == "work_authorization.us_authorized":
                value, path = True, "derived_from_us_citizenship"
            elif path == "sponsorship_combined":
                value, path = False, "derived_from_us_citizenship"
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
        if value is None:
            exact = fact(profile, "common_answers") or {}
            item = exact.get(q.label)
            if isinstance(item, dict) and item.get("verified") is True and item.get("scope") == q.scope:
                value, path = item.get("answer"), "common_answers"
        if value is None:
            return None
        if isinstance(value, bool):
            value = "Yes" if value else "No"
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
    if q.kind not in {"text", "textarea"} or concept(q.label):
        return False
    text = question_text(q.label)
    if re.search(r"citizen|visa|sponsor|export|salary|gpa|clearance|certif|attest|convict|disab|veteran|years of|license|gender|ethnic|race|religion|medical|prefer not", text):
        return False
    return bool(re.match(r"why\b|describe\b|tell us\b|what interests you\b|share (?:an example|a project)\b", text))


def written_reuse(db, q, app):
    # Exact question AND company/title context prevents stale company-specific reuse.
    rows = db.rows("SELECT * FROM written_responses WHERE question=? AND company=? AND job_title=? AND verified=1 ORDER BY id DESC",
                   (q.label, app["company"], app["title"]))
    for row in rows:
        if not q.max_length or len(row["answer"]) <= q.max_length:
            db.execute("UPDATE written_responses SET last_used_at=? WHERE id=?", (now(), row["id"]))
            return Answer(row["answer"], "verified_writing_bank")
    return None
