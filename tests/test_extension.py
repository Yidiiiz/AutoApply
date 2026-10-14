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

from autoapply.answers import fit_options

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
        "Full name": "Fixture Student", "Email": "student@example.test",
        "Phone": "2025550100", "About you": "I build local tools.",
        "Country": "united states", "Country": "United States", "Work authorization": "Yes",
        "Skills": ["Python", "JavaScript"], "Contact consent": True,
        "Existing answer": "Do not overwrite", "Short answer": "Too long",
        "Additional information": "Plain editable text", "City": "Boston",
        "Website": "https://example.test/", "Frame name": "Embedded Student",
        "School": "University of Maryland - College Park", "Degree": "bachelor",
        "Discipline": "computer science", "Existing selection": "Other",
        "Are you interested in a Spring Co-Op, Summer Internship, or Both?": "Summer Internship",
        "Nonmatching choice": "Yes", "Uncommitted choice": "Computer Science",
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
            file = {"/t040.html": "t040.html", "/t039.html": "t039.html", "/workday-experience.html": "workday_experience.html", "/workday-robust.html": "workday_robust.html", "/sections.html": "sections.html", "/phone.html": "phone.html", "/form.html": "form.html", "/frame.html": "frame.html", "/greenhouse.html": "greenhouse.html",
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
            elif self.path == "/ext/profile-sections":
                self.response(200, {"experience": [], "projects": [], "education": [], "skills": [], "links": {}})
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
            elif self.path in {"/ext/applied", "/ext/unapplied", "/ext/remember", "/ext/snapshot"}:
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
                    elif label == "Why this role?":
                        status, value = "NEEDS_WRITING", None
                    elif payload.get('url', '').endswith('/needs_input.html') and label in {'Start date month', 'Are you interested in a Spring Co-Op, Summer Internship, or Both?'}:
                        status, value = 'NEEDS_INPUT', None
                    elif label in canned:
                        status, value = "RESOLVED", canned[label]
                        if label == "Country":
                            value = fit_options(value, field["options"]) if field.get("options") else None
                            status = "RESOLVED" if value is not None else "NEEDS_INPUT"
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
            context.route("**/", lambda route: route.continue_() if urlsplit(route.request.url).hostname in {"127.0.0.1", "localhost"} or route.request.url.startswith("chrome-extension://") else route.abort())
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
    assert set(summary["needs_input"]) == {"Unresolved", "Short answer"}
    assert set(summary["needs_writing"]) == {"Cover letter", "Why this role?"}
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
    assert all(path in {"/ext/profile-sections", "/ext/ping", "/ext/match", "/ext/answers", "/ext/resume", "/ext/write", "/ext/cover-letter", "/ext/run/status", "/ext/run/next"} for path, _, _ in requests)
    expect(page.locator("#writing")).to_have_value("I enjoy building useful local tools.")

    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    panel.locator("#connection").filter(has_text="Connected — Fixture Student").wait_for()
    assert panel.locator("#company").inner_text() == "Fixture Company"
    assert panel.locator("#job").inner_text() == "Fixture Intern"
    expect(panel.locator("#filled")).to_have_text("Filled 14")
    assert "Unresolved" in panel.locator("#needs-input").inner_text()
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
    assert set(result["summary"]["needs_input"]) == {"Nonmatching choice", "Uncommitted choice"}
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
    assert fields['Resume/CV']['kind'] == fields['Cover Letter']['kind'] == 'file'
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
    assert [field['label'] for field in payload['fields']] == ['Resume/CV' if resume_label else 'Resume/CV', 'Cover Letter']
    assert [event[1] for event in page.evaluate('uploadEvents')] == ['input', 'change']
    assert page.evaluate('[submitClicks, submissions]') == [0, 0]



def test_sidepanel_answers_cover_helper_and_chat(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/form.html")
    page.evaluate("document.querySelector('form').insertAdjacentHTML('beforeend', '<label>Written open question<textarea id=helper-field required></textarea></label>')")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(tab => tab.url === url).id", page.url)
    worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    page.locator("#autoapply-sidebar iframe").wait_for()
    panel = next(frame for frame in page.frames if frame.url.startswith(f"chrome-extension://{extension_id}/sidepanel.html"))
    assert panel.locator("#tab-ask").count() == 0
    assert panel.locator('#fill-section').get_by_role('button', name='Ask AI', exact=True).count() == 0
    page.locator('#helper-field + .autoapply-ask-ai').get_by_role('button', name='Ask AI', exact=True).click()
    expect(panel.locator("#question")).to_have_value("Written open question")
    panel.locator("#generate").click()
    expect(panel.locator("#version")).to_have_text("1 / 1")
    panel.locator("#fill-answer").click()
    expect(page.locator("#helper-field")).to_have_value("I enjoy building useful local tools.")
    panel.locator("#tab-answers").click()
    expect(panel.locator(".answer-card").filter(has_text="Written open question").locator(".source-pill")).to_have_text("AI")
    panel.locator("#tab-fill").click()
    panel.get_by_label("Unresolved", exact=True).fill("User fixture answer")
    panel.locator(".prompt").filter(has_text="Unresolved").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#missing")).to_have_value("User fixture answer")
    panel.get_by_label("Unresolved", exact=True).wait_for(state="detached")
    remembered = next(payload for path, _, payload in requests if path == "/ext/remember")
    assert remembered["label"] == "Unresolved"
    assert remembered["value"] == "User fixture answer"
    assert remembered["application_id"] == 42
    panel.get_by_label("Which project should the letter highlight?", exact=True).fill("A fixture robotics project")
    panel.get_by_role("button", name="Generate with answers", exact=True).click()
    page.wait_for_function("document.querySelector('#cover').files.length === 1")
    assert page.locator("#cover").evaluate("e => e.files[0].name") == "fixture-cover.pdf"
    panel.locator("#cover-questions").get_by_role("button").wait_for(state="detached")
    cover = [payload for path, _, payload in requests if path == "/ext/cover-letter"][-1]
    assert cover["answers"] == [{"question": "Which project should the letter highlight?", "answer": "A fixture robotics project"}, {"question": "Anything else?", "answer": ""}]
    panel.locator("#tab-chat").click()
    panel.locator("#subtab-helper").click()
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
    assert refine["use_job_description"] is True
    assert "description" in refine["page"] and refine["page"]["url"] == page.url
    panel.locator("#use-job-description").uncheck()
    panel.locator("#refine").click()
    expect(panel.locator("#version")).to_have_text("3 / 3")
    disabled = [payload for path, _, payload in requests if path == "/ext/write"][-1]
    assert disabled["use_job_description"] is False and "description" not in disabled["page"]
    panel.locator("#previous").click()
    panel.locator("#previous").click()
    assert panel.locator("#answer").input_value() == first
    panel.locator("#next").click()
    assert panel.locator("#answer").input_value() == "Refined fixture answer."
    panel.evaluate("location.reload()")
    panel.locator("#tab-chat").wait_for()
    panel.locator("#tab-chat").click()
    panel.locator("#subtab-helper").click()
    panel.locator("#question").fill("Describe your robotics experience. Word limit 300")
    expect(panel.locator("#version")).to_have_text("2 / 3")
    panel.locator("#tab-chat").click()
    panel.locator("#subtab-chat").click()
    panel.locator("#chat-input").fill("How can I improve this answer?")
    panel.locator("#chat-input").press("Enter")
    panel.locator("#messages").filter(has_text="Fixture chat reply.").wait_for()
    chat = next(payload for path, _, payload in requests if path == "/ext/chat")
    assert chat["messages"] == [{"role": "user", "text": "How can I improve this answer?"}]
    assert chat["page"]["company"] == "Fixture Company"
    assert chat["use_job_description"] is True and "description" in chat["page"]
    panel.locator("#use-job-description").uncheck()
    panel.locator("#chat-input").fill("Help without job context")
    panel.locator("#send").click()
    expect(panel.locator(".bubble.assistant")).to_have_count(2)
    disabled_chat = [payload for path, _, payload in requests if path == "/ext/chat"][-1]
    assert disabled_chat["use_job_description"] is False and "description" not in disabled_chat["page"]
    expect(panel.locator(".bubble.user p").first).to_have_text("How can I improve this answer?")
    expect(panel.locator(".bubble.assistant p").first).to_have_text("Fixture chat reply.")
    assert "You:" not in panel.locator("#messages").inner_text()
    assert "AI:" not in panel.locator("#messages").inner_text()
    assert panel.locator(".bubble time").count() == 4
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
    interest = 'Are you interested in a Spring Co-Op, Summer Internship, or Both?'
    assert set(result['summary']['needs_input']) == {'Start date month', interest, 'Schedule', 'Areas'}
    assert result['summary']['skipped'] == 3
    fields = {field['label']: field for field in result['fields']}
    assert all(fields[label]['answered'] for label in ('Country', 'Preselected select', 'Preselected input'))
    assert page.get_by_role('listbox').count() == 0

    panel = context.new_page()
    panel.set_viewport_size({'width': 360, 'height': 900})
    panel.emulate_media(color_scheme=theme)
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    expect(panel.locator('#needs-input .prompt')).to_have_count(4)
    assert panel.locator('#needs-input input, #needs-input textarea').count() == 0
    assert 'Country' not in panel.locator('#needs-input').inner_text()
    choices = [
        ('Start date month', ['January', 'August'], 'August', '#month', '08'),
        (interest, ['Spring Co-Op', 'Summer Internship', 'Both'], 'Both', '[name=interest]', 'Both'),
        ('Schedule', ['Full time', 'Part time'], 'Part time', '#part-time', None),
        ('Areas', ['Hardware', 'Software'], ['Hardware', 'Software'], '#hardware', None),
    ]
    for label, options, chosen, selector, expected in choices:
        prompt = panel.locator('.prompt').filter(has=panel.get_by_label(label, exact=True))
        control = prompt.get_by_label(label, exact=True)
        assert control.evaluate('e => e.tagName') == 'SELECT'
        assert control.locator('option').all_text_contents() == (options if label == 'Areas' else ['Choose an answer', *options])
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
    assert "Phone country" not in result["summary"]["needs_input"]
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
    assert all(next(field for field in payload['fields'] if field['label'] == 'Phone country')['answered'] for payload in answers)
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
        panel.get_by_label("Unresolved", exact=True).fill("Reviewed while writing")
        panel.locator(".prompt").filter(has_text="Unresolved").get_by_role("button", name="Save", exact=True).click()
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


@pytest.mark.parametrize("date_controls", ["workday", "greenhouse"])
def test_repeatable_profile_sections(loaded_extension, extension_server, date_controls):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + "/sections.html")
    if date_controls == "greenhouse":
        page.evaluate("""() => {
            document.querySelector('#work button').textContent='Add another';
            // Also convert newly added month controls to native month selects.
            const convert = () => {for(const input of document.querySelectorAll('input[data-automation-id=dateSectionMonth]')) {
                const select=document.createElement('select');for(const attr of input.attributes) if(attr.name!=='type')select.setAttribute(attr.name,attr.value);
                select.innerHTML='<option value="">Month</option>'+Array.from({length:12},(_,i)=>'<option value="'+String(i+1).padStart(2,'0')+'">'+new Date(2024,i,1).toLocaleString('en-US',{month:'long'})+'</option>').join('');input.replaceWith(select);
            }};convert();new MutationObserver(convert).observe(document.querySelector('#work'),{childList:true,subtree:true});
        }""")
    profile = dict(
        experience=[dict(title="Engineer", company="Fixture", location="Boston", start_date="2024-08", end_date="", current=True, description="Built tools."),
                    dict(title="Freelancer", company="Self", start_date="", end_date="", current=True)],
        education=[dict(school="Fixture University", degree="Bachelor", major="Computing", start_date="", end_date="2028-05", current=False)],
        projects=[dict(title="Robot", organization="Builder", start_date="2025-03", end_date="2025-08", url="https://example.test/robot", technologies=["Python"], description="Built a robot. Tested it.")],
        skills=["Python", "JavaScript", "Rust"], links=dict(github="https://example.test/code", linkedin="https://example.test/me"))
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    prepared = worker.evaluate("""async ({id, profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: p => globalThis.autoApplyContent.prepareSections(p), args:[profile]}))[0].result""", dict(id=tab_id, profile=profile))
    missing = [f for f in prepared['fields'] if f.get('section_missing_date')]
    assert [f['label'] for f in missing] == ['Freelancer at Self: start date', 'Fixture University: start date']
    answers = prepared['answers'] + [dict(key=f['key'], status='NEEDS_INPUT') for f in missing]
    summary = worker.evaluate("""async ({id, answers}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: a => globalThis.autoApplyContent.fill(a), args:[answers]}))[0].result""", dict(id=tab_id, answers=answers))
    assert summary['needs_input'] == ['Freelancer at Self: start date', 'Fixture University: start date']
    assert page.locator('#work .entry').count() == 2
    assert int(page.locator('#work .entry').first.locator('[data-automation-id=dateSectionMonth]').first.input_value()) == 8
    assert page.locator('#work .entry').first.locator('[data-automation-id=dateSectionYear]').first.input_value() == '2024'
    assert page.locator('#work .entry input[type=checkbox]').first.is_checked()
    assert not page.locator('#work .entry .end').first.is_visible()
    assert page.locator('#projects input[type=date]').first.input_value() == '2025-03-01'
    assert page.locator('#projects input[type=date]').last.input_value() == '2025-08-01'
    assert page.locator('#skill-text').input_value() == 'Python, JavaScript, Rust'
    assert page.locator('#tags').inner_text() == 'PythonJavaScript'
    assert any(s == dict(name='skills', added=2, skipped=1) for s in prepared['sections'])
    assert 'Robot - Builder - Python: Built a robot.' in page.locator('#project-text textarea').input_value()
    assert page.locator('section').filter(has=page.locator('h2', has_text='Websites')).locator('input').count() == 2
    country = next(f for f in prepared['fields'] if f['label'] == 'Country')
    assert not country['answered']
    # A stable date question can be answered once and distributed to month/year controls.
    worker.evaluate("""async ({id, key}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: key => globalThis.autoApplyContent.fill([{key,status:'RESOLVED',value:'2023-02'}]),args:[key]}))[0].result""", dict(id=tab_id, key=missing[0]['key']))
    assert int(page.locator('#work .entry').last.locator('[data-automation-id=dateSectionMonth]').first.input_value()) == 2
    before = page.locator('form').evaluate("e => Array.from(e.querySelectorAll('input,textarea'),n=>[n.value,n.checked])")
    worker.evaluate("""async ({id, profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: async p => {const r=await globalThis.autoApplyContent.prepareSections(p);return globalThis.autoApplyContent.fill(r.answers)},args:[profile]}))[0].result""", dict(id=tab_id, profile=profile))
    assert page.locator('form').evaluate("e => Array.from(e.querySelectorAll('input,textarea'),n=>[n.value,n.checked])") == before
    assert page.evaluate('clicks') == dict(next=0, submit=0, submissions=0)


def test_saved_source_labels(loaded_extension):
    context, worker, extension_id = loaded_extension
    panel = context.new_page()
    worker.evaluate("""() => chrome.storage.session.set({lastResult:{tabId:999,page:{title:'Fixture'},
        completed:['profile:identity','USER_PROVIDED','verified_memory','verified_fuzzy_memory','AI','you'].map((source,i)=>({key:String(i),label:'Fact '+i,kind:'text',value:'Fixture',source})),
        pending:[],writing:[],summary:{filled:6,skipped:0,errors:[]},sections:[{name:'experience',added:2,skipped:0}]}})""")
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
    expect(panel.locator('.source-pill')).to_have_text(['Profile','Saved','Saved','Saved (similar)','AI','You'])
    assert panel.locator('#section-results').text_content() == 'Experience: 2 added'
    assert panel.locator('#answer-section-results').text_content() == 'Experience: 2 added'


def test_existing_resume_entry_and_displayed_choice(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/sections.html')
    page.evaluate("""() => {
        const fields=document.querySelectorAll('#work .entry input');fields[0].value='User title';fields[1].value='User employer';
        const choice=document.createElement('div');choice.className='select__control';choice.innerHTML='<input role="combobox" aria-label="Other choice" required><span class="selection">Selected fact</span>';document.querySelector('form').append(choice);
    }""")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    result = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async () => {
        const api=globalThis.autoApplyContent;
        const prepared=await api.prepareSections({experience:[{title:'Profile title',company:'Profile employer',start_date:'2024-08',current:true},{title:'Second',company:'Second employer',start_date:'2022-01',current:false}],projects:[],education:[],skills:[],links:{}});
        await api.fill(prepared.answers);return prepared;
    }}))[0].result""", tab_id)
    assert page.locator('#work .entry').first.locator('input').first.input_value() == 'User title'
    assert page.locator('#work .entry').first.locator('input').nth(1).input_value() == 'User employer'
    assert page.locator('#work .entry').first.locator('input').nth(3).is_checked() is False
    assert page.locator('#work .entry').last.locator('input').first.input_value() == 'Second'
    assert next(f for f in result['fields'] if f['label']=='Other choice')['answered'] is True
    assert page.evaluate('clicks') == dict(next=0,submit=0,submissions=0)


@pytest.mark.parametrize("collect_first", [False, True])
def test_greenhouse_empty_click_only_phone_country(loaded_extension, extension_server, collect_first):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + "/sections.html")
    country = page.locator("#country")
    country.focus()
    country.press("ArrowDown")
    assert page.locator("#country-options [role=option]").count() == 0
    page.locator(".select__control").dispatch_event("mousedown")
    assert page.locator("#country-options [role=option]").count() == 0
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    if collect_first:
        collected = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},
            func: async () => {const api=globalThis.autoApplyContent;const scan=await api.scan();
                const country=scan.fields.find(f=>f.label==='Country');
                return {initial:country, collected:await api.collectOptions([country.key])};}
        }))[0].result""", tab_id)
        assert not collected['initial']['answered'] and not collected['initial']['required']
        field = collected['collected']['fields'][0]
        assert not field['answered']  # Visible dial codes in the popup are not a selection.
        assert len(field['options']) == 244
        assert field['options'][:2] == ['United States +1', 'Afghanistan +93']
    result = worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    country_requests = [field for endpoint, _, payload in requests if endpoint == "/ext/answers"
                        for field in payload["fields"] if field["label"] == "Country" and field.get("options")]
    assert country_requests
    assert len(country_requests[-1]["options"]) == 244
    assert fit_options("United States", country_requests[-1]["options"]) == "United States +1"
    expect(page.locator(".select__single-value")).to_have_text("+1")
    assert country.input_value() == '' and country.get_attribute('aria-expanded') == 'false'
    assert any(f['label'] == 'Country' for f in result['completed'])
    assert 'Country' not in result['summary']['needs_input']
    assert not any(f['label'] == 'Country' for f in result['pending'])
    panel = context.new_page()
    panel.goto(f"chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}")
    expect(panel.locator('.prompt').filter(has_text=re.compile(r'^Country'))).to_have_count(0)
    again = worker.evaluate("id => autoApplyAutofill(id)", tab_id)
    assert next(f for f in again['fields'] if f['label'] == 'Country')['answered']
    assert 'Country' not in again['summary']['needs_input']
    assert page.evaluate('clicks') == dict(next=0, submit=0, submissions=0)


