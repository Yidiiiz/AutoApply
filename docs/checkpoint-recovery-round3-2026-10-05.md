# Checkpoint recovery and controlled preparation — round 3

The starting accepted offline baseline is 1,386 cases: the original 1,362 plus
24 round-2 regressions. Existing uncommitted work and failure evidence are retained.
Complete offline acceptance passed. The authorized five-application live round
was unsuccessful: **zero verified fields and no canonical-resume acceptance**.
Final submission remained disabled throughout.

## Recovery implementation

`autoapply recover-checkpoint ID --source "explicit user authorization"` creates
a separate one-shot manual-ledger authorization for the qualified Country
inventory failure. Preparation records evidence without releasing the hold.
Claim happens only in a fresh fill-only Engine after OS-backed profile ownership
inspection; normal browser startup reacquires the profile lock and repeats its
ownership checks. The CLI owns the worker lock across database startup, execution
and cleanup. Database startup disables incidental listing maintenance.

The binding includes the exact checkpoint bytes, attempt count/event, hold event,
checkpoint event, subsequent event history, canonical listing identity/content,
fresh posting date, canonical resume path/hash and profile revision. The document
and profile are rechecked after browser startup, before employer navigation, and
before file selection. A current upload verdict remains necessary; an ended
session or historical filename cannot satisfy resume acceptance.

Only an unfilled first-page checkpoint from the exact Country inventory failure
qualifies. Prior applicant input, document selection, upload evidence, Next intent
or delivery, generic application exceptions, security/verification holds,
submission intent/confirmation, duplicate history and protected IDs reject it.
The Country search text present in the old inventory is permitted only in its
exact observed control; it is not counted as verified applicant-field filling.

Recovery uses the remaining ordinary attempt budget. It does not refund historical
attempts, increase retry limits, enable `manual_resume_allowed`, or repurpose the
startup-only continuation. Claim retires the one-shot authorization before browser
startup; failed work cannot requeue itself. Final submission remains intercepted
and disabled, and one application page is enforced.

## Saved answers and retained holds

The user confirmed Yes for experience questions going forward. The supported
controller saved Yes for APEX 5321 questions 251 and 252 and Lunar 6127 questions
230, 232 and 233. Application state, hold, retry flags and attempt count were
checked unchanged after persistence; no execution command was queued. The
standing instruction is saved privately for compatible operator-assisted
Yes/No experience confirmations. It supplies no durations, credentials, legal
declarations or personal interests.

Lunar 238 remains unanswered: “Can you confirm this requirement? Verify
qualification: Fueled by a passion for space exploration and the boundless
possibilities beyond our world”. The exact Yes/No prompt was requested separately.

## Qualification evidence

The recovery-only run passed 50 cases before three additional binding/failure
regressions. Final focused qualification passed 78 cases, including all 53 new
recovery cases and existing reconstruction, controlled-resume and Country tests.
Original tests remain byte-identical to the ownership snapshot.

New fixture-harness failures and the interrupted earlier focused run are retained
under `.tmp/live-round3-2026-10-04/`. The first fixture wrapper intercepted the
wrong startup method, allowing only synthetic nonexistent listing GETs which
returned 404; no applicant fields or uploads were sent. The final fixtures install
interception at `_start` and navigate a local URL. A later fixture wrapper omitted
the required upload-controls argument; its failure was corrected, with accepted
and rejected upload outcomes asserted separately. These runs are not acceptance
evidence. Round-2 Windows cleanup/CIM failures remain untouched.

Complete frozen-source acceptance passed **1,439 cases in 2,240.36 seconds**,
with zero failures, errors or skips. Every one of the prior 1,386 case identities
is retained, with no missing or duplicate IDs. All original test files and frozen
source hashes are unchanged. Acceptance used one sequential worker, including the
entire browser, SmartRecruiters and submission-probe modules; no replacement
reruns or cleanup-policy exceptions were needed.

## Live outcomes

One supported recovery was followed by four distinct normal queue candidates.
Every application was considered once, with the Engine enforcing one application
page. Both runs completed and cleanup succeeded. All five checkpoints are
UNFILLED; no useful populated form was available to preserve for manual completion.
Checkpoint-only execution closed each page. No ended session is reported as ready.

