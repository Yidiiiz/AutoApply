# Browser cleanup and application 3556 retry readiness — 2026-09-30

## Scope

Offline cleanup repair and preparation of exactly one user-authorized fill-only
continuation for application **3556**, Viavi Solutions, Software Engineering Co-Op.
No production employer application is opened, no live application is processed,
and no submission is performed by this task. Temporary browser profiles, blank
pages, routed fixtures and local test servers are used for offline verification.

## Cleanup root cause and patience

The previous full acceptance run passed 1,274 test bodies but recorded a teardown
error: Windows CIM process inventory exceeded the hard-coded **15-second**
subprocess deadline inside `Browser.close()`. The subsequent isolated 144-test
pass did not make that full run clean. Slow read-only inventory is insufficient
evidence of failed infrastructure or permission to terminate unproven processes.

`browser.process_inventory_timeout_seconds` now defaults to **30 seconds**.
Configuration must be finite, greater than zero and at most 120 seconds; invalid
values, including booleans, NaN and infinity, are rejected. The budget applies to
each ownership inventory in startup inspection, capture and cleanup. Existing
bounded context/driver shutdown, startup slot count and termination limits remain.
The inventory budget does not promise that an entire shutdown takes 30 seconds.

Each inventory invokes CIM once and permits that observation to finish within its
budget. Per-owner diagnostics distinguish `still_working`, `completed`,
`definitively_failed` and `timed_out`, with budget and elapsed time. Capture before
shutdown, pre-reap identity inspection and exit verification are distinct
observations. None restarts an in-progress inventory because it crossed 15 seconds.

## Ownership and cleanup safety

Kill authority is unchanged: the exact fresh session marker plus exact dedicated
profile argument, or the current spawned Playwright driver's verified identity,
and captured descendants. Birth/parent checks and the kernel identity recheck
remain mandatory. Neither process name nor a profile match grants kill authority.
Personal browsers, unrelated drivers and older unproven sessions remain untouched.
Missing/timed-out inventory raises an error and cannot authorize any unproven kill.
Unresolved resource ownership remains recorded and prevents a clean restart.

Successful ordinary cleanup requires no application pages, leases, owned contexts
or proven owned browser processes, and released profile and worker locks.
Already-closed targets and repeated close calls remain idempotent. A cleanup with
unverified ownership is a failure, never a reported clean shutdown. The worker
lock encloses the future explicit command and all of its final cleanup.

## Offline validation

**Complete acceptance run: 1,297 passed in 2,240.71 seconds (37:20), with
zero failures, zero errors, zero skips and zero warnings.**

The new collection contains **1,297 IDs**: all **1,274** existing IDs plus **23**
regressions, with no missing baseline IDs. Focused continuation tests passed
14/14 after correcting the new fixture wiring. The initial sandboxed focused
run exposed Windows CIM access denial; approved local execution is required to
exercise the existing real ownership tests. Those failures are retained in their
original logs and are not counted as a clean acceptance run.

New regressions include a real 16-second fixture subprocess under the Windows
inventory path, successful lock release and idempotent cleanup, exhausted and
definitively failed inventory, finite configurable budgets, and preservation of
the existing personal-browser/process-identity assertions. Continuation tests
exercise zero-page startup exhaustion, health-before-attempt ordering, crash
retirement, unchanged historical events, no automatic queue entry and other gates.
The full suite also covers startup recovery, fill-only submission interception,
canonical resume, verified answer abstention and upload verification.

Acceptance command:

```powershell
.venv/Scripts/python.exe -u -m pytest -q -p no:cacheprovider `
  --basetemp=.tmp/cleanup-acceptance-full `
  --junitxml=.tmp/cleanup-acceptance-full.xml --durations=20
```

Evidence: `.tmp/cleanup-acceptance-full.log`, `.xml`,
`.tmp/cleanup-acceptance-source-hashes.json`, `.tmp/cleanup-final-ids.txt`,
`.tmp/browser-startup-final-ids.txt`, and `.tmp/cleanup-validation-audit.json`.
The completed ID/result audit confirms all **1,274 baseline IDs** and all **23
additions** passed in this same complete run, with no missing IDs and unchanged
code/test hashes throughout the run. Its JUnit SHA256 is
`4255d27617c32dfc2c161b71f47ff923e5d3998956cf05181e992ac1d889b365`.

The final OS inspection found **zero processes with exact acceptance-profile
arguments**, no conflicting production dedicated-profile processes or profile
lock files, and an accessible dedicated profile. Its production profile inventory
completed in **0.62 seconds** under the configured 30-second budget. Both production
profile and worker locks were acquired, released and reacquired. No browser was
launched and no process was terminated by this final inspection. Real headed and
headless fixture tests also verified closed contexts/drivers, empty ownership and
released profile locks. Evidence: `.tmp/cleanup-final-resource-audit.json`.

The real >15-second regression completed its fixture work in **16.05 seconds**
in the complete run, without premature cleanup failure. Original ownership/PID
reuse/personal-browser assertions remain present and passing. Ordinary application
and submission behavior is unchanged; the new continuation path requires its
explicit command ID and fill-only mode.

