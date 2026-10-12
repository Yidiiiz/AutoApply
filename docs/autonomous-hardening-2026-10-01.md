# Autonomous hardening — 2026-10-01

## 1. Initial architecture assessment

Initial branch `main`, HEAD `dc50864e6ab3d54f511a8f14b73bd9736b3fe193`.
The working tree already contains the accepted browser, Workday, eligibility,
resume and continuation repairs. Those changes are preserved; they are not new
work from this phase. An immutable copy of source, tests, configuration, scripts
and documentation is under ignored `.tmp/hardening-baseline-tree`, with original
hashes in `.tmp/hardening-before-hashes.json`. Initial collection: 1,297 test IDs.
The baseline runs against that copy so subsequent edits cannot change its result.
Sandboxed browser execution cannot perform Windows CIM ownership inspection;
the real offline baseline uses approved local execution, without live employers.

The system has strong provenance, lifecycle, submission, upload and browser
ownership boundaries. Autonomy gaps remain in the orchestration between them.
Fill-only currently requires specialized scripts; ordinary CLI work can select
the submission-capable Engine. Batch smoke qualification requires a successful
first form, and its Discord connection is mandatory. Manual holds normally save
and close the form in fill-only mode. Field inventories are cached safely, but
the Engine processes one initial list without reconciling conditional changes
until after filling and deciding whether unknown answers require a hold.

## 2. Autonomy gaps discovered

| Problem | Real-world impact | Root cause | Priority |
| --- | --- | --- | --- |
| Required profile preflight stops all filling | Known contact/education fields and resume stay untouched | Structured adapter preflight returns before the fill loop | High |
| Conditional discovery follows the unknown-answer stop | Known newly revealed fields stay blank | One initial inventory and premature `missing` decision | High |
| Conditional rounds consume page budget; removed controls remain in receipts | Dynamic forms fail validation or exhaust navigation limits | Schema reconciliation and real step advancement share one loop | High |
| Generic entry/readiness assumes the first observation | Hydrated or embedded forms may be missed | Native first-page counts and main-frame readiness only | High |
| Fill-only closes incomplete forms | User cannot finish the prepared live form | Checkpoint finalizer always releases non-ready pages | High |
| Normal CLI has no safe fill-only sequential command | Routine use depends on bespoke scripts and Discord | Qualification-only runner and submission-capable CLI | High |
| Generic checkpoint label implies filling when zero fields were filled | Misleading operator status | Legacy persistence label projected as readiness | Medium |
| Wrapped select label includes every option | Verified sponsorship answers become unknown questions | Label extraction includes descendant control text | High |
| SmartRecruiters requires email on its first step | Valid contact-free steps time out | Adapter duplicates shared readiness with an email-only assumption | High |
| Replacement step has identical question schema | One delivered Next is reported unconfirmed | Adapter ignores DOM-node replacement identity | High |
| Submission ceiling gates fill-only work | Already-submitted daily count prevents harmless preparation | Preparation and submission share a preflight ceiling | Medium |
| Generic pre-submit exception appears as submission uncertainty | Wrong recovery category and little diagnostic evidence | Broad exception handler ignores known delivery state | Medium |
| Progress snapshot includes verification inputs | Sensitive security values may enter an ordinary checkpoint | Generic field serialization lacks security redaction | High |
| Busy sample races with a replaced step signature | Fully loaded steps time out after one delivered Next | Stability timer stays unset when a ready signature was first seen during loading | High |
| Rejected upload check leaves an earlier ready verdict visible | Manual hold may be misclassified as upload-ready | Engine persists prior tracker/observation metadata after a fresh rejection or resume change | High |
| Windows briefly denies an atomic history replacement | Otherwise valid preparation fails during local persistence | File replacement lacks the bounded sharing-denial recovery already used for directory renames | High |
| Windows process inventory times out before application work | Browser startup stops despite a later successful cleanup inventory | Subprocess timeout is absent from pre-application startup error classification | High |

## 3. Repairs implemented

