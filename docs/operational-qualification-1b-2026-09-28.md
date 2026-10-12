# Operational qualification 1B — 2026-09-28

**Primary outcome: QUALIFIED_SAFE_HOLD**

Exactly one application, **4615**, entered the unchanged production `Engine(fill_only=True)` path. It was claimed, counted as one attempt, opened in one owned browser page, inspected through the Greenhouse adapter, and stopped for **eligibility verification**. The persisted result is `MANUAL_REVIEW / MANUAL_REQUIRED`, category `INPUT_REQUIRED`. No applicant fields were filled, no document was uploaded, and nothing was submitted. This qualifies the observed safe-hold behavior; it does **not** qualify successful form filling, upload acceptance, navigation, or manual-submit readiness.

## 1. Qualified source identity

- Branch: `main`.
- Commit: `dc50864e6ab3d54f511a8f14b73bd9736b3fe193`.
- All **93/93** normalized source/test SHA-256 hashes matched `docs/phase8-2026-09-27/validated-tree.json` before operational preparation, immediately before execution, and after execution. Normalization replaces CRLF with LF, matching the accepted manifest convention.
- The accepted Phase 8 tree was committed in `1bf2098`; the subsequent committed difference is the addition of `LICENSE`. No production source/test change followed acceptance.
- Initial Git status contained only the existing, untracked Qualification 1 report. It was preserved. This qualification did not stage, commit, or change branches.
- No production source, tests, adapters, resolver, lifecycle, browser ownership, or upload logic was patched. Disposable orchestration and private evidence reside under ignored `.tmp/`; this report is the new publishable deliverable.
- Git used a per-command `safe.directory` option because the execution account differs from the repository owner. Global Git configuration was not changed.

The 1,023-test baseline and supplemental final-audit tests were not rerun or represented as live evidence. Source identity was verified against their accepted tree.

## 2. Candidate-selection process

Selection used **the existing production helpers**, before nomination or any application attempt:

1. Loaded the effective configuration with process-local `auto_submit=false`, AI disabled, and Gmail/Discord disabled. The persisted auto-submit setting was already false.
2. Read `data/private/failed-batch-retirement.json` using the same membership extraction as the production batch runner: **57 excluded applications**, including 4611. Its SHA-256 remained `36e67e9832baaa864892439de57f1aaaec9f5f967cf840dffc5add4d9dc73d12`.
3. Included the fixed production exclusions `{4535, 4571, 5310, 6401, 6415, 6416}`, all **59 application IDs** with retirement/protection event evidence, and the explicit user exclusion of 4611. These sets overlap. Event evidence was supplied as exclusions rather than used to override production decisions.
4. Called `fill_batch.discover(db, config, exclusions)`. Its existing SQL and helpers enforce active listing state, fresh posting evidence, runnable status, retry eligibility/due time, manual/security restrictions, absent submission intent/confirmation, retirement settings, fixed fill-only exclusions, and shared candidate preflight.
5. Preflight used `AnswerResolver.refresh()` / the verified profile snapshot and the production `Database.submission_conflict()` policy. That checks protected SQLite records and imported application history through the shared exact/ambiguous identity policy. `verify_safety()` confirmed no controlled target and no pending manual requests.
6. The full selector returned **1,309** enrichment-permitted candidates. Every stored decision was `NEEDS_ENRICHMENT`; none had a complete positive stored eligibility assessment. Of these, **878** had a recognized ATS identity and a U.S. location according to the production identity/location helpers. These inspection counts are **not application attempts**.
7. Public evidence for the first listed Workday candidate, 6418, was inaccessible through the read-only web tool. It was not nominated or processed. Inspection continued to a publicly readable hosted form, 4615. Its employer page was retrieved without applicant data and inspected using the existing adapter's HTML inspection routine on saved public HTML.
8. For 4615, production preflight still returned `NEEDS_ENRICHMENT / eligibility_requires_enrichment`, with `proceed=true`. This is an explicitly supported production admission result, **not an assertion that all applicant qualifications are verified**. No rejection or hold was overridden. The Engine's live eligibility gate remained authoritative.
9. Before nomination, an additional scan using production `identity_match()` found no matching other SQLite application identity. Retirement events for 4615 were absent, the retirement setting was false, and the production protected-history conflict result was null.
10. Under the production worker lock, the execution harness reran `verify_safety`, `discover`, and `preflight`, then invoked `process_one(4615)` exactly once. There was no batch loop, fallback candidate, retry, reconstruction, or second qualification.

