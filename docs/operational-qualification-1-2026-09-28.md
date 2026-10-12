# Operational qualification 1 — 2026-09-28

**Primary result: QUALIFICATION_ABORTED_SAFELY**

The first candidate examined for this qualification, application **4611**, failed the historical-retirement safety check. No Engine attempt, claim, application browser page, fill, upload, or submission was performed. In accordance with the instruction to stop if any pre-run safety check fails, no replacement candidate was selected. This is an aborted pre-run qualification, not evidence that the live fill path is qualified.

## 1. Qualified source identity

- Branch: `main`.
- Commit under qualification: `dc50864e6ab3d54f511a8f14b73bd9736b3fe193`.
- Initial worktree: clean (`git status --porcelain=v1` returned no entries).
- All **93/93** normalized source/test hashes match `docs/phase8-2026-09-27/validated-tree.json`; zero mismatches. This binds the committed source to the Phase 8 tree accepted by the final audit, whose recorded HEAD preceded the commit of those changes.
- Read the final acceptance and Phase 8 reports, relevant Phase 7 lifecycle/fill-only/incident restrictions, and current candidate, configuration, Engine, browser, identity, and fill-only runner code.
- Git required the per-command `safe.directory` option because the execution account differs from the repository owner. No global Git configuration was changed. Git also warned that the user-global ignore file was unreadable; repository status otherwise completed successfully.
- No production code, tests, private configuration, database state, history, or browser profile was modified by this qualification. The sole deliverable is this report. The prior 1,023 baseline and 12 supplemental passing tests were not rerun or represented as live evidence.

## 2. Selected application

One candidate was provisionally examined; **no eligible application was admitted for processing**.

| Item | Evidence |
| --- | --- |
| Application ID | 4611 |
| Company | Veeam Software |
| Role | Software Engineering Intern - Summer 2027 |
| Location | San Jose, CA, USA |
| ATS | Greenhouse; stored ATS and public page agree |
| Canonical URL | `https://job-boards.eu.greenhouse.io/veeamsoftware/jobs/4955293101` |
| Tenant / board slug | `veeamsoftware` on `job-boards.eu.greenhouse.io` |
| Opportunity ID | `4955293101` |
| Search provenance | Existing application in the user's private internship queue; no new job was ingested |
| Stored posting evidence | `2026-09-11T15:50:20+00:00`; source evidence `9d; source revision 2026-09-20T15:50:20+00:00` |
| Stored listing state | ACTIVE; listing_active=1; within the configured 30-day age window |
| Fresh open-state evidence | Public listing retrieved on 2026-09-28; matching role, U.S. location, application controls, and final submission button were present |
| Eligibility for qualification | **Failed:** recorded historical failed-batch retirement |