1. **Normal fill-only execution:** `autoapply fill` selects the next eligible
   queued application; explicit IDs select exactly those applications. Automatic
   selection supports a finite `--limit`. `--checkpoint-only` checkpoints and
   closes each form, then continues after applicant/site holds. Default live mode
   preserves the sole owned form and services existing manual-answer/resume
   commands until the user closes it or stops the worker. Infrastructure blockers
   stop the batch. Startup, database initialization, browser work and cleanup are
   enclosed by the worker lock. Discord, Gmail and external writing providers are
   disabled for this command. A durable `fill-run-result.json` records results and
   sanitized failure types. The existing qualification-specific five-destination
   script and its approval/smoke policy are unchanged.
2. **Partial filling:** structured profile preflight identifies missing facts but
   no longer stops supported known fields. Canonical resume selection and fresh
   upload acceptance are checked even on an input hold. Unknowns remain pending
   and empty; the pending question is precise and usable by existing answer reuse.
3. **Conditional reconciliation:** question identity includes control kind,
   options, requiredness, limits and semantic mapping. Schema is reobserved after
   a committed field/upload and settles before an input hold or transition. Real
   Next transitions alone consume `max_pages`; inventory rounds have a separate
   validated positive budget, default 12 and maximum 64. Receipts avoid repeated
   input only while live state and resolver/writing revisions still agree. A
   changed profile forces fresh resolution. Removed current-step controls lose
   only their live receipts; durable answers remain. Pending questions that become
   inactive retain an `INACTIVE` row and event, without an inferred answer.
   Returning controls become pending again. Native validation still blocks a
   hidden-but-required invalid control.
4. **Typed metadata recovery:** changed native/custom dropdown options produce
   `InventoryChanged` before input, allowing bounded metadata refresh and fresh
   resolution. Other interaction exceptions do not gain replay authority. Next
   still uses one intended interaction and bounded observation after delivery.
5. **Shared readiness:** generic entry ignores hidden template inputs, observes
   asynchronous forms and recognizes supported embedded application frames and
   accessible controls. SmartRecruiters uses shared readiness rather than a
   mandatory email field, and shared DOM-node identity distinguishes replacement
   steps even when their question schemas match. Shared readiness starts stability
   when a candidate becomes ready even if its DOM signature was sampled during a
   preceding busy observation. The baseline hydration failure exposed this race;
   a deterministic real-Chromium regression reproduced it before the repair.
6. **Exact label extraction:** controls and option lists are removed from cloned
   label text. Wrapped select labels remain the actual question and exact option
   metadata remains separate. No fuzzy semantic answering was added.
7. **Truthful checkpoints:** schema version 2 records `UNFILLED`, `PARTIAL` or
   `READY` and distinct committed-field count since the current reservation/attempt
   event boundary. Non-ready checkpoints use `SAVED_PROGRESS_CHECKPOINT`. Existing
   checkpoint files and legacy reads are preserved. Historical answer rows cannot
   make a zero-fill current checkpoint appear filled. Protected frames are omitted,
   verification/code values redacted and URLs sanitized. Saved progress does not
   confer reconstruction or live-submit readiness.
   The field count follows non-upload control commits; document progress retains
   its separate upload lifecycle evidence. A resume-only preparation can therefore
   have zero field commits while its upload record reports positive acceptance.
8. **Execution classification:** unexpected pre-submit engineering exceptions use
   `ENGINEERING_FAILURE`, with sanitized stack locations, stage and delivery
   evidence. Submit-intent uncertainty remains unchanged. The daily submission
   ceiling continues to gate ordinary submissions but does not block fill-only
   preparation, which cannot create an intent.
9. **Current upload verdict:** a fresh rejected upload check invalidates an earlier
   positive receipt and synchronizes page observation with the persisted tracker
   result. Canonical resume changes keep their stronger `UPLOAD_FAILED` category;
   stale positive metadata cannot become an `UPLOAD_READY` manual hold. The check
   still returns false and no interaction is replayed.
10. **Bounded Windows recovery:** atomic history replacement retries the same
    completed, flushed temporary file at most seven times on `PermissionError`,
    with 2.55 seconds of total delay. Persistent denial preserves the old record
    and propagates; other operating-system errors are not retried. A process-
    inventory `TimeoutExpired` is eligible for the existing three-launch startup
    recovery only before application work and after verified cleanup. Each
    inventory retains its original bounded observation, ownership checks and
    fail-closed cleanup. No applicant interaction or final request is retried.

Components changed: Engine, browser startup error classification, local atomic
history persistence, shared application primitives, SmartRecruiters,
checkpoint/status projection, configuration, CLI, new `form_progress.py` and
`fill_runner.py`, documentation and regressions. No lifecycle retry budget,
final-submit interceptor, process identity or applicant provenance gate was
weakened.

