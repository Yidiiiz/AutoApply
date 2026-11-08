# Workday application-entry repair — 2026-09-29

## Confirmed root cause

The supplied live observation establishes that the legitimate main Apply click
opened a Workday “Start Your Application” chooser containing Autofill with Resume,
Apply Manually, and Use My Last Application. The previous readiness adapter
recognized only a shell Apply action or a real applicant form. After the main
Apply click, its form-only wait did not recognize navigation choices and exhausted
the readiness deadline (`TIMEOUT_UNCONFIRMED` → `FORM_NOT_READY`). The chooser was
correctly excluded from form readiness, but the intervening navigation state was
missing. This repair uses sanitized local fixtures; no live application was opened
to reproduce the issue.

## Application-entry state model

Supported ordering:

```
LOADING → SHELL_READY → guarded main Apply
→ APPLICATION_ENTRY_CHOICE → guarded Apply Manually
→ LOADING (transition confirmation) → FORM_READY
→ existing inventory, verified filling, upload, and non-final navigation
→ stop before Submit in fill-only mode
```

`SECURITY_OR_AUTH`, `TERMINAL_FAILURE`, and `TIMEOUT_UNCONFIRMED` remain distinct.
Security/authentication and terminal conditions take precedence over the chooser.
A recognized chooser returns `APPLICATION_ENTRY_CHOICE` at either readiness
boundary, including when it appears after a delay. It cannot authorize inventory
or upload. There is no click loop, guessed navigation URL, or mode fallback.

## Chooser detection and canonical policy

Within the existing Workday adapter routing, positive evidence is a displayed
entry action or a displayed “Start Your Application” heading. Controls are
observed using normalized text, `aria-label`, `aria-labelledby`, and actual
`href`, `action`, or `formaction` attributes. The known semantic actions are
Autofill with Resume, Apply Manually, and Use My Last Application. Canonical
route suffixes are `/apply/autofillWithResume`, `/apply/applyManually`, and
`/apply/useMyLastApplication`. Locale/path prefixes, route/label case, whitespace,
query parameters, and trailing slashes are normalized. Both supported hostname
families, `myworkdayjobs.com` and `myworkdaysite.com`, are covered.

Disabled or hidden choices remain evidence for rejecting a chooser with no safe
manual option; they never authorize input. Actual route evidence must be HTTPS
and same-origin on a supported Workday hostname. Foreign origins, conflicting
labels/routes, unknown supplied routes, new-window targets, downloads, native
form-submit controls, and a simultaneous applicant form/chooser are rejected.
Href-only evidence supports localized labels; conflicting known English labels
and any Submit label fail closed. Text-only buttons are also supported.

Apply Manually is the sole automatic mode. Workday resume parsing and reuse of a
previous application could introduce stale or unverified answers and attachments.
AutoApply retains control of verified facts, provenance, unknown-field abstention,
canonical resume selection, upload evidence, and validation. Entry selection is
navigation telemetry (`WORKDAY_ENTRY_MODE_SELECTED`, mode `applyManually`), never
an applicant question, answer, disclosure, eligibility fact, or filled field.

## Guarded selection and transition confirmation

Exactly one unambiguous usable manual option is required. The normal production
cursor performs its existing page/security, actionability, scrolling, geometry,
and hit-target checks. The adapter composes an additional fresh semantic/element
identity check with that cursor's original security guard; it restores the guard
afterwards. A WeakMap identity detects replacement/detachment instead of silently
retargeting a different option at the same locator index. No applicant field
tokens or values are used. The cursor and shared actionability implementation
are unchanged.

A page-session latch is set before the single guarded interaction. A later
adapter on the same page cannot repeat an attempted selection. After delivery,
the adapter waits for sustained chooser disappearance plus a real applicant form,
visible loading state, or established same-origin canonical manual-entry route.
Unrelated DOM churn, disappearance into a blank page, or a URL change while the
chooser remains visible is insufficient. Loading/route evidence confirms only
the navigation; the separate existing stable `FORM_READY` gate is still required
before inventory/fill/upload. Each wait and selection is bounded by the adapter's
readiness timeout (15 seconds in production); slow loading never triggers a
second manual-entry click.

No safe unique manual option, stale identity, disabled/hidden/occluded target,
unsafe geometry, or conflicting semantics produces
`APPLICATION_ENTRY_MODE_UNRESOLVED`. Delivered or potentially delivered input
without confirmed transition produces `TRANSITION_UNCONFIRMED`. A confirmed
transition that subsequently fails form readiness retains `FORM_NOT_READY`.
Existing Engine manual holds disable automatic retry. Security and terminal
readiness failures preserve their own state categories.

## Compatibility and policy preservation

Direct-to-form applications still support `SHELL_READY` → main Apply →
`FORM_READY`, and pages already showing a legitimate form need no entry click.
Main Apply matching no longer includes Apply Manually. The existing loading and
skeleton gates, upload pipeline, answer resolver, page limits, and submission
guards are unchanged.