@pytest.mark.parametrize("displayed, selected, closed, placeholder_gone, committed", [
    ("+1", False, False, False, True),
    ("", True, False, False, True),
    ("🇺🇸", False, True, True, True),
    ("United Kingdom +44", True, True, True, False),
    ("+44", True, True, True, False),
])
def test_greenhouse_phone_country_commit_signals(
    loaded_extension, extension_server, displayed, selected, closed, placeholder_gone, committed,
):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + "/sections.html")
    page.evaluate("""config => {
        document.querySelector('#country-options').addEventListener('click', event => {
            document.querySelector('.select__single-value').textContent = config.displayed;
            event.target.setAttribute('aria-selected', String(config.selected));
            document.querySelector('#country').setAttribute('aria-expanded', String(!config.closed));
            if (!config.placeholder_gone) {
                const placeholder = document.createElement('div');
                placeholder.className = 'select__placeholder';
                document.querySelector('.select__value-container').append(placeholder);
            }
        });
    }""", dict(displayed=displayed, selected=selected, closed=closed, placeholder_gone=placeholder_gone))
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    summary = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},
        func: async () => {
            const api = globalThis.autoApplyContent;
            const country = (await api.scan()).fields.find(f => f.label === 'Country');
            return api.fill([{key:country.key, status:'RESOLVED', value:'United States', source:'profile_rule'}]);
        }
    }))[0].result""", tab_id)
    assert summary['filled'] == int(committed)
    assert summary['needs_input'] == ([] if committed else ['Country'])
    assert [f['label'] for f in summary['completed']] == (['Country'] if committed else [])
    assert page.locator('#country').input_value() == ''
    assert page.evaluate('clicks') == dict(next=0, submit=0, submissions=0)


def robust_fixture(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, requests = extension_server
    worker.evaluate("config => chrome.storage.local.set(config)", {"server": server, "key": KEY})
    page = context.new_page()
    page.goto(server + '/workday-robust.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    return context, worker, extension_id, page, tab_id, requests


def test_workday_real_labels_conditional_options_and_attached_resume(loaded_extension, extension_server):
    context, worker, extension_id, page, tab_id, requests = robust_fixture(loaded_extension, extension_server)
    result = worker.evaluate("id=>globalThis.autoApplyAutofill(id)", tab_id)
    assert not result.get('error'), result
    expect(page.locator('input[aria-labelledby=real-question]')).to_have_value('Fixture Student')
    expect(page.locator('#email')).to_have_value('student@example.test')
    choice = next(f for f in result['pending'] if f['label']=='Lazy choice')
    assert choice['options'] == ['Yes', 'No']
    assert not any('secondaryQuestionnaire--' in f['label'] for f in result['fields'])
    assert any(f['value']=='existing-resume.pdf' for f in result['completed'])
    assert not any(endpoint=='/ext/resume' for endpoint, _, _ in requests)
    assert page.evaluate('clicks') == {'next':0,'submit':0,'uploads':0}
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    assert not panel.locator('#auto-advance').is_checked()
    panel.locator('#next-page').click()
    expect(page.locator('#submit')).to_have_text('Submit')
    panel.locator('#next-page').click()
    assert page.evaluate('clicks.submit') == 0


def test_workday_load_options_retry(loaded_extension, extension_server):
    context, worker, extension_id, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate('optionsEnabled=false')
    result = worker.evaluate("id=>globalThis.autoApplyAutofill(id)", tab_id)
    assert next(f for f in result['pending'] if f['label']=='Lazy choice')['options'] == []
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    page.evaluate('optionsEnabled=true')
    panel.get_by_role('button',name='Load options').click()
    expect(panel.locator('#needs-input select option')).to_have_text(['Choose an answer','Yes','No'])


@pytest.mark.parametrize('cancel,sign_in', [(False,True),(False,False),(True,True)])
def test_delayed_form_poll_and_cancellation(loaded_extension, extension_server, cancel, sign_in):
    context, worker, extension_id, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("""() => {
        const main=document.querySelector('#main');
        main.innerHTML='<div data-automation-id="signInContent">Sign in<input type="password"></div>';
        setTimeout(()=>{main.innerHTML='<form><label>Full name *<input id="delayed" required></label></form>';},2500);
    }""")
    if not sign_in:
        page.evaluate("document.querySelector('#main').innerHTML=''")
    result = worker.evaluate("id=>globalThis.autoApplyAutofill(id)", tab_id)
    assert result['waiting'] and not result['busy']
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    if cancel:
        panel.locator('#cancel-form-wait').click()
        page.wait_for_timeout(4500)
        expect(page.locator('#delayed')).to_have_value('')
        expect(panel.locator('#progress')).to_have_text('Form waiting cancelled.')
    else:
        expect(page.locator('#delayed')).to_have_value('Fixture Student',timeout=12000)


def test_delayed_form_poll_recovers_after_scanner_transition(loaded_extension, extension_server):
    context, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("""() => {
        const main = document.querySelector('#main');
        main.innerHTML = '';
        setTimeout(() => {main.innerHTML = '<form><label>Full name *<input id="delayed" required></label></form>';}, 2500);
    }""")
    worker.evaluate("""async id => chrome.scripting.executeScript({target:{tabId:id}, func:() => {
        const api = globalThis.autoApplyContent;
        delete globalThis.autoApplyContent;
        setTimeout(() => {globalThis.autoApplyContent = api;}, 500);
    }})""", tab_id)
    result = worker.evaluate("id => globalThis.autoApplyAutofill(id)", tab_id)
    assert not result.get('error'), result
    assert result['waiting'] and not result['busy']
    worker.evaluate("""async id => chrome.scripting.executeScript({target:{tabId:id}, func:() => {
        const api = globalThis.autoApplyContent, scan = api.scan;
        api.scan = () => {throw Error('Temporary document transition');};
        setTimeout(() => {api.scan = scan;}, 3500);
    }})""", tab_id)
    expect(page.locator('#delayed')).to_have_value('Fixture Student', timeout=12000)
    assert page.evaluate('clicks.submit') == 0


@pytest.mark.parametrize('validation', [False, True])
def test_auto_advance_stops_at_submit_or_validation(loaded_extension, extension_server, validation):
    context, worker, extension_id, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("validation=>{window.validation=validation;document.querySelector('#lazy').required=false;document.querySelector('#lazy').value='Yes';}", validation)
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    panel.locator('#auto-advance').check()
    worker.evaluate("""async () => {for(let n=0;n<100;n++){const {autoAdvanceProfiles={}}=await chrome.storage.local.get('autoAdvanceProfiles');if(Object.values(autoAdvanceProfiles).includes(true))return;await new Promise(r=>setTimeout(r,20));}throw Error('Preference not saved');}""")
    result = worker.evaluate("id=>globalThis.autoApplyAutofill(id)",tab_id)
    assert not result.get('error'), result
    assert page.evaluate('clicks.next') == 1
    assert page.evaluate('clicks.submit') == 0
    if validation:
        assert any('correct the required' in error for error in result['summary']['errors'])
    else:
        expect(page.locator('#submit')).to_have_text('Submit')
    panel.reload()
    expect(panel.locator('#auto-advance')).to_be_checked()


@pytest.mark.parametrize('caption,accessible', [('Submit',''),('Submit Application',''),('Apply',''),('Apply Now',''),('Next','Submit Application')])
def test_next_page_rejects_final_controls(loaded_extension, extension_server, caption, accessible):
    _, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("args=>{const next=document.querySelector('#next');next.textContent=args.caption;if(args.accessible)next.setAttribute('aria-label',args.accessible);}", {'caption':caption,'accessible':accessible})
    response=worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>globalThis.autoApplyContent.nextPage()}))[0].result""",tab_id)
    assert response['clicked'] is False
    assert page.evaluate('clicks') == {'next':0,'submit':0,'uploads':0}


