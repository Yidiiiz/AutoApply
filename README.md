# AutoApply

A personal internship application server built with Python, Playwright, SQLite and YAML. It discovers listings, preserves their provenance, fills verified answers, asks for missing information through Discord, and submits supported forms only after validation. All runtime data stays in ignored local storage.

## What works

- Git-based polling of all three configured internship repositories, with dynamic default-branch discovery, HTML/Markdown table parsing, revision checkpoints and a configurable 14-day freshness window.
- A persistent priority queue, ATS/requisition and URL deduplication, source references, closed/already-applied detection, conservative eligibility checks, and readable JSON archives.
- A dedicated persistent Chromium profile. Semantic form filling handles native text, email, phone, number, date, select, multi-select, radio, checkbox and file inputs. Some accessible custom dropdowns work; uncertain layouts pause.
- Greenhouse, Lever and Ashby entry points plus a generic form adapter. The shared form workflow is tested against a local employer fixture, including resume upload, unknown-question pause, user-answer replay and a confirmed submission.
- Discord DMs restricted to one configured user, persistent question buttons, choice menus, answer modals, out-of-order replies, queue/history inspection, pause/resume, retry, stop and auto-submit controls.
- Exact verified answer memory, narrow wording equivalences, context-scoped writing reuse, configurable AI providers and automatic grounding audits for ordinary application prose.
- Gmail read-only OAuth and narrowly scoped verification-message/code/link extraction utilities. Protected verification steps require manual completion; codes are not saved as application answers.
- Opt-in LinkedIn and Handshake result-card discovery using authenticated browser sessions. Explicit external application links are preferred.
- Crash recovery, retry backoff, a daily submission ceiling, durable notification delivery and a Windows worker lock.

## Current boundaries

This is an initial implementation, not universal ATS support. Workday and SmartRecruiters use the conservative generic adapter; complex account creation, resume parsers, custom widgets, internal LinkedIn/Handshake application dialogs and site-specific multi-step flows may require manual review. Browser source layouts and user-configured AI selectors need validation with your own authenticated sessions.

New AI prose is a **proposal**, never a verified personal fact. It must cite configured factual blocks and receive user confirmation before submission or verified reuse. Missing facts, ambiguous eligibility, unknown posting dates, legal declarations and numeric salary expectations pause the relevant application. Exact graduation-month boundaries, complex skill requirements and uncertain locations go to the user. Calendar-derived availability is not silently inferred: record the official source and confirm the date in the private profile when needed.

Gmail does not send, delete or modify messages. Automatic verification-link navigation is not enabled; extracted links can be reviewed manually. Sign-in, account selection, MFA, CAPTCHA and security challenges require the user. The program does not create passwords or bypass protections.

## Install on Windows

Use Python 3.11 or newer and Git. Run in this project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) 'data/private/playwright'
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m autoapply init
Copy-Item .env.example .env
```

The browser download lives in the ignored data directory. AutoApply detects that location on startup. You may instead install Playwright's browsers in its normal user cache. `init` never overwrites existing private configuration.

## Private setup

Edit `data/private/profile.yaml` using `templates/profile.example.yaml` as the schema. `null` means unknown; `false` means a verified negative answer. Do not fill unknowns with invented placeholders. Supported sections include identity, contact, education, citizenship, work authorization, employment, projects, skills, availability, locations, links, preferences, common answers and verified writing facts.

Place exactly the intended resume at `data/private/resumes/resume.pdf`. The worker checks the PDF header, uploads that path, verifies the browser file selection and records its SHA-256. It does not choose an arbitrary PDF. Required additional documents need manual handling until an adapter supports their workflow.

Full dates use `YYYY-MM-DD`. A month-only graduation date may be stored as `YYYY-MM`; an application requesting an exact day will ask you rather than invent one. Availability is term-specific, for example:

```yaml
availability:
  summer_2027:
    start_date: null
    end_date: null
    source: null
