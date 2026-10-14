# Local AutoApply dashboard

## Chrome extension

Load the repository's `extension/` directory as an unpacked extension at
`chrome://extensions` with Developer mode enabled. Open **Pairing settings**,
enter the dashboard URL and the Chrome extension pairing key from
**Settings → Dashboard**, then save and test the connection. The extension's
toolbar action opens a side panel; **Autofill this page** or **Alt+Shift+F** fills
the current application and its accessible embedded frames. It preserves existing
values, attaches the resume, and lists unresolved questions. Orange outlines mark
required fields needing review. Submission remains manual. AI writing, chat,
cover-letter generation and batch controls are reserved for later tasks.
See [the extension README](../extension/README.md) for setup and fixture checks.

## Extension API

Settings → Dashboard shows the Chrome extension pairing key with Copy and
Regenerate controls. Regeneration revokes the previous key. Every `/ext/*`
operation requires `X-AutoApply-Key`; CORS and OPTIONS preflight allow only
`chrome-extension://` origins, GET/POST and Content-Type/X-AutoApply-Key headers.
Preflight does not carry the key. The existing loopback Host check still applies.
Existing pairing keys are read without a write transaction; only initial creation
and regeneration require one. Unexpected `/ext/*` and `/api/*` failures return
JSON HTTP 503 with `{"error": "Dashboard is busy, retry shortly"}`.

The local API provides GET `/ext/ping`, POST `/ext/match` (`url`), POST
`/ext/answers` (`url`, optional `application_id`, `page`, `fields`), POST
`/ext/remember` (`url`, optional `application_id`, `label`, `kind`, `options`,
`value`), and GET `/ext/resume` (PDF filename, MIME type and base64 bytes).
Answers use the existing deterministic profile and verified memory resolver;
unresolved fields report NEEDS_INPUT, OPTIONAL_SKIP or NEEDS_WRITING. File fields
report FILE with resume/cover_letter. Overlength answers require input. Longer
extracted descriptions are stored locally. Remembered answers preserve field
kind, options and application scope and appear in the existing saved answer store.
These endpoints never submit applications. Only `POST /ext/applied` marks a listing
as applied after detected confirmation of the user’s manual submission; it records
`{application_id, url, evidence}` and reuses the protected dashboard check-off path.
Repeated confirmations are idempotent. `POST /ext/unapplied` reuses dashboard Undo.

Question labels also use normalized whole-word keyword concepts when the existing
rules do not classify them. Verified saved answers can match reworded labels with
at least 60% content-word overlap, within the same concept and scope, compatible
field kind and options, and current policy/profile constraints. Their existing
source field reports `verified_fuzzy_memory`.

When `work_authorization.us_authorized` is true, free-text authorization answers
are exactly “I am legally authorized to work for any employer in the United States.”
Choice fields select an affirmative authorization option, or Yes. Sponsorship,
citizenship, temporary authorization, visa status and clearance remain separate;
authorization never supplies those facts.

POST `/api/ext-run` accepts ordered `application_ids` from the current priority
or manual lists. GET `/api/ext-run` returns items and the current opened id;
POST `/api/ext-run/cancel` skips remaining items. Dashboard writes retain the
existing same-origin dashboard token requirement. GET `/ext/run/next` claims
the next pending item; POST `/ext/run/result` accepts `application_id`,
`state` (filled, needs_input, failed, skipped) and optional `detail`.
Runs live only in dashboard memory. Confirmed manual submissions update an item
to `submitted` through `/ext/applied`; generic confirmation text requires a submit
click in the same tab within two minutes. Validation errors prevent detection.
Final submission stays manual. Finished or cancelled runs collapse after a brief
summary toast; Last run reopens results.

The toolbar and Alt+Shift+S toggle an in-page, shadow-root isolated sidebar.
Alt+Shift+F opens it and fills. Each tab remembers open, closed or minimized state.
Fill, Answers, Ask AI and Chat retain the answer helper, refinement history,
cover-letter questions, downloads, pairing and run pause controls. Answers can be
edited inline; ordinary field edits on the page are learned automatically. Passwords,
files and hidden/honeypot fields are excluded; essays stay in this page's Answers
view without becoming reusable memory. Cover letters generate automatically only
when an upload field exists; Generate cover letter remains available manually.
Autofill returns after deterministic fields are filled, so the form can be reviewed
while AI answers fill progressively and the cover letter is written and attached
in the background. Separate progress lines show writing, attachment, or errors.
Chat uses timestamped bubbles and a one-row composer (Enter sends; Shift+Enter
adds a newline). Completed answers use compact rows with inline Save/Cancel edits.
Phone formatting follows the page's national-number and country-code controls;
Phone Extension stays empty and the profile number is unchanged.

Start from the repository directory:

```powershell
.\.venv\Scripts\python.exe -m autoapply dashboard --port 8765 --liveness-minutes 30 --no-browser
```

Visit `http://127.0.0.1:8765/`. Omit `--no-browser` to open the local page
automatically. Ctrl+C stops the server. The liveness interval defaults to 30
minutes and must be at least 5; the port defaults to 8765.

Startup skips full history validation/export and listing maintenance. The HTTP
server begins serving before the first background liveness sweep and browser
launch. The page loads immediately with an empty queue, then fetches its lists.
Queue reads cache the existing selector's ranked rows, with candidate details
fetched in one query. Database commits, profile edits and configuration changes
invalidate the cache; it also expires after 30 seconds for time-based eligibility.
GET reads allow 30 seconds. Background list polls retry once quietly before
showing a friendly message such as `Queue: request timed out`, preserving the
last successful list.

