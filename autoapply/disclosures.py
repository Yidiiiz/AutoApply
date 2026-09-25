"""Full-clause disclosure matching; unfamiliar clauses always require review."""
import re

from .jobs import normalize

SOURCE = "USER_PROVIDED_STANDING_DISCLOSURE"
FACTS = {
    "prior_employment": "No prior employment or contractor work for prospective employers or relevant affiliates unless explicitly superseded by the user.",
    "conflict_of_interest": "No personal, financial, employment, business, family, ownership, or outside relationship that creates, appears to create, or materially affects a conflict with a prospective employer.",
    "government_connections": "No relevant personal or immediate-family government connections creating compliance, ethics, anti-bribery, procurement, or employment concerns.",
}


def _list_of(item):
    return rf"{item}(?:(?: or | and | ){item})*"


def disclosure_intent(label, company):
    # Normalize punctuation only. Do not strip unknown prose or legal qualifiers.
    text = normalize(label)
    names = ["us", "this company", "the company", "the employer", "your prospective employer"]
    if company:
        names.append(normalize(company))
    employer = "(?:" + "|".join(re.escape(v) for v in names) + ")"
    affiliate = rf"(?: or (?:any of )?(?:its|our|their) (?:affiliates|related companies|subsidiaries))?"
    work = rf"(?:have you (?:ever |previously )?(?:worked for|worked at|been employed by)|were you (?:ever |previously )?employed by) {employer}{affiliate}"
    role = r"(?: as (?:an? )?(?:employee|intern|contractor)(?: (?:or |and )?(?:an? )?(?:employee|intern|contractor))*)?"
    warning = r"(?: note that providing false or misleading information may result in disqualification from the hiring process)?"
    if re.fullmatch(work + role + warning, text):
        return "prior_employment"
    relationships = _list_of(r"(?:personal relationships?|financial interests?|employment relationships?|outside business activities|outside business activity|family business relationships?|family relationships?|ownership interests?|other outside relationships?)")
    effects = _list_of(r"(?:create|appear to create|materially affect)")
    conflict = rf"(?:do you have|are there) any {relationships} that (?:could|would|may) {effects} (?:a conflict|a conflict of interest) (?:with|in relation to) {employer}"
    if re.fullmatch(conflict, text):
        return "conflict_of_interest"
    if re.fullmatch(rf"do you have any (?:actual or potential |potential )?conflicts? of interest with {employer}", text):
        return "conflict_of_interest"
    entities = _list_of(r"(?:government officials?|elected officials?|political officials?|government agencies|public sector decision makers|state owned entities|state controlled entities)")
    concern = _list_of(r"(?:compliance|ethics|anti bribery|procurement|employment)")
    government = rf"do (?:you|you or (?:an? |any )?immediate family member) have any (?:relevant )?(?:connections|relationships) (?:to|with) {entities} that (?:could|would|may) create (?:a |an? )?{concern} concerns?"
    if re.fullmatch(government, text):
        return "government_connections"
    if re.fullmatch(r"are you (?:related to|in a close personal relationship with) (?:a )?(?:government|elected|political) official", text):
        return "government_connections"
    return None


def resolve_disclosure(label, options, profile, company):
    intent = disclosure_intent(label, company)
    record = profile.get("standing_disclosures", {}).get(intent, {})
    if not intent or not isinstance(record, dict) or not (
        record.get("source") == "USER_PROVIDED" and record.get("scope") == "standing"
        and record.get("answer") is False and record.get("fact") == FACTS[intent]
    ):
        return None
    choices = [o for o in options if normalize(o) == "no"]
    if not choices and intent == "prior_employment" and company:
        company = re.escape(normalize(company))
        choices = [o for o in options if re.fullmatch(rf"i have (?:never|not previously|not) worked (?:at|for) {company}", normalize(o))]
    if len(choices) != 1:
        return None
    return {"value": choices[0], "source": SOURCE, "intent": intent}


def resolve_service_or_survey(label, options, profile, company, *, required=False):
    text = normalize(label)
    rules = profile.get("standing_disclosures", {})
    service = rules.get("military_service", {})
    if service.get("source") == "USER_PROVIDED" and service.get("scope") == "standing" and service.get("ever_served") is False:
        choices = []
        if text in {"military status", "what is your military status", "military service history"}:
            choices = [o for o in options if normalize(o) == "i have never served in the military"]
        elif text in {"have you ever served in the military", "have you previously served in the military"}:
            choices = [o for o in options if normalize(o) == "no"]
        if len(choices) == 1:
            return {"value": choices[0], "source": SOURCE, "intent": "military_service"}
    consent = rules.get("required_demographic_processing_consent", {})
    if required and company and consent.get("source") == "USER_PROVIDED" and consent.get("scope") == "standing" and consent.get("answer") is True:
        employer = re.escape(normalize(company))
        pattern = rf"(?:by checking this box )?i consent to {employer} collecting storing and processing my responses to the demographic (?:data )?surveys(?: above)?"
        if re.fullmatch(pattern, text) and options.count("Yes") == 1:
            return {"value": "Yes", "source": SOURCE, "intent": "required_demographic_processing_consent"}
    return None
