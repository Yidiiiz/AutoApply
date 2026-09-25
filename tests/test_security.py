import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

from autoapply.browser import Browser
from autoapply.config import ROOT
from autoapply.database import Database, SCHEMA
from autoapply.engine import Engine
from autoapply.models import State, now
from autoapply.providers import DOMAINS, detect_ats
from autoapply.retry import ErrorCategory as E, RetryPolicy, SiteError
from autoapply.security import SecurityDetector, classify_message, confirmation_evidence, safe_text, safe_url


@pytest.mark.parametrize('text,confirmed', [
    ("Thank you! Your application was successfully submitted. We'll contact you if there are next steps.", True),
    ("Your application was successfully submitted. No action is required.", True),
    ("If your application was successfully submitted, you will receive an email.", False),
    ("Your application was not successfully submitted. Try again.", False),
    ("Example: your application was successfully submitted.", False),
    ("When your application was successfully submitted, was a receipt shown?", False),
])
def test_confirmation_sentence_scope(text,confirmed):
    assert bool(confirmation_evidence({'text':text})) is confirmed


@pytest.fixture
async def security_browser(config, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "data/private/playwright"))
    config.data["application"]["confirmation_timeout_seconds"] = 1
    browser = Browser(config)
    await browser.start()
    # Provider markers must never load real providers in tests.
    await browser.context.route("**/*", lambda route: route.abort())
    yield browser
    await browser.close()


@pytest.mark.parametrize("provider,domains", DOMAINS.items())
def test_ats_host_recognition(provider, domains):
    assert detect_ats("https://jobs." + domains[0] + "/apply").provider == provider
    assert detect_ats("https://evil" + domains[0] + "/apply").provider == "UNKNOWN"


def test_ats_embedded_and_unknown():
    assert detect_ats("https://company.test", ["https://boards.greenhouse.io/embed/job_app"]).provider == "greenhouse"
    assert detect_ats("https://company.test", has_form=True).provider == "custom"
    assert detect_ats("https://company.test").provider == "UNKNOWN"


@pytest.mark.parametrize("message,state", [
    ("Your application was flagged as possible spam", "SPAM_REJECTED"),
    ("FLAGGED AS SPAM", "SPAM_REJECTED"), ("spam detected", "SPAM_REJECTED"),
    ("Verify you are human", "INTERACTIVE_CHALLENGE"), ("Are you a robot?", "INTERACTIVE_CHALLENGE"),
    ("Security verification", "INTERACTIVE_CHALLENGE"),
    ("Automated activity", "AUTOMATION_REJECTED"), ("Bot detected", "AUTOMATION_REJECTED"),
    ("Too many requests", "RATE_LIMITED"), ("Try again later", "RATE_LIMITED"),
    ("Verify your email", "EMAIL_VERIFICATION"), ("Verify phone", "PHONE_VERIFICATION"),
    ("Government ID", "IDENTITY_VERIFICATION"), ("Selfie verification", "IDENTITY_VERIFICATION"),
    ("Fraud review", "FRAUD_REVIEW"), ("Suspicious activity", "UNKNOWN_SECURITY_FAILURE"),
    ("Submission blocked", "UNKNOWN_SECURITY_FAILURE"), ("Application rejected", "UNKNOWN_SECURITY_FAILURE"),
])
def test_security_messages(message, state):
    assert classify_message(message).state == state