Fresh public evidence came from the [employer's Greenhouse listing](https://job-boards.eu.greenhouse.io/veeamsoftware/jobs/4955293101), a read-only web retrieval. This was not a production application browser session, and no applicant data was supplied. An open public listing does not override historical protection.

## 3. Pre-run safety checks

| Check | Result |
| --- | --- |
| Exact source commit recorded | PASS |
| Clean initial worktree | PASS |
| Effective auto_submit=false | PASS: SQLite setting contains JSON `false` |
| Fill-only mode | Production `Engine(fill_only=True)` path reviewed; not instantiated because retirement check failed |
| One-application-page limit | Production constant and Engine enforcement are 1; no browser started |
| Existing profile/resume available | PASS: profile YAML and resume PDF exist and are readable; applicant values were not printed |
| No pending submission intent for candidate | PASS: submit_intent_at is null; submission_confirmation_seen=0 |
| Fresh/currently open job | Public listing and existing dated evidence observed as described above |
| Not protected/retired by history | **FAIL:** candidate appears in failed-batch retirement evidence and has a retirement event |
| Candidate identity recorded | PASS: canonical URL, recognized ATS, board slug, opportunity ID recorded above |
| Current candidate preflight | NOT RUN: stopped on retirement exclusion before opening a production Database or calling preflight |
| Full duplicate/conflict scan | NOT RUN after decisive retirement exclusion; no claim of conflict-free eligibility |
| Runtime control settings | controlled_application_id=null; paused=true; zero pending manual_requests |

The private configuration file has `application.auto_submit=true` and AI, Gmail, and Discord enabled. The persisted SQLite auto-submit value takes precedence in the ordinary Engine and was already false. No run was started and no configuration overrides were applied. Any later authorized fill-only execution must explicitly retain false and disable provider/service execution; this report does not claim the stored configuration file was changed.

The source runner separately excludes IDs in `data/private/failed-batch-retirement.json`, in addition to the fixed incident exclusion set. ID 4611 appears in that retirement file even though its current queue row is RETRY and it has no `duplicate_submission_guard:4611` setting. The RETRY projection is not permission to disregard the runner's retirement evidence.

## 4. Execution timeline

All actions occurred on 2026-09-28; final read-only evidence capture was **2026-09-28T06:08:41.757462+00:00**. Intermediate actions are ordered below without invented timestamps.

1. Read the attached authorization, final acceptance report, and Phase 8 report; inspected Git identity/status.
2. Compared the complete 93-file Phase 8 normalized manifest with current files; all matched.
3. Inspected runtime settings using SQLite `mode=ro`; checked profile/resume availability without displaying applicant facts.
4. Read a bounded queue preview and provisionally examined its first candidate, 4611. No candidate was claimed or processed.
5. Retrieved that candidate's public listing through the read-only web tool and observed an open application form.
6. Checked the candidate's current status and historical retirement file. Found ID 4611 in that file; immediately stopped operational preparation and did not choose another candidate.
7. Collected read-only corroborating retirement event metadata and wrote this report.

No `scan`, `work-once`, batch runner, restore, retry, reconciliation, or Engine processing command was executed. No production `Database` constructor was invoked, avoiding its initialization/history-maintenance side effects.

## 5. Form/answer behavior

Not exercised. ATS adapter selection, control inventory counts, deterministic resolutions, unresolved fields, rejected/ambiguous mappings, unsupported controls, and conditional discovery are **not measured**. Public text retrieval is not an adapter inventory.

There are no unresolved/unusual field resolution records from this qualification because no resolver or field interaction ran. Applicant factual fields used zero AI calls because no answering occurred. No writing provider was authenticated, invoked, or enabled, and no generated or cached prose was used.

## 6. Upload behavior

The existing `data/private/resumes/resume.pdf` was readable (248,930 bytes). It was not selected into a browser or uploaded. Upload evidence type: none; upload readiness: not evaluated; receipt validity through later steps: not applicable. Local file availability is not remote acceptance.

## 7. Browser/page ownership

- Production application pages created: **0**.
- Peak active production application pages: **0**.
- Popup violations: **0**; no application browser was launched.
- Owned pages/leases created: **0**; no page release was necessary.
- Existing browser contexts were not inspected or controlled. These counts describe this qualification only.

The public listing was retrieved by the web tool without creating an AutoApply-owned application page. Runtime browser telemetry and page-limit enforcement were not exercised.

## 8. Security/transition observations

No authentication, MFA, CAPTCHA, email/phone verification, rate limit, security challenge, or automation rejection was encountered by an AutoApply session because none was started. The public listing was readable without authentication; that does not qualify the production session's security behavior.

Next/Continue interactions: **0**. No transitions were attempted, confirmed, or repeated. Security holds and transition ambiguity handling remain untested in this qualification.

## 9. Checkpoint/hold result

No new application checkpoint or lifecycle hold was created. The stop reason is persisted in this report; it is not misrepresented as an Engine-persisted safe hold.

Application 4611 remained in its pre-existing state:

- status: `RETRY`; application_state: `DISCOVERED`; attempts: `1`.
- stage: `form page 1`; prior reason: `TimeoutError during form page 1`.
- security_state: `PASSIVE_PROTECTION_DETECTED`; manual_action_required: `0`.
- submit_intent_at: null; submission_confirmation_seen: `0`.
- Latest recorded event: **8084**, `FAILED_BATCH_SESSION_RETIRED`, **2026-09-25T00:19:03.931474+00:00**.

Corroborating private evidence is `data/private/failed-batch-retirement.json`, SHA-256 **`36e67e9832baaa864892439de57f1aaaec9f5f967cf840dffc5add4d9dc73d12`**. Its application entry identifies the same ID, company, role, and canonical URL. Private answers and event bodies are not reproduced here.

No retry, unretirement, reconciliation, historical correction, or release was attempted.

## 10. Submission-zero proof

```text
submission intents: 0 created by this qualification
final submit clicks: 0
recognized final submission requests: 0
max active application pages: 0
auto_submit: false (persisted effective SQLite setting; unchanged)
```

These are nonexecution results, not measurements from an installed browser probe. This qualification used only read-only SQLite queries, local reads, a public listing fetch, and report creation. It never initialized Engine, a browser context, a provider, or a submit transport.

The database contained **6 historical non-null submission intents** both at the initial and final reads; none belongs to a new qualification action. ID 4611 had no intent at either inspection. The final maximum existing event ID was **8151**. No database mutations or lifecycle calls were issued. No external messages were sent.

## 11. Deviations from offline fixtures

No live adapter/form deviation can be established because the application was never processed. The concrete operational observation is that a coarse ACTIVE/RETRY queue preview can contain a historically retired batch member. The existing production batch runner accounts for that with its separate retirement-file exclusion. No architecture mismatch or defect is inferred from a preview that was not the complete production selector.

- Observed behavior: candidate 4611 was visible in the queue preview but recorded in historical retirement evidence.
- Expected behavior: retired/historical incident applications must not be admitted to qualification.
- ATS/step: Greenhouse; pre-run selection, before claim or browser creation.
- Relevant evidence: retirement file membership and event 8084.
- Failure classification: pre-run historical-retirement safety check failed.
- Safety preserved: yes; no claim, retry, browser interaction, upload, intent, or submit occurred.

## 12. Qualification outcome

**QUALIFICATION_ABORTED_SAFELY**

The instruction to stop on a failed pre-run check was honored. Exactly one candidate was examined in detail, but **zero target applications were processed**. Therefore the required one-application production flow, form semantics, upload conservatism, browser ownership, and truthful Engine checkpoint/hold persistence have not been qualified. Zero submission activity alone does not constitute a pass.

## 13. Recommended next action

Keep 4611 excluded. A separately authorized continuation should first apply the complete existing retirement, protected-history, and candidate filters before nominating a destination, then verify its fresh open state. Retain the frozen source and explicit deterministic-only, auto-submit-false, one-application constraints.

No second application or Qualification 2 was started. No provider was enabled, no application was submitted, and no production repair is proposed as part of this qualification.