verified_facts: {}
```

Enter factual writing blocks under `verified_facts`, as stable identifiers mapped to your own confirmed sentences. Style samples can be `.txt` or `.md` files in `data/private/writing_samples/`. Writing samples provide style, not permission to invent claims. Resume text is not automatically converted to verified facts.

Standing eligibility answers live in `eligibility_assertions` in the private profile. Each entry has an `id`, `meaning`, boolean `answer`, `source: USER_PROVIDED`, and `scope: standing`. Supported assertion meanings are defined in `autoapply/standing.py`; only explicitly supplied assertions are enabled. The resolver recognizes complete semantic clauses and supported combinations, and abstains on unknown qualifiers, added credentials, or stronger thresholds. A minimum-GPA assertion does not supply an exact numeric GPA. Matches retain assertion IDs and user provenance in eligibility evidence and application events; form answers retain `USER_PROVIDED_STANDING_ELIGIBILITY_ASSERTIONS` as their source.

Missing eligibility or form answers preserve the live page with automatic retries disabled. Answer the pending questions, then explicitly use `resume-manual ID` with the running worker. The resume path rechecks security and never repeats an existing submission intent.

`INPUT_REQUIRED` identifies missing applicant information. External execution holds use `EXTERNAL_EXECUTION_APPROVAL_REQUIRED` or `EXECUTION_APPROVAL_BLOCKED`; these disable retries and produce a distinct execution-approval notice without requesting applicant answers. An approval rejection before the worker launches must be recorded by the calling operator because the worker cannot observe it. Existing authorization to use saved profile data, upload the saved resume, and perform a gated submission is not missing applicant input.

On Windows, a worker launched from a network-restricted Codex shell can run as `CodexSandboxOffline`, even when inherited environment variables name the desktop user. Its Chromium children inherit that execution context. The enabled Windows firewall rule `codex_sandbox_offline_block_outbound` can block every external destination for that account, producing `ERR_NETWORK_ACCESS_DENIED` before any employer response. Compare actual process owners (or `whoami`), the rule's LocalUser SID, and a bounded public connectivity probe before attributing this error to an employer. A successful ordinary-browser visit does not establish that the worker has network access.

Use the platform's approved execution path for a networked production worker, or the existing launcher from the user's ordinary PowerShell session. Keep the worker lock and controlled-application limit in place; inspect and preserve evidence from an existing paused browser before replacing it. Do not disable sandbox firewall rules, weaken browser security, or change profiles to work around an execution restriction. If execution approval is denied, retain the inspection hold and report that restriction separately from applicant input.

Put all local settings in `data/private/config.yaml`. Missing settings inherit `config/config.example.yaml`. Never put passwords or OAuth secrets there. Secrets belong in `.env`, Google OAuth private files, or persistent authenticated sessions.

Run the setup check:

```powershell
.\.venv\Scripts\python.exe -m autoapply doctor
```

Standing disclosure defaults live in the private profile's `standing_disclosures` mapping. `autoapply/disclosures.py` matches complete supported questions about prospective-employer work history, conflicts of interest, and relevant government connections. Unknown clauses, legal thresholds, jurisdictions, and reporting obligations abstain; keywords alone never select No. Exact employer-specific answers remain scoped to their application, and later verified answers override standing defaults. Separately confirmed military-service history and required demographic-survey processing consent use similarly bounded matching.

## Discord

1. Create an application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Install it in a private server that you and the bot share; allow DMs. No privileged intents are needed: Discord supplies DM content without Message Content Intent. The bot subscribes only to guild lifecycle and direct-message events.
3. Put `DISCORD_BOT_TOKEN` and your `DISCORD_USER_ID` in `.env`. Do not paste tokens into source code or commit them.
4. Start `run`. Only DMs from that exact user can control the worker. Guild commands and all other users are ignored.

Run `python -m autoapply discord-check` first. It authenticates the bot and sends a connection-check DM only to your configured user, without starting applications or draining queued notifications. `run` also waits for the Discord gateway and a successful DM before processing jobs. If delivery is refused, install the bot in a shared server, enable DMs from that server and unblock the bot. A later DM permission failure pauses processing and preserves notifications. Restore permissions, then resume. Keep credentials in `.env`; `.env.example` must remain blank.

DM commands use `!` (a typed `/` prefix also works; these are not registered slash commands):

| Command | Result |
| --- | --- |
| `!pause`, `!resume`, `!status` | Global controls and status |
| `!queue`, `!pending`, `!recent` | Queue, pending IDs and recent history |
| `!autosubmit on` or `!autosubmit off` | Durable submission setting |
| `!application 12` | Application state and reason |
| `!application 12 description` | Captured job description |
| `!application 12 answers` | Recorded questions and answers |
| `!application 12 writing` | Stored writing for this company/role |
| `!answer 42 Your answer` | Resolve question 42, independently of reply order |
| `!skip 42` | Skip an optional question; required questions remain unresolved |
| `!ai 42`, `!accept 42` | Request a draft, then explicitly confirm it |
| `!retry 12`, `!stop 12` | Requeue or hold an application |

Question DMs also contain buttons, and selection menus when the options fit Discord's limits. For multi-select text answers, supply a JSON list of exact option labels. Question IDs survive restarts. An unavailable Discord connection leaves notifications in SQLite for retry. If sending succeeds immediately before a crash, a notification can be delivered twice; its question ID remains the same.

The application conversation currently retrieves structured context by topic. It is not an unrestricted conversational model. Use the question's reason, job description and stored answers to resolve uncertainty.

## Browser sessions

```powershell
.\.venv\Scripts\python.exe -m autoapply login
.\.venv\Scripts\python.exe -m autoapply login https://www.linkedin.com/
```

`login` opens the dedicated visible browser and waits until you press Enter in the terminal. Sign into the intended application account manually. Open employer or AI service URLs the same way. Never select an unintended Google account. Stop the worker before using `login`; both share an OS-backed lock on the same profile.

The browser profile is `data/private/browser_profile/`. It contains sensitive authenticated sessions and must remain private. Authentication/security holds preserve the application tab while the worker stays alive. Controlled runs also preserve abnormal navigation and network stops; no automatic retry occurs. HTTP failures retain sanitized URL, method, resource type, status, and frame provenance without request payloads or credentials. A browser `ERR_NETWORK_ACCESS_DENIED` is an execution-environment block, not missing applicant information. Keep the worker running while inspecting the page.

`python -m autoapply probe URL` inspects a form's labels, control types and option counts without filling answers or submitting. It can open dropdown menus to inspect available options. This is useful when checking a new ATS layout.

## Job discovery

The default sources are:

- [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)
- [speedyapply/2027-SWE-College-Jobs](https://github.com/speedyapply/2027-SWE-College-Jobs)
- [vanshb03/Summer2027-Internships](https://github.com/vanshb03/Summer2027-Internships)

Add HTTPS GitHub repository URLs to `github_sources`. Repositories are shallow bare clones in private storage; their code is never executed. Each poll reads the remote's actual default branch, fetches its tip and skips parsing an unchanged processed revision. The main and off-season README tables are supported; archived historical and new-grad files are not scanned. Closed entries remain history. Relative ages are anchored to the source commit time, not each poll, and an existing job's posting date is not refreshed just because it reappears.

Only verified posting dates within a rolling 30-day window enter the queue. `jobs.max_listing_age_days` defaults to 30, permits stricter values from 1 through 30, and rejects larger values. The cutoff uses elapsed UTC time, including seconds. Unknown, malformed, future, ambiguous-month and stale dates are rejected before ranking or application creation; only lightweight observations remain for deduplication and statistics. Discovery time and generic updated timestamps never establish freshness. U.S. location and eligibility are checked before form filling. Unknown remote-country eligibility requires review. New requisition IDs are distinct opportunities, even when title/company match. Exact ATS IDs are preferred over fuzzy title matching, which could wrongly collapse different jobs.

LinkedIn and Handshake are disabled until their private settings are configured. Set `enabled: true`, log in through `login`, and supply current filtered `search_urls`. Polling defaults to 45 minutes. LinkedIn queries use its date-posted filter (past month, week or day, retaining a stricter saved filter); local validation remains authoritative. Handshake retains the saved search URL because no portable date-filter parameter has been verified. These adapters read visible result cards and follow only actual same-host next-page links. Each query is capped at `discovery.max_pages_per_query` (5) and `max_results_per_query` (250). Each supported repository README is independently capped at 250 parsed listings. Current sources do not guarantee newest-first ordering, so encountering old results never stops a ranked search prematurely; the configured cap can still limit coverage. Unverifiable posting dates are skipped. If a source changes layout, the durable notification explains that manual checking is needed.

Listing state (`ACTIVE`, `STALE`, `CLOSED`, `REMOVED`, `UNKNOWN`) is separate from application state. Startup, discovery, and queue access refresh eligibility; application start and final submission recheck it. Old closed listings leave the active store without deleting application records, status history, answers, resumes or confirmation evidence. Cleanup alone does not rewrite application status or reopen previously final applications. Manual submission-review records remain available even if their listing expires.

Use these offline maintenance commands with the worker stopped for a migration or backup:

```powershell
.\.venv\Scripts\python.exe -m autoapply listings cleanup
.\.venv\Scripts\python.exe -m autoapply listings stats
```

The first listing migration backs up the database to ignored private storage and writes `listing_migration_report.json`. Freshness counters are included in application-history statistics and `status`. See [FRESHNESS_MIGRATION.md](FRESHNESS_MIGRATION.md) for the migration audit, validation, date assumptions and source limitations.

## Gmail OAuth

Create a Google Cloud project, enable Gmail API, configure your OAuth consent screen and add a **Desktop app** OAuth client. Download its credentials as `data/private/oauth/gmail-client.json`.

```powershell
.\.venv\Scripts\python.exe -m autoapply gmail-auth
```

The requested permission is [Gmail read-only](https://developers.google.com/workspace/gmail/api/auth/scopes). Authorized tokens are saved at `data/private/oauth/gmail-token.json`. Configure exact trusted sender domains per application hostname in private config:

```yaml
gmail:
  enabled: true
  sender_domains:
    apply.employer.example:
      - employer.example