The dashboard opens on **Run**, with the batch controls at the top. Its 56px fixed
navbar shows worker status (Idle, Running, Paused or Stopping) and a 32px
**Reload data** circular-arrow icon, which only reloads dashboard data. It spins
while loading and shows elapsed seconds, then a ticking **Updated Ns ago** label
with the absolute completion time on hover. Reduced-motion preferences stop the spin. Tabs are ordered **Run**, **Queue**, **Applications**, **Answers**, **Settings**.
Applications has a remembered **Needs you**, **Failed**, **Applied** segmented switch
with counts; its navbar badge shows the Needs you count. Applied includes Not eligible.
Select a tab with the mouse or Left/Right arrow keys;
Home/End select the first/last tab. The selected tab persists in the URL hash
(`#run`, `#priority`, `#applications`, `#answers`, `#settings`); `#queue` also opens Queue.
Legacy `#needs-you`, `#failed` and `#applied` links select the matching Applications segment.
Every tab defaults to a dense **List** view and offers **Grid**, remembered per tab
in localStorage when available. Grid cards use a compact company/checkbox header,
role and ID, a status/ATS/relative-time line, up to three detail lines with full
text on hover, and an Open footer link. A client-side filter matches company, role or ID;
counts reflect the full list before filtering. Press `/` outside a text field
to focus the current tab's filter. The neutral page follows your system's
light/dark theme and stacks rows on narrow screens. Polls reconcile rows in place. Errors
appear in a dismissible banner without discarding the last successful list.
Hidden notifications reserve no space below the tabs. Run's count is the current
published batch size; the other counts reflect their respective lists.

Checking the **Applied** checkbox on Run, Needs you, Failed or Queue records that you applied
manually. The item disappears immediately from those tabs and appears in
**Applied**. An eight-second toast offers **Undo**. Unchecking in Applied puts
a dashboard mark back, with an Undo toast to mark it again. Queue ranks renumber
immediately after removal; both actions refetch the queue and all tab counts,
waiting for any older reads to finish before issuing fresh requests. Permanent
records show a disabled checked checkbox with a **Recorded in Discord** tooltip.
Failed requests restore the item and show an error. Check-offs record a duplicate
guard; they do not submit to an employer.

Queue and Needs you rows also offer **Not eligible** beside Applied, in both List
and Grid. The dialog names the company and role and offers reasons for degree,
citizenship/clearance, experience, graduation date, location, student/internship
requirements, or Other. Notes are optional and limited to 500 characters; Other
requires a nonblank note. Cancel or Esc closes the dialog and returns focus to
the button. Marking removes the item and immediately renumbers the queue; an
eight-second **Marked not eligible** toast offers **Undo**. Applied includes a
**Not eligible** section with company, title, reason, note, local mark date and
**Restore**. Its count includes both recorded applications and not eligible items.

`POST /api/not-eligible` accepts `{application_id, reason, note}` (note defaults
to empty). It stores `{reason, note, marked_at, source: 'DASHBOARD_MANUAL'}` in
`user_not_eligible:<id>` and records `USER_MARKED_NOT_ELIGIBLE` in one transaction.
`POST /api/eligible` accepts `{application_id}` and clears only a dashboard manual
mark, recording `USER_UNMARKED_NOT_ELIGIBLE`. Both require the same token/origin,
positive integer ID and protected-ID rules as Applied; unknown IDs return 404,
invalid input 400, and conflicting marks 409. The mark is separate from submission
guards and leaves application state, retries and holds unchanged. `GET /api/applied`
adds `not_eligible` to its existing `applications` response. Discovery excludes
truthy not eligible settings, so future batch selection follows the same queue.
Both writes invalidate the queue cache and refresh the displayed lists.

Only the content below the fixed navbar scrolls, with a stable scrollbar gutter
on the main content container and fixed navbar count-badge space. Switching tabs
or application segments scrolls content to the top.

Lists show 50 items per page by default in List and Grid. Their footers show the
range, page count, previous/next arrows and a Per page input (10..200). Integer
values are clamped; invalid or fractional input returns to the last valid value.
The per-page preference and each list's sort are remembered in localStorage when
available. Click any column header to sort, then click again to reverse direction.
Queue defaults to rank ascending; other lists retain their initial order until
sorted. Filters and sort changes return to page 1. Manual marks retain the current
page while it exists, otherwise moving to the last page.

Company and role names decode HTML entities for display. States, readiness,
security, error categories and batch phases use readable labels; hover over a
label to see its original code. Reason text omits leading category or
`[redacted]:` prefixes. Times use your local timezone with a relative hint,
and hovering shows the original ISO timestamp. Missing times show a placeholder.

The Queue tab shows rank, application ID, company linked to its listing,
title, ATS, posted date, listing status and the latest liveness result/time,
alongside the subtle sweep summary and **Find new listings** action. It calls `fill_batch.discover`
directly, so ranking, queued/retry filters, retry deadlines, freshness, candidate
policy, historical exclusions and submission/hold protections match batch
selection. The queue updates every 60 seconds without a full reload.

