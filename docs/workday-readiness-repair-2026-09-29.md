# Workday readiness repair — 2026-09-29

## Root cause recorded before behavior changes

Read-only evidence: `.tmp/fair-test-final.json`, archived events and screenshots in
`data/private/application_history/manual_required/3556_Viavi Solutions_Software Engineer Co-op/`,
and `data/private/fill-only-checkpoints/3556.json`. The security screenshot shows
the Workday shell and skeleton placeholders, without an applicant form. The
checkpoint contains an empty `frames[0].fields`, no resume hash, and no validation
timestamp. The four saved user-confirmed answers are historical answers, not
evidence of fields filled in this attempt.

The later `attention.png` shows the loaded job-description page and an Apply
button, still without applicant fields. This supplies additional evidence that
the failure preceded asynchronous shell completion; it is not evidence that an
application form or upload ever failed. `.tmp/fair-04.html` is the saved public
listing bootstrap HTML, not a post-navigation applicant-form DOM.

Exact premature path: `Browser.navigate(wait_until='domcontentloaded')` →
`adapter_for` falls back to `GenericApplicationAdapter` for Workday → generic
`begin` finds neither fields nor an Apply button yet and silently returns →
`get_questions` returns an empty inventory (zero candidates is not an error) →
Engine's empty filling loop → unconditional `check_uploads` with `{}` →
`wait_for_uploads` → `UploadTracker.verdict` → manual handoff. `APPLYING` was
recorded at 06:39:57.873514 UTC; `UPLOAD_READINESS` at 06:39:58.332539 UTC.
There is no `UPLOAD_FILE_SELECTED` or `UPLOAD_REQUEST_STARTED` event.

The upload aggregator initializes `replace_visible`, `replace_usable`, and
`file_entry` to true for Boolean AND reduction. With no controls, those initial
values survive untouched. They are not observations of Workday markup. Meanwhile
`attached=bool(controls)` is false, and `verdict` maps every unattached state to
`UPLOAD_FAILED`, even without an attempt. This independently reproduces the
misclassification without opening any employer page. Generic file discovery also
exempts hidden file inputs from visibility filtering; that separate weakness must
not be inherited by the new Workday adapter.

The saved screenshot does not supply a DOM snapshot of the skeleton. Local
regression markup will model the observed state and adversarial controls; it will
not be described as a byte-for-byte captured employer DOM.

Validation baseline: 1,110 collected tests. Canonical resume SHA-256 verified as
`3b71d225bad9e0ab51a65d1dfed2ccf3bb9dfe7bc60b1fb0abecf9d41acb6439`.
Implementation and completed offline validation are recorded below.

## Readiness and inventory ordering

`adapter_for` now recognizes both Workday domain families and constructs
`WorkdayAdapter`. Readiness observations use a 15-second overall deadline per
boundary and a 300 ms stable interval. DOM mutations wake observations early;
bounded 100 ms wakeups also detect geometry changes. A successful observation
requires connected, visible, enabled controls; stable control identity/geometry,
URL and DOM revision; form structure with applicant controls and one supported
Next/Submit action; and no visible loading/skeleton/progress state. The deadline
bounds security and DOM calls as well as the polling loop.

The state model is `LOADING`, `SHELL_READY`, `FORM_READY`, `SECURITY_OR_AUTH`,
`TERMINAL_FAILURE`, and `TIMEOUT_UNCONFIRMED`. Shell readiness authorizes only
inspection or one uniquely identified application-entry click through the normal
production cursor, which retains security, geometry, visibility and hit testing.
It never authorizes inventory or upload. No navigation or click retry is added.
Security and authentication continue through Engine's security gate; a readiness
timeout becomes a `FORM_NOT_READY` manual hold with automatic retry disabled.

The gate runs before listing inspection, form entry, every semantic inventory,
and actual file selection. Workday can fill a verified earlier step and use its
existing guarded Next transition before a resume control appears. It defers
upload validation only while there are no uploads, selections, or tracked upload
requests. Final readiness still requires a verified resume. Accepted attachments
can cross an observed step using the existing same-session receipt mechanism,
never a restored database flag. Receipt selectors are restricted to file inputs
so reused field tokens on later text fields cannot impersonate the attachment.

## Upload semantics and actionability

| Evidence | Category / behavior |
| --- | --- |
| No selection and no upload request | `UPLOAD_NOT_ATTEMPTED`; no final lifecycle evaluation |
| Discovered Workday control is hidden, disabled, detached or otherwise unavailable before input | `UPLOAD_CONTROL_NOT_READY` |
| Attempted upload loses attachment or its request fails | Existing `UPLOAD_FAILED` |
| Existing positive attachment, network and stable UI checks pass | Existing `UPLOAD_READY` |
| Attempted but pending/unconfirmed evidence | Existing `UPLOAD_PENDING`, `UPLOAD_NOT_STARTED`, `UPLOAD_STALLED` |

Empty upload inventories now report false for replace/file-entry flags. Workday
inventory removes the generic hidden-file exemption, rejects templates, hidden or
inert ancestors, ARIA-hidden/busy controls, zero geometry, CSS hidden/transparent
elements and disabled controls. It rechecks the production
`ActionabilityValidator` immediately before selection. Hidden custom file pickers
are conservatively unsupported until their visible control association is
verified; no speculative picker clicking was added. Upload UI visibility also
checks connection, ancestor visibility and geometry. Workday receipt observations
recheck actionability when the original control is present.