@pytest.mark.parametrize("html,provider,state", [
    ('<div class="g-recaptcha" style="width:300px;height:80px"></div>', "Google reCAPTCHA", "PASSIVE_PROTECTION_DETECTED"),
    ('<script src="https://www.google.com/recaptcha/api.js"></script>', "Google reCAPTCHA", "PASSIVE_PROTECTION_DETECTED"),
    ('<div class="grecaptcha-badge" style="width:256px;height:60px"></div><p>Protected by reCAPTCHA</p>', "Google reCAPTCHA", "PASSIVE_PROTECTION_DETECTED"),
    ('<textarea name="h-captcha-response" hidden>SECRET</textarea><div class="h-captcha" style="width:300px;height:80px"></div>', "hCaptcha", "INTERACTIVE_CHALLENGE"),
    ('<div class="cf-turnstile" style="width:300px;height:80px"></div>', "Cloudflare Turnstile", "INTERACTIVE_CHALLENGE"),
    ('<h1>Checking your browser</h1><div id="cf-chl-widget" style="width:300px;height:100px"></div>', "Cloudflare", "INTERACTIVE_CHALLENGE"),
    ('<iframe title="FunCaptcha" src="https://client-api.arkoselabs.com/challenge"></iframe>', "Arkose Labs", "INTERACTIVE_CHALLENGE"),
    ('<iframe src="https://geo.captcha-delivery.com/datadome"></iframe>', "DataDome", "INTERACTIVE_CHALLENGE"),
    ('<iframe src="https://inquiry.withpersona.com/verify"></iframe>', "Persona", "IDENTITY_VERIFICATION"),
    ('<h1>CLEAR identity verification</h1>', "CLEAR", "IDENTITY_VERIFICATION"),
    ('<div role="alert">Verify you are human</div>', "UNKNOWN", "INTERACTIVE_CHALLENGE"),
    ('<div role="alert">Submission blocked</div>', "UNKNOWN", "UNKNOWN_SECURITY_FAILURE"),
])
async def test_provider_dom_detection(security_browser, html, provider, state):
    page = await security_browser.new_page()
    await page.set_content("<body>" + html + "</body>")
    result, snapshot = await SecurityDetector().detect(page)
    assert result.provider == provider
    assert result.state == state
    assert "SECRET" not in snapshot["text"]


async def test_job_named_persona_and_technical_challenge_are_not_security(security_browser):
    page = await security_browser.new_page()
    await page.set_content('<h1>Persona</h1><h2>A technical challenge</h2><p>Build fraud detection software.</p><form><textarea>Thank you for applying</textarea></form>')
    result, snapshot = await SecurityDetector().detect(page)
    assert not result.blocking and result.provider == "UNKNOWN"
    assert confirmation_evidence(snapshot) is None


@pytest.mark.parametrize('html,blocking', [
    ('<div class="grecaptcha-badge"><div class="grecaptcha-logo" style="width:256px;height:64px"><iframe title="reCAPTCHA" src="https://www.recaptcha.net/recaptcha/enterprise/anchor?size=invisible" width="256" height="60"></iframe></div><textarea name="g-recaptcha-response" hidden>SECRET</textarea></div>', False),
    ('<iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/anchor?size=invisible" width="256" height="60"></iframe>', False),
    ('<div class="g-recaptcha" data-size="invisible" style="width:300px;height:80px"></div>', False),
    ('<div hidden><iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/bframe"></iframe></div>', False),
    ('<div style="opacity:0"><iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/bframe"></iframe></div>', False),
    ('<iframe style="visibility:hidden" title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/bframe"></iframe>', False),
    ('<iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/anchor?size=normal" width="304" height="78"></iframe>', True),
    ('<iframe title="reCAPTCHA challenge" src="https://www.google.com/recaptcha/api2/bframe" width="400" height="500"></iframe>', True),
    ('<div class="grecaptcha-badge"><iframe title="reCAPTCHA challenge" src="https://www.google.com/recaptcha/enterprise/bframe" width="400" height="500"></iframe></div>', True),
    ('<div role="dialog">Complete the reCAPTCHA</div>', True),
    ('<div class="grecaptcha-badge"><div class="grecaptcha-logo" style="width:256px;height:64px"></div></div><div role="alert">Verify you are human</div>', True),
])
async def test_recaptcha_passive_integration_and_real_challenges(security_browser, html, blocking):
    page = await security_browser.new_page()
    await page.set_content('<script src="https://www.recaptcha.net/recaptcha/enterprise.js"></script><form><input type="email" required><input type="file"><button>Submit</button></form>' + html)
    result, snapshot = await SecurityDetector().detect(page)
    assert result.blocking is blocking
    assert result.state == ('INTERACTIVE_CHALLENGE' if blocking else 'PASSIVE_PROTECTION_DETECTED')
    assert snapshot['has_form'] and not snapshot['disabled']
    assert 'SECRET' not in snapshot['text']


