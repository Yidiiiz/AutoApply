# Controlled live preparation, round 2 — 2026-10-02

Five applications were processed sequentially: one supported input-hold
reconstruction, followed by four candidates from normal queue selection. **No
application achieved confirmed field filling or canonical-resume acceptance.**
This round provides engineering evidence, not live ATS preparation qualification.
Final submission remained disabled throughout.

## Preconditions and protected history

The accepted starting suite contains 1,362 tests. Configuration and durable
settings were rechecked: production paused, auto-submit off, and the old
controlled-application restriction null. The private user policy and address/name
audit agreed with the profile and saved required preferred-name answer.

Both worker/profile locks were acquired. Exact dedicated-profile inspection found
no conflicting process or held Chromium profile lock. There were no pending
manual requests. The queue contained 1,305 candidates passing normal preflight.
Historical uncertain submissions and the stale session record for protected
application 4535 were retained; their database flags were not mistaken for a
currently owned browser. Application 3556's one-use continuation remained
PREPARED and byte-identical in the manual-command ledger.

Every original application, question, event and manual-command row was retained.
All rows in those tables outside the five trial applications are identical to
the initial private snapshot. Tyler 4522's terminal outcome and Netsmart 6397's
uncertain entry history were not reopened or replayed. No commit, staging, push,
message, authentication bypass or final submission was performed.

The ordinary privacy CLI initialized startup listing maintenance and refreshed
921 stale listing-metadata rows outside the trial. Application/job statuses and
protected listing rows were unchanged. This separate metadata effect is recorded
in the private listing-maintenance audit; subsequent privacy scans used the
read-only helper directly.

## Application outcomes

| Application | Verified fields / resume | Observed stopping point | Current input and next step |
| --- | --- | --- | --- |
| 4611 — Veeam Software, Software Engineering Intern | 0 committed fields; no selection or accepted resume | Supported reconstruction opened the listing. Refreshed metadata required six already-confirmed eligibility answers to be rebound. Same-page validation then stopped while inspecting the Country dropdown. | Preferred First Name is answered. Resume question 171 remains pending because no upload was accepted. The subsequent adapter hold does not authorize another reconstruction; qualify a supported recovery before any further automatic interaction. |
| 5320 — APEX Analytix, Data Analyst Intern | 0; no accepted resume | Eligibility confirmation for SQL query proficiency | The compatible saved skills policy has now answered question 226. The checkpoint-only hold remains in place; answer persistence did not authorize replay. |
| 5321 — APEX Analytix, Data Engineering Intern | 0; no accepted resume | The listing had two Apply now controls. Its locale Search selector was mistaken for an applicant question. | Search question 227 is retained as INACTIVE, without an answer. The hold is correctly categorized as APPLICATION_ENTRY_MODE_UNRESOLVED. Required experience questions 251 and 252 remain; resolve them and qualify unambiguous entry separately. |
| 6127 — Lunar Outpost, Embedded Software Engineer Intern | 0; no accepted resume | Eligibility questions; Desired Qualifications were incorrectly included as mandatory | Compatible schedule, degree and proficiency answers are saved. Preferred-only questions are retained as SKIPPED by normal reassessment. Four required experience/interest questions remain. No hold was released. |
| 6418 — CACI, Software Test Engineer Intern | 0; no accepted resume | HTTP 403 / UNKNOWN_SECURITY_FAILURE | No personal answer can resolve an employer access denial. Use manual employer inspection; automatic retry remains disabled. |

The dedicated Engine enforced a cap of one application page. Veeam's unfilled
page was inspected and checkpointed before release; later work used checkpoint-only
execution. None of the ended sessions is reported as live-ready. No useful filled
form was available to retain for manual completion.

## Exact remaining personal questions

APEX 5321, answer Yes or No:

- **251:** “Can you confirm this requirement? Verify qualification: Strong programming skills in Python, with experience in data engineering, data science, or a related field.”
- **252:** “Can you confirm this requirement? Verify qualification: Experience with SQL and database management systems.”

Lunar Outpost 6127, answer Yes or No:

- **230:** “Can you confirm this requirement? Verify qualification: Experience in embedded development”
- **232:** “Can you confirm this requirement? Verify qualification: Experience using version control tools such as Git”
- **233:** “Can you confirm this requirement? Verify qualification: Experience with Continuous Integration/Continuous Deployment (CI/CD) pipeline”
- **238:** “Can you confirm this requirement? Verify qualification: Fueled by a passion for space exploration and the boundless possibilities beyond our world”

Veeam's historical **171 — Resume** is a document-preparation obligation, not a
request for a textual Yes or a filename. It requires actual fresh ATS acceptance.

No additional credentials, domain experience, numeric duration, legal declaration
or personal interest was inferred. Existing policy was applied only to compatible
schedule, current degree and ordinary proficiency questions. No specific profile
availability date was inferred from a posted program period.

## Engineering repairs

1. **Canonical resume preparation during input recovery.** Resume/CV file rows
   no longer masquerade as missing personal answers when a valid canonical PDF
   is available. These rows remain pending until the current upload workflow
   establishes acceptance. Other documents, compound document requirements and
   factual questions still block. Existing retry, listing, session, delivery,
   security and submission gates are unchanged.