```

Use domains verified from actual employer correspondence. Queries read at most ten recent messages from those domains and require a verification-related subject and a timestamp after this application's first start. Multiple candidates, multiple codes, broad result sets or unexpected link domains do not produce an automatic choice. Expired/revoked tokens need `gmail-auth` again. No inbox-wide download or outbound mail operation exists.

## Runtime AI

No paid API is configured. Browser providers are opt-in and use your legitimate existing session. Each provider specifies the exact UI controls and displayed model name; a model mismatch stops that provider. You are responsible for configuring a service/account where your use is permitted and subscription usage is included. Do not configure automatic paid overages or account cycling.

```yaml
ai:
  enabled: true
  allow_paid: false
  providers:
    - name: my_included_provider
      billing: included
      tier: 3
      url: https://your-provider.example/new-conversation
      input_selector: '[aria-label="Message"]'
      send_selector: '[aria-label="Send"]'
      response_selector: '[data-role="assistant"]'
      model_selector: '[aria-label="Selected model"]'
      model_text: Exact displayed model name
```

These are example selectors, not a configured live service. Use a fresh conversation URL, verify the selectors and sign in once with `login`. Providers receive the application question, verified profile facts, current job context and style samples. Tier 3 writing never silently falls back to a lower tier. An unavailable provider stops the application without fabricating prose.

For an existing ChatGPT subscription login, an installed Codex CLI can be configured as an included provider: `{name: chatgpt_subscription, type: codex_cli, billing: included, tier: 3}`. This uses ephemeral, read-only, text-only `codex exec` requests in a temporary directory, disables tools/plugins, and refuses API-key authentication. It uses the CLI's default model unless `model` is explicitly configured. Subscription usage limits still apply. See [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

Company/role interest prompts are classified as `FREE_RESPONSE_COMPANY_INTEREST` and `FREE_RESPONSE_ROLE_INTEREST`. Required and optional ordinary narratives both run before the optional-field skip rule. Generation targets 60–120 words, respects field limits, and selects relevant verified resume/profile facts plus the current listing. A separate factual audit must accept all claims and supply exact source quotations before automatic entry. Generated answers and supporting evidence are logged privately; they are not marked user-verified memory. A model audit reduces unsupported claims but cannot prove semantic correctness. Sensitive/legal prompts stay outside this route, and optional demographic disclosures retain their existing policy.

The final Submit uses one `page.mouse.move` / `page.mouse.click` sequence. Fresh boxes before and after movement must agree, and the center must hit the button or its descendant (including ancestor-frame occlusion checks). Obstructions stop with `SUBMIT_ELEMENT_OBSTRUCTED`. There is no locator-click fallback or automatic second click. ATS upload network activity, busy controls, disabled Replace controls and visible upload warnings gate readiness. Mouse move, down, up, click, submission traffic and affirmative employer confirmation are recorded separately. HTTP 200 alone never confirms submission.

## Run and inspect

```powershell
.\.venv\Scripts\python.exe -m autoapply scan
.\.venv\Scripts\python.exe -m autoapply queue
.\.venv\Scripts\python.exe -m autoapply work-once
.\.venv\Scripts\python.exe -m autoapply run
```

`scan` only discovers jobs; it does not fill or submit applications. `work-once` processes one queued application and **can submit** when auto-submit is enabled. `run` starts the source polling loop, Discord bot and one browser worker. `run` and `work-once` require contact details, resume and enabled Discord credentials. `work-once --allow-incomplete` is available for controlled inspection; unknown answers still pause.

Auto-submit defaults to true. Disable it before manual validation if desired:

```powershell
.\.venv\Scripts\python.exe -m autoapply autosubmit off
.\.venv\Scripts\python.exe -m autoapply pending
.\.venv\Scripts\python.exe -m autoapply answer 42 "Your verified answer"
.\.venv\Scripts\python.exe -m autoapply inspect 12
.\.venv\Scripts\python.exe -m autoapply retry 12
```

Local commands also include `pause`, `resume`, `recent`, `status`, `skip` and `stop`. Applications waiting for input do not block unrelated jobs. An application in `READY` must be retried after enabling auto-submit, so the worker reopens and revalidates it.

Security holds preserve the current browser tab and populated form while the worker runs. `run` and `work-once` require headed mode. Complete the protected step yourself, then send `!resume-manual ID` in Discord or run `python -m autoapply resume-manual ID` in another terminal. `work-once` stays alive during this handoff. Resuming after a recorded submit intent only inspects the result; it never clicks Submit again. Verification outcomes use `verification ID passed|failed|skipped`. See [SECURITY.md](SECURITY.md) for states, detection, diagnostics, retry policy, and session limitations.

## Submission and restart recovery

`SUBMITTING` and a durable intent timestamp are committed before the final click. `SUBMITTED` requires positive confirmation text. A timeout or crash after intent is **not** retried automatically. Check the employer's application history and reconcile with evidence:

```powershell
.\.venv\Scripts\python.exe -m autoapply reconcile 12 submitted --evidence "Employer confirmation number from my account"
.\.venv\Scripts\python.exe -m autoapply reconcile 12 not-submitted --evidence "Employer history confirms no application exists"
```

Only choose the outcome you verified. Security rejections cannot be reconciled into automatic retry. Existing employer applications use `ALREADY_APPLIED`, not a new submission. The daily cap counts submission intents, including uncertain outcomes, in UTC. Its default is 100. Attempts are separated by 60 seconds by default. Pre-submit network/5xx/429 failures use bounded exponential backoff and jitter, with three retries. Terminal jobs cannot be casually reopened; new requisitions receive new records, while unresolved matching employer/role/ATS history requires review.

## Windows server startup

Use the same Windows user that owns the browser profile, with an interactive session available. Test `work-once` before enabling unattended operation. The helper below registers an at-logon scheduled task; it is never run automatically by installation or development:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Register-AutoApplyTask.ps1
```

