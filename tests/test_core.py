import json
import subprocess
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from autoapply.answers import AnswerResolver, concept, fit_options, validate_answer, written_reuse
from autoapply.archive import archive_application
from autoapply.archive import safe_name
from autoapply.config import Config
from autoapply.control import Controller
from autoapply.database import Database
from autoapply.engine import Engine
from autoapply.jobs import canonical_url, eligibility, freshness, job_identity, location_rank, parse_date, us_location
from autoapply.models import Answer, Question, State
from autoapply.privacy import privacy_check
from autoapply.runtime import ProcessLock
from autoapply.sources import parse_repository


@pytest.mark.parametrize("url,expected", [
    ("https://jobs.lever.co/team/abc/apply?utm_source=x&lever-source=y", "https://jobs.lever.co/team/abc"),
    ("https://example.test/jobs/?gh_jid=123&ref=x#apply", "https://example.test/jobs?gh_jid=123"),
    ("https://example.test/job?id=123&foo=bar", "https://example.test/job?foo=bar&id=123"),
])
def test_canonical(url, expected):
    assert canonical_url(url) == expected


def test_requisition_identity():
    assert job_identity("https://boards.greenhouse.io/acme/jobs/123") == job_identity("https://careers.example.test/?gh_jid=123")
    assert job_identity("https://jobs.lever.co/acme/a") != job_identity("https://jobs.lever.co/acme/b")
    assert job_identity("https://a.wd1.myworkdayjobs.com/en-US/site/job/Boston/Intern_REQ1") != job_identity("https://b.wd1.myworkdayjobs.com/en-US/site/job/Boston/Intern_REQ1")


@pytest.mark.parametrize("location,result", [("Remote", None), ("Remote - United States", True), ("Toronto, Canada", False), ("London, UK", False), ("New York, NY", True), ("San Francisco, CA; Toronto, Canada", None), ("San Jose", True)])
def test_us_locations(location, result):
    assert us_location(location) is result


def test_location_order(config):
    groups = config["locations"]["preferred_groups"]
    assert location_rank("San Francisco, CA", groups) > location_rank("New York, NY", groups) > location_rank("Remote US", groups)


def test_freshness_and_relative_reference():
    reference = datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert parse_date("14d", reference) == "2026-09-06T00:00:00+00:00"
    assert parse_date("Dec 31", reference) == "2025-12-31T00:00:00+00:00"
    assert freshness("2026-09-06", 14, reference.date()) is True
    assert freshness("2026-09-05", 14, reference.date()) is False
    assert freshness("2026-09-21", 14, reference.date()) is None
    assert freshness(None, 14) is None


