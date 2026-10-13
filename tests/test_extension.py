"""Load the actual unpacked MV3 extension; all HTTP traffic is fixture-only."""
import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error, expect, sync_playwright

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "extension"
KEY = "fixture-pairing-key"
RESUME = b"%PDF-1.4\n% synthetic fixture resume\n%%EOF\n"


@pytest.fixture
def extension_server():
    requests = []
    run = {"items": [], "index": 0}
    gates = {name: threading.Event() for name in ("write", "cover", "chat")}
    for gate in gates.values():
        gate.set()
    canned = {
        "Full name *": "Fixture Student", "Email": "student@example.test",
        "Phone": "2025550100", "About you": "I build local tools.",
        "Country *": "united states", "Work authorization *": "Yes",
        "Skills": ["Python", "JavaScript"], "Contact consent": True,
        "Existing answer *": "Do not overwrite", "Short answer *": "Too long",
        "Additional information": "Plain editable text", "City": "Boston",
        "Website": "https://example.test/", "Frame name *": "Embedded Student",
        "School": "University of Maryland - College Park", "Degree": "bachelor",
        "Discipline": "computer science", "Existing selection": "Other",
        "Are you interested in a Spring Co-Op, Summer Internship, or Both?": "Summer Internship",
        "Nonmatching choice *": "Yes", "Uncommitted choice *": "Computer Science",
        "Start date month": "August", "Start date year": "2024",
        "End date month": "5", "End date year": "2028",
        "Job Title": "Fixture Engineer", "Company": "Fixture Employer",
        "How Did You Hear About Us?": ["LinkedIn"], "I have a preferred name": True,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def response(self, status, data):
            self.send_response(status)
            origin = self.headers.get("Origin", "")
            if origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-AutoApply-Key")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_OPTIONS(self):
            if not self.headers.get("Origin", "").startswith("chrome-extension://"):
                self.response(403, {"error": "Extension origin required"})
            else:
                self.response(200, {})

        def do_GET(self):
            if self.path.startswith("/ext/"):
                self.ext(None)
                return
            file = {"/phone.html": "phone.html", "/form.html": "form.html", "/frame.html": "frame.html", "/greenhouse.html": "greenhouse.html",
                    "/async_search.html": "async_search.html", "/needs_input.html": "needs_input.html",
                    "/batch-one.html": "batch_form.html", "/batch-two.html": "batch_form.html", "/job.html": "batch_job.html", "/workday-job.html": "workday_job.html", "/workday-signin.html": "workday_signin.html", "/workday-spa.html": "workday_spa.html"}.get(self.path)
            if not file:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((FIXTURES / file).read_bytes())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            self.ext(payload)

        def ext(self, payload):
            key = self.headers.get("X-AutoApply-Key")
            requests.append((self.path, key, payload))
            if key != KEY:
                self.response(403, {"error": "Wrong fixture key"})
                return
            if self.headers.get("Origin") and not self.headers["Origin"].startswith("chrome-extension://"):
                self.response(403, {"error": "Wrong fixture origin"})
                return
            if self.path == "/ext/fixture-delay":
                for name in payload.get("hold", []):
                    gates[name].clear()
                for name in payload.get("release", []):
                    gates[name].set()
                run.update(auto_cover=payload.get("auto_cover", run.get("auto_cover", False)))
                self.response(200, {"ok": True})
            elif self.path == "/ext/fixture-start":
                base = f"http://127.0.0.1:{self.server.server_port}"
                run.update(items=[dict(application_id=1, url=base + ("/workday-job.html" if payload.get("workday") else "/job.html"), company="Fixture", title="One", index=1, total=2),
                                  dict(application_id=2, url=base + "/batch-two.html", company="Fixture", title="Two", index=2, total=2)], index=0)
                self.response(200, {"ok": True})
            elif self.path == "/ext/run/next":
                if run["index"] < len(run["items"]):
                    item = run["items"][run["index"]]
                    run["index"] += 1
                    self.response(200, item)
                else:
                    self.response(200, {"done": True})
            elif self.path in {"/ext/run/result", "/ext/run/status"}:
                self.response(200, {"ok": True})
            elif self.path == "/ext/ping":
                self.response(200, {"ok": True, "profile_name": "Fixture Student"})
            elif self.path == "/ext/match":
                self.response(200, {"application_id": 42, "company": "Fixture Company", "title": "Fixture Intern"})
            elif self.path == "/ext/resume":
                self.response(200, {"filename": "fixture-resume.pdf", "mime": "application/pdf", "base64": base64.b64encode(RESUME).decode()})
            elif self.path == "/ext/write":
                gates["write"].wait(15)
                answer = "Refined fixture answer." if payload.get("previous") else "I enjoy building useful local tools."
                self.response(200, {"answer": answer, "words": len(answer.split()), "chars": len(answer)})
            elif self.path in {"/ext/applied", "/ext/unapplied", "/ext/remember"}:
                self.response(200, {"ok": True})
            elif self.path == "/ext/chat":
                gates["chat"].wait(15)
                self.response(200, {"reply": "Fixture chat reply."})
            elif self.path == "/ext/cover-letter":
                gates["cover"].wait(15)
                if "answers" not in payload and not run.get("auto_cover"):
                    self.response(200, {"questions": ["Which project should the letter highlight?", "Anything else?"]})
                else:
                    self.response(200, {"filename": "fixture-cover.pdf", "mime": "application/pdf", "base64": base64.b64encode(RESUME).decode()})
            elif self.path == "/ext/answers":
                answers = []
                for field in payload["fields"]:
                    label = field["label"]
                    if field["kind"] == "file":
                        status, value = "FILE", "cover_letter" if "Cover" in label else "resume"
                    elif label == "Why this role? *":
                        status, value = "NEEDS_WRITING", None
                    elif label in canned:
                        status, value = "RESOLVED", canned[label]
                        if label == "Start date month" and field["kind"] == "number":
                            value = "8"
                    else:
                        status, value = ("NEEDS_INPUT" if field["required"] else "OPTIONAL_SKIP"), None
                    answers.append({"key": field["key"], "value": value, "source": "fixture", "status": status})
                self.response(200, {"answers": answers})
            else:
                self.response(404, {"error": "No fixture endpoint"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        for gate in gates.values():
            gate.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def loaded_extension(tmp_path):
    extension = ROOT / "extension"
    with sync_playwright() as playwright:
        # channel='chromium' selects full Chromium's new headless mode, not headless shell.
        try:
            context = playwright.chromium.launch_persistent_context(
                str(tmp_path / "chrome-profile"), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                      "--disable-background-networking", "--disable-component-update"],
            )
        except Error as error:
            # Do not hide an extension-specific launch error behind a skip.
            launch_errors = []
            for channel in ("chromium", None):
                try:
                    probe = playwright.chromium.launch_persistent_context(
                        str(tmp_path / f"launch-probe-{channel or 'default'}"),
                        channel=channel, headless=True,
                        args=["--disable-background-networking", "--disable-component-update"],
                    )
                except Error as probe_error:
                    launch_errors.append(str(probe_error))
                else:
                    probe.close()
                    pytest.fail(f"Chromium launches, but loading the extension failed: {error}")
            pytest.skip("Chromium cannot launch at all: " + "\n".join(launch_errors))
        try:
            context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in {"127.0.0.1", "localhost"} or route.request.url.startswith("chrome-extension://") else route.abort())
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker", timeout=15000)
            # A missing worker/invalid manifest is a failure, never a skip.
            assert worker.evaluate("""async () => {
                for (let attempt = 0; attempt < 500; attempt++) {
                    if (typeof globalThis.autoApplyAutofill === 'function') return true;
                    await new Promise(resolve => setTimeout(resolve, 10));
                }
                return false;
            }"""), "Background module failed to initialize"
            yield context, worker, worker.url.split("/")[2]
        finally:
            context.close()


def test_unpacked_extension_autofill(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/form.html")
    page.frame_locator("iframe:not([title='AutoApply application assistant'])").locator("#name").wait_for()
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert "error" not in result, result
    assert result["matched"]["application_id"] == 42
    summary = result["summary"]
    assert summary["filled"] == 13
    assert not result["busy"]
    assert set(summary["needs_input"]) == {"Unresolved required *", "Short answer *"}
    assert set(summary["needs_writing"]) == {"Cover letter *", "Why this role? *"}
    assert summary["skipped"] == 2
    for selector, value in {"#name": "Fixture Student", "#email": "student@example.test", "#phone": "2025550100", "#about": "I build local tools.", "#country": "us", "#city": "Boston", "#website": "https://example.test/", "#existing": "User supplied"}.items():
        assert page.locator(selector).input_value() == value
    assert page.locator("#editor").inner_text() == "Plain editable text"
    assert page.frame_locator("iframe[title='Embedded form']").locator("#name").input_value() == "Embedded Student"
    for selector in ("#authorized", "#python", "#js", "#consent"):
        assert page.locator(selector).is_checked()
    assert not page.locator("#unauthorized").is_checked()
    assert not page.locator("#java").is_checked()
    assert page.locator("#resume").evaluate("e => [e.files.length, e.files[0].name, e.files[0].type]") == [1, "fixture-resume.pdf", "application/pdf"]
    assert page.locator("#resume").evaluate("async e => Array.from(new Uint8Array(await e.files[0].arrayBuffer()))") == list(RESUME)
    for selector in ("#missing", "#cover", "#too-long"):
        assert page.locator(selector).evaluate("e => e.style.outline") == "rgb(217, 144, 34) solid 2px"
    assert page.locator("#name").evaluate("e => e.style.outline") == "rgb(104, 168, 121) solid 2px"
    assert page.evaluate("[submitClicks,nextClicks,submissions]") == [0, 0, 0]
    assert page.frames[1].evaluate("submissions") == 0
    events = page.evaluate("fieldEvents")
    assert all(["name", kind] in events for kind in ("input", "change", "blur"))
    answers_payload = next(payload for path, _, payload in requests if path == "/ext/answers")
    assert answers_payload["page"]["company"] == "Fixture Company"
    assert answers_payload["page"]["title"] == "Fixture Intern"
    assert answers_payload["page"]["description"] == "Build useful local tools."
    fields = answers_payload["fields"]
    assert len({field["key"] for field in fields}) == len(fields)
    assert not any("trap" in field["label"].lower() or "Search jobs" == field["label"] for field in fields)
    assert next(field for field in fields if field["label"] == "Skills")["options"] == ["Python", "JavaScript", "Java"]
    assert next(field for field in fields if field["label"] == "About you")["max_length"] == 200
    assert sum(path == "/ext/resume" for path, _, _ in requests) == 1
    assert all(key == KEY for _, key, _ in requests)
    assert all(path in {"/ext/ping", "/ext/match", "/ext/answers", "/ext/resume", "/ext/write", "/ext/cover-letter", "/ext/run/status", "/ext/run/next"} for path, _, _ in requests)
    expect(page.locator("#writing")).to_have_value("I enjoy building useful local tools.")

    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    panel.locator("#connection").filter(has_text="Connected — Fixture Student").wait_for()
    assert panel.locator("#company").inner_text() == "Fixture Company"
    assert panel.locator("#job").inner_text() == "Fixture Intern"
    expect(panel.locator("#filled")).to_have_text("Filled 14")
    assert "Unresolved required *" in panel.locator("#needs-input").inner_text()
    expect(panel.locator("#cover-questions")).to_contain_text("Which project")
    # Refill through the actual runtime message path, with the fixture tab active.
    page.bring_to_front()
    second = panel.evaluate("() => chrome.runtime.sendMessage({type: 'autofill'})")
    assert "error" not in second, second
    assert second["summary"]["filled"] == 0
    assert page.locator("#existing").input_value() == "User supplied"
    assert page.evaluate("[submitClicks,nextClicks,submissions]") == [0, 0, 0]


def test_pairing_options_and_errors(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    options = context.new_page()
    options.goto(f"chrome-extension://{extension_id}/options.html")
    options.locator("#server").fill(server)
    options.locator("#key").fill(KEY)
    options.get_by_role("button", name="Save", exact=True).click()
    expect(options.locator("#status")).to_have_text("Pairing saved.")
    assert worker.evaluate("() => chrome.storage.local.get(['server', 'key'])") == {"server": server, "key": KEY}
    options.get_by_role("button", name="Test connection", exact=True).click()
    options.locator("#status").filter(has_text="Connected — Fixture Student").wait_for()
    options.locator("#key").fill("wrong-key")
    options.get_by_role("button", name="Test connection", exact=True).click()
    options.locator("#status").filter(has_text="Pairing key rejected").wait_for()
    worker.evaluate("() => chrome.storage.local.remove('key')")
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    panel.locator("#connection").filter(has_text="Not paired").wait_for()
    # Unknown local path returns a real HTTP failure, rendered as a clear error.
    options.locator("#server").fill(server + "/missing")
    options.locator("#key").fill(KEY)
    options.get_by_role("button", name="Test connection", exact=True).click()
    options.locator("#status").filter(has_text="Dashboard request failed (404)").wait_for()
    assert any(path == "/ext/ping" and key == KEY for path, key, _ in requests)


def test_greenhouse_comboboxes_hidden_upload_and_dates(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/greenhouse.html")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert "error" not in result, result
    assert result["summary"]["filled"] == 9
    assert set(result["summary"]["needs_input"]) == {"Nonmatching choice *", "Uncommitted choice *"}
    assert result["summary"]["needs_writing"] == ["Cover Letter"]
    for name, value in {"school": "University of Maryland - College Park", "degree": "Bachelor's Degree", "discipline": "Computer Science", "interest": "Summer Internship", "existing": "User supplied"}.items():
        root = page.locator(f'.select[data-id="{name}"]')
        assert root.locator('.select__single-value').inner_text() == value
        assert root.locator('[type=hidden]').input_value() == value
        assert root.locator('[role=combobox]').input_value() == ""
        assert root.locator('[role=combobox]').get_attribute('aria-expanded') == 'false'
    for name in ("invalid", "uncommitted"):
        assert page.locator(f'#{name}').input_value() == ""
        assert page.locator(f'[name="{name}"]').input_value() == ""
    assert page.get_by_role('listbox').count() == 0
    for name, value in {"start-month": "08", "start-year": "2024", "end-month": "05", "end-year": "2028"}.items():
        assert page.locator(f'#{name}').input_value() == value
    assert page.locator('#resume').evaluate('async e => Array.from(new Uint8Array(await e.files[0].arrayBuffer()))') == list(RESUME)
    assert page.locator('.filename').first.inner_text() == 'fixture-resume.pdf'
    assert page.locator('#cover_letter').evaluate('e => e.files.length') == 0
    assert page.evaluate('uploadEvents') == [['resume', 'input'], ['resume', 'change']]
    payload = next(payload for path, _, payload in requests if path == '/ext/answers')
    fields = {field['label']: field for field in result['fields']}
    # Resolved choices go directly to the filler without scan-time menu collection.
    assert result["timings"]["scan_ms"] < 1000
    assert result["timings"].get("options_ms", 0) == 0
    assert fields['School']['options'] == []
    assert fields['Degree']['options'] == []
    assert fields['Discipline']['options'] == []
    assert fields['Are you interested in a Spring Co-Op, Summer Internship, or Both?']['options'] == []
    assert all(field['options'] == [] for field in payload['fields'] if field['kind'] == 'combobox')
    assert all(fields[label]['section'] == 'Education' for label in ('Start date month', 'Start date year', 'End date month', 'End date year'))
    assert fields['Resume/CV *']['kind'] == fields['Cover Letter']['kind'] == 'file'
    assert not any(field['label'] == 'Attach' for field in payload['fields'])
    assert 'University of Maryland - College Park' in page.evaluate('schoolSearches')
    second = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert second['summary']['filled'] == 0
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


def test_async_school_retries_prefix_and_commits_exact_match(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/greenhouse.html')
    page.locator('.select[data-id="school"]').evaluate("e => e.dataset.prefixOnly = 'true'")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    assert 'School' not in result['summary']['needs_input']
    root = page.locator('.select[data-id="school"]')
    assert root.locator('.select__single-value').inner_text() == 'University of Maryland - College Park'
    assert root.locator('[type=hidden]').input_value() == 'University of Maryland - College Park'
    assert root.locator('[role=combobox]').input_value() == ''
    searches = page.evaluate('schoolSearches')
    assert searches.index('University of Maryland - College Park') < searches.index('University of Maryland')
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


@pytest.mark.parametrize('mode', ['search', 'prefix', 'failure'])
def test_async_school_searches_beyond_first_page(loaded_extension, extension_server, mode):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/async_search.html')
    if mode != 'search':
        page.locator('.select').evaluate("(e, mode) => e.dataset[mode === 'prefix' ? 'prefixOnly' : 'noResults'] = 'true'", mode)
    # Opening alone renders ten unrelated schools, never the wanted answer.
    page.locator('#school').press('ArrowDown')
    assert page.get_by_role('option').count() == 10
    assert page.evaluate('searchEvents') == []
    page.locator('#school').press('Escape')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    expected = 'University of Maryland - College Park'
    assert result['summary']['filled'] == (0 if mode == 'failure' else 1)
    assert result['summary']['needs_input'] == (['School'] if mode == 'failure' else [])
    assert page.locator('.select__single-value').inner_text() == ('' if mode == 'failure' else expected)
    assert page.locator('[name=school]').input_value() == ('' if mode == 'failure' else expected)
    assert page.locator('#school').input_value() == ''
    assert page.locator('#school').get_attribute('aria-expanded') == 'false'
    assert page.get_by_role('listbox').count() == 0
    searches = page.evaluate('searchEvents')
    assert searches[0] == {'term': expected, 'inputType': 'insertText', 'data': expected}
    if mode != 'search':
        assert searches[1]['term'] == 'University of Maryland'
    if mode == 'failure':
        assert searches[-1]['term'] == ''
    payload = next(payload for path, _, payload in requests if path == '/ext/answers')
    assert result['fields'][0]['options'] == []  # Resolved search avoids opening the menu during scanning.
    assert payload['fields'][0]['options'] == []
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


@pytest.mark.parametrize('resume_label,resume_attributes', [
    ('<label for="attachment">Resume/CV *</label>', 'id="attachment"'),
    ('<div id="upload-label-resume">Resume/CV *</div>', 'id="attachment" aria-labelledby="upload-label-resume"'),
    ('<div id="description">Resume/CV *</div>', 'id="attachment" aria-describedby="description"'),
    ('<h3>Resume/CV *</h3>', 'id="attachment"'),
    ('', 'id="resume" aria-label="Attach"'),
    ('', 'id="attachment" name="cv"'),
])
def test_upload_field_labels_ignore_generic_buttons(loaded_extension, extension_server, resume_label, resume_attributes):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/greenhouse.html')
    page.evaluate("""markup => {
        document.querySelector('form').innerHTML = markup;
        for (const input of document.querySelectorAll('[type=file]')) {
            for (const type of ['input', 'change']) input.addEventListener(type, () => {
                uploadEvents.push([input.id, type]);
                setTimeout(() => input.parentElement.querySelector('.filename').textContent = input.files[0]?.name || '', 150);
            });
        }
    }""", f'''{resume_label}<div class="upload"><button type="button">Attach</button>
        <input {resume_attributes} type="file" accept=".pdf" hidden required><span class="filename"></span></div>
        <div class="upload"><button type="button">Choose</button>
        <input id="other" name="cover_letter" type="file" hidden><span class="filename"></span></div>''')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    assert result['summary']['filled'] == 1
    assert result['summary']['needs_input'] == []
    assert result['summary']['needs_writing'] == ['Cover Letter']
    assert page.locator('.filename').first.inner_text() == 'fixture-resume.pdf'
    assert page.locator('[type=file]').first.evaluate('e => e.files[0].name') == 'fixture-resume.pdf'
    assert page.locator('#other').evaluate('e => e.files.length') == 0
    payload = next(payload for path, _, payload in requests if path == '/ext/answers')
    assert [field['label'] for field in payload['fields']] == ['Resume/CV *' if resume_label else 'Resume/CV', 'Cover Letter']
    assert [event[1] for event in page.evaluate('uploadEvents')] == ['input', 'change']
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]



def test_sidepanel_answers_cover_helper_and_chat(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/form.html")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    page.locator("#autoapply-sidebar iframe").wait_for()
    panel = next(frame for frame in page.frames if frame.url.startswith(f"chrome-extension://{extension_id}/sidepanel.html"))
    panel.get_by_label("Unresolved required *", exact=True).fill("User fixture answer")
    panel.locator(".prompt").filter(has_text="Unresolved required *").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#missing")).to_have_value("User fixture answer")
    panel.get_by_label("Unresolved required *", exact=True).wait_for(state="detached")
    remembered = next(payload for path, _, payload in requests if path == "/ext/remember")
    assert remembered["label"] == "Unresolved required *"
    assert remembered["value"] == "User fixture answer"
    assert remembered["application_id"] == 42
    panel.get_by_label("Which project should the letter highlight?", exact=True).fill("A fixture robotics project")
    panel.get_by_role("button", name="Generate with answers", exact=True).click()
    page.wait_for_function("document.querySelector('#cover').files.length === 1")
    assert page.locator("#cover").evaluate("e => e.files[0].name") == "fixture-cover.pdf"
    panel.locator("#cover-questions").get_by_role("button").wait_for(state="detached")
    cover = [payload for path, _, payload in requests if path == "/ext/cover-letter"][-1]
    assert cover["answers"] == [{"question": "Which project should the letter highlight?", "answer": "A fixture robotics project"}, {"question": "Anything else?", "answer": ""}]
    panel.locator("#tab-ask").click()
    panel.locator("#question").fill("Describe your robotics experience. Word limit 300")
    panel.locator("#generate").click()
    expect(panel.locator("#version")).to_have_text("1 / 1")
    first = panel.locator("#answer").input_value()
    panel.locator("#refinement").fill("Shorter")
    panel.locator("#refine").click()
    expect(panel.locator("#version")).to_have_text("2 / 2")
    assert panel.locator("#answer").input_value() == "Refined fixture answer."
    refine = [payload for path, _, payload in requests if path == "/ext/write"][-1]
    assert refine["previous"] == first
    assert refine["instruction"] == "Shorter"
    panel.locator("#previous").click()
    assert panel.locator("#answer").input_value() == first
    panel.locator("#next").click()
    assert panel.locator("#answer").input_value() == "Refined fixture answer."
    panel.evaluate("location.reload()")
    panel.locator("#tab-ask").wait_for()
    panel.locator("#tab-ask").click()
    panel.locator("#question").fill("Describe your robotics experience. Word limit 300")
    expect(panel.locator("#version")).to_have_text("2 / 2")
    panel.locator("#tab-chat").click()
    panel.locator("#chat-input").fill("How can I improve this answer?")
    panel.locator("#chat-input").press("Enter")
    panel.locator("#messages").filter(has_text="Fixture chat reply.").wait_for()
    chat = next(payload for path, _, payload in requests if path == "/ext/chat")
    assert chat["messages"] == [{"role": "user", "text": "How can I improve this answer?"}]
    assert chat["page"]["company"] == "Fixture Company"
    expect(panel.locator(".bubble.user p")).to_have_text("How can I improve this answer?")
    expect(panel.locator(".bubble.assistant p")).to_have_text("Fixture chat reply.")
    assert "You:" not in panel.locator("#messages").inner_text()
    assert "AI:" not in panel.locator("#messages").inner_text()
    assert panel.locator(".bubble time").count() == 2
    panel.locator("#clear-chat").click()
    assert panel.locator("#messages").inner_text() == ""
    assert page.evaluate("[submitClicks,nextClicks,submissions]") == [0, 0, 0]


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_needs_input_choices_and_compact_actions(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/needs_input.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    interest = 'Are you interested in a Spring Co-Op, Summer Internship, or Both? *'
    assert set(result['summary']['needs_input']) == {'Start date month*', interest, 'Schedule *', 'Areas *'}
    assert result['summary']['skipped'] == 3
    fields = {field['label']: field for field in result['fields']}
    assert all(fields[label]['answered'] for label in ('Country *', 'Preselected select *', 'Preselected input *'))
    assert page.get_by_role('listbox').count() == 0

    panel = context.new_page()
    panel.set_viewport_size({'width': 360, 'height': 900})
    panel.emulate_media(color_scheme=theme)
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    expect(panel.locator('#needs-input .prompt')).to_have_count(4)
    assert panel.locator('#needs-input input, #needs-input textarea').count() == 0
    assert 'Country *' not in panel.locator('#needs-input').inner_text()
    choices = [
        ('Start date month*', ['January', 'August'], 'August', '#month', '08'),
        (interest, ['Spring Co-Op', 'Summer Internship', 'Both'], 'Both', '[name=interest]', 'Both'),
        ('Schedule *', ['Full time', 'Part time'], 'Part time', '#part-time', None),
        ('Areas *', ['Hardware', 'Software'], ['Hardware', 'Software'], '#hardware', None),
    ]
    for label, options, chosen, selector, expected in choices:
        prompt = panel.locator('.prompt').filter(has=panel.get_by_label(label, exact=True))
        control = prompt.get_by_label(label, exact=True)
        assert control.evaluate('e => e.tagName') == 'SELECT'
        assert control.locator('option').all_text_contents() == (options if label == 'Areas *' else ['Choose an answer', *options])
        save = prompt.get_by_role('button', name='Save', exact=True)
        skip = prompt.get_by_role('button', name='Skip', exact=True)
        save_box, skip_box = save.bounding_box(), skip.bounding_box()
        assert save_box['y'] == skip_box['y']
        assert skip_box['x'] >= save_box['x'] + save_box['width']
        assert save_box['width'] < 100 and skip_box['width'] < 100
        control.select_option(label=chosen)
        save.click()
        if expected is None:
            expect(page.locator(selector)).to_be_checked()
        else:
            expect(page.locator(selector)).to_have_value(expected)
        expect(control).to_have_count(0)
        remembered = [payload for path, _, payload in requests if path == '/ext/remember' and payload['label'] == label]
        assert len(remembered) == 1
        assert remembered[0]['value'] == chosen
        assert remembered[0]['application_id'] == 42
    assert page.locator('#software').is_checked()
    assert page.locator('#phone-country').input_value() == ''
    assert page.locator('.css-phone-singleValue').inner_text() == 'US +1'
    assert page.evaluate('optionClicks') == ['Both']
    assert page.locator('#interest').get_attribute('aria-expanded') == 'false'
    assert page.get_by_role('listbox').count() == 0
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


def test_preselected_country_and_replaced_upload(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, _ = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/greenhouse.html")
    page.locator('.select[data-id="existing"] label').evaluate("e => e.textContent = 'Phone country *'")
    page.locator('.select[data-id="existing"] .select__single-value').evaluate("e => e.textContent = 'US +1'")
    page.evaluate("""() => {
        document.querySelector('#resume').addEventListener('change', event => {
            const input = event.target, filename = input.files[0].name;
            setTimeout(() => {
                const container = input.closest('.upload');
                container.innerHTML = '<p>' + filename + '</p><button type="button">Remove</button>';
            }, 100);
        });
    }""")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert "error" not in result
    assert "Phone country *" not in result["summary"]["needs_input"]
    assert result["summary"]["skipped"] >= 1
    assert result["summary"]["errors"] == []
    replaced_upload = page.locator('.upload').filter(has=page.get_by_role("button", name="Remove", exact=True))
    expect(replaced_upload).to_have_text("fixture-resume.pdfRemove")
    expect(replaced_upload.locator("p")).to_have_text("fixture-resume.pdf")
    expect(replaced_upload.get_by_role("button", name="Remove", exact=True)).to_be_enabled()
    expect(page.locator('#resume')).to_have_count(0)
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    panel.get_by_role("button", name="Generate with answers", exact=True).click()
    page.wait_for_function("document.querySelector('#cover_letter').files.length === 1")
    expect(page.locator('.filename')).to_have_text('fixture-cover.pdf')
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]


def test_chrome_run_opens_fills_and_keeps_two_tabs(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused: true, chromeRun: {paused: true, index: 0, total: 0}})")
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    worker.evaluate("""async server => {
        await fetch(server + '/ext/fixture-start', {method:'POST', headers:{'X-AutoApply-Key':'fixture-pairing-key', 'Content-Type':'application/json'}, body:'{}'});
    }""", server)
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    expect(panel.locator('#run-pause')).to_have_text('Resume run')
    panel.locator('#run-pause').click()
    expect(panel.locator('#chrome-run-status')).to_contain_text('Run: 2/2', timeout=30000)
    expect(panel.locator('#chrome-run-status')).to_contain_text('(idle)', timeout=30000)
    results = [payload for path, _, payload in requests if path == '/ext/run/result']
    assert [(item['application_id'], item['state']) for item in results] == [(1, 'filled'), (2, 'filled')]
    pages = [page for page in context.pages if page.url in {server + '/batch-one.html', server + '/batch-two.html'}]
    assert len(pages) == 2
    for page in pages:
        expect(page.locator('#name')).to_have_value('Fixture Student')
        expect(page.locator('#email')).to_have_value('student@example.test')
        expect(page.locator('#writing')).to_have_value('I enjoy building useful local tools.')
        assert page.evaluate('[submitClicks, submissions]') == [0, 0]
    answers = [payload for path, _, payload in requests if path == '/ext/answers']
    assert [payload['application_id'] for payload in answers] == [1, 2]
    assert all(next(field for field in payload['fields'] if field['label'] == 'Phone country *')['answered'] for payload in answers)
    panel.locator('#run-pause').click()
    expect(panel.locator('#chrome-run-status')).to_contain_text('(paused)')


@pytest.mark.parametrize("batch", [False, True])
def test_workday_entry_stops_at_sign_in(loaded_extension, extension_server, batch):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused: true})")
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    detail = "Sign in to Workday in this tab. Autofill will resume automatically."
    if batch:
        worker.evaluate("""async server => {
            await fetch(server + '/ext/fixture-start', {method:'POST', headers:{'X-AutoApply-Key':'fixture-pairing-key', 'Content-Type':'application/json'}, body:JSON.stringify({workday:true})});
            await chrome.storage.session.set({chromeRunPaused:false});
            await globalThis.autoApplyRunTick();
        }""", server)
        results = [payload for endpoint, _, payload in requests if endpoint == '/ext/run/result']
        assert results[0]['state'] == 'needs_input'
        assert results[0]['detail'] == detail
        page = next(page for page in context.pages if page.url == server + '/workday-signin.html')
    else:
        page = context.new_page()
        page.goto(server + '/workday-job.html')
        result = worker.evaluate("""async url => {
            const [tab] = await chrome.tabs.query({url});
            return globalThis.autoApplyAutofill(tab.id);
        }""", page.url)
        assert result['state'] == 'needs_input'
        assert result['detail'] == detail
        assert not result.get('error')
    expect(panel.locator('#progress')).to_have_text(detail)
    expect(page.locator('#email')).to_have_value('')
    expect(page.locator('#password')).to_have_value('')
    assert page.evaluate('credentialInputs') == 0
    assert page.evaluate('[sessionStorage.applyClicks,sessionStorage.manualClicks,sessionStorage.wrongEntry,sessionStorage.submissions]') == ['1', '1', None, None]
    assert not any(payload and payload.get('url') == server + '/workday-signin.html' for endpoint, _, payload in requests if endpoint == '/ext/answers')


def test_generic_sign_in_stops_before_profile_answers(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/workday-signin.html')
    page.evaluate("document.querySelectorAll('[data-automation-id]').forEach(el=>el.removeAttribute('data-automation-id'))")
    result = worker.evaluate("""async url => {
        const [tab] = await chrome.tabs.query({url});
        return globalThis.autoApplyAutofill(tab.id);
    }""", page.url)
    assert result['state'] == 'needs_input'
    assert not result.get('error')
    assert page.evaluate('credentialInputs') == 0
    assert not any(endpoint == '/ext/answers' for endpoint, _, _ in requests)


@pytest.mark.parametrize('run_tab', [False, True])
def test_workday_spa_refills_without_navigation(loaded_extension, extension_server, run_tab):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused:true, chromeRun:{error:'Not paired. Open Pairing settings and save your dashboard key.'}})")
    worker.evaluate("config => chrome.storage.local.set(config)", {'server': server, 'key': KEY})
    page = context.new_page()
    page.goto(server + '/workday-spa.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    match = {'application_id': 7, 'company': 'Run Company', 'title': 'Run Intern'} if run_tab else None
    result = worker.evaluate("async args => globalThis.autoApplyAutofill(args.id, args.match)", {'id': tab_id, 'match': match})
    assert not result.get('error'), result
    expect(page.locator('#name')).to_have_value('Fixture Student')
    expect(page.locator('#country')).to_have_text('United States')
    expect(page.locator('[data-automation-id=selectedItem]')).to_have_text('LinkedIn')
    expect(page.locator('#preferred')).to_be_checked()
    assert page.evaluate('[nextClicks,submitClicks,submissions]') == [0, 0, 0]
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    expect(panel.locator('#chrome-run-status')).not_to_contain_text('Not paired')
    # Only the user advances. Workday replaces fields while staying in the same tab.
    page.locator('#next').click()
    expect(page.locator('#job-title')).to_have_value('Fixture Engineer', timeout=20000)
    expect(page.locator('#employer')).to_have_value('Fixture Employer')
    expect(page.locator('#month')).to_have_value('8')
    expect(page.locator('#year')).to_have_value('2024')
    expect(page.locator('#writing')).to_have_value('I enjoy building useful local tools.')
    expect(page.locator('#filename')).to_have_text('fixture-resume.pdf')
    expect(panel.locator('#progress')).to_contain_text('Workday step: My Experience - filled')
    expect(panel.locator('#company')).to_have_text('Run Company' if run_tab else 'Fixture Company')
    answers = [p for endpoint, _, p in requests if endpoint == '/ext/answers']
    assert {p['application_id'] for p in answers} == {7 if run_tab else 42}
    assert all(p['page']['company'] == ('Run Company' if run_tab else 'Fixture Company') for p in answers)
    assert sum(endpoint == '/ext/match' for endpoint, _, _ in requests) == (0 if run_tab else 1)
    assert page.evaluate('[nextClicks,submitClicks,submissions]') == [1, 0, 0]
    # Filling and opening menus must not cause a refill loop.
    page.wait_for_timeout(1200)
    assert len([p for endpoint, _, p in requests if endpoint == '/ext/answers']) == len(answers)


def test_workday_resumes_after_sign_in(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, requests = extension_server
    worker.evaluate("() => chrome.storage.session.set({chromeRunPaused:true})")
    worker.evaluate("config => chrome.storage.local.set(config)", {'server':server, 'key':KEY})
    page = context.new_page()
    page.goto(server + '/workday-signin.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert result['state'] == 'needs_input'
    assert page.evaluate('credentialInputs') == 0
    # Fixture simulates the successful sign-in redirect without any employer service.
    page.goto(server + '/workday-spa.html')
    expect(page.locator('#name')).to_have_value('Fixture Student', timeout=15000)
    assert page.evaluate('[nextClicks,submitClicks,submissions]') == [0, 0, 0]


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_sidebar_answers_learning_phone_and_manual_cover(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.emulate_media(color_scheme=theme)
    page.goto(server + '/phone.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert 'error' not in result, result
    assert not any(path == '/ext/cover-letter' for path, _, _ in requests)
    assert page.locator('#autoapply-sidebar').evaluate('el => !!el.shadowRoot')
    panel = page.frame_locator('#autoapply-sidebar iframe')
    expect(panel.locator('#connection')).to_contain_text('Connected')
    fields = [dict(field, key=field['key'].split(':', 1)[1]) for field in result['fields'] if field['key'].startswith('0:')]
    values = [dict(key=field['key'], status='RESOLVED', value='+1 301-401-9768', force=True) for field in fields if 'phone' in field['label'].lower()]
    worker.evaluate('async ({tabId, values}) => chrome.scripting.executeScript({target: {tabId}, func: values => autoApplyContent.fill(values), args: [values]})', dict(tabId=tab_id, values=values))
    for selector, value in {'#wd': '3014019768', '#gh': '3014019768', '#us': '(301) 401-9768', '#intl': '+1 301-401-9768', '#ext': ''}.items():
        expect(page.locator(selector)).to_have_value(value)
    expect(page.get_by_label('Country Phone Code')).to_have_value('us')
    page.locator('#us').fill('2025550100')
    page.locator('#us').press('Tab')
    expect(panel.locator('#saved-status')).to_have_text('Saved')
    assert any(path == '/ext/remember' and payload['value'] == '2025550100' for path, _, payload in requests)
    panel.locator('#tab-answers').click()
    # Editing replaces the displayed value; keep the card locator stable.
    card = panel.locator('.answer-card').filter(has=panel.locator('.field-label', has_text=re.compile(r'^Phone$')))
    expect(card.locator('.answer-value')).to_have_text('2025550100')
    assert card.bounding_box()['height'] <= 64
    assert card.locator('.source-pill').count() == 1
    assert card.get_by_role('button', name='Edit', exact=True).inner_text() == '✎'
    card.get_by_role('button', name='Edit', exact=True).click()
    expect(card).to_have_class(re.compile(r'editing'))
    card.get_by_role('button', name='Cancel', exact=True).click()
    expect(card.locator('.answer-value')).to_have_text('2025550100')
    card.get_by_role('button', name='Edit', exact=True).click()
    card.get_by_label('Phone', exact=True).fill('3015550100')
    card.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('#us')).to_have_value('3015550100')
    # The page fills before the asynchronous remember request finishes.
    expect(card.locator('.answer-value')).to_have_text('3015550100')
    assert any(path == '/ext/remember' and payload['value'] == '3015550100' for path, _, payload in requests)
    before = len([entry for entry in requests if entry[0] == '/ext/remember'])
    page.locator('#password').fill('fixture secret')
    page.locator('#password').press('Tab')
    page.locator('#essay').fill('My own essay')
    page.locator('#essay').press('Tab')
    expect(panel.locator('#completed-answers')).to_contain_text('My own essay')
    assert len([entry for entry in requests if entry[0] == '/ext/remember']) == before
    panel.locator('#tab-fill').click()
    panel.locator('#cover-letter').click()
    expect(panel.locator('#cover-questions')).to_contain_text('Which project')
    page.screenshot(path=str(ROOT / '.tmp' / ('t032-sidebar-' + theme + '.png')), animations='disabled')
    panel.locator('#minimize').click()
    expect(page.locator('#autoapply-sidebar .restore')).to_be_visible()
    page.locator('#autoapply-sidebar .restore').click()
    expect(page.locator('#autoapply-sidebar iframe')).to_be_visible()
    panel.locator('#close').click()
    expect(page.locator('#autoapply-sidebar')).to_be_hidden()
    worker.evaluate("id => globalThis.autoApplySidebar(id, 'toggle')", tab_id)
    expect(page.locator('#autoapply-sidebar iframe')).to_be_visible()


@pytest.mark.parametrize('scenario', ['confirmed', 'no_click', 'invalid'])
def test_detected_manual_submission(loaded_extension, extension_server, scenario):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/phone.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    # Fixture button has no submission handler; this records only a human click.
    if scenario != 'no_click':
        page.locator('#fixture-submit').click()
    page.locator('#confirmation').evaluate("(el, invalid) => el.innerHTML = 'Thank you for applying' + (invalid ? '<p role=alert>Required field error</p>' : '')", scenario == 'invalid')
    panel = page.frame_locator('#autoapply-sidebar iframe')
    if scenario == 'confirmed':
        expect(panel.locator('#applied-status')).to_be_visible()
        page.locator('#confirmation').evaluate("el => el.append(' ')" )
        page.wait_for_timeout(700)
        assert len([entry for entry in requests if entry[0] == '/ext/applied']) == 1
        panel.locator('#undo-applied').click()
        expect(panel.locator('#applied-status')).to_be_hidden()
        assert any(path == '/ext/unapplied' for path, _, _ in requests)
    else:
        page.wait_for_timeout(900)
        assert not any(path == '/ext/applied' for path, _, _ in requests)


def fixture_delay(worker, **payload):
    worker.evaluate("""async payload => {
        const {server, key} = await chrome.storage.local.get(['server', 'key']);
        const response = await fetch(server + '/ext/fixture-delay', {method:'POST',
            headers:{'Content-Type':'application/json', 'X-AutoApply-Key':key}, body:JSON.stringify(payload)});
        if (!response.ok) throw new Error('Fixture delay configuration failed');
    }""", payload)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chat_bubbles_typing_composer_and_scroll(loaded_extension, extension_server, theme):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/phone.html")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    panel = context.new_page()
    panel.set_viewport_size({"width": 360, "height": 640})
    panel.emulate_media(color_scheme=theme)
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}")
    panel.locator("#tab-chat").click()
    fixture_delay(worker, hold=["chat"])
    composer = panel.locator("#chat-input")
    composer.fill("First line")
    composer.press("Shift+Enter")
    composer.press("End")
    composer.type("Second line")
    assert "\n" in composer.input_value()
    composer.press("Enter")
    expect(panel.locator(".typing")).to_be_visible()
    expect(panel.locator(".bubble.user p")).to_contain_text("First line")
    assert composer.bounding_box()["height"] <= 40
    assert panel.locator("#send").bounding_box()["y"] == composer.bounding_box()["y"]
    fixture_delay(worker, release=["chat"])
    expect(panel.locator(".bubble.assistant p")).to_have_text("Fixture chat reply.")
    expect(panel.locator(".typing")).to_have_count(0)
    user, assistant = panel.locator(".bubble.user"), panel.locator(".bubble.assistant")
    assert user.bounding_box()["x"] > assistant.bounding_box()["x"]
    assert user.evaluate("e => getComputedStyle(e).backgroundColor") != assistant.evaluate("e => getComputedStyle(e).backgroundColor")
    assert user.evaluate("e => e.offsetWidth <= e.parentElement.clientWidth * .86")
    assert not re.search(r"You:|AI:", panel.locator("#messages").inner_text())
    for index in range(8):
        composer.fill("A longer fixture message to exercise scrolling. " * 3)
        composer.press("Enter")
        expect(panel.locator(".bubble.assistant")).to_have_count(index + 2)
        expect(panel.locator("#send")).to_be_enabled()
    panel.wait_for_function("() => {const e=document.querySelector('#messages');return e.scrollHeight > e.clientHeight && e.scrollTop + e.clientHeight >= e.scrollHeight - 2;}")
    panel.screenshot(path=str(ROOT / '.tmp' / ('t032-chat-' + theme + '.png')), animations='disabled')
    panel.locator("#clear-chat").click()
    expect(panel.locator(".bubble")).to_have_count(0)


def test_preselected_phone_flag_and_displayed_choice_without_options(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/phone.html")
    page.locator("form").evaluate('''form => form.insertAdjacentHTML('afterbegin',
        '<div class="intl-phone"><div class="country-selector"><button type="button" role="combobox" aria-label="Country" aria-required="true"><span class="flag" title="United States">🇺🇸</span></button></div><span>+1</span><input type="tel" aria-label="National phone"></div>' +
        '<div role="combobox" aria-label="Schedule choice" aria-required="true">Full time</div>')''')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    for label in ("Country", "Schedule choice"):
        field = next(field for field in result["fields"] if field["label"] == label)
        assert field["answered"] and field["options"] == []
        assert label not in result["summary"]["needs_input"]
    assert result["summary"]["skipped"] >= 2


@pytest.mark.parametrize("with_ai", [False, True])
def test_autofill_returns_before_delayed_writing_and_cover(loaded_extension, extension_server, with_ai):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    fixture_delay(worker, hold=["write", "cover"] if with_ai else ["cover"], auto_cover=True)
    page = context.new_page()
    page.goto(server + "/form.html")
    if not with_ai:
        page.locator("#writing").fill("My existing answer")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    assert not result.get("error") and not result["busy"]
    assert page.locator("#name").input_value() == "Fixture Student"
    assert page.locator("#resume").evaluate("e => e.files.length") == 1
    assert page.locator("#cover").evaluate("e => e.files.length") == 0
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}")
    expect(panel.locator("#autofill")).to_be_enabled()
    if with_ai:
        expect(panel.locator("#writing-progress")).to_contain_text("Writing:")
        assert page.locator("#writing").input_value() == ""
        # Review and save a deterministic answer while the fake AI call is held.
        panel.get_by_label("Unresolved required *", exact=True).fill("Reviewed while writing")
        panel.locator(".prompt").filter(has_text="Unresolved required *").get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("#missing")).to_have_value("Reviewed while writing")
        fixture_delay(worker, release=["write"])
        expect(page.locator("#writing")).to_have_value("I enjoy building useful local tools.")
        expect(panel.locator("#writing-progress")).to_contain_text("Answer filled:")
    expect(panel.locator("#cover-progress")).to_have_text("Writing cover letter...")
    assert page.locator("#cover").evaluate("e => e.files.length") == 0
    fixture_delay(worker, release=["cover"])
    expect(panel.locator("#cover-progress")).to_have_text("Cover letter attached")
    assert page.locator("#cover").evaluate("e => e.files[0].name") == "fixture-cover.pdf"
    final = worker.evaluate("async id => (await chrome.storage.session.get('tabResults')).tabResults[id]", tab_id)
    assert final["summary"]["needs_writing"] == []
    assert not final["coverBusy"]
    if with_ai:
        assert any(field["value"] == "Reviewed while writing" for field in final["completed"])
    assert page.evaluate("[submitClicks,nextClicks,submissions]") == [0, 0, 0]