| Application | Verified fields | Fresh resume acceptance | Stop and remaining inputs |
| --- | --- | --- | --- |
| 4611 — Veeam Software | 0 | No | Same exact Country-option inspection failure. Attempts increased normally from 3 to 4; ordinary budget is exhausted. Resume 171 remains pending and requires actual fresh ATS acceptance. Required Preferred First Name remains saved. |
| 6419 — Enterprise | 0 | No | JOB_DESCRIPTION_UNAVAILABLE after the metadata budget. Checkpoint includes two frames with the same listing URL and no applicant fields. No applicant answer can supply missing public metadata. |
| 4569 — Principal Financial Group | 0 | No | CLOSED after HTTP 404. No replay or missing applicant input. |
| 5347 — Highgate | 0 | No | Eligibility hold. Ten compatible current-degree, ordinary-skill, posted program-availability and office-requirement confirmations were subsequently saved through the controller. Minimum Junior status 260 and posted compensation/schedule terms 270 remain pending. The exact application row and hold were unchanged by answer persistence; no execution command was queued. |
| 6438 — Inmar Intelligence | 0 | No | Workday APPLICATION_ENTRY_MODE_UNRESOLVED after guarded entry failed. Readiness reached APPLICATION_ENTRY_CHOICE, but no entry delivery or transition was confirmed. The interaction is not automatically replayable. One bounded zero-application-page startup recovery was recorded before the single ordinary attempt. |

The original held applications were not replayed. APEX 5320 has its SQL answer
saved and no pending personal question, but retains its checkpoint hold. APEX
5321 has both experience answers saved; duplicate Apply controls still need an
unambiguous supported entry. Lunar 6127 has its three experience answers saved
and still needs personal-interest question 238. CACI 6418 retains its HTTP 403
security/access hold; personal answers cannot resolve it. Their verified-field
and resume-acceptance totals remain zero.

Highgate's exact remaining prompts, answer Yes or No:

- **260:** “Can you confirm this requirement? Verify qualification: Minimum Junior status.”
- **270:** “Can you confirm this requirement? Verify qualification: Salary - $20/hour for 40-hour week, with a duration of 10 weeks.”

Question 270 is posted pay/schedule information classified as a qualification by
the current parser. No salary expectation or acceptance was inferred. It remains
held pending clarification or a qualified administrative-clause correction.

## Observed diagnosis and next engineering step

Veeam's main document returned HTTP 200 and had passive reCAPTCHA, not an
interactive security block. Background `users/self` GET 401 responses are retained
as diagnostics. Fresh readiness saw 38 controls without a preceding transition;
the checkpoint retained Country inspection search text `United States +1`, with
no committed applicant fields, file selection or upload evidence. The same exact
saved-option lookup then failed. This does not prove that the option is absent,
that search is the only cause, or that an accessible-name/display-text mismatch
occurred: fresh option names/text and association-root evidence were not recorded.

The previous dial-code menu fixture therefore did not qualify the real employer
layout. The next concrete autonomy step is bounded Country inspection diagnostics
covering associated listbox identity, visible option display text, accessible
names and search state, followed by a fixture built from observed semantics.
Selection must remain exact, uniquely associated and verified; no fuzzy option
guess or replay is authorized. Veeam cannot use this recovery again under its
exhausted ordinary budget. It needs manual completion or a separately supported,
explicit authorization after budget review; attempts and retry policy stay intact.

Enterprise's missing metadata and Inmar's unconfirmed entry require their own
adapter qualifications. Answering Highgate/Lunar questions does not release any
checkpoint or entry hold.

## Final safety and preservation

Both live run summaries record zero final-submit clicks, zero employer submission
requests, zero created intents and zero blocked submission requests. The final
OS ownership/read-only database audit confirms paused=true, auto_submit=false,
controlled_application_id=null, no dedicated-profile process/held lock, no pending
execution command and no new submission intent.

Every original application, question, event and manual-command row is retained.
Rows outside the five new round applications and the two explicitly answered old
applications are unchanged. Protected checkpoint hashes match. Application 3556's
PREPARED ledger row is byte-identical and unconsumed; Tyler 4522 was not reopened,
and Netsmart 6397 was not replayed. Round-2 cleanup/CIM failure records remain
preserved. All uncommitted foundation work remains; this phase modified only four
pre-existing files and added the recovery module, its tests and this report.

No staging, commit, push, outbound message, authentication bypass or final
submission occurred. Complete verification and live operational evidence remain
separate: the recovery guard is accepted offline, while live field filling and
canonical-resume acceptance are still unqualified.

Private evidence includes `before.json`, original source hashes, ownership audits,
saved-answer audit, preserved Veeam checkpoint, frozen-source manifest, collected
case IDs, focused JUnit and complete-suite log/JUnit, `acceptance-audit.json`,
`veeam-result.json`, `queue-result.json`, `highgate-policy-audit.json`,
`application-report.json` and `final-readonly-audit.json`. Read-only audits open
SQLite with `mode=ro` and do not initialize the production database. Privacy and
whitespace checks passed.