def test_workday_preceding_text_replaces_identifier_label(loaded_extension, extension_server):
    _, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("""() => {
        const input=document.querySelector('[aria-labelledby=real-question]');
        input.setAttribute('aria-labelledby','fake-label');
        const fake=document.createElement('span');fake.id='fake-label';fake.textContent='secondaryQuestionnaire__abcdef0123456789';fake.hidden=true;document.body.append(fake);
        document.querySelector('#real-question').removeAttribute('data-automation-id');
    }""")
    response=worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:()=>globalThis.autoApplyContent.scan()}))[0].result""",tab_id)
    assert response['fields'][0]['label'] == 'Full name'


def test_greenhouse_filename_and_remove_skip_resume(loaded_extension, extension_server):
    _, worker, _, page, tab_id, _ = robust_fixture(loaded_extension, extension_server)
    page.evaluate("""() => {
        const region=document.querySelector('[data-automation-id=formField-resume]');
        region.removeAttribute('data-automation-id');region.className='upload';region.querySelector('div').innerHTML='old-cv.docx <button type="button">Remove</button>';
    }""")
    response=worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        const api=globalThis.autoApplyContent;const {fields}=await api.scan();const field=fields.find(f=>f.kind==='file');
        const filled=await api.fill([{key:field.key,status:'FILE',value:'resume',force:true}],{resume:{filename:'replacement.pdf',mime:'application/pdf',base64:'YWJj'}});
        return {field,filled};}}))[0].result""",tab_id)
    assert response['field']['answered'] is True
    assert page.evaluate('clicks.uploads') == 0