**Needs you** shows recent READY, NEEDS_INPUT, AUTH_REQUIRED and manual-review
applications, with ready-to-submit items first. Items include the
company, role, state, pending questions (or reason), updated time, and an **Open
application** link when the URL uses HTTP or HTTPS. Forms still open in AutoApply's
browser must be unlocked with **Review #ID** in Discord before clicking Submit.
Record the outcome with `!submitted ID <confirmation>`.

**Failed** separates automation failures from user-input holds. It includes
FAILED applications and MANUAL_REVIEW applications with one of these categories:
ENGINEERING_FAILURE, FORM_NOT_READY, APPLICATION_ENTRY_MODE_UNRESOLVED,
TRANSITION_UNCONFIRMED, ADAPTER_CAPABILITY_FAILURE, FORM_INVENTORY_UNSTABLE,
UPLOAD_PENDING, UPLOAD_RACE_DETECTED, FORM_TERMINAL_FAILURE,
SUBMIT_CLICK_NOT_DELIVERED or BROWSER_START_FAILED. Manual-review reasons
containing `upload readiness failed`, `Error during` or `TargetClosedError`
(case-insensitive) also qualify. Other user-input, account, security/identity,
captcha and eligibility holds remain in Needs you.

Both attention tabs use a 14-day window, exclude protected IDs, submitted or
retired applications, confirmed submissions and inactive listings, and are
disjoint. Failed cards are newest first and include category, a reason capped
at 200 characters, attempts, maximum retries, retry availability and an employer
link. Retry availability means `attempts < max_retries + 1`, delivery is
NOT_STARTED and no submit intent exists; it is informational and changes no
attempts or holds. Needs you, Failed and Applied each refresh every **30 seconds**.

The Run panel accepts an integer batch size from **1 to 10**, default **5** or
the saved `dashboard.default_batch_size` on page load.
Only Start is enabled while idle. Disabled controls remain readable and explain
why in a tooltip and accessibility description: no worker, an existing worker
lock, a pending request, or waiting for the server. Start shows a spinner and
**Starting...** until status reconciles. Status polls every five seconds. If a
status read fails or times out, the navbar and Run panel show **Dashboard server
offline - retrying in Ns**; retries back off from one second to 30 seconds and
recover automatically when the server responds. The last successful lists stay
visible during an outage.

Start launches a detached child with the dashboard's Python interpreter and
project root: `python -m autoapply --root <root> semi-auto --limit <count>`.
The worker retains at most that many forms per batch, requires enabled Discord
and a visible browser, and forces automatic submission off. Start clears an old
stop request and resumes processing. The page shows the PID, running/paused/stopping
state, last published batch phase, and cards with each application's ID, company,
role, state, readiness, security state and verified field count. Status refreshes
every **5 seconds**.

Pause and Resume persist the `paused` setting. Pausing stops new filling at safe
points and the worker clears manual review arming; forms remain open. Stop persists
`stop_requested`; the worker checks it in its half-second idle loop and between
applications/refills, finishes the current action, then performs normal cleanup.
It clears the stop request on exit. The dashboard never kills the worker or browser.
The worker honors persisted pause state at startup, including pauses requested
while Discord delivery is initializing.

Start returns HTTP 409 while its child is alive or `worker.lock` is held, including
by discovery. Refresh also refuses a live dashboard child during startup before
the child acquires its lock. The worker's CLI acquires `worker.lock` itself and
holds it through cleanup, so an independently launched worker remains protected.
Output goes to a unique `data/private/logs/semi-auto-<id>.log`; check it if the child
exits during setup. Closing the dashboard leaves its detached worker running.
After a dashboard restart the worker lock still prevents another start; the PID
is unknown for a worker this dashboard did not launch. Batch phase and summaries
come from the last atomically published report and can remain visible after exit.

## Endpoints

| Endpoint | Result |
| --- | --- |
| `GET /` | Self-contained HTML, CSS and JavaScript; no CDN assets |
| `GET /api/priority` | `{rows: [...], sweep: {...}}`, including sweep times/counts |
| `GET /api/manual` | `{applications: [...]}`; user action items, ready first, excluding automation failures |
| `GET /api/failed` | `{applications: [...]}`; recent automation failures, newest first |
| `GET /api/applied` | `{applications: [...]}`; truthy duplicate guards, newest marks first, excluding protected IDs |
| `POST /api/applied` | JSON `{application_id: ID}`; records a dashboard manual mark, returns `{application_id, marked_at}` |
| `POST /api/unapplied` | JSON `{application_id: ID}`; reverses a dashboard manual mark, returns `{application_id}` |
| `POST /api/refresh` | Starts background discovery and returns HTTP 202 with `{id: "..."}` |
| `GET /api/refresh/<id>` | Job status (`queued`, `running`, `succeeded`, `failed`), timestamps, result/error |
| `POST /api/run` | JSON `{count: 5}`; HTTP 202 with `{pid: ...}`, 400 for invalid counts, 409 when busy |
| `POST /api/pause` | Sets `paused=true`, returns current status |
| `POST /api/resume` | Sets `paused=false`, returns current status |
| `GET /api/settings` | Safe fields grouped into sections, locked safety fields and restart note |
| `POST /api/settings` | JSON `{changes: {"jobs.max_listing_age_days": 21}}`; saved values and note; 400 with per-field `errors` |
| `GET /api/resume` | Current filename, size, SHA-256 prefix, UTC modification time and archived PDF metadata |
| `GET /api/resume/file` | Current PDF with inline disposition for preview |
| `POST /api/resume` with `application/pdf` | Raw PDF replacement, maximum 10 MB; 409 while the worker lock is held |
| `POST /api/stop` | Requests graceful stop, returns current status |
| `GET /api/status` | `running`, `paused`, `stopping`, `pid`, `worker_locked`, `phase`, `batch_id`, `updated_at`, `applications`, `report_error` |