The task runs `scripts/Start-AutoApply.ps1`, uses the project virtual environment and working directory, and restarts on failure. The helper uses an interactive user logon instead of storing a Windows password. User logon is required after reboot for visible browser operation. You can configure equivalent settings in Task Scheduler yourself. Stop/disable the task there before maintenance.

## History, tests and privacy

SQLite at `data/private/autoapply.sqlite3` is the canonical state store. Readable per-application archives contain listing, sources, questions/answers, events, status, confirmation and screenshots. Closed, invalid and ineligible listings remain in the same archive structure with explicit status/reasons. Rotating operational logs live under `logs/`. Private data is not encrypted by this application; protect it with your Windows account, disk permissions and appropriate backups. Back up the private directory with the worker stopped.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -m "not browser" -q
.\.venv\Scripts\python.exe -m autoapply privacy
```

Most tests use synthetic data; browser tests use only a local HTTP fixture and do not contact employers. Privacy checks inspect staged Git contents plus untracked, non-ignored files and flag private paths and common secret patterns. They complement review; no regex can guarantee detection of every secret. Before a commit, inspect `git diff --cached` and run the privacy command. Never force-add ignored files. This project never pushes to GitHub.

Troubleshooting: run `doctor` for missing setup, `inspect ID` for recorded stage and reason, `login URL` for expired sessions, and `retry ID` for retry-eligible pre-submit issues. Protected steps use `resume-manual ID` with the original worker and tab still open. Browser installation errors usually mean Chromium was not installed for this environment. A worker-lock error means another worker/login session is using the profile. Source errors remain in `source_revisions`; failed scans do not advance processed revisions.

See [DESIGN.md](DESIGN.md) for implementation boundaries and validation details.

## Browser cursor

Ordinary application and AI-provider clicks now use one per-page cursor controller:
resolve, scroll, stabilize, hit-test, move the actual browser pointer along a Bézier
path, validate again, then press and release through the same backend. Position
persists between actions. Native select and file upload operations choose the
framework API before interacting; there is no click fallback after a failed press.

The `cursor` mapping in the example config defaults to deterministic `restricted`
mode and the Playwright backend. `backend: cdp` selects Chromium CDP mouse input.
`enabled: false` stops cursor operations; `perform_physical_clicks: false` allows
movement but rejects presses. Neither setting redirects clicks to another API.
All timing, geometry and path defaults are centralized in
`autoapply/cursor/types.py::CursorConfig`; unknown or invalid settings fail at startup.

For an owned local demo, explicitly allow its exact origin before enabling seeded
presentation variation. Child frames must also have allowed origins:

```python
import random
from autoapply.cursor import CursorController, CursorConfig, Point

