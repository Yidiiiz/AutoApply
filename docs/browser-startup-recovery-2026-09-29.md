# Browser startup recovery and application-attempt accounting

## Scope and evidence

This repair was developed offline. No employer application was opened or processed,
and no employer submission was performed. Browser tests use temporary profiles,
blank pages, routed fixtures, and local HTTP servers. Production application data,
including application 3556's attempt count, was not modified.

The saved evidence was inspected read-only:

- `.tmp/fair-3556-browser-start-diagnostic.json`
- `.tmp/fair-test-3556-patience-retry-evidence.json`
- `.tmp/fair-test-3556-patience-recovery-evidence.json`
- `.tmp/fair-test-3556-patience-live-evidence.json`
- `docs/fair-test-3556-patience-run-2026-09-29.md`

The three execution records truthfully record attempts **3 → 4 → 5 → 6**.
The final run recorded target-creation errors at approximately 20:19:57,
20:20:06, 20:20:15 and 20:20:24 UTC, with zero application pages, leases,
employer navigation, applicant interaction, uploads, or submission activity.
The separate blank diagnostic recovered after approximately 10.17 seconds.

## Failure mechanism

The accounting defect was definite: Engine called `begin_attempt()` before
`Browser.new_page()`. Every fresh Engine invocation could consume an application
attempt even when Chromium never created a page. The generic exception/handoff
path could then label this affirmative zero-delivery failure `SUBMISSION_UNKNOWN`.

A sufficient browser failure mechanism was reproduced locally in a fresh,
temporary **headed** Chromium persistent profile: launch, create and close a blank
health page, close the original startup blank, then request a new page. The final
request returned the same `Target.createTarget: Failed to open a new tab` error.
Three fresh launches with the same last-window closure repeated the failure.
Headless Chromium did not reproduce it. The old fill-only startup sequence closed
Chromium's original blank before calling `new_page()`, exposing this exact window
lifecycle problem. Explicit CDP `newWindow` creation did not recover this local
zero-window state either.

This is stronger evidence than a speculative profile-lock explanation, but the
saved production files contain no Chromium crash dump or process/lock inventory
that proves it was the sole cause of the original run. Other paths remain possible:
an unexpectedly closed context, partial driver launch, stale dedicated processes,
or profile ownership/access problems. The repair checks and bounds these paths.

## Health probe and page ownership

Every fresh dedicated context must create a separate owned `about:blank` health
page, evaluate its location, and close it successfully. The health page has no
application lease, cursor, applicant observer, or application-attempt event.

Headed Chromium's **original startup blank** is tracked separately as an owned
startup page. It keeps the window alive after the health page closes. This is an
intentional implementation detail required by the local reproducer:

1. Launch only the dedicated profile; reject unexpected restored nonblank targets.
2. Create, evaluate, and close the health page.
3. Verify health pages = 0, application pages = 0, and application leases = 0.
4. Retain at most the original startup blank in headed mode (none in headless mode).
5. Create exactly one application page and acquire its lease.
6. Close the original startup blank before returning the page to Engine.
7. Verify the application-tab limit, prepare the page, then begin employer work.

The startup page is never navigated and never becomes an application page. It
cannot conceal a popup: only the exact initial page object is excluded from
application counts. Unexpected subsequent pages still acquire leases and trigger
the existing sticky tab-limit violation. Before employer navigation, the context
contains only the application page. `MAX_ACTIVE_APPLICATION_TABS = 1` is preserved.

## Bounded startup recovery

The recovery loop wraps only fresh startup and application-page acquisition. It
never wraps navigation, form filling, uploads, security interaction, or submission.

- Maximum three launch/recovery slots per page-acquisition execution.
- Maximum two clean restarts, with four seconds of settling time between them.
- Forty-five-second deadline for each startup/page-acquisition operation;
  Chromium launch has a thirty-second timeout and the health probe ten seconds.
- Context close, driver stop, cursor cancellation, and pending page cleanup have
  ten-second operation bounds. OS inventory calls have fifteen-second subprocess
  bounds; owned-process termination/exit checks are bounded as well.
- A cancellation waits for an in-progress bounded OS inspection to finish before
  releasing its lock, preventing a background ownership check from racing cleanup.