def test_workday_experience_education_and_languages(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/workday-experience.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    profile = dict(experience=[dict(title='Freelancer', company='Self', start_date='2019', current=True),
                               dict(title='Crew', company='Moge Tee', start_date='2026-06', end_date='2026-08', current=False)],
                   education=[dict(school='Fixture University', degree="Bachelor's", major='Computer Science',
                                   start_date='2024-08', end_date='2028-05', gpa='3.8')],
                   languages=[dict(language='English', fluent=True, proficiency='highest')])
    invoke = """async ({id,profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: async p => {const api=globalThis.autoApplyContent;const result=await api.prepareSections(p);
                         await api.fill(result.answers);return result;},args:[profile]}))[0].result"""
    prepared = worker.evaluate(invoke, dict(id=tab_id, profile=profile))
    work = page.locator('#work .entry')
    assert work.count() == 2
    assert work.first.locator('[data-automation-id=dateSectionMonth-input]').first.input_value() == '01'
    assert work.first.locator('[data-automation-id=dateSectionYear-input]').first.input_value() == '2019'
    assert work.first.locator('[type=checkbox]').is_checked()
    assert work.first.locator('.end input').first.input_value() == ''
    assert not work.first.locator('.end').is_visible()
    assert work.last.locator('[data-automation-id=dateSectionMonth-input]').first.input_value() == '06'
    assert work.last.locator('[data-automation-id=dateSectionMonth-input]').last.input_value() == '08'
    assert not work.last.locator('[type=checkbox]').is_checked()
    order = page.evaluate('order')
    adds = [i for i, event in enumerate(order) if event == 'add:work']
    assert any(event == 'fill:work' for event in order[adds[0]+1:adds[1]])
    assert '2019' in page.evaluate('commits')
    assert page.locator('#education .entry').count() == 1
    assert page.get_by_label('Degree', exact=True).input_value() == "Bachelor's Degree"
    assert page.get_by_label('Field of Study', exact=True).input_value() == 'Computer Science'
    assert page.get_by_label('To', exact=True).input_value() == '05/2028'
    assert page.get_by_label('GPA', exact=True).input_value() == '3.8'
    assert page.locator('#languages .entry').count() == 1
    assert page.get_by_label('Language', exact=True).input_value() == 'English'
    assert page.get_by_label('I am fluent in this language', exact=True).is_checked()
    for name in ('Comprehension', 'Overall', 'Reading', 'Speaking', 'Writing', 'Listening'):
        assert page.get_by_label(name, exact=True).input_value() == '5 - Fluent'
    assert next(f for f in prepared['fields'] if f['label']=='When are you expected to graduate?')['placeholder'] == 'MMM-YY'
    assert prepared['completed']
    before = page.evaluate('order.length')
    worker.evaluate(invoke, dict(id=tab_id, profile=profile))
    assert page.evaluate('order.length') == before
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)


