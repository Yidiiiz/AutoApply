# Browser patience and Workday transition stabilization — 2026-09-29

## Scope and observed failure

Offline development only. No production application was resumed, processed, or
submitted. Local Chromium fixtures use temporary profiles and intercepted fixture
origins. Existing working-tree changes were preserved.

The recorded live retry selected Apply Manually at 18:51:46.517903 and confirmed
the entry transition at 18:51:47.816431. The form gate saw the chooser again at
18:51:48.398421 and terminalized at 18:51:48.968868: about 2.45 seconds after the
click, while the final screenshot still showed loading. The old `wait_ready`
returned `APPLICATION_ENTRY_CHOICE` immediately; `require_form` converted that
into `APPLICATION_ENTRY_MODE_UNRESOLVED`, losing the confirmed transition context.

## Shared observation policy

`autoapply/patience.py` owns `PatiencePolicy` and `TransitionPatience`. Configuration
is under `browser.patience` in `config/config.example.yaml` and validated at
configuration loading. Each owned page receives the policy.

| Setting | Default | Behavior |
| --- | --- | --- |
| `grace` | 8 seconds | Minimum transient failure observation; cannot be configured below eight |
| `timeout` | 30 seconds | Normal readiness window; cannot be shorter than grace |
| `extension` | 5 seconds | Maximum additional observation for late meaningful progress |
| `stable_seconds` | 0.3 seconds | Failure predicate must recur across observations |
| `poll_seconds` | 0.1 seconds | Bounded wake between observations; DOM changes can wake earlier |

Positive readiness completes immediately after the existing short stability check;
there is no mandatory eight-second sleep. Explicit short adapter test budgets are
clamped to eight seconds. Larger supplied budgets remain larger. Progress can
extend the deadline to at most the original deadline plus the extension (normally
35 seconds). A hard async timeout also bounds stalled browser observations.
Each entry confirmation, post-entry form hydration, or step transition has its
own observation boundary. These are separate bounded phases, not a single
unbounded application-wide timer.

New URL paths, loading state changes, chooser disappearance, manual-route state,
document readiness, form structure, visible field/file counts, and action
appearance are semantic progress signals. Only previously unseen semantic states
count, with at most 64 distinct states retained. Raw mutation revisions, random
attributes, and animation geometry do not extend deadlines. Mutation revisions
still participate in Workday's conservative positive form-stability checks and in
determining whether chooser markup is stable. No field values or raw signatures
are added to timing telemetry.

## Workday page-session state

After guarded one-time Apply Manually delivery and positive transition evidence,
the page records `_autoapply_workday_manual_entry_confirmed`. Telemetry records:

```text
WORKDAY_MANUAL_ENTRY_CONFIRMED -> POST_ENTRY_LOADING -> FORM_READY
```

A new adapter on the same page inherits this state and the existing manual-entry
attempt latch. Main Apply also has a page-session attempt latch. Waiting contains
no click retries. A canonical supported manual route plus loading can confirm
entry even with residual chooser markup; URL-only changes with an unchanged
chooser remain conservatively unconfirmed, preserving the existing safety test.
The route is evidence of mode selection, never proof of form readiness.
Missing/disabled manual-entry options also receive bounded observation before
delivery; conflicting targets or a definitively ambiguous decision stop safely.

During post-entry hydration, a lingering or reappearing chooser cannot reopen the
completed decision, switch modes, or authorize another click. Visible loading
indicators continue observation. A canonical manual route with a persistent
chooser eventually holds as `FORM_NOT_READY`, rather than falsely declaring that
manual entry was never selected.

Away from that route, `APPLICATION_ENTRY_MODE_UNRESOLVED` requires the bounded
window to expire, minimum grace and recurring failure observations, no ready
form or loading signal, and stable current visible/actionable chooser markup.
Replaced/changing markup cannot justify that specific chooser classification.
Late semantic progress receives the capped extension. Other exhausted readiness
windows hold as `FORM_NOT_READY`; entry confirmation exhaustion remains
`TRANSITION_UNCONFIRMED`.

## General transition and safety review

Generic and Lever post-click entry and generic ATS Next/Continue hydration use
the shared observation budget. A blank intermediate step is no longer sufficient
to declare step advancement. Pre-click step replacement is re-observed within
the preparation deadline before a single guarded delivery. Ashby post-entry controls use the shared form wait;
its entry control and SmartRecruiters' email control use the configured normal
readiness timeout. Document navigation uses at least that timeout, preserving a
larger configured browser timeout, and never repeats `goto` on slowness.