cursor = CursorController(page, CursorConfig(
    mode="owned_test", owned_origins=("http://localhost:8000",),
    target_strategy="GAUSSIAN_INTERIOR", noise_enabled=True,
    overshoot_enabled=True, timing_variation=True, curve_variation=True,
), rng=random.Random(42))
await cursor.synchronize(Point(1, 1))
await cursor.click_element(page.get_by_role("button", name="Demo"))
```

Use `get_cursor(page)` for application pages rather than creating a second
controller. Enter manual mode before handing the pointer to a person, and call
`exit_manual_mode(Point(x, y))` only after the protected step is complete. Resume
rechecks security and deliberately moves to the supplied viewport position; browser
APIs cannot read the user's current physical pointer location. Failed or cancelled
operations require explicit synchronization before further automation. No cursor
operation retries an uncertain application submission.

Cursor tests use synthetic pages with intercepted network requests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_cursor.py
```

### Application history

History remains private in `data/private/application_history`. Each application
keeps its existing bundle (application/listing/answers/events/confirmation JSON,
description and screenshots), under its current status:

```text
application_history/
  discovered/<id>_<company>_<title>/application.json
  opened/                     filling/
  ready_to_submit/            submitting/
  submitted/                  manual_required/
  failed/                     unknown/
  rate_limited/               closed/
  invalid/                    duplicate/
  ineligible/                 already_applied/
  statistics.json
  migration_report.json
```