def test_workday_degree_discipline_and_word_proficiency(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/workday-experience.html')
    page.locator('#education button').click()
    page.locator('#languages button').click()
    page.evaluate("""() => {
        const degree=document.querySelector('#education select');
        degree.innerHTML='<option value="">Select one</option><option>Computer Science</option><option>Math</option>';
        for(const select of document.querySelectorAll('#languages select')) if(select.getAttribute('aria-label')!=='Language')
            select.innerHTML='<option value="">Select one</option><option>Basic</option><option>Advanced</option><option>Fluent</option><option>Native</option>';
    }""")
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t => t.url === url).id", page.url)
    worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async () => {
        await globalThis.autoApplyContent.prepareSections({education:[{school:'Fixture',degree:"Bachelor's",major:'Computer Science'}],
            languages:[{language:'English',fluent:true,proficiency:'highest'}]});
    }}))[0].result""", tab_id)
    assert page.get_by_label('Degree', exact=True).input_value() == 'Computer Science'
    for name in ('Comprehension', 'Overall', 'Reading', 'Speaking', 'Writing', 'Listening'):
        assert page.get_by_label(name, exact=True).input_value() == 'Native'
    for option in ('Bachelors', 'BS', 'B.S.', 'Bachelor of Science'):
        page.get_by_label('Degree', exact=True).evaluate("(e,option)=>{e.innerHTML='<option value="">Select one</option>';const o=new Option(option,option);e.add(o);}", option)
        summary = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async () => {
            const api=globalThis.autoApplyContent;const field=(await api.scan()).fields.find(f=>f.label.startsWith('Degree') && f.kind==='select');
            return api.fill([{key:field.key,status:'RESOLVED',value:"Bachelor's"}]);
        }}))[0].result""", tab_id)
        assert summary and summary.get('filled') == 1, summary
        assert page.get_by_label('Degree', exact=True).input_value() == option
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)