@pytest.mark.parametrize("status,state", [(429, "RATE_LIMITED"), (403, "UNKNOWN_SECURITY_FAILURE")])
async def test_http_security(security_browser, status, state):
    page = await security_browser.new_page()
    await page.set_content('<body>Request could not be completed</body>')
    result, _ = await SecurityDetector().detect(page, {"status": status})
    assert result.state == state


def test_retry_categories_bounded_and_no_post_intent_retry():
    policy = RetryPolicy()
    for category in E:
        decision = policy.decide(category, 1, 3)
        assert decision.allowed == (category in {E.NETWORK_ERROR, E.SITE_ERROR, E.RATE_LIMIT})
        assert not policy.decide(category, 1, 3, submitted_intent=True).allowed
        assert not policy.decide(category, 4, 3).allowed
    assert 60 <= policy.decide(E.NETWORK_ERROR, 1, 3).delay <= 65
    assert policy.decide(E.SITE_ERROR, 1000, 1000).delay <= 1800


@pytest.mark.parametrize("text,confirmed", [
    ("Thank you for applying", True), ("Application submitted", True),
    ("Your application has been received", True), ("Application confirmation number: ABC-12345", True),
    ("Your application has not been submitted", False),
    ("If successful, you will see Thank you for applying", False),
    ("When your application has been submitted we will email you", False),
    ("We could not display application submitted", False), ("Submit clicked", False),
])
def test_confirmation_requires_affirmative_evidence(text, confirmed):
    assert bool(confirmation_evidence({"text": text})) == confirmed


def test_diagnostics_redact_credentials():
    assert safe_url("https://u:secret@company.test/verify/secret?token=abcd#secret") == "https://company.test/[redacted-verification-path]"
    assert "secret" not in safe_text("token=secret password=secret verification code 123456")
    assert "123456" not in safe_text("verification code 123456")