Manual marks store `duplicate_submission_guard:<id>` as
`{"permanent": false, "source": "DASHBOARD_MANUAL", "marked_at": "<UTC ISO time>"}`
and record `USER_MARKED_APPLIED` in the same short SQLite transaction. Put back
writes JSON `false` and records `USER_UNMARKED_APPLIED`. Application status,
attempts and holds are unchanged. Existing discovery and automation protections
exclude marked IDs; Run summaries also exclude guarded IDs. Check-offs do not
acquire `worker.lock` and work while a worker holds it.

Both write endpoints return 400 for invalid IDs/bodies, 404 for unknown IDs and
403 for protected IDs. Mark returns 409 for any existing truthy guard, including
a duplicate dashboard mark. Put back returns 409 unless the guard is a dictionary
with source `DASHBOARD_MANUAL` and permanent exactly false; Discord and permanent
guards cannot be cleared. Applied fields are `application_id`, `company`, `title`,
`ats`, `url`, `marked_at`, `source` and `reversible`. Legacy truthy guards without
a time appear after dated marks. Mark times are displayed in local time.

Inline markup, CSS and JavaScript live in `autoapply/dashboard_ui.py`. The server
imports `PAGE` and substitutes the CSP nonce and initial queue data.

## Settings and resume replacement

Settings uses a section menu for General, Discovery, Filling, Dashboard, Resume
and Safety. Editable fields have inline validation. A footer provides Discard and
Save changes while edits are pending and shows the local saved time after success.
Light and dark themes use the same dashboard palette. The editable settings are:

| Setting | Accepted values |
| --- | --- |
| `jobs.max_listing_age_days` | Integer 1..30 |
| `jobs.target_season` | Text, at most 40 characters |
| `polling.github_minutes` | Integer 1..1440 |
| `polling.linkedin_minutes`, `polling.handshake_minutes` | Integer 5..1440 |
| `application.delay_seconds` | Integer 0..600 |
| `application.min_confidence` | Number 0.90..1.0; UI step 0.01 |
| `application.max_pages` | Integer 1..30 |
| `application.confirmation_timeout_seconds` | Integer 5..120 |
| `processing.max_applications_per_day` | Integer 1..500 |
| `dashboard.default_batch_size` | Integer 1..10; default 5 |
| `dashboard.liveness_minutes` | Integer 5..1440; default 30 |

Automatic submission and headless mode display locked Off values for semi-auto
operation. Locked booleans display Off/On; retry budgets retain their numeric
values. Retry budgets and Discord enablement are locked with reasons. Any
attempt to write these or an unknown key is rejected before any file changes.
No secrets, environment files or token values are exposed by settings endpoints.
If the existing private YAML has automatic submission enabled, saving is refused
until it is disabled outside the dashboard; settings never rewrite an enabled
automatic-submission value.

Saving reads the current YAML, merges only changed keys, and replaces it using
an fsynced temporary file in the same directory with bounded PermissionError
retries. A timestamped UTC backup preserves the previous bytes under
`data/private/backups/config-<utc>.yaml`; at most 20 config backups are retained.
YAML comments may be lost in the saved file. The running worker picks up settings
at its next start. Restart the dashboard for its liveness interval; a saved
interval replaces the CLI's default 30, while other explicit CLI intervals take
precedence. New page loads use the saved batch default.

Resume shows the current PDF, size, last modified time, hash prefix and Preview
link above a styled PDF drop zone and archive list. Choose file opens the hidden
native picker and supports keyboard activation. Dropping or choosing a PDF shows
its name and size; Upload replaces the resume, while Cancel clears the selection.
The drop zone highlights during dragging in light and dark themes. Upload shows a
determinate progress bar, followed by an affected-application count on success
or an error with the selection retained for retry. Uploads
use raw `application/pdf` with a numeric Content-Length. The server checks the
`%PDF-` signature and bounded body size; it does not extract or infer profile facts.
`X-Filename` is optional metadata and never affects a path. Empty control requests
and JSON `{}` on `/api/resume` retain the existing unpause behavior.

Replacement holds `worker.lock` throughout and also refuses a dashboard child
still starting. It writes `resumes/resume.pdf.tmp`, archives the prior PDF as
`resumes/archive/resume-<old sha prefix>-<utc>.pdf`, and atomically replaces the
canonical PDF. Archived resumes are never pruned. It backs up `profile.yaml`
under `backups/profile-<utc>.yaml` and atomically updates only `resume_revision`
with the canonical relative filename and full SHA-256. SQLite invalidation and
the `resume_replaced:<utc>` audit setting share one transaction; the audit records
old/new hashes and affected application IDs. Existing profile facts remain
unchanged. The success message asks you to review the Answers tab. Recoverable write failures restore
the old PDF/profile and roll back database changes. These operations cannot form
a single crash-atomic transaction across SQLite and the filesystem; backups and
archives preserve recovery evidence if the process exits during replacement.

