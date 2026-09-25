import pytest

from autoapply.disclosures import FACTS, disclosure_intent, resolve_disclosure


def profile():
    return {"standing_disclosures": {k: dict(source="USER_PROVIDED", scope="standing", answer=False, fact=v) for k, v in FACTS.items()}}


@pytest.mark.parametrize("label,intent", [
    ("Have you ever worked for Robinhood as an employee, intern or contractor? Note that providing false or misleading information may result in disqualification from the hiring process.", "prior_employment"),
    ("Have you previously been employed by Acme or any of its affiliates?", "prior_employment"),
    ("Have you ever worked at Acme or its related companies as a contractor?", "prior_employment"),
    ("Do you have any personal relationship, financial interest, employment relationship, outside business activity, family business relationship, family relationship, ownership interest, or other outside relationship that could create, appear to create, or materially affect a conflict with Acme?", "conflict_of_interest"),
    ("Do you have any potential conflicts of interest with Acme?", "conflict_of_interest"),
    ("Do you or an immediate family member have any relevant connections to government officials, elected officials, government agencies or state-owned entities that could create compliance, ethics, anti-bribery, procurement or employment concerns?", "government_connections"),
    ("Are you related to a government official?", "government_connections"),
])
def test_equivalent_clauses(label, intent):
    company = "Robinhood" if "Robinhood" in label else "Acme"
    assert disclosure_intent(label, company) == intent
    assert resolve_disclosure(label, ["Yes", "No"], profile(), company)["value"] == "No"


@pytest.mark.parametrize("label", [
    "Have you ever worked for a government agency?",
    "Have you ever worked for Acme or been investigated for fraud?",
    "Have you ever worked for Acme's competitor?",
    "Have you ever worked for Acme in a regulated capacity under UK law?",
    "Do you have any potential conflicts of interest with Acme under Section 5?",
    "Do you have any potential conflicts of interest with Acme or a criminal conviction?",
    "Do you own more than 5% of a publicly traded company?",
    "Do you have intellectual property that you wish to retain?",
    "Are you a politically exposed person under local law?",
    "Are you related to a government official as defined by the FCPA?",
    "Are you related to a government official or required to make any other regulatory disclosure?",
    "Do you or any family member have government connections?",
    "Do you receive government benefits?",
    "Will you comply with the conflict of interest policy?",
    "Have you had a conflict with your previous employer?",
    "Do you have financial reporting obligations?",
])
def test_abstains_on_different_facts_or_qualifiers(label):
    assert resolve_disclosure(label, ["Yes", "No"], profile(), "Acme") is None


def test_provenance_options_and_employer_identity():
    label = "Have you ever worked for Acme?"
    assert resolve_disclosure(label, ["Yes", "No"], {}, "Acme") is None
    p = profile()
    p["standing_disclosures"]["prior_employment"]["answer"] = True
    assert resolve_disclosure(label, ["Yes", "No"], p, "Acme") is None
    assert resolve_disclosure(label, ["Yes", "No"], profile(), "Different Company") is None
    assert resolve_disclosure(label, ["I have never worked at Acme"], profile(), "Acme")["value"] == "I have never worked at Acme"
    assert resolve_disclosure(label, ["No prior employment or criminal convictions"], profile(), "Acme") is None


def test_military_and_survey_boundaries():
    from autoapply.disclosures import resolve_service_or_survey
    p = {"standing_disclosures": {
        "military_service": dict(source="USER_PROVIDED", scope="standing", ever_served=False),
        "required_demographic_processing_consent": dict(source="USER_PROVIDED", scope="standing", answer=True),
    }}
    assert resolve_service_or_survey("What is your military status?", ["I have never served in the military"], p, "Acme")
    assert resolve_service_or_survey("Have you ever served in the military?", ["Yes", "No"], p, "Acme")["value"] == "No"
    assert not resolve_service_or_survey("Have you ever served in the military or worked for a defense contractor?", ["Yes", "No"], p, "Acme")
    label = "By checking this box, I consent to Acme collecting, storing, and processing my responses to the demographic data surveys above."
    assert resolve_service_or_survey(label, ["Yes", "No"], p, "Acme", required=True)["value"] == "Yes"
    assert not resolve_service_or_survey(label, ["Yes", "No"], p, "Acme", required=False)
    assert not resolve_service_or_survey(label + " I waive my legal rights.", ["Yes", "No"], p, "Acme", required=True)
    assert not resolve_service_or_survey(label.replace("demographic data surveys", "medical tests"), ["Yes", "No"], p, "Acme", required=True)


def test_resolver_provenance_and_explicit_exception(config, db, listing):
    import yaml
    from autoapply.answers import AnswerResolver
    from autoapply.models import Answer, Question
    from autoapply.disclosures import SOURCE

    (config.private / "profile.yaml").write_text(yaml.safe_dump(config.profile | profile()))
    app_id, _ = db.ingest(listing, config)
    app = db.application(app_id)
    q = Question("employment", "Have you ever worked for Example Company?", "combobox", True, ["Yes", "No"])
    resolver = AnswerResolver(config, db)
    answer = resolver.resolve(q, app)
    assert answer.value == "No" and answer.source == SOURCE
    row = db.question(app_id, q)
    db.save_answer(row["id"], Answer("Yes", "user_confirmed"), verified=True)
    assert resolver.resolve(q, app).value == "Yes"
    essay = Question("essay", q.label, "textarea", True)
    assert resolver.resolve(essay, app) is None