2. **Fresh acceptance before resolving a file question.** File selection alone
   no longer writes an answered document row. A successful current upload check,
   bound to the exact control and canonical document snapshot, records the
   filename and acceptance provenance. An unrelated historical file question is
   not resolved by another control's acceptance.
3. **Country display-label search.** A visible exact saved option is not searched
   again merely because the menu is long. Selection still attempts the existing
   search; if that search hides a previously observed unique exact option, only
   the search is cleared, within the same absolute budget, before fresh exact
   option and commitment checks. No selection click is replayed. This was
   reproduced with a local dial-code menu; the live Veeam hold was not bypassed
   to claim a successful repair.
4. **Entry ambiguity remains an engineering hold.** Multiple Apply controls
   cannot establish applicant-form readiness from unrelated locale fields.
   No arbitrary first Apply control is chosen. The original saved Rippling
   screenshot and checkpoint establish this defect. The
   [public listing](https://ats.rippling.com/apexanalytix-careers/jobs/52353bce-cb36-423f-ae0a-f2057ef3b5d9)
   independently contains duplicate Apply now entries and The Must-Haves.
5. **Required/preferred section corrections.** Desired Qualifications and
   Desired Requirements are recognized as preferred headings. Explicit posted
   internship scheduling remains required even after a preferred section.
   The Must-Haves is recognized as required, preventing false eligibility from
   an ignored section. The complete observed APEX employer-brand paragraph is
   administrative; changed or added clauses remain assessable.

## Verification

The initial recovery reproduction failed on both blocked resume continuations
and missing acceptance-based resolution. The country fixture reproduced both
saved-hint inspection failure and selection failure before repair.

Focused checks include 76 recovery/lifecycle passes, 14 new recovery passes with
real accepted/rejected browser upload cases, and 89 section/entry/eligibility
passes. A later final requirement run passed 89 with the two entry cases
deliberately outside that targeted selection; those entry cases passed in the
earlier full focused module and remain in complete acceptance.

The initial complete run executed all 1,362 original IDs plus 14 new cases, but
ended with two failures and three teardown errors, all from the existing strict
Windows Owned process termination failed check. It is not acceptance evidence.
The 50-case dropdown/browser run also had one such teardown error. Another
restricted-shell focused run failed at Windows process inspection and was rerun
through approved execution. These failures remain preserved; ownership checks,
cleanup waits and retry budgets were not weakened.

The frozen-source two-shard run then executed all 1,386 cases: 1,384 passed and
two failed only in `engine.close`/`browser.close`, after their test assertions,
with the same Windows owned-process termination error. Shard 0 passed all 700
cases. Both failure traces are retained. A first rerun was launched with system Python and
stopped at collection because it lacked Playwright; the project virtual
environment was restored for the actual rerun. No source changed between runs.

The sequential rerun was interrupted before completion and is not acceptance
evidence. Verification resumed on October 3 with all 686 affected cases retained:
the complete cleanup-sensitive module passed 14/14 alone, including both earlier
failures. The remaining 352 and 320 cases then completed with no assertion
failures and two fixture-teardown errors: Windows CIM process inventory exceeded
its existing 30-second budget under concurrent workers. Both complete affected
modules then passed sequentially: SmartRecruiters 36/36 and submission probe
21/21. Every other module's clean frozen-source evidence is retained; the final
audit checks the entire 1,386-case coverage, original identities and source hashes.

**Final complete frozen-source acceptance on October 3: 1,386 passed; zero
failures, errors or skips.** All 1,362 original case identities are retained,
with no duplicates or missing cases, and all original test files remain
byte-identical to the accepted starting manifest. Twenty-four new regressions
are retained. Source hashes were unchanged throughout final verification.
The five scoped repairs are accepted offline; live field filling and upload
acceptance remain unqualified. Windows inventory/cleanup failures under
concurrent offline workers remain an execution limitation, not a suppressed
test result or relaxed ownership check.

The combined clean JUnit and coverage/provenance audit are
`.tmp/live-round2-2026-10-02/acceptance-final-clean/acceptance-complete.xml` and
`acceptance-audit.json`. The audit identifies the retained complete-suite
evidence and the entire modules replaced by clean sequential reruns. Privacy
scans found no publishable private-file or secret findings; Git whitespace
checks passed.

Private evidence is in `.tmp/live-round2-2026-10-02/`: initial database snapshot,
ownership/runtime audits, initial and queue run results, exact saved-answer replay
audit, final operational/preservation audits, reproductions, focused JUnit files,
and complete acceptance runs. Production remains paused/off with no dedicated
profile process, no new submission intent, and no pending execution command.
The final October 3 audit opened SQLite read-only without database initialization:
protected rows and 3556's prepared command were unchanged, and no trial-profile
browser process remained.

## Next concrete autonomy qualification

Resolve the six exact personal questions without releasing a hold. The next
engineering step is a supported recovery for a proven pre-input adapter failure,
bound to the checkpoint and fresh ownership, retry, listing and delivery checks.
The existing one-use startup continuation only covers BROWSER_START_FAILED and
cannot be repurposed for Veeam's adapter hold. After that recovery is qualified,
Veeam's Country correction should prove verified contact fields and fresh
canonical-resume acceptance in one zero-submission live run. Rippling needs an observed,
unambiguous application-entry identity. CACI requires manual access resolution.
Do not consume 3556's prepared continuation or replay protected/uncertain history
to increase the successful-preparation count.