## Application history and authorization

Read-only inspection found application 3556 held in `MANUAL_REVIEW`, category
`BROWSER_START_FAILED`, with **attempts = 6**, `max_retries = 5`,
`delivery_state = NOT_STARTED`, no preserved session, no submission intent or
confirmation, safe security state and automatic retry disabled. Its historical
per-execution `attempt_started = 1` remains untouched during preparation.

**Prepared authorization:** `3d0d7d8187634161850f180138cfbfc1`, durable state
`PREPARED`, action `fill-only-continuation`, application ID **3556**. It has not
been claimed or executed.

Preparation completed under the production worker lock after clean validation,
with a consistent SQLite backup at
`data/private/cleanup-and-3556-readiness-2026-09-30/before-authorization.sqlite3`.
The before/after audit confirms **attempts 6 → 6**, and identical row values
for all applications, jobs, questions, settings and manual requests. Every existing
event and manual-command row is unchanged. Exactly one new prepared command and
one `FILL_ONLY_CONTINUATION_PREPARED` event were added for 3556. No attempt event
was deleted or changed, no attempt count was decremented, and no retry limit was
increased. Application 3556 remains held with `retry_allowed = 0`; global
`paused = true` and `auto_submit = false` remain unchanged.

Evidence: `.tmp/3556-continuation-preparation-audit.json` and
`data/private/cleanup-and-3556-readiness-2026-09-30/authorization.json`.
The normal database history initializer retained its existing ambiguous UNKNOWN
record for 4535; no application was processed by that metadata validation, and
the before/after audit confirms all application and existing event rows unchanged.

The continuation uses the existing durable `manual_commands` ledger and
`ManualCommand` identity/encoding, acknowledgement, result and crash-recovery
semantics. A new `PREPARED` state deliberately creates no `manual_requests` queue
entry. Normal workers, manual-request polling and batch discovery cannot consume
it. It requires an explicit `Engine(fill_only=True).process_one(3556,
continuation_id=AUTHORIZATION_ID)` call through the provided single-command runner.

Exact constraints are application ID 3556, mode `fill-only`, maximum executions
**1**, `auto_submit = false`, historical attempt count **6**, unchanged original
retry policy, the recorded startup hold, canonical URL and canonical resume SHA256.
The command records the explicit user request as its source. Preparation checks
protected/duplicate history, retirement, submission intent/uncertainty, security,
verification, listing/eligibility preflight and canonical resume/profile binding.
Those gates are rechecked at claim. There is no global retry bypass.

Only the proven zero-delivery `BROWSER_START_FAILED` hold and exhausted attempt
budget receive an exception. The ordinary Engine fill-only path still enforces
one-page ownership, verified answers, unknown-field abstention, fresh upload
verification, security/authentication and final-submit prohibition. The command
cannot reconstruct or authorize final submission. Automatic retry remains off.

## Exact consumption and attempt accounting

The explicit invocation atomically changes `PREPARED → IN_PROGRESS` and records
`manual_command_claimed` **before browser startup**. Within that same transaction,
the existing allowed `MANUAL_REVIEW → RETRY → CHECKING` reservation path clears
only the startup hold for this execution, with `retry_allowed = 0` throughout.
It resets the current-execution `attempt_started` flag; it never refunds attempts,
deletes events or changes the historical count. The ledger binds the budget
exception only to this active fill-only Engine invocation and this application.

Ordering remains dedicated profile ownership → bounded browser startup recovery
→ healthy probe → exactly one owned application page → `begin_attempt()` →
employer navigation. Only that employer-work boundary can increment **6 → 7**.
`begin_attempt` preserves the application's original ordinary retry limit.

The authorization is **consumed at claim even if startup fails**. A zero-page
failure records the startup hold, leaves attempts at **6**, and completes the
ledger result without granting another execution. The existing ledger has no
safe automatic unclaim/replay distinction, so there is deliberately no refund of
this one-use command. At return, `ACKNOWLEDGED` means the invocation finished,
not that filling succeeded. Its result records status, attempts and reason.
An escaping error marks `FAILED`; interruption after claim is retired by existing
`ManualCommands.recover()`. Both are non-replaying. Pre-claim safety rejection
performs no browser work and does not claim the authorization.

## Future explicit execution boundary

Application 3556 is **ready for one explicitly initiated live fill-only
continuation**, subject to the ordinary live safety gates. The future command is:

```powershell
.venv/Scripts/python.exe scripts/fill_only_continuation.py 3556 `
  --authorization 3d0d7d8187634161850f180138cfbfc1
```

**This command is not executed during this task.** It uses `worker.lock`, performs
one explicitly selected Engine call, disables auto-submit and external providers,
persists the ordinary fill-only checkpoint, pauses again and closes the Engine
and browser before releasing the worker lock. It does not run a queue loop or
reconstruct a checkpoint. Offline readiness cannot guarantee employer availability
or form behavior; every live gate must still pass.

This task stops after offline validation, ownership/lock qualification and durable
authorization preparation. Historical attempts remain **6**, auto-submit remains
**false**, and no production employer application has been opened or submitted.
