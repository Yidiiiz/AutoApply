# Security holds and submission safety

AutoApply uses the existing Python/async Playwright worker, a headed persistent Chromium profile, SQLite WAL database, semantic form adapters, and owner-only Discord outbox. It does not simulate human behavior or solve, bypass, or manipulate anti-abuse mechanisms.

## State and compatibility

Schema version 2 adds fields to existing applications in a transaction. Existing IDs, answers, source links, event history, confirmation evidence, and original update timestamps survive migration. Legacy queue `status` values remain compatible with commands and discovery. Separate fields describe the application and verification independently:

| Dimension | Values |
| --- | --- |
| `application_state` | DISCOVERED, OPENED, FILLING, READY_TO_SUBMIT, SUBMITTING, SUBMITTED, FAILED, MANUAL_REQUIRED, UNKNOWN, RATE_LIMITED |
| `security_state` | NONE, PASSIVE_PROTECTION_DETECTED, INTERACTIVE_CHALLENGE, RATE_LIMITED, SPAM_REJECTED, AUTOMATION_REJECTED, EMAIL_VERIFICATION, PHONE_VERIFICATION, IDENTITY_VERIFICATION, FRAUD_REVIEW, UNKNOWN_SECURITY_FAILURE |
| `verification_state` | NOT_REQUIRED, PENDING, PASSED, FAILED, SKIPPED, UNKNOWN |

For example, `status=MANUAL_REVIEW` can have `application_state=UNKNOWN`. An Indeed application with affirmative confirmation followed by Persona has `status=SUBMITTED`, `application_state=SUBMITTED`, `security_state=IDENTITY_VERIFICATION`, and `verification_state=PENDING`. Verification failure or a user-reported skip cannot erase confirmed submission.

Company, role, job ID, application URL, and legacy ATS identity remain in the existing joined application/job model. Added metadata includes detected ATS, provider/type, HTTP status, security message, confirmation flags/evidence, manual reason, retry permission, URLs before/after submission, previous/current URL, title, screenshot path, diagnostic timestamp, and whether the worker owns a live held tab.

## Shared components

- `providers.py`: ATS recognition, independent of stable job identities and form support. Recognizes Ashby, Greenhouse, Lever, Workday, Workable, Indeed, LinkedIn, SmartRecruiters, iCIMS, Taleo, Jobvite, BambooHR, Rippling, SAP SuccessFactors, ADP, custom forms, and UNKNOWN. Hostnames, embedded provider URLs/DOM markers, and Greenhouse job parameters provide evidence.
- `security.py`: `SecurityDetector`, `SecurityResult`, `SubmissionClassifier`, and `PreSubmitState`. Recognizes reCAPTCHA, hCaptcha, Cloudflare Turnstile/challenge pages, Arkose/FunCaptcha, DataDome, Persona, CLEAR, and unknown challenges. Script/badge presence is passive; displayed challenge widgets, rejection messages, verification prompts, and relevant HTTP failures can block processing.
- `retry.py`: error categories and bounded `RetryPolicy`.
- `handoff.py`: `ManualHandoffManager` owns preserved tabs, diagnostics, and clear private notifications.
- Existing `browser`, `applications`, `engine`, `database`, `control`, and CLI connect these components to the existing workflow. Discord continues to deliver through its durable outbox.

Detection runs after entry/navigation, around each form step, before the final click, while observing its result, and after exceptions. It uses visible text excluding editable values, DOM metadata, iframe/script markers, URL/host, browser dialogs, HTTP status and bounded first-party JSON error messages. Response bodies are never stored. Disabled submit controls and missing attachments cause validation holds. Conditional fields are re-inspected before submission.

reCAPTCHA badge descendants and anchors with `size=invisible` are passive integration; provider-named containers alone do not establish an interactive challenge. Visible normal checkbox anchors, challenge frames, and rendered challenge prompts still block. Hidden or transparent ancestors are considered when checking visibility, and provider iframe contents are not read.

A background GET fetch/XHR returning 401 remains in sanitized HTTP provenance but does not independently replace the document's security status. Document and write-request failures, rendered authentication/security prompts, and explicit security error messages retain their blocking behavior. This distinction does not establish that every background failure is harmless; normal form validation and submission confirmation are still required.

Greenhouse's hosted React upload component removes its file input during upload.
AutoApply follows the stable attachment container instead of waiting for that
removed input. Readiness requires the exact filename in the accepted attachment
component, an enabled Remove control, no progress/error state, and a completed
successful tracked S3 upload for each file selection. Object paths and signed URLs
are not retained. Attachment references survive reinspection of the same page;
the attachment and network evidence are checked again before submission.