`application.json` includes a stable `application_id`, `application_state`, and
`status_history`. SQLite remains authoritative for live workflow operations.
Database transitions and security updates automatically move bundles and update
statistics after commit. Closed, invalid, duplicate, ineligible, and already-applied
outcomes remain distinct from failed or newly confirmed submissions.

Startup validates records, repairs misplaced bundles, reconciles duplicate IDs,
preserves ambiguous records as UNKNOWN, and exports missing database applications.
The first migration copies the old tree to the sibling
`application_history_migration_backup` directory. Conflicting duplicate files and
malformed bytes are retained inside the canonical bundle's `preserved` directory.
Explicit database deletion moves its bundle to `application_history_deleted`;
neither backup nor deleted bundles count in statistics. Empty legacy directories
are reported and excluded from application totals.

```powershell
.\.venv\Scripts\python.exe -m autoapply history migrate
.\.venv\Scripts\python.exe -m autoapply history validate
.\.venv\Scripts\python.exe -m autoapply history stats
.\.venv\Scripts\python.exe -m autoapply history rebuild-stats
```

Rebuild reads application records, not the old statistics file. Statistics include
status/ATS/company/location/source counts; security-state/provider events;
submission, failure, manual and unknown rates; attempts and manual interventions;
UTC daily/weekly/monthly activity; timing averages/median and oldest/recent records.
Daily detail retains 366 calendar days. Rates are fractions; percentage fields
multiply by 100. `completed_attempt_success_rate` uses SUBMITTED + FAILED as its
denominator, whereas `submission_rate` uses all applications. Already-applied
records without affirmative confirmation do not count as newly submitted.

`total_submission_attempts` counts recorded SUBMITTING entries (with a lower bound
of one when intent or confirmation exists). The worker's `attempts` field counts
processing runs and is deliberately not used for that metric.
`total_failed_submissions` requires both FAILED and recorded submission intent.
Manual and security totals count recorded episode changes; repeated diagnostics
do not create new episodes. Missing historical timestamps yield null durations;
only completed manual episodes contribute to their average. Company, location,
and source values are stored values; no new role or remote-work classification is
inferred. Multi-source applications contribute to each recorded source count.