Cleanup time is additional to the startup-operation deadline. These are finite
per-operation budgets, not a claim that all three launches finish within forty-five
seconds. The two settling intervals preserve the default eight-second grace even
if all three target failures return immediately. Existing centralized Workday,
hydration, upload, and transition patience behavior is unchanged.

Recoverable target-creation errors, closed-browser/context errors, and startup
timeouts can restart only while the current scope has created no application page,
has no application leases/observations, and the Engine state still affirmatively
has no started application attempt, submission intent/confirmation, uncertain
delivery, or security/manual hold. Once any application page exists, even a setup
failure cannot replay creation. Existing conservative application recovery rules
continue to govern all later failures. Tab-limit violations never trigger restart.

Ownership conflicts, unexpected restored targets, inaccessible profiles, and
unverified cleanup stop safely rather than trying more launches. Exhaustion creates
a non-retrying hold, so repeatedly polling the queue cannot reset this budget.

## Dedicated profile and process ownership

`browser_resources.py` uses standard-library OS inspection; no dependency or browser
installation is required. On Windows it reads process metadata with local CIM;
on Linux it reads `/proc`. Unsupported platforms fail closed.

A separate OS-backed `browser-profile.lock` protects the dedicated profile even
for direct Browser callers. Existing CLI and batch `worker.lock` ownership remains
in force around the complete worker execution. A redirected profile path is
rejected before profile modification. Profile accessibility and Chromium singleton
lock files are inspected before launch/relaunch. Ambiguous singleton locks are not
deleted. Windows' leftover `lockfile` must be exclusively openable; otherwise the
run stops. Existing processes referencing this profile are conflicts, not permission
to terminate them. Zoom preference repair now occurs only after ownership checks.

Each launch receives a fresh random command-line session marker. Kill authority
requires that exact marker and exact dedicated-profile argument, or the freshly
spawned Playwright driver's PID/parent/command identity and captured descendants.
Creation times are recorded, and termination rechecks the process identity through
an OS handle (Windows) or pidfd (Linux), preventing PID-reuse kills. Another
Playwright driver, a personal browser, or an older unproven dedicated session is
never terminated by name or profile match alone. Raw command lines are not stored
in application telemetry.

Windows CIM timestamps are bound to the kernel's exact creation time before
termination; CIM truncates the last 100-nanosecond digit. Descendants must also be
newer than their recorded parent, excluding older unrelated orphans whose parent
PID was reused. A real disposable child-process test verifies termination of the
owned process while an unrelated fixture child remains alive. Windows behavior
was exercised directly; the Linux implementation was not platform-qualified here.

Cleanup captures owned descendants before closing the context and stopping its
driver. If graceful close fails, only proven owned processes may be reaped. A
verified successful fallback permits a clean restart; ownership/cleanup ambiguity
does not. Outstanding cleanup ownership is retained rather than overwritten by a
later launch. Normal shutdown verifies no owned processes remain and releases the
profile lock. Engine's outer worker still closes the browser and releases the
worker lock at execution completion; intentional live handoffs retain their owner
until explicit shutdown. Startup exhaustion cleans inline, before Engine returns.

## Attempt accounting, classification, and history

The new normal ordering is:

`worker lock → claim/reservation → preflight → healthy browser → owned application
page → close startup page → begin_attempt → employer navigation`

An unsuccessful launch/probe/page-creation operation does not consume or refund an
application attempt. Employer navigation does consume one attempt, even if that
navigation subsequently fails. Preserved live sessions do not start another
attempt. Explicit checkpoint reconstruction verifies that its existing live page
is available before beginning its separate application attempt.

`BrowserStartFailed` is handled explicitly by the lifecycle as
`BROWSER_START_FAILED`, with automatic retry and same-session resume disabled.
It is not an upload, form-readiness, entry-mode, or submission-uncertainty result.
The lifecycle rechecks delivery evidence before accepting this classification;
uncertain delivery continues through conservative existing failure handling.

Telemetry distinguishes:

- `BROWSER_EXECUTION`: per-acquisition `browser_start_count`,
  `browser_restart_count`, `browser_health_probe_failures`, and the application
  attempt count at the browser boundary.
- `BROWSER_STARTUP_DIAGNOSTICS`: sanitized failure types, target-creation indicator,
  profile lock/conflict/accessibility evidence, and cleanup failures/recovery.
- `APPLICATION_ATTEMPT_STARTED`: the incremented `application_attempt_count` at
  the employer-navigation boundary.

