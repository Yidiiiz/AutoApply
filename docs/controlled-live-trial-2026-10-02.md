# Controlled live fill-only trial — 2026-10-02

Five existing queue applications were attempted sequentially through the normal
`autoapply fill` command. None reached field filling or canonical-resume upload.
This was an unsuccessful end-to-end preparation trial that exposed actionable
engineering defects; it does not qualify these live employer forms as supported.
Final submission remained disabled, and no submission intent was created.

## Execution and application outcomes

The first run used `python -u -m autoapply fill`, with a live handoff. Subsequent 
runs used `fill --limit 4 --checkpoint-only` and, after repairing a route-handler
failure, `fill --limit 2 --checkpoint-only`. The interrupted four-candidate batch
processed only two applications. The later selection filled the two remaining
slots, for five distinct applications and five processing attempts in this trial.
No held application or uncertain entry interaction was replayed.

| Application | Filled/uploaded in this trial | Stop | Next concrete step |
| --- | --- | --- | --- |
| 4611 — Veeam Software, Software Engineering Intern | No fields; no upload | Eligibility confirmation before form filling. Seven exact confirmations were subsequently saved. Required `Preferred First Name` was deliberately left blank; a historical pending `Resume` row also remains. | Manual completion or a separately validated continuation after resolving the required-field conflict. The checkpoint is unfilled and does not authorize reconstruction. |
| 4704 — Two Sigma, Software Engineering Intern | No fields; no upload | `AUTH_REQUIRED`: employer sign-in requires the intended configured account. | Restore authentication manually through the dedicated login workflow before any later authorized continuation. |
| 6397 — Netsmart, Cloud Engineer Future | No fields; no upload | Workday entry choice remained unresolved. Binary gzip requests broke the fill-only route callback; the subsequent checkpoint failed. | Inspect/complete the employer entry manually. No entry transition was confirmed and no automatic replay is authorized. |
| 4522 — Tyler Technologies, Software Development Intern | No fields; no upload | False `INELIGIBLE` result because the site-branding H1 replaced the internship title. | Review the final historical outcome explicitly. Corrected public metadata and an audit event are saved; the terminal-status gate has not been bypassed or the application reopened. |
| 4524 — Lunar Outpost, Ground Software Engineer Intern | No fields; no upload | Blank public metadata was incorrectly presented as an applicant eligibility question. | Reacquire inspectable public listing metadata before eligibility assessment. The stop is now `JOB_DESCRIPTION_UNAVAILABLE`; the erroneous question is retained as inactive, without an answer. |

The first handoff was preserved while questions were answered. Ending that terminal
session interrupted the worker before its run report finalized. Before continuing,
the operator proved the production browser process was absent and reacquired both
worker/profile locks, then cleared only that application's stale live-session
flags through the supported database API. An independent first-run audit records
this interruption; the incomplete report is not presented as a clean run.
The first worker's in-memory click/request counters are unavailable and are
recorded as null, rather than reconstructed as zero. SQLite independently
verifies zero new submission intents and zero submission events for the trial.

Checkpoint-only runs closed their pages. No live employer form remains open.
Netsmart's missing checkpoint is explicitly reported rather than reconstructed.
No new applicant facts, dates, credentials or answers were inferred. User-provided
answers retain normal question signatures and provenance. Standing instructions
are saved privately for future compatible operator-assisted questions; the
deterministic resolver's existing compatibility and scope rules remain in force.

## Repairs

1. Recognize `Common Requirements` / `Common Qualifications` as required sections,
   retaining every qualification. Exclude only the complete observed two-sentence
   sponsorship-answer instruction. Additional/changed clauses and genuine
   citizenship/sponsorship restrictions still enter eligibility assessment.
2. Read Playwright request bodies as bytes. Inspect gzip actions with a bounded
   expansion; malformed, oversized or trailing compressed mutations fail closed.
   Binary document data retains ASCII action-marker inspection. Apply the same
   classification to fill-only routing, request observation and the older
   destination-specific approval route. Payloads are not logged or persisted.
3. Extract Jobvite's observed job-detail H2 and description container, avoiding
   site branding, sharing controls and similar-job qualifications.
4. Observe an empty description within the existing page metadata budget before
   assessing eligibility. Persistent absence becomes an engineering hold rather
   than a Yes/No question about unavailable evidence. This observation performs no
   applicant interaction and cannot supply missing facts.

Ownership, upload acceptance, attempt limits, final-status protection, answer
provenance and fill-only submission protections are retained. Production YAML now
also defaults auto-submit off; durable settings remain paused/off. The old
`controlled_application_id` was already null, so no restriction needed clearing.
Protected application rows and application 3556's prepared command remain
identical to the initial private snapshot. Its continuation was not consumed.

## Validation and evidence

The starting acceptance is the existing **1,343-test** offline result; no baseline
rerun was required. All pre-existing source/test work is preserved except the four
production files deliberately repaired here. No original test was edited,
removed, skipped or weakened. Nineteen new regression cases cover the live defects.

Focused checks: 154 eligibility/standing tests, 35 candidate tests, 120 binary
guard/fill-only/Workday tests, a separate real-Chromium binary/checkpoint test, 121
passing metadata/eligibility/candidate cases before correcting a new fixture's
incomplete patience stub, and four final metadata tests. Reproduction runs failed
on the observed defects before their corresponding repairs.

Complete offline acceptance: **1,362 passed**, with **zero failures, errors or
skips**, across four isolated test processes. The combined clean JUnit file is
`.tmp/live-trial-2026-10-02/acceptance-clean.xml`; its audit is
`acceptance-clean-audit.json`. It contains **all 1,343 original IDs plus 19 new
regressions**, with no missing or duplicate IDs. Every original test file is
byte-identical to the starting snapshot; the final source manifest is unchanged.

The initial complete parallel run had zero assertion failures and one strict
Windows process-cleanup teardown error (`BrowserOwnershipError: Owned process
termination failed`). Its process was subsequently absent, and the exact test
passed in isolation. The entire affected 296-test process was rerun and passed in
511.64 seconds. The clean result combines that complete rerun with the other
three clean processes. The initial errored result remains as evidence; no
ownership check, termination wait or test was relaxed. Intermittent Windows
cleanup remains an operational risk, and failures continue to stop safely.

Earlier full runs interrupted to add repairs exposed by later live applications
are not acceptance evidence. The existing publishable-content privacy scan found
zero findings, and tracked diff whitespace checks passed. Production history,
questions and manual-command rows outside the five trial IDs remain identical.

Ignored private evidence is under `.tmp/live-trial-2026-10-02/`, including database
and source snapshots, first/interrupted/final run results, public-layout evidence,
focused JUnit files, source manifests and the production audit. Existing per-job
checkpoints/history remain in private runtime storage. No commit, staging, push,
Discord message, email, authentication bypass or final submission was performed.