## 4. Problems deliberately not fixed

Security challenges, authentication and unverified personal answers retain their
existing boundaries. No applicant facts or credentials are invented. No live
employer application, provider, Discord message or submission is executed.

Closed shadow controls, unfamiliar composite education/address widgets and review
pages with no inspectable controls remain bounded holds: without observable
semantics, inventing a generic interaction would reduce safety. Conditional fields
that appear after a long silent timer without a loading signal can still require
another explicit inspection. The existing qualification/continuation scripts keep
their narrower policies; this phase adds normal execution rather than silently
expanding an already-prepared authorization.

## 5. Manual intervention audit

| Remaining stop | Category | Handling |
| --- | --- | --- |
| Unknown personal facts, dates, preferences, writing, documents or consent | GENUINELY_REQUIRES_USER | Fill unrelated supported fields; retain exact pending question and partial checkpoint; live handoff available |
| CAPTCHA, MFA, password/account login, email/phone/identity verification | SECURITY_REQUIRES_USER | No bypass; preserve dedicated page in live mode, protect checkpoint contents; resume only through existing validated commands |
| Employer validation, disabled controls, explicit closure, access denial, rate limits, unavailable listings | EMPLOYER/SITE_LIMITATION | Preserve evidence; bounded observation/retry policy and security holds remain authoritative |
| Unlabelled controls, unsupported required widgets, ambiguous actions/uploads, unconfirmed delivery, never-settling inventories | ENGINEERING_LIMITATION_REMAINING | Do not guess or replay uncertain actions; user can complete the preserved form; checkpoint batches can continue to other eligible applications |
| Exhausted browser startup/recovery or unverified process cleanup | ENGINEERING_LIMITATION_REMAINING | Existing three-launch startup recovery applies; global execution blocker stops further jobs |

Unsupported required controls can still prevent reliable inventory of the entire
step, so some forms require more manual filling than others. This phase improves
common reliable controls and recoverable metadata, rather than claiming every
ATS widget is supported.

## 6. ATS autonomy assessment

- Generic/Greenhouse: shared native-control, wrapped-label, lazy-entry and supported
  iframe readiness improvements. Custom upload acceptance remains provider-specific.
- Lever/Ashby: existing entry, option-selection and upload evidence boundaries are
  retained. Ashby still requires remote file-metadata acceptance; file selection
  alone is insufficient.
- SmartRecruiters: missing profile values no longer stop unrelated filling;
  email-free entry and identical-schema DOM replacement use shared primitives.
  Open-shadow control metadata and session attachment receipts remain supported.
- Workday: existing loading/chooser/Apply Manually state machine and visibility
  rules remain. Conditional reconciliation uses the same Engine path. Account
  creation/login and unfamiliar education/address widgets may still require users.

These are local fixture qualifications, not broad live employer certifications.
Final review pages with no inspectable controls, ambiguous custom actions and
closed shadow components remain conservative holds. Direct URL intake still
needs the normal listing discovery/provenance gates; the new command selects
stored eligible opportunities rather than fabricating posting dates.

## 7. Browser resilience assessment

Existing startup recovery, process identity, exact dedicated profile ownership,
page leases and one-page limits are retained. Dedicated browser/profile locks are
reacquired in the real CLI regression after shutdown; file existence alone is not
lock ownership on Windows. Metadata context destruction is reobserved within its
budget; field input and Next are not blindly replayed. Infrastructure failures
stop sequential work. Cleanup runs even if persisting the paused setting fails.
No fingerprint spoofing, anti-bot evasion or process-name-based killing was added.

## 8. Answer-resolution assessment

Deterministic resolver, immutable profile snapshots, exact option validation and
answer provenance remain authoritative. Fixing labels increases recognition of
already-supported concepts without broadening factual inference. Receipt reuse
checks fact/profile/policy and writing revisions; changed verified facts trigger
re-resolution. Unknowns stay unanswered. Existing user-answer commands retain
scope/signature checks for later reuse. New fill commands disable narrative
providers, leaving unknown writing to the user after filling supported facts.

## 9. Upload/recovery assessment