Failed application fields are `application_id`, `company`, `title`, `ats`,
`error_category`, `reason`, `attempts`, `max_retries`, `retry_possible`, `url`,
and `updated_at`. Unsafe URLs are returned as an empty string. Status application
summaries retain their existing fields and add `company`, `title`, `ats` and a
safe listing `url` when the
application exists in the database. Queue rows also include application `updated_at` for the grid meta line.
Saved answers are available in the Answers tab.
Needs you summaries also include `state_code` alongside the existing `state`
label, so the original state or account/security category is available on hover.

Refresh uses the same `Engine.scan(force=True)` discovery path as
`python -m autoapply scan`, including configured sources. It never calls the
application processing loop. Source discovery may open a source browser when
LinkedIn or Handshake discovery is enabled in existing configuration.
The page polls the job once per second and updates the list when discovery ends.
**Find new listings** is separate from Reload data: it calls `POST /api/refresh`
and scans job sources, which can take minutes. During discovery it shows
**Searching... 1m 12s**. Completion shows the local finish time, duration and
`result.new` listing count. The last completion metadata persists in localStorage
through page reloads; unavailable polling retries without starting another search.
Only one refresh may run per dashboard. A busy worker lock returns HTTP 409 with
`worker running` immediately; a second refresh returns `refresh already running`.
The server retains the latest 20 refresh jobs in memory. There are no submit,
review-unlock, hold-clearing or attempt-reset endpoints.

## Liveness and safety limits

A background sweep starts immediately and repeats after each configured interval.
It probes at most 100 eligible queued/retry listings per sweep with at most four
concurrent probe threads. Each also fetches and stores public descriptions shorter
than 200 characters, using the same cap and pool. HTTP calls run without database
write locks and use bounded timeouts (15 seconds per request). Candidates missing
descriptions go first, then least recently checked candidates, so the queue rotates
through the cap. Probe failures count as `UNKNOWN`; `described` counts enrichment.
After all probes finish, descriptions and retirement results are written in one
short batch with one history flush. Dashboard connections share a history cache;
the existing archive revision token avoids rescanning unchanged records.
The next queue rebuild re-evaluates eligibility using the stored text: PhD
rejections leave the queue without retiring or changing application state.
The page shows sweep start/end times, counts, errors and results; this monitoring
state stays in memory and resets when the dashboard restarts.

`OPEN` and `UNKNOWN` do not change application state. Definitive `CLOSED` or
`REMOVED` results use `db.mark_listing_closed` and a `CLOSED` lifecycle transition
with a recorded event. Each retirement rechecks the current application inside a
short transaction; applications claimed or held by a worker during the probe
are skipped. Attempts are not consumed or reset. Sweeps never acquire
`worker.lock`; refresh holds it only while discovery runs. Separate
SQLite connections reuse the CLI-initialized schema without repeated startup
maintenance; list reads use read-only connections. The cached queue connection
serializes access across request threads.

Applications **3556, 4522, 6397 and 4611** are never probed or retired. They may
appear in the read-only list if batch selection would include them. Dashboard
write connections also guard these application IDs and their associated listing
records against discovery's bulk updates; protected pending history exports are
left for their owning worker. These guards are connection-local and do not alter
another worker's database behavior.

The HTTP server binds only to `127.0.0.1`. It accepts only `127.0.0.1` or
`localhost` Host headers with the server's actual port. POST, PUT and DELETE requests require
the per-process `X-Dashboard-Token` embedded in the page and exactly one Origin
header matching the page's exact HTTP origin; cross-site fetches are refused.
No CORS access is enabled. Responses are not cached, displayed listing text is
treated as text, and inline assets use a nonce-based content security policy.
All dynamic text uses `textContent`; employer links open in a new tab with
`rel="noopener noreferrer"` and permit only HTTP/HTTPS URLs.
The page returns listing/queue metadata and batch summaries; the Answers endpoints
also expose editable profile fields and saved answers locally. Missing or malformed reports
do not prevent status reads; malformed reports produce a summary error.

Already running discovery/probes may finish after the server stops accepting
requests; each owns its connection, and an active discovery retains its worker
lock until it finishes (or the process exits). Restarting the dashboard does not
cancel or clear existing application holds or change auto-submit settings.

## Answers

The paired Chrome extension can also use these writing endpoints with
`X-AutoApply-Key` and a `chrome-extension://` Origin. POST bodies are limited to
64 KB. Generation runs in the request thread and uses the configured Codex
writing provider and the same professional profile facts as cover letters.

| Endpoint | Request and result |
| --- | --- |
| `POST /ext/write` | `{url?, application_id?, page?: {title, company, description}, question, instruction?, previous?}` returns `{answer, words, chars, warning?}`. Pass the current answer as `previous` and the refinement prompt as `instruction`; the side panel can retain response history. Word/character limits in the question guide writing; over-limit output gets a warning without truncation. |
| `POST /ext/chat` | `{messages: [{role: user\|assistant, text}], application_id?, page?: {url, title, company, description}}` returns `{reply}`. Keeps the last 12 messages, each capped at 2000 characters. |
| `POST /ext/cover-letter` | `{url?, application_id?, page?, answers?: [{question, answer}]}` returns `{questions}` or `{filename, mime: 'application/pdf', base64}`. The filename is `<Name> - Cover Letter - <Company>.pdf`. |