The canonical resume remains `data/private/resumes/resume.pdf`, SHA-256
`3b71d225bad9e0ab51a65d1dfed2ccf3bb9dfe7bc60b1fb0abecf9d41acb6439`.
Entry navigation creates no upload receipt. The eventual file field still goes
through AutoApply's existing canonical-resume validation and upload pipeline.
Verified fields are filled; unknown questions remain pending without guessed
answers. Provider calls are blocked throughout qualification.

## Regression coverage and validation

`tests/fixtures/workday-entry-choice.html` is a sanitized chooser, and
`tests/test_workday_entry_mode.py` exercises real local Chromium and production
Engine fill-only paths. Coverage includes labels, accessible names, hrefs and
actions; both hostname families; normalization; single manual selection; zero
alternative-mode clicks; direct-to-form paths; delayed chooser/form appearance;
missing, duplicate, hidden, disabled, detached, replaced, occluded, and conflicting
targets; ambiguous structures; no second click; unconfirmed delivery; loading
after delivery; security/terminal priority; no applicant-answer or upload evidence
from navigation; preserved unknown answers; zero submission intent/requests; and
the one-page invariant. Existing Workday tests retain the generic-not-specific
routing assertion and the loading/skeleton regressions.

Focused result: **290 unique test IDs passing** across the broader focused run
and final Workday rerun. The broader run passed 287 tests and exposed one new
href-navigation regression: JavaScript context replacement during navigation
was prematurely treated as an unconfirmed transition. Bounded observation
retries corrected that issue; both actual-navigation variants then passed.
The final Workday rerun passed **98 tests in 165.43 seconds**, including **62 new
entry-mode regressions** and all 36 existing Workday cases. Logs/JUnit:
`.tmp/workday-entry-focused.log`, `.tmp/workday-entry-focused.xml`,
`.tmp/workday-entry-focused-final.log`, and
`.tmp/workday-entry-focused-final.xml`. No baseline test or safety assertion was
edited.

Complete offline result: **1,208 passed in 1,009.46 seconds (16:49)**, with zero
failures, errors, or skips. All **1,146 baseline test IDs** remain present and
passing; **62 new tests** pass. The run covers readiness, entry mode, actionability,
transitions, uploads, fill-only, page ownership, and submission guards alongside
the entire existing suite. An earlier full-suite launch was interrupted before
completion; these results come from the subsequent complete uninterrupted run.

Evidence: `.tmp/workday-entry-full.log`, `.tmp/workday-entry-full.xml`, and
`.tmp/workday-entry-validation-results.json`. The preservation audit passed:
the complete logical production database, canonical resume, application checkpoint,
and archived application evidence match their pre-development hashes. This also
preserves historical attempts, verified answers, resume provenance, and submission
history. Tested source/test/fixture hashes remained unchanged throughout final
qualification. The only pre-existing source/test file changed by this repair is
`autoapply/workday.py`; all unrelated working-tree changes are preserved.
`git diff --check` passed.

Qualification uses the existing `offline_validation` and `offline_providers`
pytest plugins: external sockets/DNS and unmocked browser requests are blocked,
live writing providers are disabled, and all browser profiles/databases/resumes
are temporary test fixtures. Actual href-navigation regressions exercise both
main Apply → chooser → form and chooser → form with the fill-only submission
request guard installed. Navigation-context replacement permits bounded
observation retries only; no input is retried.

Reproduction commands (PowerShell):

```powershell
$env:PYTHONPATH = "$PWD\docs\phase5-2026-09-25;$PWD\docs\phase8-2026-09-27"
.venv/Scripts/python.exe -u -m pytest -q -p offline_validation -p offline_providers `
  tests/test_workday_entry_mode.py tests/test_workday_readiness.py `
  --basetemp=.tmp/workday-entry-focused-final --junitxml=.tmp/workday-entry-focused-final.xml
.venv/Scripts/python.exe -u -m pytest -q -p offline_validation -p offline_providers `
  --basetemp=.tmp/workday-entry-full-final --junitxml=.tmp/workday-entry-full.xml
.venv/Scripts/python.exe .tmp/workday_entry_audit.py after
```

The broader focused selection also includes `test_cursor.py`, `test_browser.py`,
`test_phase6_browser.py`, `test_phase7_fill.py`, `test_uploads.py`,
`test_submission_probe.py`, and `test_resume_migration.py`. Baseline IDs were
collected before editing into `.tmp/workday-entry-baseline-tests.txt`. Read-only
preservation hashes and a logical hash of the entire production database were
captured before implementation. Validation also checks every pre-existing
source/test file against its original hash and every tested source against its
hash at the start of final qualification.

## Readiness to retry application 3556

The repair is **ready for a separately authorized fill-only retry** of application
3556. Offline qualification demonstrates the supported chooser path, one guarded
manual-mode interaction, explicit ambiguity holds, and continued form readiness
gating. Live Workday compatibility and any later authentication or form requirements
remain unverified; this result does not claim an existing filled form or uploaded
resume. Application 3556 was not resumed, no other real application was processed,
and nothing was submitted. Existing attempts, checkpoint, screenshots, provenance,
answers, and submission history are preserved. This task stops after offline
implementation and validation.