def test_t039_labels_rules_navigation_and_snapshot(loaded_extension, extension_server):
    context, worker, extension_id = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    worker.evaluate("config=>chrome.storage.local.set(config)", {"server":server,"key":KEY})
    page.goto(server + '/t039.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    response = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        const api=globalThis.autoApplyContent, scanned=await api.scan();
        const filled=await api.fill(scanned.fields.filter(f=>f.label.includes('Address')||f.label==='Suite'||f.label==='Phone Type').map(f=>({key:f.key,status:'RESOLVED',value:f.label==='Phone Type'?'Mobile':'Never insert'})));
        return {scanned,filled,snapshot:api.pageStructure(),next:await api.nextPage()};
    }}))[0].result""", tab_id)
    labels = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        const variants=['Select One Required','select','Choose','Please Select','Required','Optional','*','Search','-- select --'];
        const result=[];
        for(const value of variants){
            document.querySelector('[aria-haspopup=listbox]').setAttribute('aria-label',value);
            document.querySelector('#major').setAttribute('aria-label',value);
            document.querySelector('fieldset').setAttribute('aria-label',value);
            result.push((await globalThis.autoApplyContent.scan()).fields.map(f=>f.label));
        }
        return result;
    }}))[0].result""", tab_id)
    assert all('Phone Device Type' in rows and 'Your major' in rows and 'Previously employed?' in rows for rows in labels)
    fields = {f['label']: f for f in response['scanned']['fields']}
    assert {'Phone Device Type', 'Previously employed?', 'Tell us why', 'Address line2', 'Suite', 'Address 2', 'Your major'} <= fields.keys()
    assert fields['Phone Device Type']['required']
    assert fields['Previously employed?']['options'] == ['Yes', 'No']
    assert response['filled']['needs_input'] == ['Address 2']
    assert page.locator('#address').input_value() == ''
    assert page.locator('#suite').input_value() == ''
    assert page.locator('#phone-type').input_value() == 'Cell'
    assert response['next']['clicked'] and page.evaluate('nextClicks') == 1
    serialized = json.dumps(response['snapshot'])
    assert 'SECRET_VALUE' not in serialized and 'SECRET_FILE' not in serialized
    assert 'Phone Device Type' in serialized and 'formField-phoneType' in serialized
    final = worker.evaluate("""async id => (await chrome.scripting.executeScript({target:{tabId:id},func:()=>globalThis.autoApplyContent.nextPage()}))[0].result""", tab_id)
    assert final['message'] == 'Final submit is yours'
    assert page.evaluate('submitClicks') == 0
    panel = context.new_page()
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html?tabId={tab_id}')
    panel.locator('#send-structure').click()
    expect(panel.locator('#snapshot-status')).to_have_text('Page structure sent.')
    worker.evaluate("""async id=>chrome.storage.session.set({tabResults:{[id]:{tabId:id,busy:true,waiting:true,fields:[],pending:[],writing:[],page:{},summary:{filled:0,skipped:0,errors:[]}}}})""", tab_id)
    expect(panel.locator('#autofill')).to_be_disabled()
    for name in ('fill','answers','chat'):
        panel.locator('#tab-'+name).click()
        expect(panel.locator('#next-page')).to_be_visible()
        expect(panel.locator('#next-page')).to_be_enabled()
        assert panel.locator('#next-page').bounding_box()['y'] < panel.viewport_size['height']
    panel.locator('#next-page').click()
    expect(panel.locator('#progress')).to_have_text('Final submit is yours')
    page.locator('#submit').evaluate('e=>e.remove()')
    panel.locator('#next-page').click()
    expect(panel.locator('#progress')).to_have_text('No Next page button found.')
    assert page.evaluate('submitClicks') == 0
    worker.evaluate("id=>globalThis.autoApplySidebar(id,'minimized')",tab_id)
    flag = page.locator('#autoapply-sidebar').get_by_role('button',name='Restore AutoApply sidebar')
    box=flag.bounding_box()
    page.mouse.move(box['x']+5,box['y']+5)
    page.mouse.down()
    page.mouse.move(box['x']+5,box['y']+105,steps=5)
    page.mouse.up()
    assert flag.bounding_box()['y'] > box['y']+50
    saved=worker.evaluate("async()=> (await chrome.storage.local.get('flagPosition:127.0.0.1'))['flagPosition:127.0.0.1']")
    assert saved >= 0
    page.reload()
    expect(flag).to_be_visible()
    assert abs(flag.bounding_box()['y']-saved) < 2
    expect(page.locator('#large + .autoapply-ask-ai')).to_have_count(1)
    writing = page.locator('#essay + .autoapply-ask-ai')
    expect(writing).to_have_count(1)
    page.locator('#essay').evaluate('e=>e.remove()')
    expect(writing).to_have_count(0)