Matched jobs use the stored listing and fetch missing descriptions through the
existing cover-letter path. Unmatched pages use supplied page context. Cover
letters share the fixed layout, salutation validation, and saved per-application
answers with `/api/cv`; unmatched pages create no per-application files. A blank
cover-letter answer skips that question. Writing and chat use only professional
facts, job context and user-supplied facts, and never infer applicant experience.
Disabled or unconfigured AI returns 409 with
`AI writing needs an AI provider in Settings`; provider failures return 409 and
invalid output returns 400. Extension PDF rendering failures return an error so
that the response always contains a PDF when generation succeeds. Protected
applications remain rejected. No writing endpoint changes application state,
the canonical resume or submission settings.

Queue, Needs you and Applied listings have a **Generate Cover letter** document
icon in list and grid views. Save profile edits in Answers first. Generation uses
the configured Codex writing provider and saved education, employment, projects,
skills and links with the job description. It sends no demographics, eligibility
or sponsorship fields. Up to three `cover_letter_samples/*.txt` files (4000
characters each) supply style only. AI must be enabled and configured in Settings;
unavailable providers or malformed results show an error without a resume fallback.
If a configured Codex executable was removed by an app update, writing falls back
to `codex` on PATH.

`POST /api/cv` accepts `{application_id, answers?: [{question, answer}]}` and
returns `{file, warnings}` or `{questions}`. Missing facts open a question dialog;
Generate is always enabled: answers are optional, and a blank answer skips the
question. Skipped questions persist and the provider is told to write without that
information and not ask again. No tab opens until the letter is generated. If the
browser blocks the new tab, a persistent notification offers an **Open cover letter**
link until clicked or dismissed. Answers persist
per application in `documents/cv/<id>-<company>.answers.json` and are reused on
later requests. The request timeout is 240 seconds. The existing token, origin,
ID validation and protected-ID rejection remain in place. Provider writing and
PDF rendering run without a dashboard or database lock; simultaneous generation
for one application returns 409.

Descriptions shorter than 200 characters are fetched from the canonical listing
URL and stored before writing, without holding a database lock during HTTP.
If fetching fails, the writer uses the title and company anyway. Questions are
only for personal facts the applicant alone knows; the writer never requests the
job description, posting text, company name or role. Posting questions are also
filtered server-side. If only such questions return without paragraphs, generation
shows an error asking the user to retry.

Letters have a name/contact header, date, recipient, salutation, 3-5 AI-written
body paragraphs and signature in Times New Roman 12pt, black, left-aligned, with
1.15 line spacing, 12pt paragraph gaps and one-inch Letter margins.
The writer may include a salutation naming a recruiter or hiring manager only when
the job description identifies that person as the contact for this role. A valid
salutation is one line, at most 80 characters, starting with `Dear ` and ending
with a comma or colon; missing or invalid values use `Dear <Company> Hiring Team,`.
The recipient block always remains `<Company> Hiring Team` / `<Company>`.
Contact links omit schemes, leading `www.` and trailing slashes; US states use two-letter codes.
Chromium shrinks the contact font if needed to keep it on one line. Job fields are
HTML-decoded before writing. HTML and PDFs remain under `data/private/documents/cv`.
Chromium blocks network requests and scales long letters to one page. Rendering
failure serves print-ready HTML with a warning and removes the stale PDF.
`GET /api/cv/<id>` requires the token and serves the latest letter inline as
`<Name> - Cover Letter - <Company>.pdf`. Generation changes no profile facts,
canonical resume, application state, submission settings or retry budgets.