## Submission and retry rules

Pre-submit results are NO_SECURITY_BLOCK, PASSIVE_PROTECTION_PRESENT, INTERACTIVE_SECURITY_STEP, or VALIDATION_ERROR. Passive scripts/badges alone allow processing. Required answers, browser validation, uploaded file presence/size, resume digest, pause/auto-submit settings, daily limits, and duplicate history are checked before submission. Playwright actionability and DOM-change waits synchronize interactions. A process lock, an asyncio processing lock, and a SQLite transaction guard the final intent. The intent is committed before a single click.

Only affirmative rendered confirmation text or a clearly labeled application confirmation number establishes success. Negated/hypothetical messages, a click, HTTP 200, or an arbitrary `/success` URL do not. A simultaneous explicit rejection cannot create new success evidence. A bounded observation window continues after initial success to catch a subsequent verification step. Existing affirmative evidence is permanent even if verification later fails.

| Category | Automatic retry |
| --- | --- |
| NETWORK_ERROR, SITE_ERROR, RATE_LIMIT | Only before submission intent; limited by `processing.max_retries` |
| FORM_VALIDATION_ERROR | Manual correction; no automatic retry |
| CAPTCHA, BOT_CHALLENGE, SPAM_REJECTION, AUTOMATION_REJECTION | Disabled |
| EMAIL_VERIFICATION, PHONE_VERIFICATION, IDENTITY_VERIFICATION, FRAUD_REVIEW | Disabled |
| UNKNOWN_SECURITY_FAILURE, SUBMISSION_UNKNOWN | Disabled |
| AUTH_REQUIRED | Manual sign-in in the preserved tab |

Backoff starts at 60 seconds, doubles per attempt, adds at most five seconds of jitter, and caps the total at 1,800 seconds. Any failure after intent remains manual/unknown, including 429, timeout, and 5xx. Spam/automation/fraud rejection does not become retryable merely because its banner disappears. Existing confirmed or unresolved application identities block duplicate clicks. Matching employer/title/ATS aliases are conservatively held for review, including potentially distinct requisitions with the same name.

## Manual workflow

Production `run` and `work-once` require headed mode (`browser.headless: false`, the default). Local test fixtures can use headless mode.

1. A protected step holds the current tab without navigation, reload, form clearing, cookie clearing, or automatic resubmission. The application queue pauses while live manual tabs remain. Discord states whether the application is already confirmed, the provider/reason, URL, and whether the session is preserved.
2. Complete the step yourself in that browser. Keep the worker running. Identity documents and identity verification are handled only by you.
3. Send `!resume-manual ID` to Discord, or run this from a second terminal:

   ```powershell
   .\.venv\Scripts\python.exe -m autoapply resume-manual 12
   ```

4. The running worker inspects that exact tab again. If the challenge cleared before any submit intent, an eligible form resumes there and is revalidated. If submit was already attempted, resuming only checks the result; it never clicks Submit again. Continued security failure keeps the hold. Confirmation resolves the application and permits the queue to continue.
5. For a recorded verification step, explicitly report its outcome if necessary: `!verification ID passed`, `!verification ID failed`, or `!verification ID skipped`. Equivalent local CLI commands are available. Reports are recorded as user reports. They never change SUBMITTED back to FAILED. Disappearance without affirmative verification evidence remains UNKNOWN.

`work-once` stays running during a live handoff and accepts Discord/local resume requests. If Discord fatally disconnects during a daemon handoff, the browser stays open and local controls remain available; restart the worker after manual work to restore delivery. Notifications remain queued during outages. Manual completion can also be reconciled with existing `reconcile ... --evidence` commands; security rejections cannot use reconciliation to enable automatic retry.

## Diagnostics and limits

Diagnostics live only in ignored private storage and archives. URLs exclude queries/fragments and verification paths are redacted. Text is bounded and common credential/token/code patterns are redacted. Screenshots mask editable controls, frames, videos, and canvases. Identity, email/phone verification, and open-dialog pages omit screenshots to avoid retaining documents, faces, or codes. Raw DOM/HTML dumps, cookies, tokens, request payloads, full response bodies, and verification answers are not added to diagnostics. Existing browser-profile persistence is unchanged. Existing Gmail read-only utilities remain available; protected verification prompts now require manual completion instead of storing codes as application answers.

Detection is conservative and fixture-tested, not universal ATS form support. Only the existing Ashby/Greenhouse/Lever entry adapters have dedicated application behavior; other providers use the generic form adapter. Custom widgets and ambiguous text may require manual review. JSON error inspection requires a bounded Content-Length and ignores opaque/non-JSON responses. Known-provider URLs alone do not establish submission success. Verification redirects occurring after the configured observation window may require a later manual check.