An initial attempt to use the production read helpers against an uninitialized read-only connection failed because `answer_revision` did not yet exist. No candidate was processed. A consistent SQLite backup was saved locally, then the **unchanged normal Database initializer** ran with `startup_maintenance=False`. It added the release's existing schemas, including `answer_revision`, `writing_revision`, `narrative_cache`, `listing_maintenance`, and `manual_commands`, plus their normal additive metadata/indexes/triggers. This was normal release startup, not a custom schema repair.

Startup validated 6,841 history records: zero moves, zero duplicate merges, and zero empty directories. The existing ambiguous record 4535 remained UNKNOWN and excluded. Application status counts and maximum event ID remained unchanged at this boundary. The final comparison against the pre-startup backup found **only application 4615 changed** across pre-existing application columns; 4611 was unchanged.

## 3. Selected application

| Item | Evidence |
| --- | --- |
| Application ID | **4615** |
| Company | Veeam Software |
| Role | AI & Automation Engineering Intern – Summer 2027 |
| Location | Public listing: Remote, Georgia, USA; stored location: Georgia |
| ATS / adapter | Greenhouse / `GreenhouseAdapter` |
| Canonical URL | `https://job-boards.eu.greenhouse.io/veeamsoftware/jobs/4955296101` |
| Tenant / board | `veeamsoftware`, `job-boards.eu.greenhouse.io` |
| Opportunity identity | `greenhouse:4955296101` |
| Posting evidence | `2026-09-12T01:31:42+00:00`; `repository.posted`; medium confidence |
| Freshness | Approximately 16 days old; within configured 30-day window |
| Configured season | Summer 2027; role matches |
| Initial listing / application state | ACTIVE, listing_active=1; QUEUED; zero attempts |
| Fresh open evidence | Public HTTPS GET returned 200 at `2026-09-28T07:21:21.906336+00:00`; matching role, U.S. location and application form present. Production navigation subsequently returned 200 at the same destination. |
| Preflight | Stored: `NEEDS_ENRICHMENT / incomplete_listing_evidence`; public enrichment: `NEEDS_ENRICHMENT / eligibility_requires_enrichment`; production `proceed=true` in both cases |
| Retirement / protection | No fixed/file/event exclusion, no retirement setting, no protected-history conflict |
| Duplicate / conflict | No other SQLite identity match; production conflict result null |
| Submission intent / confirmation | Null / 0 before and after execution |