@pytest.mark.parametrize("initial", ["none", "empty", "user"])
def test_t040_real_workday_repeatable_sections(loaded_extension, extension_server, initial):
    context, worker, extension_id = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/t040.html')
    if initial != 'none':
        page.evaluate('initialEntry()')
    if initial == 'user':
        page.locator('#work [data-automation-id=formField-jobTitle] input').fill('User role')
        page.locator('#work [data-automation-id=formField-companyName] input').fill('User employer')
    profile = dict(experience=[dict(title='Freelance Software Developer', company='Self', start_date='2019', current=True),
                               dict(title='Engineer', company='Fixture', start_date='2025-02', end_date='2026-03')],
                   education=[dict(school='Fixture University', degree='Bachelor of Science', major='Computer Science',
                                   start_date='2024', end_date='2028-05', gpa='3.5', current=True)],
                   skills=['Python', 'JavaScript', 'Missing Skill'])
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    invoke = """async ({id,profile}) => (await chrome.scripting.executeScript({target:{tabId:id},
        func: async profile => {const api=globalThis.autoApplyContent;const prepared=await api.prepareSections(profile);
            await api.fill(prepared.answers);return prepared;},args:[profile]}))[0].result"""
    prepared = worker.evaluate(invoke, dict(id=tab_id, profile=profile))
    work = page.locator('#work .entry')
    assert work.count() == 2
    assert work.first.locator('[data-automation-id=formField-jobTitle] input').input_value() == ('User role' if initial=='user' else 'Freelance Software Developer')
    if initial != 'user':
        assert work.first.locator('[type=checkbox]').is_checked()
        assert work.first.locator('.end input').first.is_disabled()
        assert not work.first.locator('.end').is_visible()
        assert work.first.locator('[data-automation-id=dateSectionMonth-input]').first.input_value() == '01'
        assert work.first.locator('[data-automation-id=dateSectionYear-input]').first.input_value() == '2019'
        assert 'current:click' in page.evaluate('order')
    assert work.last.locator('[data-automation-id=formField-jobTitle] input').input_value() == 'Engineer'
    assert work.last.locator('[data-automation-id=dateSectionMonth-input]').first.input_value() == '02'
    assert work.last.locator('[data-automation-id=dateSectionYear-input]').last.input_value() == '2026'
    assert page.locator('#education .entry').count() == 1
    assert page.locator('[data-automation-id=formField-firstYearAttended] input').input_value() == '2024'
    assert page.locator('[data-automation-id=formField-lastYearAttended] input').input_value() == '2028'
    assert '2024' in page.evaluate('commits')
    assert page.evaluate("dateKeys.every(key=>/^[0-9]$/.test(key))")
    assert page.evaluate('dateKeys.length') >= 20
    assert page.locator('[data-automation-id=formField-fieldOfStudy] [data-automation-id=selectedItem]').inner_text() == 'Computer Science'
    assert page.locator('#skills [data-automation-id=selectedItem]').all_text_contents() == ['Python', 'JavaScript']
    assert page.evaluate('searches') == ['Computer Science', 'Python', 'JavaScript', 'Missing Skill']
    assert all(',' not in value for value in page.evaluate('searches'))
    assert next(s for s in prepared['sections'] if s['name']=='skills') == dict(name='skills', added=2, skipped=1)
    experience = next(s for s in prepared['sections'] if s['name']=='experience')
    assert experience['added'] == (1 if initial=='user' else 2)
    assert experience['total'] == 2
    assert next(s for s in prepared['sections'] if s['name']=='education')['added'] == 1
    order = page.evaluate('order')
    adds = [i for i,event in enumerate(order) if event=='add:work']
    assert len(adds) == (2 if initial=='none' else 1)
    if initial=='none':
        assert 'fill:work' in order[adds[0]+1:adds[1]]
    # React may remove completed entries' old Add button; re-query for Add Another.
    assert page.locator('#work [data-automation-id=add-button]').inner_text() == 'Add Another'
    before = page.locator('form').evaluate("e=>Array.from(e.querySelectorAll('input'),n=>[n.value,n.checked])")
    worker.evaluate(invoke, dict(id=tab_id, profile=profile))
    assert work.count() == 2
    assert page.locator('form').evaluate("e=>Array.from(e.querySelectorAll('input'),n=>[n.value,n.checked])") == before
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)
    panel = context.new_page()
    worker.evaluate("sections=>chrome.storage.session.set({lastResult:{tabId:999,page:{title:'Fixture'},completed:[],pending:[],writing:[],summary:{filled:0,skipped:0,errors:[]},sections}})", prepared['sections'])
    panel.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    expect(panel.locator('#section-results')).to_have_text(f"Experience {experience['added']}/2 · Education 1/1 · Skills: 2 added / 1 not in list")