Existing upload selection, association, hash verification, attachment receipts,
and 180-second upload lifecycle budgets remain unchanged. An explicitly stale
or unsafe upload target is still rejected before selection. More patience does
not perform additional file selections or manufacture upload readiness.

Security and explicit authentication are checked on observations and can stop
immediately. Unsupported/unsafe input, definitive browser/backend failures,
duplicate/protected applications, eligibility/data failures, final submission
boundaries, and submission uncertainty retain their existing safeguards.
Unknown questions still abstain. The one-page ownership and fill-only submission
invariant remain in force. No final-action retry was added.

The canonical file remains `data/private/resumes/resume.pdf`. Its SHA-256 was
verified read-only as:

```text
3b71d225bad9e0ab51a65d1dfed2ccf3bb9dfe7bc60b1fb0abecf9d41acb6439
```

## Diagnostics

Readiness events include monotonic timestamps (seconds) for `interaction_at`,
`grace_started_at`, progress observations, `last_meaningful_progress`,
`readiness_established_at`, `terminal_condition_first_seen_at`,
`terminal_condition_stable_at`, and `total_wait_duration`, plus effective
deadlines and the final sanitized observation. Null readiness means success was
not established; null progress means no new semantic milestone was observed.

On a transient adapter hold, Engine records
`BROWSER_READINESS_FINAL_OBSERVATION`, invokes existing masked screenshot/state
diagnostics, then records `BROWSER_READINESS_TERMINAL` before the hold/checkpoint
and cleanup. Existing protected-page screenshot exclusions remain intact.
Definitive browser failure can make diagnostics unavailable; existing diagnostic
failure handling preserves the safe hold.

## Regression coverage and validation

The baseline collection was captured before editing: **1,208 test IDs**. No
existing test assertions or IDs were changed for this repair.

New tests in `tests/test_browser_patience.py` cover immediate positive readiness,
nonterminal 1–2 second observations, real 1/5/8/10-second Workday form hydration,
bounded persistent loading, semantic late-progress extension, animation churn,
page-session confirmation, stale chooser replacement on the manual route,
stable unresolved chooser holds, single Apply/Apply Manually/Next/Continue
delivery, immediate security/authentication, final-boundary readiness without a
submit click, and diagnostic ordering. The local fixture is
`tests/fixtures/workday-post-entry-delayed.html`. A source regression rejects
application-specific branching in the policy/Workday code. Existing suites cover
unknown-answer abstention, upload failure closure, and submission guards.

Final collection: **1,235 test IDs**, comprising all **1,208** original IDs plus
**27** new tests; the baseline ID set difference is empty.

Focused commands (using `.venv/Scripts/python.exe -m pytest`):

```text
tests/test_browser_patience.py tests/test_workday_entry_mode.py
tests/test_workday_readiness.py tests/test_phase6_browser.py tests/test_smartrecruiters.py -q

tests/test_browser.py tests/test_uploads.py tests/test_fill_batch.py
tests/test_phase7_fill.py tests/test_submission_probe.py tests/test_manual_submission_retirement.py -q
```

The final combined readiness/browser/transition group passed **197 tests** in
508.80 seconds. The focused fill-only/upload/submission safety group passed
**96 tests** in 234.51 seconds.

Complete-suite command:

```text
.venv/Scripts/python.exe -u -m pytest -q --tb=short --junitxml=.tmp/patience-final-results.xml
```

**Complete offline result: 1,235 passed in 1,156.87 seconds (19:16).**
The JUnit results were compared with the original collection by test ID:
all **1,208 baseline IDs are present and passing**, all **27 additions pass**,
and there are no missing, failed, errored, or skipped current tests.
Local validation artifacts are `.tmp/patience-final-offline.txt`,
`.tmp/patience-final-results.xml`, and `.tmp/patience-baseline-audit.json`.

`compileall` and `git diff --check` pass. The canonical resume hash was rechecked
after editing and still matches.

## Readiness for a future retry

Offline validation is complete and the repair is ready for a separately authorized
retry of 3556. It targets the demonstrated premature hold. It does not establish
that the live employer form will hydrate or accept the resume. A future authorized
retry should inspect confirmation, post-entry wait telemetry, `FORM_READY`, and
verified upload evidence while preserving fill-only/final-action safeguards.
Application 3556 was not resumed, and no other live application was processed.
Work stops after offline validation.