Canonical document snapshots and fresh acceptance evidence remain mandatory.
Incomplete applications can still upload/verify the resume before an input hold.
Changing a document invalidates acceptance as before. Existing same-session
attachment carry requires an observed Next transition; new browser/documents
cannot inherit a database upload receipt. Stale resume hashes, ambiguous upload
controls and uncertain step delivery remain holds. Checkpoints carry progress,
not permission to restore or submit; existing explicit reconstruction gates remain.
No historical attempt, resume revision or submission evidence was changed.

## 10. Focused validation

Before the final readiness repair, the implementation passed **84 tests in
392.67 seconds**, covering the first 34 new regressions plus existing
SmartRecruiters and fill-only coverage. JUnit:
`.tmp/hardening-focused-frozen.xml`. Earlier debugging/compatibility runs passed
117 and 207 tests; those are supplementary evidence, not complete acceptance.
The subsequent readiness-race repair passed **28 focused patience tests in
143.14 seconds**, including the deterministic regression and both original
delayed-Next/Continue cases. JUnit: `.tmp/hardening-readiness-fixed.xml`.
Final upload-evidence and changed-behavior checks passed **8 tests in 98.12
seconds**. JUnit: `.tmp/hardening-final-repairs.xml`.
The fixture-contract repairs passed **45 tests in 51.71 seconds**, covering the
complete persistence and resume-migration modules plus real-browser upload
rejection tests. JUnit: `.tmp/hardening-observation-contract-fixed.xml`.

Real local Chromium verifies missing facts alongside known fields/resume,
conditional reveal/replacement/hydration, disappeared unknown controls, changed
dropdown metadata, identical-schema step replacement, embedded application frames,
hidden entry templates and profile revision changes. Real CLI subprocesses process
two local HTTP applications sequentially, both explicit and automatic selection,
despite a first input hold. Those cases assert zero submission intents, zero final
clicks, zero final requests and zero server POSTs, and reacquire worker/profile
locks after shutdown. Unknowns remain empty and exact pending questions survive.

Additional regressions cover bounded metadata refresh, truthful current-attempt
checkpoint counts, protected-field redaction, sanitized engineering failures,
daily submission limits and batch termination after infrastructure failure.
Nine further regressions cover temporary/persistent atomic replacement denial,
unrelated filesystem errors, pre-application inventory timeout recovery/exhaustion,
unverified cleanup and prohibition on replay after an application page exists.
The Windows recovery compatibility run passed **138 tests in 294.01 seconds**,
including all new regressions, complete startup/cleanup/history modules and both
previous setup-error cases. JUnit: `.tmp/hardening-windows-compatibility.xml`.

## 11. Complete offline validation

The initial immutable baseline produced **1 failure and 1,296 passes in 2,526.75
seconds**, with no skips or warnings reported. Its delayed `Continue` hydration
case exhausted the 30-second observation budget. Baseline JUnit:
`.tmp/hardening-baseline-snapshot.xml`. This was the unchanged pre-edit snapshot;
the supplied accepted 1,297-pass result was not reproduced exactly in this run.
Interrupted sandbox/probe runs are not clean acceptance evidence.

The first final run was interrupted after identifying and reproducing the shared
readiness race; it is not acceptance evidence. The second complete run produced
**3 failures and 1,329 passes in 2,572.27 seconds**, without skips or warnings.
JUnit: `.tmp/hardening-acceptance-full-v2.xml`. Two assertions expected behavior
deliberately repaired here: stopping unrelated upload for unknown facts and
allowing generic entry to accept a persistent loading skeleton. Their original
test IDs remain and now assert partial preparation or bounded not-ready holds,
with the existing no-submit/no-unknown-answer checks. The third failure exposed
stale upload classification, repaired with two additional regressions for a
fresh rejected check and a changed canonical resume.

The third full run produced **2 failures and 1,332 passes in 2,919.73 seconds**,
with no skips or warnings. JUnit: `.tmp/hardening-acceptance-full-v3.xml`. Both
failures were reproduced separately: an unhashable page placeholder in the
upload-batching unit test and a browser stub without the existing `observation()`
API in the resume-migration test. Fixtures now support the real page/browser
contract and additionally assert agreement between observed and persisted upload
verdicts. Original batching, no-transaction-during-await, no-upload-on-old-hash
and historical evidence assertions are retained. Those fixture-contract repairs
changed no production source.