Fixture-only cover letter checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-cv tests/test_cv.py tests/test_dashboard.py
```

The Answers tab sits between Applications and Settings. Its section menu includes
Autofill preview, Personal, Contact, Education, Experience, Projects,
Skills, Links, Work authorization, Availability, Preferences & demographics,
Eligibility and Saved answers. The section menu stays on one line per item;
form rows use a compact label/help column and control column. Fields show a You or Resume source badge. Skills
and other tag lists accept Enter or comma, remove tags with ×, and deduplicate
case-insensitively. Repeatable entries have inline Add/Edit forms, Remove, and
up/down ordering controls. The primary `education` remains a dictionary; additional
education is stored separately. Website is explicitly shared by Website, Personal
website and Portfolio questions.

Tag inputs name the field being added (a minor, skill or interest). Nullable
profile booleans offer Yes, No and Not set; repeatable-entry and dynamically typed
booleans use a Yes/No switch. Selects show readable labels such as Decline to
answer and I am not a protected veteran while retaining their stored values.

The sticky changes bar provides Discard and Save changes and shows a local saved
time. Changes apply to the next field the running worker resolves; saving does
not stop a worker or alter application state. The preview searches about 40 common
questions and shows the exact real resolver result with Profile, Rule, Saved or
Needs you. It uses typical options and an example employer, so application-specific
questions may differ. It refreshes after a save, on demand, and every 30 seconds
while visible. Additional education, employment, projects and other links are
stored for repeatable sections; adding them does not extend the existing resolver's
supported question mappings. Missing or unsupported answers remain Needs you.
State offers the shared US state list so abbreviations such as MD fit Maryland;
Province/Region of residence previews the same profile rule without options.
Both use the worker's resolver and its saved overrides and policies.

| Endpoint | Result |
| --- | --- |
| `GET /api/answers` | `{sections, note}` with field path, label, type, value, help, source, select options and list item schemas |
| `PUT /api/answers` | `{changes: {path: value}}`; validates all fields before writing; 400 includes per-field `errors` |
| `GET /api/answers/preview` | `{questions, note}`; question, answer, display source and original resolver source; read-only SQLite, no question rows or usage receipts |
| `GET /api/answers/saved` | `{answers}`; known answers and `verified_fact:<path>` overrides, with ID, question, scope, answer, source, last-used time and usage |
| `PUT /api/answers/saved/<id>` | `{answer: string or string[]}`; edits a known answer or verified-fact setting |
| `DELETE /api/answers/saved/<id>` | Deletes a known answer or verified-fact override; 404 for missing IDs |

All answer writes require the dashboard token and exact same origin. Profile
saves reread the current file, preserve unrelated keys and `resume_revision`,
validate `ProfileSnapshot.create`, and mark changed paths in `fact_sources` with
`source: USER_PROVIDED`, `basis: Dashboard edit` and a UTC `supplied_at`. The previous
bytes are backed up to `data/private/backups/profile-<utc>.yaml` (20 retained).
An fsynced temporary file and atomic replace with bounded PermissionError retries
protect writes. YAML comments may be lost. Profile edits share the resume editor's
mutex, without acquiring the worker lock.

Repeatable list items contain these keys (dates use YYYY-MM; availability uses
YYYY-MM-DD; current is a boolean; GPA is numeric or null):

| Path | Item schema |
| --- | --- |
| `education_history` | school, degree, major, start_date, graduation_date, gpa, current |
| `employment` | company, title, location, start_date, end_date, current, description |
| `projects` | name, role, url, start_date, end_date, technologies (string list), description |
| `links.other` | label, url |
| `eligibility_assertions` | id, meaning, answer (boolean), source, scope, supplied_at |

Saved facts can override profile values, so the Saved answers section exposes
them alongside known answers. Saved edits and deletes use a SQLite transaction
and append before/after values to the `answer_edit_log` setting, retaining the last
200 entries; no application events or history exports are created. Delete hides
the answer immediately and offers an eight-second Undo toast; the delete request
is sent when that window expires. Undo cancels the pending request.
The List view is a filterable compact list with Question, Answer, Applies to,
Last used and Actions columns. Scope labels show All applications or
Application #ID, and small source badges show You, Rule or Eligibility. Edit and
Delete icon buttons have accessible labels and tooltips. Edit opens an inline
row with Save and Cancel.

Fixture-only Answers checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-answers tests/test_dashboard_answers.py
```

