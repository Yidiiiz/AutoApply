# AutoApply — project context for chat

Prepared September 25, 2026 from local project documentation, source structure, configuration examples, and selected implementation files. This is a concise handoff, not a full source export or a verified production-status report.

## Suggested prompt

Use this file as context for my AutoApply project. Distinguish implemented behavior, documented behavior, and behavior verified by tests. Preserve the data-provenance, privacy, and submission safeguards below. Ask for relevant source files when a change requires details absent from this summary. Historical operations and scripts do not authorize new live operations. My next request is: [describe the task].

## Purpose and stack

AutoApply is a personal internship discovery and application assistant. It discovers listings, deduplicates and ranks them, fills supported employer forms with verified applicant information, requests missing answers through Discord, and records application outcomes.

- Python 3.11+, package version 0.1.0; no separate web frontend or Node build.
- Async Playwright with a persistent Chromium profile.
- SQLite for queue, questions, settings, audit events, and recovery; YAML for configuration/profile.
- Git and Beautiful Soup for repository listing discovery.
- discord.py for owner-restricted direct-message controls and notifications.
- Google API/OAuth libraries for read-only Gmail utilities.
- Configurable AI writing providers; pytest and pytest-asyncio for tests.
- Windows launcher and scheduled-task scripts.

## Processing flow

Source discovery → dated listings and source provenance → canonical URL/ATS identity deduplication → freshness and eligibility checks → persistent queue → browser inspection → verified answers or user input → upload/form validation → durable submission intent → affirmative confirmation or manual hold → audit/history.

One asyncio worker owns application processing and the browser. Discord runs concurrently on the event loop. Blocking Git/Gmail work uses threads. SQLite uses WAL and short transactions. A process lock protects the browser profile from concurrent workers.

## Source map

Paths below are relative to the repository root.

| Files | Responsibilities |
| --- | --- |
| `autoapply/__main__.py`, `config.py`, `models.py` | CLI, configuration/profile access, shared models |
| `engine.py`, `runtime.py` | Worker orchestration, lifecycle, exclusive ownership |
| `sources.py`, `jobs.py`, `freshness.py`, `listing_store.py` | Discovery, identity, eligibility, date policy, listing lifecycle |
| `database.py`, `archive.py`, `history_statistics.py` | Durable queue, questions, transitions, history, statistics |
| `browser.py`, `applications.py`, `providers.py` | Browser lifecycle, semantic forms, ATS detection and entry behavior |
| `dropdowns.py`, `combobox.py`, `smartrecruiters.py`, `field_mapping.py` | Custom controls and field interpretation |
| `uploads.py`, `form_validation.py`, `submission_probe.py` | Upload completion, readiness, click/request/confirmation evidence |
| `scrolling.py`, `cursor/` | Scrolling, geometry, actionability, serialized physical input |
| `answers.py`, `standing.py`, `disclosures.py` | Verified answers and bounded standing assertions/disclosures |
| `ai.py`, `codex_writer.py` | Writing generation, grounding, provider integration |
| `security.py`, `retry.py`, `handoff.py` | Security classification, retry policy, preserved tabs and resume |
| `control.py`, `discord_bot.py`, `gmail.py` | CLI/Discord control, durable notices, read-only email extraction |
| `fill_batch.py`, `batch_approval.py` | Fill-only selection, checkpoints, reconstruction, destination restrictions |
| `eligibility_repair.py`, `scripts/` | Maintenance, launchers, and incident-specific operational utilities |
| `tests/`, `tests/fixtures/` | Unit/regression coverage and local synthetic browser fixtures |

## Configuration and private data

Public schemas/defaults are in `config/config.example.yaml`, `templates/profile.example.yaml`, and `.env.example`.

Runtime data belongs in ignored local storage: `data/private/config.yaml`, `data/private/profile.yaml`, the intended `data/private/resumes/resume.pdf`, browser sessions, OAuth files, writing samples, database, and application records. Credentials belong in `.env` or private OAuth/session storage.

Example defaults, which private settings and durable runtime controls can override:

- Target season: Summer 2027; listing age limit: 30 days.
- GitHub polling: 5 minutes; LinkedIn/Handshake polling: 45 minutes.
- Discovery caps: 250 results per query, 5 pages per query.
- Headed browser; automatic submission enabled in the example configuration.
- Submission confidence threshold: 0.98; daily processing ceiling: 100 applications.
- LinkedIn/Handshake discovery disabled until configured.
- AI enabled in the example, paid usage disabled, provider list empty.

Configured repository sources are SimplifyJobs/Summer2027-Internships, speedyapply/2027-SWE-College-Jobs, and vanshb03/Summer2027-Internships. These names describe local configuration; their remote availability was not checked for this handoff.

## Behavior and invariants to preserve

