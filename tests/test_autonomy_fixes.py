"""Operator affirmative rule, removed-listing detection, cookie consent and liveness parsing."""
import pytest

from autoapply.affirmative import affirmative
from autoapply.freshness import closed_status
from autoapply.security import classify_message


@pytest.mark.affirmative
@pytest.mark.parametrize("text", [
    "Are you willing to work onsite in Laramie, WY?",
    "Can you confirm this requirement? Verify qualification: Proficiency in Windows and Microsoft Office",
    "Can you confirm this requirement? Verify qualification: Willingness to learn about technology, especially AI",
    "Can you confirm this requirement? Confirm full-program availability: June to August 2027 (10-week program).",
    "Are you able to commute to our office 3 days a week?",
    "Do you have experience with SQL?",
])
def test_affirmative_keywords_answer_yes(text):
    assert affirmative(text)


@pytest.mark.affirmative
@pytest.mark.parametrize("text", [
    "Are you authorized to work in the US?",
    "Will you now or in the future require sponsorship?",
    "Have you ever been convicted of a felony? Please confirm.",
    "Do you have a GPA of 3.5 or higher?",
    "How many years of experience do you have with SQL?",
    "Have you previously worked for this company?",
    "Can you confirm this requirement? Must be a U.S. citizen",
    "Gender",
])
def test_protected_topics_never_auto_yes(text):
    assert not affirmative(text)


def test_rule_disabled_for_legacy_policy_tests():
    assert not affirmative("Are you willing to relocate?")


def test_removed_workday_page_is_closed_not_security():
    text = "The page you are looking for doesn't exist. Search for Jobs"
    assert closed_status(text) == 'REMOVED'
    assert closed_status("This job is no longer available") == 'CLOSED'
    assert closed_status("We are hiring interns") is None
    # The security classifier alone still treats a bare 403 as a security failure.
    assert classify_message("", 403).blocking


def test_workday_api_s22_means_removed(monkeypatch):
    import autoapply.liveness as live
    responses = {
        "/wday/cxs/acme/Careers/job/X/Intern_R1": (403, "", '{"errorCode":"S22","httpStatus":403}'),
        "/wday/cxs/acme/Careers/job/X/Intern_R2": (200, "", '{"jobPostingInfo":{"id":"1","canApply":true}}'),
        "/wday/cxs/acme/Careers/job/X/Intern_R3": (200, "", '{"jobPostingInfo":{"id":"1","canApply":false}}'),
    }
    monkeypatch.setattr(live, "_get", lambda url, timeout, accept='': next(
        v for k, v in responses.items() if url.endswith(k)))
    base = "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/X/"
    assert live._workday(live.urlsplit(base + "Intern_R1"), 1) == 'REMOVED'
    assert live._workday(live.urlsplit(base + "Intern_R2"), 1) == 'OPEN'
    assert live._workday(live.urlsplit(base + "Intern_R3"), 1) == 'CLOSED'


@pytest.mark.browser
async def test_cookie_banner_declined_before_blocked_click(tmp_path):
    from playwright.async_api import async_playwright
    from autoapply.cursor import click_element
    html = """<html><body style="height:1500px">
      <button id=apply onclick="document.body.dataset.applied=1">Apply</button>
      <div id=backdrop style="position:fixed;inset:0;background:rgba(0,0,0,.5)">
        <div class=cookie-banner style="background:#fff;padding:20px">We use cookies to improve your experience.
          <button onclick="document.getElementById('backdrop').remove()">Decline</button>
          <button onclick="document.body.dataset.accepted=1">Accept Cookies</button></div></div></body></html>"""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html)
        await click_element(page, page.locator('#apply'))
        assert await page.evaluate("document.body.dataset.applied") == '1'
        assert await page.evaluate("document.body.dataset.accepted") is None
        await browser.close()


PROFILE = {
    'contact': {'country': 'United States', 'state': 'Virginia', 'postal_code': '22030', 'city': 'Fairfax'},
    'education': {'graduation_date': '2028-05', 'currently_enrolled': True, 'good_academic_standing': True},
    'work_authorization': {'us_authorized': True, 'sponsorship_now': False, 'sponsorship_future': False},
    'citizenship': {'us_citizen': True},
    'application_preferences': {'previous_employer_work_default': False, 'willing_to_work_any_location': True},
    'locations': {},
}


@pytest.mark.parametrize("label,kind,options,expected", [
    ("Country", "combobox", ["United States +1", "Canada +1", "Afghanistan +93"], "United States +1"),
    ("Country of residence", "combobox", ["Afghanistan", "United States", "Canada"], "United States"),
    ("To be eligible for this position, your primary residence must be located in the United States. Which state?",
     "combobox", ["Texas", "Virginia"], "Virginia"),
    ("State", "select", ["TX", "VA"], "VA"),
    ("Please enter the ZIP code of your primary residence", "text", [], "22030"),
    ("End date month", "combobox", ["January", "May"], "May"),
    ("End date year", "number", [], "2028"),
    ("Have you previously worked for Veeam (including as a contractor or intern)?", "combobox", ["Yes", "No"], "No"),
    ("Do you currently own, operate, or provide services to any other business or organization?", "combobox", ["Yes", "No"], "No"),
    ("Are you willing to relocate?", "combobox", ["Yes", "No"], "Yes"),
    ("If yes, please describe the nature of the activity", "textarea", [], "N/A"),
])
def test_profile_rules_answer_common_questions(label, kind, options, expected):
    from autoapply.profile_rules import resolve
    assert resolve(label, kind, options, PROFILE) == expected


def test_profile_rules_never_invent_missing_facts():
    from autoapply.profile_rules import resolve
    assert resolve("ZIP code", "text", [], {'contact': {}}) is None
    assert resolve("Favorite color", "text", [], PROFILE) is None


@pytest.mark.parametrize("requirement,expected", [
    ("Eligibility to work in the United States", True),
    ("Enrollment in a bachelor's or graduate degree program", True),
    ("Must not require visa sponsorship now or in the future", True),
    ("Must be a U.S. citizen", True),
])
def test_requirement_confirmations_use_profile_facts(requirement, expected):
    from autoapply.profile_rules import confirm_requirement
    assert confirm_requirement(requirement, PROFILE) is expected


def test_workable_radio_options_strip_question_prefix():
    label = "Are you authorized to work in the US?"
    options = [label + " YES", label + " NO"]
    stripped = [o[len(label):].strip() for o in options]
    assert stripped == ["YES", "NO"]


def test_source_career_and_veteran_rules():
    from autoapply.profile_rules import resolve
    profile = dict(PROFILE, application_preferences=dict(PROFILE['application_preferences'],
        veteran='not a protected veteran', application_source_answer='Other',
        career_goal='Get a software engineering internship and grow toward a senior engineer role.'),
        education=dict(PROFILE['education'], major='Computer Science', school='Example University'))
    assert resolve('What led you to apply for this opportunity? Select all that apply.', 'multiselect',
                   ['Website', 'Referral', 'Other'], profile) == 'Other'
    assert resolve('Veteran Status', 'combobox', ['I am not a protected veteran', "I don't wish to answer"], profile) == 'I am not a protected veteran'
    plan = resolve('What are your career plans?', 'textarea', [], profile)
    assert plan.startswith('My plan is to get a software engineering internship') and 'Example University' in plan
    assert resolve('What are your career plans?', 'textarea', [], PROFILE) is None  # no stated goal, no essay