def test_t040_inferred_current_and_extra_user_entries(loaded_extension, extension_server):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/t040.html')
    tab_id = worker.evaluate("async url => (await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        await globalThis.autoApplyContent.prepareSections({experience:[{title:'Current',company:'Self',start_date:'2024'}]});
    }}))[0].result""", tab_id)
    assert page.locator('#work [type=checkbox]').is_checked()
    assert page.locator('#work .end input').first.is_disabled()
    page.evaluate("initialEntry();initialEntry()")
    page.locator('#work .entry').last.locator('[data-automation-id=formField-jobTitle] input').fill('Extra user entry')
    before = page.evaluate('order.length')
    worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
        await globalThis.autoApplyContent.prepareSections({experience:[{title:'Current',company:'Self',start_date:'2024'}]});
    }}))[0].result""", tab_id)
    assert page.locator('#work .entry').count() == 3
    assert page.locator('#work .entry').last.locator('[data-automation-id=formField-jobTitle] input').input_value() == 'Extra user entry'
    assert page.evaluate('order.length') == before
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)


@pytest.mark.parametrize("mode", ["delayed_add", "rejected_date", "portal_prompt"])
def test_t040_waits_verifies_and_handles_prompt_portals(loaded_extension, extension_server, mode):
    context, worker, _ = loaded_extension
    server, _ = extension_server
    page = context.new_page()
    page.goto(server + '/t040.html')
    if mode == 'delayed_add':
        page.evaluate("() => {document.querySelector('#work button').onclick=()=>setTimeout(initialEntry,1800);}")
    elif mode == 'rejected_date':
        page.evaluate("() => {initialEntry();document.querySelector('#work [data-automation-id=dateSectionYear-input]').oninput=e=>e.target.value='';}")
    else:
        page.evaluate("""() => {
            const input=document.querySelector('#skills input'), list=document.querySelector('#skills .results');
            document.body.append(list);
            input.closest('[data-automation-id=formField-skills]').querySelector('label').textContent='Technology prompt';
        }""")
    tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", page.url)
    result = worker.evaluate("""async ({id,mode})=>(await chrome.scripting.executeScript({target:{tabId:id},func:async mode=>{
        const api=globalThis.autoApplyContent;
        if(mode==='portal_prompt') {
            const field=(await api.scan()).fields.find(f=>f.label==='Technology prompt');
            return {field,filled:await api.fill([{key:field.key,status:'RESOLVED',value:['Python','JavaScript']}])};
        }
        return api.prepareSections({experience:[{title:'Current',company:'Self',start_date:'2024'},
            ...(mode==='rejected_date'?[{title:'Second',company:'Other',start_date:'2020',end_date:'2022'}]:[])]});
    },args:[mode]}))[0].result""", dict(id=tab_id, mode=mode))
    if mode == 'portal_prompt':
        assert result['field']['kind'] == 'combobox'
        assert result['field']['multiple'] is True
        assert result['filled']['filled'] == 1
        assert page.locator('#skills [data-automation-id=selectedItem]').all_text_contents() == ['Python', 'JavaScript']
    else:
        assert page.locator('#work .entry').count() == 1
        section = next(s for s in result['sections'] if s['name']=='experience')
        assert section['added'] == (0 if mode=='rejected_date' else 1)
        assert page.locator('#work [data-automation-id=dateSectionYear-input]').first.input_value() == ('' if mode=='rejected_date' else '2024')
    assert page.evaluate('clicks') == dict(submit=0, submissions=0)