1. Unknown profile values remain unknown. `null` does not mean `false`. Do not invent applicant facts, exact dates, salary expectations, or credentials.
2. Answers use verified provenance and bounded semantic matching. Unknown qualifiers and stronger requirements require review. Employer-specific prose stays scoped appropriately.
3. Generated prose is a proposal, not a verified personal fact. Preserve grounding and user-confirmation requirements for submission and verified reuse.
4. Verified posting dates must meet the rolling freshness limit, capped at 30 days. Discovery time is not posting time. Unknown/stale dates do not establish eligibility.
5. Deduplicate by canonical URL and tenant/requisition identity, not title alone. Preserve all source references and submission-conflict guards.
6. Validate required fields, answers, uploads, confidence, pause controls, and submission limits before submission. File selection alone does not establish upload completion.
7. Persist submission intent before the final interaction. Success requires affirmative confirmation. A timeout, crash, or ambiguous response after intent must not automatically trigger another submission.
8. Preserve confirmed outcomes, manual retirement guards, and reconciliation evidence. Listing cleanup must not erase application history or reopen final applications.
9. Authentication, MFA, CAPTCHA, security challenges, and unsupported forms pause for user handling. Gmail access is read-only; automatic verification-link navigation is not enabled.
10. Preserve worker-owned held tabs and same-session resume semantics. Resumption after existing intent inspects the outcome instead of repeating Submit.
11. Distinguish missing applicant input from browser/navigation failures and execution-approval holds. Environment network denial is not evidence that an employer rejected the application.
12. Controlled application settings constrain discovery, dispatch, resume, and mutations. Do not clear a controlled hold without the relevant operating authorization.

## Fill-only and ATS-specific work

Recent source includes fill-only batch tooling. `fill_batch.py` requires automatic submission off, no controlled-application setting, and no pending manual requests. It limits active application tabs to one, filters queue candidates, and stores private checkpoints. Reconstruction is explicitly verified before treating a draft as recoverable.

`batch_approval.py` implements an incident-specific approval format for exactly five HTTPS destinations, matching application identity and domain, with final-submission restrictions. `fill_batch.py` also contains explicit historical application exclusions. These are specialized operational constraints, not a general-purpose batch API.

Dedicated entry behavior exists for Greenhouse, Lever, and Ashby. SmartRecruiters has focused control/repair code and fixtures, while broader support still requires careful inspection. Workday, complex custom widgets, account creation, and authenticated source flows are not universally supported. Local fixture success does not establish compatibility with every live ATS.

## Commands for development and operation

Typical Windows setup:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) 'data/private/playwright'
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m autoapply init
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m autoapply doctor
```

Supply the private profile, resume, and required integration settings separately. Initialization is documented to preserve existing private configuration.

Run the test suite with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests include freshness, eligibility, history, security, execution approval, disclosures, answers, controls, uploads, submission telemetry, controlled resume, and fill-only behavior. Browser tests use local Chromium fixtures and require the browser installation.

CLI entry point: `python -m autoapply`. Important commands include `status`, `queue`, `pending`, `inspect ID`, `scan`, `autosubmit off`, `answer ID VALUE`, `resume-manual ID`, `reconcile ID submitted|not-submitted --evidence TEXT`, `listings cleanup|stats`, and `history migrate|validate|stats|rebuild-stats`.

`scan` performs discovery. `work-once` and `run` process applications and can submit when automatic submission is enabled. `login` uses the dedicated profile and should not compete with a live worker. Incident scripts may change real private records or operate live forms; inspect their scope before using them.

## Documentation and known drift

- `README.md`: setup, controls, discovery, writing, history, browser/input behavior.
- `DESIGN.md`: architecture and invariants; some sections describe earlier implementation stages.
- `SECURITY.md`: security states, holds, detection, diagnostics, retry and resume.
- `FRESHNESS_MIGRATION.md`, `HISTORY_MIGRATION.md`: migration decisions and historical validation.
- `CONTROLLED_WORKER_RESUME_FOLLOWUP.md`, `SMARTRECRUITERS_REPAIR.md`: focused follow-up/repair context.

README's introductory feature list still mentions 14-day freshness, while its detailed policy and current example configuration specify 30 days maximum/default. Some older support descriptions predate focused ATS repairs. Read implementation and relevant tests before relying on older documentation or historical test totals.

## Scope of this handoff

No tests, live worker, discovery, application filling, submission, or external messaging were run to create this file. Private runtime files were not inspected or included. Current queue, application outcomes, active sessions, credentials, production health, and private settings are unknown.

The older `exports/AutoApply_AI_Context.md` and companion ZIP already present in the repository are separate snapshots; they were not refreshed by this handoff and may omit newer code. For implementation work, provide current relevant source files rather than assuming those older exports match this summary.