Source: [employer's public application listing](https://job-boards.eu.greenhouse.io/veeamsoftware/jobs/4955296101). The saved public HTML has SHA-256 `14418638f960d96df0088c025be25e449e520670543f2f9bb2446fd38aa1605f`.

## 4. Pre-run safety checks

```text
target applications = 1
auto_submit = false (process configuration and persisted setting)
fill_only = true
MAX_ACTIVE_APPLICATION_TABS = 1
ai.enabled = false
gmail.enabled = false
discord.enabled = false
controlled_application_id = null
pending manual_requests = 0
```

The worker lock prevented concurrent AutoApply worker processing. The harness used the existing `FillDatabase` submission guards and the normal Engine, which installed its production run-wide `FillOnlyInvariant` click observer, recognized submission-request blocker, intent checks, and application-page limit. The ordinary `Engine.run()` service loop and the Discord-connected batch orchestration were not started.

Shell networking required execution permission outside the restricted sandbox. The initial public GET failed with Windows socket error 10013; the approved read-only GET then succeeded. This occurred before any application attempt. The one-shot Engine execution received network execution permission before starting. There was no approval rejection, no site-security workaround, and no browser retry caused by the sandbox.

## 5. Execution timeline

All times below are UTC on 2026-09-28.

| Time | Action / durable evidence |
| --- | --- |
| 07:19:03 | First complete production selection result recorded; no attempts |
| 07:20:20 | Selection evidence expanded with recognized U.S. candidates; no attempts |
| 07:21:21 | Public employer HTML fetched successfully; fresh evidence saved |
| 07:22:36 | One-shot execution acquired worker lock |
| 07:23:21.202 | Final production safety, selection and preflight checks passed |
| 07:23:21.243 | Single `Engine.process_one(4615)` invocation began |
| 07:23:21.263 | Claim persisted: event **8152**, CHECKING; normal `begin_attempt` subsequently changed attempts 0 → 1 before page work |
| 07:23:25.252 | Live listing assessment required verification: events **8153–8154**, NEEDS_INPUT / hold |
| 07:23:25.425 | Normal handoff persisted MANUAL_REVIEW: event **8155** |
| 07:23:26.352–26.508 | Network provenance and security diagnostics: events **8156–8157** |
| 07:23:26.713 | Production checkpoint persisted: event **8158** |
| 07:23:27.316 | Engine returned after releasing its page |
| 07:23:27.658 | Browser cleanup complete; worker paused |
| 07:23:28.106 | Worker lock released |
| 07:24:21 | Final database/checkpoint/source verification evidence recorded |

## 6. Form and answer behavior

The Engine navigated to the employer, selected `GreenhouseAdapter`, read the live listing, and ran normal `eligibility()`. It confirmed U.S. location and student-opportunity signals but returned `eligible=null` with unresolved qualification clauses. The Engine stopped **before `adapter.begin()`, question inventory, deterministic field resolution, and filling**.

- Persisted question **206**, key `eligibility`, requires user verification of listed requirements and U.S. opportunity; status PENDING. No answer was supplied.
- Employer controls visible in the saved page included contact, education, residence, work authorization, disclosure, document, demographic, consent, and final submission controls. These were not interacted with.
- The automatic stop checkpoint captured **36 raw DOM controls**: 29 text, one search, one telephone, one file, one number, two textarea, and one checkbox. This broad checkpoint snapshot is **not** the adapter's visible semantic inventory and does not establish supported controls.
- Deterministic field resolutions / committed answers: **0**. `LIVE_CONTROL_COMMITTED` and `FIELD_FILLED` events: **0**.
- Unresolved issue: eligibility. No rejected field mapping or unsupported-control result was reached.
- Provider generation calls: **0**, observed by a process-local call observer; provider instances: **0**. AI was disabled throughout. No provider authentication, novel narrative generation, verified-writing reuse, or template assembly occurred.

No private applicant facts or answer values are reproduced here. Local Engine notifications were not dispatched through external messaging services.

## 7. Upload behavior

Resume selection, upload, and acceptance checks were **not reached**. `resume_sha256` and `upload_result:4615` are null. No ATS upload receipt exists for this attempt, and upload readiness is not established. There was no advancement based on a receipt, stale or otherwise. Upload acceptance and later invalidation remain unqualified.

## 8. Navigation and security behavior

One canonical application navigation occurred. Next/Continue interactions: **0**; transition retries: **0**. Step transition behavior was not exercised.

Production diagnostics recorded main-page HTTP 200, `security_state=NONE`, and `verification_state=NOT_REQUIRED`. No blocking authentication, MFA, CAPTCHA, email/phone verification, rate limit, or automation rejection was detected. No authentication or challenge interaction was attempted.

Event 8156 records a background **GET 401** to `https://my.greenhouse.io/users/self`. This was an auxiliary account lookup, not a final submission request. The main public application remained readable; the persisted stop was eligibility input, not an authenticated-flow continuation. The masked production screenshot was inspected and corroborates the public application page and untouched form.

## 9. Browser ownership

| Measurement | Result |
| --- | --- |
| Target applications processed | **1** |
| Application pages created | **1** |
| Peak active application pages | **1** |
| Unexpected popup violations | **0** |
| Page-limit violation latch | false |
| Active pages / owned leases at Engine return | 0 / 0 |
| Owned leases after Engine close | 0 |
| Browser context closed | true |
| Worker lock released / paused restored | yes / true |

The production fill-only finalizer saved the checkpoint, cleared the temporary manual session, disabled manual resume of that closed session, and released the owned page. Normal Engine shutdown closed the context. There is no retained live application form.

## 10. Checkpoint or hold result

```text
status = MANUAL_REVIEW
application_state = MANUAL_REQUIRED
error_category = INPUT_REQUIRED
reason = Eligibility requires verification
manual_action_required = 1
retry_allowed = 0
manual_resume_allowed = 0
session_preserved = 0
attempts = 1
```

Private checkpoint: `data/private/fill-only-checkpoints/4615.json`, SHA-256 `c356ddf8bbf41792a3f6f8e2f9f88292e046cb2fd5c07c2916c5f8f0669019d7`.

The production checkpoint label is **`FILLED_CHECKPOINT`**, with `reconstruction_verified=false` and `validated_at=null`. That generic persisted label does **not** mean fields were filled in this run. Its state is MANUAL_REQUIRED. It is neither `RECONSTRUCTABLE_CHECKPOINT` nor `LIVE_READY_FOR_MANUAL_SUBMIT`. No reconstruction was attempted.

## 11. Submission-zero proof

```text
submission intents: 0 created
final submit clicks: 0
recognized final submission requests: 0
recognized final submission requests blocked: 0
max active application pages: 1
auto_submit: false
provider calls: 0
```

Evidence is the installed production `FillOnlyInvariant`, browser counters, lifecycle records, and before/after database comparison. All invariant checks passed. The six historical non-null submission intents were identical before and after execution; no SUBMITTING event was added. The only new events were 8152–8158, all for application 4615. All other application rows were unchanged during execution, and comparison to the pre-startup backup confirmed 4611 remained unchanged.

These counters cover the production classifier's recognized final-submit labels/endpoints; they are not a universal classification of arbitrary web traffic. In this run the Engine also stopped before any application-control interaction.

## 12. Deviations from offline fixtures

No demonstrated violation of the frozen runtime safeguards was observed. Public HTML preflight and live Engine assessment both conservatively required eligibility verification. One-page ownership, zero submission, persisted hold, and cleanup behaved as the production path specifies.

Two interpretation limits were observed and preserved without repair:

- The eligibility uncertainties include both qualification clauses and later benefits/compensation/attestation wording. This suggests an overly broad requirements section in this live page's extracted text. It produced a conservative hold, not inferred answers or unauthorized continuation. This run does not establish a separate fixture mismatch; any parser review belongs in separately authorized development.
- The generic `FILLED_CHECKPOINT` label was saved despite no field filling. State, null validation timestamp, absence of committed-answer events, and this report prevent treating it as successful preparation.

The initial read-only schema failure was resolved only through normal frozen startup. No live exception triggered a source repair or another application attempt. Full form, upload, conditional-control, and Next/Continue behavior remain untested by this qualification.

## 13. Qualification outcome

**QUALIFIED_SAFE_HOLD**

The production admission policy permitted one unprotected, fresh, open U.S. internship to enter live enrichment. The Engine then truthfully required eligibility input and stopped. This is a completed one-application **safe-hold qualification**, not proof of full applicant eligibility or successful fill readiness. Exactly one attempt and one application page were used; every submission counter and provider-call counter remained zero.

## 14. Recommended next action

Have the user review the actual employer requirements and pending eligibility question 206 before any separately authorized continuation. Do not automatically answer it, retry 4615, reopen its closed session, weaken preflight, or substitute another candidate. Any review of the broad eligibility parsing or checkpoint naming should occur outside this frozen qualification.

Qualification 1B is stopped. Qualification 2 was not started. Application 4611 was not processed. No submission or provider use was authorized or performed.

Supporting private operational evidence: `.tmp/qualification-1b-{startup,selection,listing-evidence,enriched-preflight,execution,final-verification}.json`, the pre-startup SQLite backup, the saved public HTML, the normal application-history bundle, and the normal fill-only checkpoint. These contain local operational evidence and are intentionally excluded from Git.