def test_html_and_markdown_sources():
    html = '<table><tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr><tr><td>Example</td><td>Intern</td><td>NYC</td><td><a href="https://example.test/1">Apply</a></td><td>2d</td></tr><tr><td>↳</td><td>Intern 2</td><td>NYC</td><td><a href="https://example.test/2">Apply</a></td><td>3d</td></tr></table>'
    md = '| Company | Position | Location | Salary | Posting | Age |\n|---|---|---|---|---|---|\n| Example | Intern | NYC | 1 | [Apply](https://example.test/3) | 2d |'
    result = parse_repository(html + "\n" + md, "https://github.com/example/source", datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert len(result) == 3
    assert result[1].company == "Example"
    assert result[0].posted_at == "2026-09-18T00:00:00+00:00"


def test_dedup_sources_and_reposts(config, db, listing):
    first, created = db.ingest(listing, config)
    second, again = db.ingest(replace(listing, url=listing.url + "?utm_source=other", source="another"), config)
    assert created and not again and first == second
    assert len(db.rows("SELECT * FROM job_sources")) == 2
    third, created = db.ingest(replace(listing, url="https://jobs.lever.co/example/new"), config)
    assert third != first and created
    assert len(db.rows("SELECT * FROM applications")) == 2


def test_older_source_evidence_does_not_rejuvenate_job(config, db, listing):
    db.ingest(listing, config)
    earlier = (date.today() - timedelta(days=35)).isoformat()
    db.ingest(replace(listing, source="older_source", posted_at=earlier), config)
    assert db.application(1)["listing_status"] == "STALE"
    assert db.claim() is None
    db.ingest(replace(listing, source="newer_source"), config)
    assert db.application(1)["posted_at"].startswith(earlier)
    assert db.application(1)["listing_status"] == "STALE"


def test_old_and_unknown_preserved(config, db, listing):
    db.ingest(replace(listing, posted_at=(date.today() - timedelta(days=31)).isoformat()), config)
    db.ingest(replace(listing, url="https://example.test/unknown", posted_at=None), config)
    assert db.rows("SELECT * FROM applications") == []
    assert len(db.rows("SELECT * FROM listing_observations")) == 2
    assert len(Controller(config, db).pending()) == 0
    assert db.claim() is None


def test_queue_recovery_and_submission_boundary(config, db, listing):
    db.ingest(listing, config)
    app = db.claim()
    assert app["status"] == "CHECKING"
    db.recover()
    assert db.application(app["id"])["status"] == "RETRY"
    app = db.claim()
    db.transition(app["id"], State.SUBMITTING, submit_intent_at=datetime.now(timezone.utc).isoformat())
    db.recover()
    assert db.application(app["id"])["status"] == "MANUAL_REVIEW"
    with pytest.raises(ValueError):
        db.retry(app["id"])
    with pytest.raises(ValueError):
        db.transition(app["id"], State.SUBMITTED)
    Controller(config, db).reconcile(app["id"], True, "Employer confirmation 123")
    with pytest.raises(ValueError):
        db.retry(app["id"])


async def test_daily_ceiling_counts_uncertain_submission_intents(config, db, listing):
    config.data["processing"]["max_applications_per_day"] = 1
    db.ingest(listing, config)
    first = db.claim()
    db.transition(first["id"], State.SUBMITTING, submit_intent_at=datetime.now(timezone.utc).isoformat())
    db.recover()
    db.ingest(replace(listing, url="https://example.test/another"), config)
    engine = Engine(config, db)
    assert not await engine.process_one()
    assert db.application(2)["status"] == "QUEUED"
    assert engine.browser.context is None


def test_answers_never_guess_or_match_negation(config, db, listing):
    db.ingest(listing, config)
    app = db.claim()
    resolver = AnswerResolver(config, db)
    assert resolver.resolve(Question("1", "First Name", required=True), app).value == "Test"
    assert resolver.resolve(Question("2", "Will you now or in the future require sponsorship?", options=["Yes", "No"]), app).value == "No"
    assert resolver.resolve(Question("3", "Are you NOT authorized to work in the United States?", required=True), app) is None
    assert resolver.resolve(Question("4", "Are you a US citizen and do you have a clearance?"), app) is None
    assert resolver.resolve(Question("5", "Have you ever been convicted of a crime?"), app) is None
    assert resolver.resolve(Question("6", "Expected compensation", "number", True), app) is None
    assert fit_options("No", ["No, but I require sponsorship later", "Yes"]) is None


def test_explicit_office_and_discovery_preferences(config, db, listing):
    import yaml
    profile = config.profile
    profile['application_preferences'] = {'office_five_days': True, 'discovery_source': 'Job board'}
    (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    db.ingest(listing, config)
    app = db.claim()
    resolver = AnswerResolver(config, db)
    q = Question('office', 'Are you able to come into the San Francisco office 5 days a week?', 'radio', True, ['Yes','No'])
    assert resolver.resolve(q,app).value == 'Yes'
    assert resolver.resolve(replace(q,label='Are you able to come into the office 5 days a week and relocate at your own expense?'),app) is None
    q = Question('source','How did you first learn about us?','radio',True,['University Job Board','LinkedIn','Other'])
    assert resolver.resolve(q,app).value == 'Other'
    assert resolver.resolve(replace(q,options=['University Job Board','Online job board','Other']),app).value == 'Online job board'
    assert resolver.resolve(replace(q,options=['University Job Board','LinkedIn']),app) is None


def test_any_location_willingness_does_not_supply_qualifications(config, db, listing):
    db.ingest(listing, config)
    app = db.claim()
    resolver = AnswerResolver(config, db)
    q = Question('office', 'Are you willing to work four days per week in our San Francisco office?', 'radio', True, ['Yes','No'])
    assert resolver.resolve(q, app) is None
    db.set_setting('verified_fact:application_preferences.willing_to_work_any_location', {'value':'Yes','source':'USER_PROVIDED'})
    assert resolver.resolve(q, app).value == 'Yes'
    for label in ['Are you willing to work in our office and relocate at your own expense?',
                  'Are you legally authorized to work in the country where the job is located?',
                  'Are you unwilling to work in our London office?']:
        assert resolver.resolve(replace(q, label=label), app) is None


def test_claim_explicit_application_never_falls_back(config, db, listing):
    db.ingest(listing, config)
    db.ingest(replace(listing,url='https://example.test/second'),config)
    assert db.claim(999) is None
    assert db.claim(2)['id'] == 2
    assert db.claim(2) is None
    assert db.application(1)['status'] == 'QUEUED'


def test_controlled_claim_does_not_run_global_cleanup(config, db, listing, monkeypatch):
    db.ingest(listing, config)
    db.set_setting('controlled_application_id', 1)
    def forbidden():
        raise AssertionError('Controlled claim attempted unrelated maintenance')
    monkeypatch.setattr(db, 'cleanup_stale_listings', forbidden)
    assert db.claim(1)['id'] == 1


def test_out_of_order_answers_resume_only_own_application(config, db, listing):
    db.ingest(listing, config)
    db.ingest(replace(listing, url="https://example.test/second"), config)
    a, b = db.claim(), db.claim()
    q1 = db.question(a["id"], Question("1", "Unknown A", required=True, scope=f"application:{a['id']}"))
    q2 = db.question(b["id"], Question("2", "Unknown B", required=True, scope=f"application:{b['id']}"))
    db.transition(a["id"], State.NEEDS_INPUT)
    db.transition(b["id"], State.NEEDS_INPUT)
    control = Controller(config, db)
    control.answer(q2["id"], "Verified B")
    assert db.application(a["id"])["status"] == "NEEDS_INPUT"
    assert db.application(b["id"])["status"] == "RETRY"
    control.answer(q1["id"], "Verified A")
    assert db.application(a["id"])["status"] == "RETRY"
    with pytest.raises(ValueError):
        control.answer(q1["id"], "Again")


def test_eligibility_required_preferred(config):
    base = {"title": "Software Intern", "location": "New York, NY", "description": "Undergraduate internship.\nPreferred qualifications\nGPA of 4.0"}
    assert eligibility(base, config.profile).eligible is True
    assert eligibility(dict(base, description="Minimum GPA of 3.5 required"), config.profile).eligible is None
    assert eligibility(dict(base, title="Software Intern PhD"), config.profile).eligible is False
    assert eligibility(dict(base, location="Toronto, Canada"), config.profile).eligible is False
    assert eligibility(dict(base, description="Must have an active security clearance"), config.profile).eligible is None
    assert eligibility(dict(base, description="Must be a U.S. citizen"), config.profile).eligible is True


@pytest.mark.parametrize('description', [
    'Data Science Institute Undergraduate Student Intern - Summer 2027',
    'If you are starting a graduate degree program in Fall of 2027 you must apply for the Graduate position.',
])
def test_degree_level_year_is_not_graduation_window(config, description):
    profile = config.profile
    profile['education']['graduation_date'] = '2028-05'
    result = eligibility({'title': 'Software Intern', 'location': 'Livermore, CA',
                          'description': description}, profile)
    assert 'Graduation year outside stated window' not in result.reasons


def test_explicit_graduation_window_still_rejects(config):
    profile = config.profile
    profile['education']['graduation_date'] = '2028-05'
    result = eligibility({'title': 'Software Intern', 'location': 'Livermore, CA',
                          'description': 'Must graduate in 2027.'}, profile)
    assert result.eligible is False
    assert 'Graduation year outside stated window' in result.reasons


def test_basic_programming_requirement_needs_verified_experience(config):
    base = {"title": "Software Intern", "location": "New York, NY", "description": "Previous programming experience is a must."}
    profile = config.profile
    assert eligibility(base, profile).eligible is None
    profile["qualifications"] = {"programming_experience": True}
    assert eligibility(base, profile).eligible is True
    optional_stack = " While we use Ruby/Rails, Typescript, React, Redux, Android, iOS, and Python, we care more about engineering skills than knowledge of specific languages or frameworks"
    assert eligibility(dict(base, description=base["description"] + optional_stack), profile).eligible is True
    for extra in [" Must have five years of experience.", " Must have an active security clearance.", " Python proficiency required."]:
        assert eligibility(dict(base, description=base["description"] + extra), profile).eligible is None


def test_question_changed_options_invalidates_saved_answer(config, db, listing):
    db.ingest(listing, config)
    app = db.claim()
    q = Question("same", "Question", "select", True, ["Yes", "No"])
    row = db.question(app["id"], q)
    db.save_answer(row["id"], Answer("Yes", "user"))
    row = db.question(app["id"], replace(q, options=["Yes, I have a clearance", "No"]))
    assert row["status"] == "PENDING" and row["answer"] is None


def test_archive_and_config(config, db, listing):
    db.ingest(listing, config)
    app = db.claim()
    row = db.question(app["id"], Question("writing", "A written response", "textarea", True))
    db.set_setting(f"draft:{row['id']}", "A draft requiring review")
    folder = archive_application(config, db, app["id"])
    assert json.loads((folder / "application.json").read_text())["id"] == app["id"]
    assert folder.is_relative_to(config.private)
    assert json.loads((folder / "generated_responses.json").read_text())[0]["draft"] == "A draft requiring review"
    assert (folder / "status.json").exists()
    with pytest.raises(ValueError):
        Config(config.root, {"application": {"auto_submit": "false"}})


def test_windows_archive_names(config, db, listing):
    assert not safe_name("x" * 69 + " " + "rest").endswith(" ")
    db.ingest(replace(listing, title="Intern " + "a long title " * 20, company="CON"), config)
    folder = archive_application(config, db, 1)
    assert folder.name.startswith("1_CON_") and len(folder.name) < 100


def test_process_lock(config):
    with ProcessLock(config.private / "worker.lock"):
        with pytest.raises(RuntimeError):
            with ProcessLock(config.private / "worker.lock"):
                pass


def test_privacy_detects_staged_private_and_secret(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    base = ["git", "-c", "safe.directory=" + tmp_path.as_posix(), "-C", str(tmp_path)]
    (tmp_path / ".env").write_text("DISCORD_USER_ID=" + "123456789012345678")
    subprocess.run(base + ["add", ".env"], capture_output=True, check=True)
    findings = privacy_check(tmp_path)
    assert any("Private file" in f for f in findings)
    assert any("secret" in f for f in findings)