def test_existing_v1_database_migrates_without_losing_history(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO jobs(id,identity_key,company,title,location,canonical_url,discovered_at,status) VALUES (1,'key','A','Intern','NY','https://a.test/job','date','SUBMITTED')")
    conn.execute("INSERT INTO applications(id,job_id,status,updated_at,confirmation_text,submit_intent_at) VALUES (1,1,'SUBMITTED','date','Thank you for applying','date')")
    conn.execute("PRAGMA user_version=1")
    conn.commit(); conn.close()
    db = Database(path)
    app = db.application(1)
    assert app["application_state"] == "SUBMITTED" and app["submission_confirmation_seen"]
    assert app["confirmation_text"] == "Thank you for applying" and not app["retry_allowed"]
    db.close()
    db = Database(path)
    assert db.application(1)["confirmation_text"] == "Thank you for applying"
    assert db.one("PRAGMA user_version")["user_version"] == 2
    db.close()


async def setup_submission(config, db, listing, browser, html):
    db.ingest(listing, config)
    db.claim()
    db.transition(1, State.SUBMITTING, submit_intent_at=now())
    engine = Engine(config, db, browser)
    page = await browser.new_page()
    await page.set_content(html)
    return engine, page


async def test_spam_preserves_tab_and_disables_retries(config, db, listing, security_browser):
    engine, page = await setup_submission(config, db, listing, security_browser, '<input value="filled"><div role="alert">Your application was flagged as possible spam</div>')
    await engine.post_submit(1, page)
    app = db.application(1)
    assert app["security_state"] == "SPAM_REJECTED" and app["application_state"] == "MANUAL_REQUIRED"
    assert not app["retry_allowed"] and not app["submission_confirmation_seen"]
    assert not page.is_closed() and await page.locator('input').input_value() == "filled"
    with pytest.raises(ValueError):
        db.retry(1)
    with pytest.raises(ValueError):
        engine.control.reconcile(1, False, "I checked")
    notification = json.loads(db.rows('SELECT payload FROM notifications')[-1]["payload"])["message"]
    assert "APPLICATION NOT YET CONFIRMED" in notification and "preserved" in notification
    assert await engine.resume_manual(1) is False
    assert db.application(1)["security_state"] == "SPAM_REJECTED"


async def test_handoff_resumes_same_tab_after_manual_success(config, db, listing, security_browser):
    engine, page = await setup_submission(config, db, listing, security_browser, '<h1>Verify you are human</h1>')
    await engine.post_submit(1, page)
    await page.set_content('<h1>Thank you for applying</h1>')
    engine.control.command('resume-manual 1')
    await engine.service_manual_requests()
    assert db.application(1)["status"] == "SUBMITTED"
    assert not db.application(1)["manual_action_required"]
    assert not engine.handoff.pages
    assert not page.is_closed()


async def test_no_confirmation_stays_unknown_and_cannot_resubmit(config, db, listing, security_browser):
    engine, page = await setup_submission(config, db, listing, security_browser, '<h1>Please wait</h1><button>Submit</button>')
    await engine.post_submit(1, page)
    assert db.application(1)["application_state"] == "UNKNOWN"
    assert not db.application(1)["retry_allowed"]
    assert not await engine.resume_manual(1)
    assert not await engine.process_one(1)


async def test_validation_after_click_keeps_form(config, db, listing, security_browser):
    engine, page = await setup_submission(config, db, listing, security_browser, '<label>Email<input type="email" required></label><p role="alert">Please correct invalid email</p>')
    await engine.post_submit(1, page)
    app = db.application(1)
    assert app["error_category"] == "FORM_VALIDATION_ERROR" and app["application_state"] == "FILLING"
    assert not page.is_closed() and not app["retry_allowed"]


@pytest.mark.parametrize("outcome", ["FAILED", "SKIPPED", "PASSED"])
async def test_confirmed_submission_then_persona_keeps_submission(config, db, listing, security_browser, outcome):
    engine, page = await setup_submission(config, db, listing, security_browser, '<h1>Thank you for applying</h1><h2>Verify your identity</h2><iframe src="https://inquiry.withpersona.com/verify"></iframe>')
    await engine.post_submit(1, page)
    app = db.application(1)
    assert app["status"] == "SUBMITTED" and app["verification_state"] == "PENDING"
    assert app["security_provider"] == "Persona" and app["manual_action_required"]
    assert app["screenshot_path"] is None  # Never capture an identity verification session.
    notification = json.loads(db.rows('SELECT payload FROM notifications')[-1]["payload"])["message"]
    assert "APPLICATION ALREADY SUBMITTED" in notification and "POST-SUBMISSION VERIFICATION REQUIRED" in notification
    await engine.resume_manual(1, outcome)
    assert db.application(1)["application_state"] == "SUBMITTED"
    assert db.application(1)["verification_state"] == outcome


async def test_delayed_persona_after_success_is_observed(config, db, listing, security_browser):
    engine, page = await setup_submission(config, db, listing, security_browser, '<h1>Thank you for applying</h1>')
    async def redirect():
        await asyncio.sleep(.3)
        await page.set_content('<h1>Verify your identity</h1><iframe src="https://inquiry.withpersona.com/verify"></iframe>')
    task = asyncio.create_task(redirect())
    await engine.post_submit(1, page)
    await task
    assert db.application(1)["submission_confirmation_seen"]
    assert db.application(1)["verification_state"] == "PENDING"


def test_duplicate_history_blocks_alternate_url(config, db, listing):
    db.ingest(listing, config)
    db.claim()
    db.transition(1, State.SUBMITTED, confirmation_text="Thank you for applying")
    db.ingest(replace(listing, url='https://jobs.lever.co/example/alternate-id'), config)
    assert db.submission_conflict(2)["id"] == 1


@pytest.mark.parametrize("category", [E.NETWORK_ERROR, E.SITE_ERROR, E.RATE_LIMIT])
def test_retryable_failures_before_click_only(config, db, listing, category):
    db.ingest(listing, config)
    db.claim()
    db.fail(1, "temporary error", 3, category)
    assert db.application(1)["status"] == "RETRY" and db.application(1)["retry_at"]
    db.transition(1, State.SUBMITTING, submit_intent_at=now())
    db.fail(1, "temporary error", 3, category)
    assert db.application(1)["status"] == "MANUAL_REVIEW" and not db.application(1)["retry_allowed"]


async def test_closed_session_resume_never_restarts(config, db, listing, security_browser):
    engine, page = await setup_submission(config, db, listing, security_browser, '<h1>Security check</h1>')
    await engine.post_submit(1, page)
    await page.close()
    assert not await engine.resume_manual(1)
    assert not db.application(1)["session_preserved"] and not db.application(1)["retry_allowed"]


async def test_rate_limited_before_submit_uses_backoff(config, db, listing, security_browser):
    db.ingest(listing, config); db.claim()
    engine = Engine(config, db, security_browser)
    page = await security_browser.new_page()
    await page.set_content('<h1>Too Many Requests</h1>')
    assert await engine.security_gate(1, page)
    app = db.application(1)
    assert app["application_state"] == "RATE_LIMITED" and app["retry_allowed"] and app["retry_at"]


@pytest.mark.parametrize('error,category', [(TimeoutError(), 'NETWORK_ERROR'), (SiteError(), 'SITE_ERROR')])
async def test_engine_timeout_and_server_error_use_policy(config, db, listing, security_browser, monkeypatch, error, category):
    db.ingest(listing, config)
    engine = Engine(config, db, security_browser)
    async def fail(page, url):
        await page.set_content('<body>Temporary site problem</body>')
        raise error
    monkeypatch.setattr(security_browser, 'navigate', fail)
    await engine.process_one(1)
    assert db.application(1)['status'] == 'RETRY'
    assert db.application(1)['error_category'] == category


async def test_unknown_iframe_and_dialog_are_manual(security_browser):
    page = await security_browser.new_page()
    await page.set_content('<iframe title="Unknown security challenge"></iframe>')
    result, _ = await SecurityDetector().detect(page)
    assert result.blocking and result.provider == 'UNKNOWN'
    result, _ = await SecurityDetector().detect(page, {'dialog_open': True, 'dialogs': ['Unresolved browser dialog']})
    assert result.state == 'UNKNOWN_SECURITY_FAILURE'


@pytest.mark.parametrize('execution_blocked', [False, True])
async def test_controlled_navigation_failure_preserves_browser(config, db, listing, security_browser, monkeypatch, execution_blocked):
    db.ingest(listing, config)
    db.set_setting('controlled_application_id', 1)
    engine = Engine(config, db, security_browser)
    async def fail(page, url):
        await page.set_content('<body>Connection unavailable</body>')
        security_browser.observation(page)['execution_blocked'] = execution_blocked
        raise TimeoutError()
    monkeypatch.setattr(security_browser, 'navigate', fail)
    await engine.process_one(1)
    app = db.application(1)
    assert app['status'] == 'MANUAL_REVIEW'
    assert app['session_preserved'] and not app['retry_allowed']
    assert app['error_category'] == ('EXECUTION_APPROVAL_BLOCKED' if execution_blocked else 'NETWORK_ERROR')
    assert not engine.handoff.pages[1].is_closed()
    assert not app['submit_intent_at']


async def test_presubmit_passive_widget_does_not_fail(security_browser):
    from autoapply.applications import GenericApplicationAdapter
    from autoapply.security import SubmissionClassifier
    page = await security_browser.new_page()
    await page.set_content('<form><label>Email<input type="email" required value="a@example.test"></label><button>Submit</button></form><p>Protected by reCAPTCHA</p>')
    readiness, issues = await SubmissionClassifier().pre_submit(page, GenericApplicationAdapter(page))
    assert readiness == 'PASSIVE_PROTECTION_PRESENT' and not issues
    await page.locator('button').evaluate('e => e.disabled=true')
    readiness, issues = await SubmissionClassifier().pre_submit(page, GenericApplicationAdapter(page))
    assert readiness == 'VALIDATION_ERROR' and 'disabled' in issues[0]


async def test_cleared_spam_banner_does_not_enable_automatic_submission(config, db, listing, security_browser):
    db.ingest(listing, config); db.claim()
    engine = Engine(config, db, security_browser)
    page = await security_browser.new_page()
    await page.set_content('<div role="alert">Flagged as possible spam</div>')
    await engine.security_gate(1, page)
    await page.set_content('<button>Submit</button>')
    assert not await engine.resume_manual(1)
    assert not await engine.resume_manual(1)
    assert not db.application(1)['retry_allowed']
    assert db.application(1)['submit_intent_at'] is None


def test_recovery_keeps_manual_hold_without_claiming_live_session(config, db, listing):
    db.ingest(listing, config); db.claim()
    db.transition(1, State.SUBMITTING, submit_intent_at=now())
    db.update_security(1, session_preserved=1)
    db.recover()
    app = db.application(1)
    assert app['application_state'] == 'UNKNOWN' and app['manual_action_required']
    assert not app['session_preserved'] and not app['retry_allowed']


@pytest.mark.parametrize('earlier_state', ['MANUAL_REVIEW', 'SUBMITTING'])
def test_unresolved_duplicate_protects_new_alias(config, db, listing, earlier_state):
    db.ingest(listing, config); db.claim()
    db.transition(1, earlier_state)
    db.ingest(replace(listing, url='https://jobs.lever.co/example/alternate-id'), config)
    assert db.submission_conflict(2)['id'] == 1


async def test_response_error_message_without_visible_banner(security_browser):
    body = json.dumps({'error': 'Your application was flagged as possible spam', 'token': 'never persist this'})
    async def handle(route):
        if route.request.url.endswith('/submit'):
            await route.fulfill(status=200, content_type='application/json', headers={'Content-Length': str(len(body))}, body=body)
        else:
            await route.fulfill(status=200, content_type='text/html', body='<body>Application form</body>')
    await security_browser.context.route('https://fixture.test/**', handle)
    page = await security_browser.new_page()
    await page.goto('https://fixture.test/apply')
    await page.evaluate("async () => (await fetch('/submit', {method:'POST'})).json()")
    for _ in range(50):
        if security_browser.observation(page)['messages']:
            break
        await asyncio.sleep(.01)
    observation = security_browser.observation(page)
    assert 'never persist this' not in json.dumps(observation)
    result, _ = await SecurityDetector().detect(page, observation)
    assert result.state == 'SPAM_REJECTED'


async def test_embedded_application_verification_is_not_read_as_an_answer(security_browser):
    async def handle(route):
        body = ('<label>Verification code<input value="private-code"></label>' if route.request.url.endswith('/application')
                else '<iframe src="/application"></iframe>')
        await route.fulfill(status=200, content_type='text/html', body=body)
    await security_browser.context.route('https://fixture.test/**', handle)
    page = await security_browser.new_page()
    await page.goto('https://fixture.test/apply', wait_until='load')
    result, snapshot = await SecurityDetector().detect(page)
    assert result.state == 'EMAIL_VERIFICATION'
    assert 'private-code' not in snapshot['text']


async def test_http_observer_tracks_redirect_status_without_query(security_browser):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/start':
                self.send_response(302)
                self.send_header('Location', '/blocked?token=secret')
                self.end_headers()
            else:
                self.send_response(403)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<h1>Forbidden</h1>')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        await security_browser.context.route('http://127.0.0.1:*/**', lambda route: route.continue_())
        page = await security_browser.new_page()
        await security_browser.navigate(page, f'http://127.0.0.1:{server.server_port}/start')
        result, _ = await SecurityDetector().detect(page, security_browser.observation(page))
        assert result.http_status == 403 and result.blocking
        assert 'secret' not in safe_url(page.url)
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()