Code uses `db.history.get_application(id)`, `find_by_job_id(id)`,
`find_by_url(url)`, and `list_applications(status=None)` instead of constructing
paths. `archive_application(config, db, id)` still returns the current bundle path.
Standalone maintenance functions in `autoapply.archive` are
`migrate_application_history(root)`, `validate_application_history(root)`, and
`rebuild_application_statistics(root)`. Status changes continue through
`Database.transition` and `update_security`; both preserve durable transition
history. Imported submission records also participate in submission-conflict checks.

Distinct explicit application IDs remain distinct; missing IDs are derived from
job/URL metadata and matched to an explicit ID when unambiguous. Unknown records
without identifying metadata receive a stable recovery ID. Review migration
warnings before interpreting uncertain legacy data as confirmed outcomes.
# Browser navigation and durable Discord input

Production submission telemetry distinguishes intent, the click call, trusted
mouse events reaching the selected button, form/navigation changes, candidate
submission requests, response status, and affirmative confirmation. It records
button geometry and hit-test evidence before the click and diagnostics afterward.
Request bodies, headers, and form values are excluded. A request endpoint matching
submission terminology is evidence of a candidate request, not proof of success.

If the observation window ends with no form, navigation, or network effect,
`SUBMIT_CLICK_NOT_DELIVERED` pauses the application without retry. Its telemetry
still states whether a trusted click reached the button: a delivered click with a
silent handler must not be described as a proven pointer miss. If a request or
form response occurs without confirmation, the outcome remains
`SUBMISSION_UNKNOWN`. User reconciliation records the previous intent and supplied
evidence before allowing an explicitly authorized new attempt on the same record.

Before readiness is evaluated, the worker waits for tracked upload requests and
the upload control's busy/disabled replacement state to clear. Selecting a local
file alone does not establish that the ATS has finished receiving it. A bounded
upload wait preserves the form without clicking Submit if the upload stays busy.
Confirmation detection evaluates sentences independently so follow-up wording
such as “we will contact you if there are next steps” does not negate an explicit
successful-submission sentence.

Application windows use a desktop layout (1440x900 by default), with headed
windows and viewports fitted to the available monitor. Before opening the dedicated
profile, saved per-site zoom is backed up and reset; navigation verifies that zoom
has not changed again. This takes effect on the next browser launch, not an
already-preserved live tab. Directional scrolling
checks the target's ancestors for nested scroll areas, verifies movement,
and falls back from wheel input to incremental container scrolling and
PageUp/PageDown. Searches stop after 24 steps. Navigation failures retain
`SCROLL_FAILED` or `ELEMENT_NOT_REACHABLE` diagnostics instead of asking for
a nonexistent missing profile fact.

Discord sends grouped input requests, submission confirmations, and actionable
manual/terminal failures. Routine progress stays in local logs. Commands are
acknowledged immediately: `!help`, `!status [APP_ID]`, `!answer QUESTION_ID ANSWER`,
`!yes [QUESTION_ID]`, `!no [QUESTION_ID]`, `!skip QUESTION_ID`, `!resume [APP_ID]`,
and `!stop [APP_ID]`. Shortcuts require an unambiguous waiting target. Answering
the last pending question queues resumption; a partial answer leaves the hold.

Input holds have no expiration. Questions, options, answers, uploaded-document
metadata, URL, ATS, progress, and audit history are persisted in SQLite. After
restart, an explicitly resumed, resolved `INPUT_REQUIRED` hold can reconstruct
the same application using its saved answers and resume. Existing submission
intent, confirmation, security rejection, or conflicting application identity
prevents this reconstruction. Security steps still require their own inspection.

The persisted `controlled_application_id` setting freezes discovery and general
queue dispatch and restricts claims, resumption, and Discord mutations to that
application. It remains set after a controlled regression ends. Do not clear it
until production queue processing is explicitly requested.

Recognized reusable factual answers entered in Discord also populate the
verified canonical-fact store; employer-specific prose remains scoped to its
application. Narrative AI drafts require a configured included provider and
verified writing facts. Ordinary new prose is entered after a separate grounding audit;
legal/factual prompts are not routed to prose generation merely because they
use a text box. No provider is invented or silently substituted.