The fourth full run produced **1,332 passes and 2 setup errors in 3,013.92
seconds**, with no skips or warnings. JUnit:
`.tmp/hardening-acceptance-full-v4.xml`. One setup hit Windows `WinError 5` during
atomic statistics replacement; the destination was later inspectable and not
read-only. The other hit a 30-second process-inventory timeout before Chromium
launch. Both local operations now have the bounded recovery described above;
no test was removed, skipped or converted to an expected failure.

Clean complete final acceptance passed **1,343 tests in 2,961.31 seconds
(49 minutes, 21 seconds)**, with **zero failures, errors, skips or warnings**.
JUnit: `.tmp/hardening-acceptance-full-v5.xml`. Actual execution contains
**all 1,297 baseline IDs plus 46 new regressions**, with no missing, unexpected
or duplicate IDs. The final audit verifies all 129 frozen source files and the
initial snapshot remained unchanged: `.tmp/hardening-final-test-audit.json`.
Command: `python -u -m pytest -q -p no:cacheprovider
--basetemp=.tmp/hardening-acceptance-full-v5
--junitxml=.tmp/hardening-acceptance-full-v5.xml --durations=20`.
The source manifest and collection audit are saved in
`.tmp/hardening-frozen-source-hashes.json` and `.tmp/hardening-test-id-audit.json`.
JUnit SHA256:
`cf691e4b2992102b51464a37ca5a37107342bb708e11c6538e4477a237452a9d`.
Frozen source-manifest SHA256:
`56146a85e652fd99763d2b41f7b0bf7a7d0c4c33cb48127ad56a002163dd8c48`.

Git review distinguishes this phase's 21 files from the pre-existing accepted
repairs. An ignored `.tmp/hardening-only.patch` records that delta. The ordinary
tracked diff and the new source/report files pass whitespace checks. Private
configuration/database, browser data and synthetic test artifacts remain ignored.
The existing publishable-content scan and a supplemental scan of current working
content in all 21 phase files return zero privacy findings. Evidence:
`.tmp/hardening-final-privacy-audit.json`. No files are staged.
No commit is created because the starting tree contains
interdependent uncommitted accepted work; committing that foundation as this
phase would mix ownership. HEAD remains the initial commit. No push or history
rewrite is performed.

## 12. Remaining risks

Full real-employer behavior is unqualified in this offline phase. The existing
submission request classifier recognizes supported labels/endpoints, not arbitrary
network traffic. Fill-only must still prevent reaching any final interaction.
Unsupported composite widgets, inaccessible review-page semantics and silent
delayed conditional controls remain the principal engineering limitations.

## 13. Production readiness recommendation

Ready for controlled semi-automatic fill-only use based on complete offline
acceptance. Supported facts, resume preparation, conditional steps and sequential
handoffs are qualified with local fixtures. Production remains paused with
auto-submit off. Start with one explicitly selected eligible application when
live execution is separately requested, preserving the form for manual input.
This does not certify every live ATS layout or authorize consumption of the
existing application 3556 continuation. Complex controls and security steps
retain the manual boundaries described above.

## 14. Application 3556 continuation validity

Read-only production inspection under the worker lock passed the existing
`continuation.safety()` gates. The authorization remains **PREPARED**, application
status **MANUAL_REVIEW / BROWSER_START_FAILED**, attempts **6**, delivery
**NOT_STARTED**, no submission intent or confirmation. Historical attempt count,
retry policy, hold reason, canonical URL and canonical resume SHA256 still match
its binding. Global `paused=true` and `auto_submit=false` are unchanged.

The existing continuation is safe/compatible with these changes and remains
unconsumed. Its source code was not modified. Its special runner retains the
original checkpoint-only Engine mode. Normal `fill` selection cannot claim this
held application or consume a prepared command. Listing freshness and live safety
must still be rechecked at its future explicit invocation. Evidence:
`.tmp/hardening-continuation-audit.json`; command-content SHA256
`b0be02d67161cb53cce9fb1959418ab8724e3f216a0fc46204e358d248ee1dd0`.
A repeated read-only audit confirms the same command hash, bindings, state,
attempt count and global settings: `.tmp/hardening-continuation-final-audit.json`
records `unchanged_from_initial_audit=true`. This final audit was repeated after
the clean complete acceptance run. It did not claim or consume the command.