Offline checks use temporary databases and stubbed discovery/liveness:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-dashboard tests/test_dashboard.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-settings tests/test_dashboard_settings.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-semi-auto tests/test_semi_auto_qualification.py
```

Run-control tests monkeypatch `subprocess.Popen`; worker tests mock processing,
pages and Discord. These checks launch no real worker, browser or Discord session.
When Node is available, dashboard tests also execute the inline JavaScript
renderers with a small offline DOM to verify labels, timestamps and tab counts.
The CLI accepts `python -m autoapply semi-auto --limit N` with the same 1..10
validation and default of 5; it honors an existing persisted pause until resumed.


Saved answers opens in a compact Grouped view; List view retains sorting,
pagination and individual editing. Each group has one row with an ellipsized
question (full wording in its tooltip), answer, category chip and accessible icon
actions. Only conflicting groups show an answer selector. Category chips open a
small selector; category filters include counts on one scrollable line. The
shared pager defaults to 50 groups per page. Groups with multiple records have
a +N similar expander with indented variants and application scopes.
Search ranks normalized keyword and character-trigram similarity; `sponsor` and
`visa` find sponsorship variants. Categories use canonical concepts first, then
small keyword rules. Manual choices persist as `question_category:<normalized>`
settings and take precedence. Accept suggested categories preserves overrides.
`PUT /api/answers/categories` accepts `{changes: {question: category}}` with the
same origin/token validation as other answer writes.

Use this answer for all applies the selected value through the existing audited
saved-answer edit path, preserving every scope, and offers Undo with original
values. Link to profile field explicitly copies the selected saved answer into
its canonical editable field (booleans must be Yes/No). It uses
`PUT /api/answers/saved/<id>` with `{link_profile: true}` and the normal profile
validation, backups and provenance. Linking never changes application state.

Gender, race/ethnicity, disability and veteran status each have one editable
`demographics.*` field. Legacy demographic aliases and application preferences
remain intact and provide a read-time fallback. Conflicting values are shown
with their paths and choice buttons; unresolved conflicts abstain. Saving an
explicit choice selects the canonical field without overwriting legacy values.
Decline-equivalent phrases compare as the canonical `decline` option; Yes/No
casing does not create a conflict. These comparisons do not rewrite saved data.
Answers containing their own question show a `Check answer` warning, including
when expanded; the warning never changes the stored text automatically.
Enrollment, age 18+, academic standing, gender, race and skills use the same
profile resolver in preview and the worker. Missing sensitive facts never use
an affirmative default.

Grouping is a standard-library, review-only hybrid: canonical concept matches,
content-token Jaccard and character-trigram cosine, with answer-kind, timeframe
and distinct-claim guards. Distinct canonical concepts and dotted profile paths
cannot merge, even through a shared wording variant. Dotted paths use human
labels. Sponsor/sponsorship variants share a concept and normalized stem.
Requirement confirmations use Skills for technology/knowledge requirements and
Eligibility & requirements otherwise; consents and conflict disclosures use
Eligibility & requirements, prior employer questions use Experience, team
choices use Preferences/Availability, and education end-date parts use Education.
Short token subsets alone can over-merge, while
embeddings add dependencies; this follows the deterministic cascade in
[the matching research](question-matching-research-2026-10-06.md). Similarity and
category overrides are never consulted to answer application questions.


Queue and Needs you rows have Select for Chrome checkboxes. Select top N adds
applications in the current filtered order. Run in Chrome (N) starts the selected
queue; the status panel shows each result and Cancel run skips unfinished items.
Pair the extension in Settings. If it has not polled for 15 seconds, the panel
shows an Open Chrome hint. Discovery uses an MV3 alarm (at most 30 seconds while
asleep); an awake runner polls every three seconds.

The extension opens and fills one tab at a time, including exact Apply navigation
links outside forms. Tabs remain open. It never presses submission buttons.
The side panel shows Run: current/total and Pause/Resume; pause takes effect after
the current fill. Revisit a filled tab to see its saved prompts in the panel.
Unresolved answers or cover-letter questions report needs_input.
Combobox scanning opens only unanswered, unresolved menus; profile answers go
directly to search. Autofill results include scan_ms and options_ms timings.

Chrome selection uses a compact checkbox at the left of Queue and Needs you lists,
and a corner checkbox on grid cards. The header checkbox selects or clears the
currently visible page, respecting its filter. Run in Chrome posts the selected IDs.

When no editable fields are present, Autofill follows a clearly labeled Apply
entry and chooses Apply Manually in the Workday dialog. Sign-in pages stop with
**Sign in to Workday in this tab. Autofill will resume automatically.** and report needs_input.
Credentials and final submission remain manual.

Workday tabs autofilled during this browser session automatically refill after sign-in or a step change. Use Next page in Fill or Save and Continue on the page; the extension fills the new step, reports its name and unresolved fields, and keeps the original application match. Workday dropdowns, searchable prompts, split date inputs and resume uploads are supported. The first saved employment or education entry supplies repeatable section facts; additional entries can be added manually. Review and final submission remain manual.


The paired extension fetches explicit resume sections from `GET /ext/profile-sections` without AI. It fills up to six employment entries, six projects, three education entries, and website links using the section’s Add controls. Existing populated entries are preserved. Month dates use day 1 in full date inputs; ongoing roles check the current checkbox and omit end dates. Missing required start dates appear once as a named question: enter `YYYY-MM` in the panel and Save to reuse it on future forms. Skills are added individually to matching tags, with unmatched counts shown in Fill and Answers; free text gets the skills list. Simple projects textareas get a compact list, while essay questions continue through the writer.

The Fill tab has Next page and a per-profile auto-advance preference, off by default. Only Next, Continue, or Save and Continue controls are eligible; Submit and Apply are never clicked. Auto-advance requires all required fields filled, no pending answers or writing, and no validation errors. It stops at Review or after ten steps. Autofill polls once per second for up to 60 seconds after sign-in or an empty form; Cancel waiting stops it. Workday forms settle for three more seconds, and up to three rescans fill newly revealed controls. Choice prompts offer Load options when an async menu is still unavailable. Existing uploaded filenames or Successfully Uploaded status are reported as already attached.

Question recognition uses the stemmed, whole-word concept table in
`autoapply/question_keywords.py` for wording variants. Its any/all/exclude
phrases and specificity score keep long questions recognizable without matching
substrings. Existing tested, narrow rules in `concepts.py` remain higher-priority
overrides. Standard answers use explicit profile preferences; advertised salary
ranges take precedence over the configured default (hourly pay is annualized
at 2,080 hours when requested). Negated commitments and Hispanic/Latino fields
without a decline option require user input.

Control boilerplate such as "Select One Required" and machine identifiers always
return Needs input, even with saved answers or supplied concept metadata. RTX-style
prior employment uses the explicit previous-employer default; federal government
and independent-auditor employment questions follow the No policy. Credit hours
come from the education credit-hours fact, GPA choices use the range containing
the profile GPA, and conditional major/permanent-address fields use profile facts.
Sex and race/ethnicity decline choices accept equivalent wording and country
suffixes. Terms consent uses the terms acknowledgement preference. A "not a
veteran" preference selects the plain nonveteran choice before falling back to
"not a protected veteran". Phone device type uses the contact preference, and
optional address line 2 remains empty.

Chrome runs show a compact progress line with an icon cancel action. The line closes when the run finishes; Last run is a muted, collapsed disclosure with one line per application.

The extension Chat tab includes Chat and Answer helper. Written fields offer Ask AI, and generated answers stay editable in Answers for that application. Use job description controls whether the saved posting text and link are sent as AI reference context. Scrollbars follow the light or dark theme.

The extension keeps Next page and auto-advance in a footer across all tabs. Final submission stays manual. Written-response fields have a small Ask AI button below them that opens Chat → Answer helper. The minimized flag can be dragged vertically; its position is remembered per site. Send page structure in the footer sends only form metadata (no input values or file contents) through the paired `POST /ext/snapshot` endpoint. Snapshots are capped at 1 MB and saved under `data/private/snapshots/<host>-<timestamp>.json` on demand for markup debugging.