All existing upload success requirements remain in place, including selection,
attachment filename/size, completed known network evidence where required,
metadata acceptance, no active/failed upload, clear UI, stable receipt, and fresh
file snapshot/hash compatibility. A changed canonical file invalidates prior
evidence. No older resume or answer policy is selected.

## Cleanup correction

Page/context/driver teardown tolerates only Playwright `TargetClosedError` as
successful already-completed cleanup. Closing twice is safe. Cursor cancellation
and pending-page-close errors of other types propagate after resource cleanup.
Closed pages release their leases, including close races; an unrelated failed
page close does not discard ownership of a still-open page. Driver disposal and
lease cleanup stay in `finally`. Persisted telemetry is not rewritten or erased.
The existing OS-backed worker-lock context manager is unchanged; regression tests
prove it can be reacquired after already-closed-context teardown.

## Checkpoint assessment

`FILLED_CHECKPOINT` is an existing generic persistence label used by held forms
and baseline consumers. Public-label migration is deferred to avoid a lifecycle
redesign. New checkpoints/settings explicitly include `form_filled=false` and
`upload_ready=false` for unready/manual holds, retain null `validated_at`, and do
not assert reconstruction. Operator readiness masks stale strong checkpoint
labels during a manual hold. Existing lifecycle restoration rejects held or
unprepared checkpoints; regression tests cover both the real pre-fill hold and a
stale `RECONSTRUCTABLE_CHECKPOINT` label. Historical answers alone never prove
live filling. The actual saved checkpoint for application 3556 is not modified.

## Regression coverage

`tests/test_workday_readiness.py` and the synthetic loading fixture cover:
loading/empty inventory; delayed shell and form; stable-DOM deadline; hidden,
template, inert, transparent, busy and disabled file controls; detached controls;
security/authentication/terminal outcomes; both Workday hostname families;
not-attempted versus attempted upload failure; positive successful upload and
hash invalidation; production Engine fill-only readiness/timeout/unknown-question
holds; later-step upload and accepted same-session carry; checkpoint truthfulness;
closed context/page races and idempotency; worker-lock release; preservation of
telemetry; and propagation of unrelated browser errors. Existing upload/browser/
fill-only tests retain their assertions, including metadata and submission guards.

## Validation results

Focused result: **75 passed in 86.08 seconds**. Selection: Workday readiness,
uploads, phase-7 fill-only, and resume-migration tests. The Workday selection
includes real local Chromium/Engine tests, not only unit mocks. A final ownership
ordering correction was additionally verified with **4 passed in 0.63 seconds**
(the unchanged foreign-page rejection baseline, both page-close race cases, and
already-closed-context telemetry/lock release). Logs/JUnit are in
`.tmp/workday-focused-final.log` and `.tmp/workday-focused-final.xml`.

Exploratory validation caught a multi-step fixture without the positive file-entry
evidence needed to carry a receipt, and an early closed-page check that ran before
the existing ownership check. The fixture now exposes a real selected filename;
the existing receipt requirements were not relaxed. Ownership validation again
precedes page access. The earlier broad run's two failures are recorded in
`.tmp/workday-focused.log`; both are corrected and passed the full run.

Full offline result: **1,146 passed in 788.99 seconds (13:08)**, with zero
failures, errors, or skips. The JUnit result was compared against the 1,110
baseline test IDs collected before editing: **all 1,110 remain present and
passing; 36 new regression cases pass**. No existing test assertion was changed
for this repair. All 105 recorded source/test/fixture hashes remained unchanged
through the complete run.

Evidence: `.tmp/workday-full.log`, `.tmp/workday-full.xml`,
`.tmp/workday-validation-results.json`, `.tmp/workday-baseline-tests.txt`, and
`.tmp/workday-tested-source.json`. The preservation audit confirmed identical
canonical resume, archived application evidence, saved checkpoint, application
row/relevant settings, and all four verified-answer rows (including signatures
and provenance). `git diff --check` passed. Existing unrelated working-tree edits
were preserved.
Validation uses the existing `offline_validation` and `offline_providers` pytest
plugins, isolated temporary profiles/databases, blocked external sockets/DNS and
browser routes, and fake writing providers. Workday URLs in tests are fulfilled
locally; no employer page is fetched. The original 1,110 test IDs were collected
before editing. Production answers, application/checkpoint state, archived
evidence and canonical resume are independently checked for preservation.

Commands (PowerShell):

```powershell
$env:PYTHONPATH = "$PWD\docs\phase5-2026-09-25;$PWD\docs\phase8-2026-09-27"
.venv/Scripts/python.exe -u -m pytest -q -p offline_validation -p offline_providers `
  tests/test_workday_readiness.py tests/test_uploads.py tests/test_phase7_fill.py `
  tests/test_resume_migration.py --basetemp=.tmp/workday-focused-final `
  --junitxml=.tmp/workday-focused-final.xml
.venv/Scripts/python.exe -u -m pytest -q -p offline_validation -p offline_providers `
  --basetemp=.tmp/workday-full --junitxml=.tmp/workday-full.xml
.venv/Scripts/python.exe .tmp/workday_validation_audit.py after
```

## Readiness to retry application 3556

Offline readiness gating and failure classification are repaired; a future
explicitly authorized fill-only retry can evaluate the real next page safely.
Live Workday form compatibility and authentication remain unverified. Unsupported
picker/entry variants or loading will stop for review, and this repair does not
claim the form is already filled or the resume uploaded. Application 3556 has
not been resumed, no other application has been processed, and nothing has been
submitted. This task stops after offline validation.
