"""Load the actual unpacked MV3 extension; all HTTP traffic is fixture-only."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error, sync_playwright

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "extension"
KEY = "fixture-pairing-key"
RESUME = b"%PDF-1.4\n% synthetic fixture resume\n%%EOF\n"


@pytest.fixture
def extension_server():
    requests = []
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
            file = {"/form.html": "form.html", "/frame.html": "frame.html", "/greenhouse.html": "greenhouse.html"}.get(self.path)
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
            if self.path == "/ext/ping":
                self.response(200, {"ok": True, "profile_name": "Fixture Student"})
            elif self.path == "/ext/match":
                self.response(200, {"application_id": 42, "company": "Fixture Company", "title": "Fixture Intern"})
            elif self.path == "/ext/resume":
                self.response(200, {"filename": "fixture-resume.pdf", "mime": "application/pdf", "base64": base64.b64encode(RESUME).decode()})
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
    page.frame_locator("iframe").locator("#name").wait_for()
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert "error" not in result, result
    assert result["matched"]["application_id"] == 42
    summary = result["summary"]
    assert summary["filled"] == 13
    assert set(summary["needs_input"]) == {"Unresolved required *", "Short answer *"}
    assert set(summary["needs_writing"]) == {"Why this role? *", "Cover letter *"}
    assert summary["skipped"] == 2
    for selector, value in {"#name": "Fixture Student", "#email": "student@example.test", "#phone": "2025550100", "#about": "I build local tools.", "#country": "us", "#city": "Boston", "#website": "https://example.test/", "#existing": "User supplied"}.items():
        assert page.locator(selector).input_value() == value
    assert page.locator("#editor").inner_text() == "Plain editable text"
    assert page.frame_locator("iframe").locator("#name").input_value() == "Embedded Student"
    for selector in ("#authorized", "#python", "#js", "#consent"):
        assert page.locator(selector).is_checked()
    assert not page.locator("#unauthorized").is_checked()
    assert not page.locator("#java").is_checked()
    assert page.locator("#resume").evaluate("e => [e.files.length, e.files[0].name, e.files[0].type]") == [1, "fixture-resume.pdf", "application/pdf"]
    assert page.locator("#resume").evaluate("async e => Array.from(new Uint8Array(await e.files[0].arrayBuffer()))") == list(RESUME)
    for selector in ("#missing", "#writing", "#cover", "#too-long"):
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
    assert all(path in {"/ext/match", "/ext/answers", "/ext/resume"} for path, _, _ in requests)

    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    panel.locator("#connection").filter(has_text="Connected — Fixture Student").wait_for()
    assert panel.locator("#company").inner_text() == "Fixture Company"
    assert panel.locator("#job").inner_text() == "Fixture Intern"
    assert panel.locator("#filled").inner_text() == "Filled 13"
    assert "Unresolved required *" in panel.locator("#needs-input").inner_text()
    assert "Cover letter *" in panel.locator("#needs-writing").inner_text()
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
    options.locator("#status").filter(has_text="Pairing saved.").wait_for()
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
    fields = {field['label']: field for field in payload['fields']}
    assert fields['Degree']['options'] == ["Bachelor's Degree", "Master's Degree"]
    assert fields['Discipline']['options'] == ['Computer Science', 'Mechanical Engineering']
    assert fields['Are you interested in a Spring Co-Op, Summer Internship, or Both?']['options'] == ['Spring Co-Op', 'Summer Internship', 'Both']
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