Live tabs and populated forms are preserved while the worker and browser remain alive. Explicit shutdown, OS/browser crashes, tab closure, or site-driven expiry can lose them. Recovery reports that limitation, retains submission intent, and never assumes a form was restored or automatically recreates an uncertain submission. Existing private history is preserved; old screenshots are not retroactively scrubbed.

Tests cover the requested success, validation, provider, passive protection, 403/429, spam/automation rejection, unknown outcome, retries, duplicates, migration, protected-step resume, and post-submission verification cases using synthetic local pages. No live employer submission is part of this implementation validation.

## Changed files and validation

### Cursor interaction boundary

The cursor controller uses deterministic paths and safe interior targets on
authorized third-party pages. Gaussian/uniform targets, smooth noise, overshoot,
and timing/curve randomization require `owned_test` mode and an explicit exact-origin
allowlist, including the target's frame ancestors. They are presentation/test
features and do not react to security-provider scores or rejection outcomes.
No browser identity, automation flags, fingerprints, proxy identities or challenge
responses are modified.

The existing security detector runs before interactions, during long movements,
before presses and before resumption. Classified JSON security errors and open
browser dialogs also stop the page cursor. Manual handoff invalidates active and
queued generations, releases owned input and marks position unknown while keeping
the populated tab available. Clearing a banner alone does not authorize submission:
the engine's existing security and submission-intent checks still apply.

Input cleanup may send one release for an already-owned button. A browser can
interpret that release as a click; cancelling after mouse-down cannot undo a
delivered event. The system never treats cancellation as proof of non-submission
and never follows it with a fallback click. Uncertain input failures remain stopped
until explicit recovery; uncertain final submissions retain their durable hold.

Added `autoapply/providers.py`, `autoapply/security.py`, `autoapply/retry.py`, `autoapply/handoff.py`, `tests/test_security.py`, and this document. Updated `autoapply/models.py`, `autoapply/database.py`, `autoapply/browser.py`, `autoapply/applications.py`, `autoapply/engine.py`, `autoapply/control.py`, `autoapply/__main__.py`, `tests/test_browser.py`, `README.md`, and `DESIGN.md`.

Validation for the earlier security-state implementation (the subsequent cursor
integration passed all 207 tests; see [DESIGN.md](DESIGN.md#cursor-subsystem)):

- Full existing/new suite: **141 passed**; the subsequently added embedded-application-frame regression also passed independently (**1 passed**). There are 142 tests in the resulting suite, including 88 added for this task.
- Ruff syntax/undefined-name checks over all application/test files passed. The broader `F` checks passed for the new provider/security/retry/handoff modules and security tests.
- Mypy with untyped-body checking passed for the five shared model/provider/security/retry/handoff modules. This is a targeted check; the existing application does not have project-wide strict typing configured.
- Python bytecode compilation, local wheel build, CLI help, and Git privacy checks passed. The missing build backend was installed in the local virtual environment before the successful build. Lint/type tools were also installed there.
- The existing Persona history migrated to schema v2 as MANUAL_REQUIRED / SPAM_REJECTED, with retry disabled and no claimed submission confirmation. No live retry was attempted.

Commands used (from the project root):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/test_security.py -k embedded_application -q
.\.venv\Scripts\python.exe -m ruff check --select E9,F63,F7,F82 autoapply tests
.\.venv\Scripts\python.exe -m ruff check --select F autoapply/security.py autoapply/providers.py autoapply/retry.py autoapply/handoff.py tests/test_security.py
.\.venv\Scripts\python.exe -m mypy --check-untyped-defs --ignore-missing-imports --follow-imports=silent autoapply/security.py autoapply/providers.py autoapply/retry.py autoapply/models.py autoapply/handoff.py
.\.venv\Scripts\python.exe -m compileall -q autoapply tests
.\.venv\Scripts\python.exe -m pip wheel --no-deps --no-build-isolation --wheel-dir data/private/build-check .
.\.venv\Scripts\python.exe -m autoapply privacy
.\.venv\Scripts\python.exe -m autoapply --help
```

Controlled reconstruction preserves application isolation: an explicit application claim checks that listing directly, without queue-wide cleanup. Controlled closure and freshness guards also avoid unrelated listing maintenance. Saved searchable-dropdown answers are used as search hints only; the current form must expose the exact option, and reuse requires unchanged question identity, scope, required status, and length constraints. Normal validation, fresh upload lifecycle evidence, security detection, and the single physical Submit sequence remain required.