No migration, decrement, event rewrite, or history correction was introduced.
Application 3556's historical 3 → 6 increments remain truthful. If the user later
wants a retry-budget exception, it should be a separately authorized, audited
maintenance operation preserving those historical counts, not a silent refund.

## Regression coverage and validation

`tests/test_browser_startup.py` covers healthy probe isolation; first-probe and
first-application-target failures; second-launch recovery; exhausted/partial
launches; zero-attempt startup holds; one navigation attempt after recovery;
unchanged historical events; page/lease limits; real headed and headless blank-page
startup and process cleanup; startup-page handoff; partial page creation; cancellation;
graceful-close fallback; ambiguous lock/redirect rejection; orphan ownership;
personal and unrelated driver exclusion; PID reuse; delivery/security/intent/upload
guards; absolute startup bounds; exhausted application retry budgets; closed-page
reconstruction; and cleanup after telemetry failure.

The baseline was collected before editing: **1,235 IDs**. The final collection
contains **1,274 IDs**, with **39 additions** and **no missing baseline IDs**.
Existing test files and IDs were not removed or weakened for this repair.

The broad focused run covered browser lifecycle/ownership, patience, worker locks,
attempt/retry lifecycle, fill-only, security/submission, and Workday entry/readiness:
427 passed and two failures. The tab-limit guard regression was corrected; the
local fixture navigation failure passed on recheck. The final focused startup and
previous-failure selection passed **41/41 in 26.42 seconds**. The final ownership
ancestry safeguard also passed the 36 non-browser startup checks. The complete
acceptance run includes all of those final changes and all broad focused IDs.

Complete offline command (temporary profiles/databases only):

```powershell
.venv/Scripts/python.exe -u -m pytest -q -p no:cacheprovider `
  --basetemp=.tmp/startup-acceptance-full `
  --junitxml=.tmp/startup-acceptance-full.xml --durations=20
```

**Complete run: 1,274 passed, one warning, one teardown error in 2,668.79 seconds
(44:28).** There were no failed test-body assertions. The teardown error occurred
in a routed security-provider fixture when the read-only Windows CIM process
inventory exceeded its fifteen-second deadline during `Browser.close()`. The
implementation reported unverified cleanup rather than treating missing ownership
evidence as permission to kill. The warning was a Windows asyncio closed-pipe
deallocator warning. This result is not described as a clean single-pass suite.

The isolated recheck of `tests/test_security.py tests/test_browser_startup.py`
passed **144/144 in 292.56 seconds (4:52)**, with zero failures, errors, or skips.
No code changed between the complete acceptance run and this recheck. All Workday
entry/readiness tests passed in the complete acceptance run.

The final ID/result audit confirms **all 1,235 baseline IDs and all 39 additions
are present and passing in their latest results**, with no missing IDs and no
source-hash changes. It retains the original full-run teardown error rather than
overwriting that evidence. The full run exercised **443 IDs** from the requested
broad focused groups. `git diff --check` passed for the changed tracked code.
The final read-only process inventory found **zero remaining browser processes
with exact startup-test session/profile arguments**. The isolated real browser
tests also verified empty leases, closed contexts/drivers, and released ownership
locks. No personal browser process was terminated or modified by this task.

Validation artifacts:

- `.tmp/browser-startup-baseline-ids.txt`
- `.tmp/browser-startup-final-ids.txt`
- `.tmp/startup-final-targeted-07.xml`
- `.tmp/startup-acceptance-full.log` and `.xml`
- `.tmp/startup-acceptance-recheck.log` and `.xml`
- `.tmp/browser-startup-acceptance-source-hashes.json`
- `.tmp/browser-startup-validation-audit.json`

## Application 3556 and a future authorized run

The repair removes the reproduced headed startup failure mechanism and prevents
future zero-page failures from consuming application retry budget. It does **not**
establish live Workday readiness or grant authorization to resume application 3556.
The saved production record remains held with automatic retry disabled and its
existing attempt count intact.

A later explicitly authorized fill-only run is appropriate only after an audited
decision about that existing hold/retry budget, the fresh dedicated-profile health
check, and the normal fill-only, single-page, resume, and submission safeguards.
Offline validation cannot guarantee employer availability or form behavior. This
task stops after implementation, offline tests, and this report.
