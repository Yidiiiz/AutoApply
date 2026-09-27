# AutoApply technical audit and implementation plan

Audit date: September 25, 2026. Source baseline: `2008f09ec0ece64611bad0e432510ed2e920a38a`.

**Recommendation:** retain Python, Playwright, SQLite, the existing adapters, and the submission safeguards. First restore the failing local regression baseline; then eliminate repeated maintenance and persistence work, and consolidate answer resolution and browser inspection incrementally. The repository already uses deterministic techniques for ordinary factual answers; replacing a pervasive AI-driven engine is not the task this code actually needs.

This phase changes documentation and adds an offline measurement harness only. No production worker, live discovery, employer application, submission, private configuration update, or historical-record maintenance was run. Tests use temporary profiles/databases and local or intercepted browser fixtures. Production private data was not needed for the audit; its current contents and active-worker state are not asserted here. The default configuration's `auto_submit: true` was read as source, never enabled in production. Test fixtures exercise simulated submission in isolation.

## 1. Current architecture

### Repository and sources of truth

The application is a single Python package, with 45 Python files under `autoapply/` including the cursor package. There is no web frontend. `tests/` contains 27 test modules and three committed HTML fixtures. `scripts/` contains Windows launchers, general-looking runners, and narrowly scoped historical repair tools. `config/config.example.yaml` supplies executable defaults; `templates/profile.example.yaml` documents the profile schema. `.tmp/`, `data/`, `logs/`, build output, and virtual environments are ignored runtime/generated material.

Reviewed the README, DESIGN, SECURITY, freshness/history migration reports, controlled-resume follow-up, SmartRecruiters repair report, and current chat handoff. Older exported source in `exports/AutoApply_AI_Context.md` is a snapshot, not a second runtime implementation. Current source and tests take precedence:

- README's introductory 14-day description is stale; the enforced maximum/default is 30 elapsed days (`freshness.window`, configuration).
- README/SECURITY's generic-only SmartRecruiters description is stale; a dedicated adapter is selected now.
- The prose that new writing always requires confirmation does not describe the normal engine path: audited prose is filled automatically, while `written_responses.verified` stays zero. Explicit Discord draft acceptance is a separate route.
- The handoff describes short SQLite transactions, but `Database.flush_history` holds a write transaction throughout filesystem export and statistics work.
- Historical test totals are not this audit's baseline.

### Actual processing path

```text
__main__.main
  load .env -> Config -> Database (schema/history validation + listing maintenance)
  execute -> ProcessLock for run/scan/work-once/login/probe -> Engine

Engine.run
  recover -> Discord delivery prerequisite -> service_manual_requests
  -> scan if unpaused, uncontrolled, and no held application
      scan_github: poll revision -> parse bounded README tables
      BrowserJobSource: optional bounded LinkedIn/Handshake card discovery
      Database.ingest: source/URL/ATS dedup -> posting evidence -> freshness/closure
        -> observation only when rejected, else job + application + priority
  -> process_one (asyncio lock; one application at a time)
      claim -> freshness/retirement/controlled-target guards -> CHECKING
      open page -> navigate -> security -> bind final URL/dedup
      optional external application link -> inspect listing -> eligibility
      APPLYING -> adapter.begin -> inspect existing confirmation
      repeat bounded form steps:
        discover controls -> resolve verified answers -> fill/upload
        missing information/security/capability failure -> durable manual hold
        check uploads -> rediscover conditional fields -> validate -> Next
      final branch:
        fill_only -> READY_FOR_MANUAL_SUBMIT + checkpoint + retained sole tab
        auto_submit off -> READY + close ordinary page
        auto_submit on -> final safeguards -> commit SUBMITTING intent
          -> one physical Submit via SubmissionProbe
          -> affirmative confirmation -> SUBMITTED
          -> otherwise validation/security/unknown hold; never blind resubmit
      finally: telemetry, history, checkpoint where applicable, page ownership/cleanup
```

Relevant implementation: `engine.py:90,221,234,685,696,804,860`; `database.py:251,374`; `sources.scan_github`; `applications.adapter_for`; `submission_probe.SubmissionProbe`.

This is **not** eligibility-before-queue in the ordinary daemon: ingest checks freshness/closure but does not call `eligibility`. Full eligibility runs after browser navigation and listing extraction. `scripts/fill_only_batch.py:run` separately adds a saved-listing eligibility check before opening a tab.

### Responsibilities and coupling

| Area | Actual owner and important coupling |
|---|---|
| Orchestration | `Engine` is 906 lines; `_process_one` spans roughly 450 lines. It owns queue dispatch, listing enrichment, answer precedence, ATS branches, telemetry, uploads, submit, retries, and cleanup. |
| Storage | `Database` (511 lines) inherits `ListingStore`; generic `execute()` also triggers history export. Engine/controller/scripts issue SQL directly. |
| History | `ApplicationHistory` (443 lines) validates/migrates bundles, synchronizes DB snapshots and sidecars, preserves imported records, provides duplicate lookup, and rebuilds statistics. It is also consulted as a safety authority. |
| Browser | `Browser` owns one persistent context, page observations, upload tracking, cursor initialization, viewport settings and security-aware navigation. |
| Forms | Generic adapter extracts/fills/validates; Greenhouse shares it almost entirely, Lever/Ashby specialize entry, SmartRecruiters specializes inventory/navigation/attachment receipts. |
| Answers | `AnswerResolver`, standing/disclosure rules, dropdown preferences, and `FieldMapper` overlap in field interpretation. Profile is read on demand rather than loaded into a normalized snapshot. |
| Safety | `SecurityDetector`, `SubmissionClassifier`, `SubmissionProbe`, `ManualHandoffManager`, `RetryPolicy`, `Database.transition` and control commands jointly implement boundaries. |
| Input | `cursor/` provides serialized motion/actionability/hit tests and cleanup; `combobox.py` uses fresh Playwright locators directly for dropdown actions. Final Submit intentionally has its own single physical-click path. |
| Operator interfaces | CLI calls `Controller.command`; Discord generally calls `Controller.route`. Held pages are in memory; commands/settings/acknowledgements are durable. |
| Special execution | Fill-only runner subclasses Database, patches final Submit, disables AI, installs destination restrictions, verifies reconstruction, and sends its own question notices. Controlled runner uses the Engine but sets durable target/submit controls. |

Git operations are offloaded through `asyncio.to_thread`; DB/filesystem work remains synchronous on the event loop. Browser discovery and application work are scheduled serially. There are no parallel application workers to remove or expand.

## 2. Baseline and measured observations

### Existing tests

**Full baseline: 631 cases, 622 passed, 9 failed, 0 skipped, 0 errors; pytest exit code 1; 668.11 seconds (11:08).** The JUnit suite reports 667.858 seconds; the console wall-time summary above is retained verbatim. Started at `2026-09-25T00:21:44.432026-04:00`. This is not a green baseline.

Command launched from the repository root, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --durations=20 --junitxml=.tmp/audit-2026-09-25/baseline.xml
```

Output is preserved in `.tmp/audit-2026-09-25/baseline.log`; JUnit contains the exact collected case outcomes. Portable outcome metadata is in `test-results.json` beside this report. No test selection/exclusions or implementation edits were made for this run. Python is 3.14.3. Browser fixtures use temporary profiles, not the user's production browser profile. Most select the installed repository Chromium binaries, but six direct-Browser tests instead attempted the user-cache executable and failed to launch. AI provider execution is mocked and employer fixtures are local/intercepted; no live AI provider was invoked by this audit.

| Baseline failure | Diagnosis |
|---|---|
| `test_dropdown_browser.py::test_open_async_city_menu_is_committed_without_toggle` | Baseline `spawn EPERM` launching user-cache Chromium. With repository binary configured, exposes a committed-value/menu-closed failure. |
| `test_stable_combobox.py::test_replaced_control_delayed_options_hidden_duplicate_and_commit` | Same baseline launch failure; with repository binary configured, exposes the same menu-closure failure class. |
| `test_stable_combobox.py::test_missing_option_reports_state_without_click` | Baseline launch failure; passes diagnostic rerun. |
| `test_stable_combobox.py::test_native_select` | Baseline launch failure; passes diagnostic rerun. |
| `test_stable_combobox.py::test_multiselect_chip_keeps_menu_open[True]` | Baseline launch failure; passes diagnostic rerun. |
| `test_stable_combobox.py::test_multiselect_chip_keeps_menu_open[False]` | Baseline launch failure; passes diagnostic rerun. |
| `test_manual_submission_retirement.py::test_retired_application_blocks_before_database_or_browser_work` | Constructs `Engine.__new__` without initialization; `_process_one` now accesses missing `fill_only` before the retirement guard. Reproduces. This does not demonstrate a production retirement bypass. |
| `test_smartrecruiters.py::test_discovery_fill_and_transition` | Fixture City control keeps `aria-expanded=false` and has only an oninput opener; shared selector requires expansion after click, before typing. Deterministic fixture/control contract mismatch. Reproduces. |
| `test_smartrecruiters.py::test_normal_engine_two_steps_and_one_confirmed_submit` | Engine holds `MANUAL_REVIEW` with `KeyError during form page 2`. Earlier-step `live_committed` receipts remain after current controls are cleared/replaced; `validate -> control_state` looks up the missing earlier key. Reproduces. |

**Diagnostic rerun: 4 passed, 5 failed in 39.91 seconds**, same nine cases, no implementation/test edits, process-local `PLAYWRIGHT_BROWSERS_PATH` pointed at `data/private/playwright`. This clarifies rather than replaces the baseline. Command:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) 'data/private/playwright'
.\.venv\Scripts\python.exe -m pytest -q tests/test_dropdown_browser.py tests/test_stable_combobox.py tests/test_manual_submission_retirement.py tests/test_smartrecruiters.py::test_discovery_fill_and_transition tests/test_smartrecruiters.py::test_normal_engine_two_steps_and_one_confirmed_submit --durations=10 --junitxml=.tmp/audit-2026-09-25/failure-diagnostics.xml
```

The menu-closure failures point to `combobox.committed_state`: an emptied listbox retains layout width, so `menuVisible` remains true even after `aria-expanded=false` and the exact value has committed. A valid fix must distinguish an empty retained menu from visible selectable options; it must still reject a genuinely open or uncommitted dropdown.

A final **instrumented diagnosis of those two cases plus the SR two-step case: 3 failed in 25.27 seconds**. The temporary pytest plugin logged state without changing return values: both dropdowns had `expanded='false', value=True, invalid=False, closed=False`; SR validation had three committed receipts but one current control and tried a missing key. This confirms the specific causes above. Log/plugin are under `.tmp/audit-2026-09-25/`; this was diagnostic instrumentation, not a fix or replacement baseline.

Slowest baseline calls included `test_fill_batch::test_discovery_never_opens_tabs[100]` **43.03s**, its 57-row variant **11.70s**, auto-submit-off/revalidation **25.50s**, durable input reconstruction **24.73s**, and the submit telemetry flow **23.45s**. The batch-discovery tests individually ingest every row before their data-only discovery assertion, so their duration is not evidence that the discovery SELECT itself takes 43 seconds. Successful form tests also include configured observation windows; do not interpret total test time as pure browser fill time.

### Offline measurements

Run `docs/audit-2026-09-25/measure.py` with the project Python. It creates and disposes synthetic databases/history/profile files in `TemporaryDirectory`, never opens production storage, and never constructs a browser/provider. Raw results are in `measurements.json`. SQLite version: 3.50.4. Measurements are single samples taken while the baseline suite was running, not production throughput or stable timing thresholds.

| Measurement | 100 synthetic listings | 1,000 synthetic listings |
|---|---:|---:|
| Unchanged full cleanup | 14.626 ms | 49.346 ms |
| SELECT calls during that cleanup | 204 | 2,004 |
| UPDATE calls despite unchanged listing eligibility | 100 | 1,000 |
| 10 separately committed application events | 323.587 ms; 10 history syncs | 455.463 ms; 10 history syncs |
| Same number of events inside one existing transaction | 30.319 ms; 1 history sync | 40.617 ms; 1 history sync |
| 20 ordinary first-name resolutions | 60 profile YAML reads; 60 SELECTs | 60 profile YAML reads; 60 SELECTs |

The event comparison adds later events to the same synthetic application, so the batched case has a slightly larger history to export. It demonstrates about 10.7–11.2x lower elapsed time for **this event-writing workload**, not an application-throughput multiplier. Counting ten versus one exports is the more portable result. Cleanup still writes observations even when no `jobs` rows need changing.

Measured `EXPLAIN QUERY PLAN` results on both synthetic sizes:

- `identity_key=? OR canonical_url=?`: `SCAN jobs`, despite an existing unique identity index.
- Per-application ordered events: `SCAN events`.
- Per-job sources: `SCAN job_sources`.
- Verified writing reuse: `SCAN written_responses`.
- Exact known answer: uses the existing `(normalized_question, scope)` unique index.
- Queue: uses `listing_active_idx`, the unique application job-ID index, settings primary key, and a temporary B-tree for priority ordering.
- Daily intent count with `substr(submit_intent_at,1,10)`: scans applications.

Candidate indexes were created **only in the disposable measurement databases**. URL lookup became `MULTI-INDEX OR`; events, sources and writing became indexed searches. No production index or schema was changed. Writing's table is empty in this harness; its plan demonstrates index eligibility, not measured production selectivity. Queue timing/index choice still needs a realistic status/priority distribution.

No representative employer latency, actual queue distribution, production database contention, LLM latency, or applications/hour measurement is available from this offline audit. Expected benefits below are code-supported hypotheses unless tied to the measurements above.

`probe_policies.py` separately reproduces two policy findings using only synthetic temporary records (`policy-probes.json`): two distinct Lever requisitions with the same company/title produce a submission conflict; a field with conflicting Email/given-name signals maps to `UNKNOWN_FIELD` at confidence zero but AnswerResolver still supplies the email through its legacy label fallback. No employer page or real applicant value is involved.

## 3. Highest-impact problems, ranked

The ranking considers throughput, reliability and implementation cost. Safety defects can take priority even when their time savings are unmeasured.

### Prerequisite: restore the measured regression baseline

- **Files/functions:** `combobox.committed_state/open_and_select_combobox`; `SmartRecruitersAdapter.advance/get_questions`; generic `validate/control_state`; `tests/conftest.py`, direct browser tests, SR fixture, retirement test.
- **Current behavior:** five cases remain failing once the installed browser path is selected: two menu-closure cases, one fixture expansion mismatch, one stale earlier-step receipt, and one incomplete test object.
- **Why it matters:** an implementation known to stop on valid local controls/multi-step forms cannot be a reliable speed-comparison baseline. Environment failures masked two behavioral failures.
- **Proposed change:** narrowly repair those contracts, centralize browser-test executable selection, and preserve negative tests for false commitment, unexpected field removal, invalid sessions and retired applications. Do not weaken validation to obtain a green run.
- **Benefit:** removes reproducible local blockers and makes subsequent optimization results assessable; no numerical throughput promise.
- **Risk:** medium. Scope receipt invalidation only to an affirmatively observed step transition; upload receipts and same-step tamper checks must survive. This is the revised recommended Phase 2.

### 1. History export and statistics amplify tiny writes

- **Files/functions:** `database.py:174` `flush_history`, `:199` `execute`, `:242` `update_security`; `archive.py:382` `sync`, `:259` `_statistics`; `history_statistics.calculate_statistics`; Engine `inspect_security`, `check_uploads`; SubmissionProbe event callbacks.
- **Current behavior:** DML outside an explicit transaction calls `flush_history`. Dirty-table triggers cause a full application bundle refresh; every sync calculates statistics across all cached history records. Each export rereads all application events/questions/transitions/sources. `job_description.txt` is always rewritten; statistics always include a fresh `generated_at`. Even writes to settings/notifications call an empty-history `BEGIN IMMEDIATE`/commit. Repeated security inspections update timestamps and export even when the state is identical.
- **Cost:** filesystem reads, JSON serialization, fsyncs, historical aggregates, and DB write-lock time on the asyncio event loop. Submit observation callbacks themselves can spend time exporting while trying to observe a short-lived event.
- **Change:** first batch logically related DB-only writes into short existing transactions, avoid unchanged security writes, remove redundant explicit archive calls, and separate diagnostics timestamps from state transitions. Later make history export an explicit durable dirty-queue boundary. Do not defer submission intent, confirmation or holds. Moving export outside the DB lock requires a revisioned snapshot/ack protocol first.
- **Benefit:** measured ten-to-one export reduction in the event workload; likely better browser/Discord responsiveness.
- **Risk:** high for deferred export; medium for scoped batching. Crash recovery, imported metadata, lock ordering, and exact submitted-history preservation must remain tested. Never hold a transaction across browser awaits.

### 2. Idle/discovery/queue paths repeatedly walk the entire listing store

- **Files/functions:** `Engine.run/scan`, `ListingStore.cleanup_stale_listings/refresh_listing_statistics`, `Database.claim`, `Controller.pending/command('status'|'queue')`.
- **Current behavior:** every eligible worker cycle calls `scan`; cleanup runs before and after it even when each source returns immediately because polling is not due. A due claim calls cleanup again. A status request cleans up (which refreshes statistics), then refreshes statistics again. Each cleanup does per-job date/settings/observation reads, unconditional observation updates, and full-history aggregate work.
- **Cost:** O(all stored listings + observations + history) work at roughly each two-second idle-loop wakeup, rather than at source cadence or freshness deadlines. Historical inactive rows stay in those scans.
- **Change:** one maintenance owner, due-time scheduling, update-if-changed observations, one statistics refresh per dirty batch, then indexed expiry candidates. Always keep live freshness evaluation at claim, application start, and the final submission boundary; a cached active view is never permission to submit.
- **Benefit:** measured 2,004 SELECTs and 1,000 redundant updates avoided per unchanged 1,000-row sweep; fewer idle writes and status delays.
- **Risk:** medium. Use exact UTC cutoff/clock injection tests, strict windows, restart behavior, unknown dates, and preserved holds. Do not clear controlled targets to make maintenance run.

### 3. Overlapping browser snapshots, enumeration and control verification

- **Files/functions:** `browser.page_condition`, `Engine.security_gate/inspect_security/_process_one`, `SecurityDetector.detect/SNAPSHOT`, `SubmissionClassifier.pre_submit`, `GenericApplicationAdapter.get_questions/answer_question/validate`, `SmartRecruitersAdapter.get_questions`, `cursor.controller._move`.
- **Current behavior:** navigation runs `page_condition`; Engine immediately takes another security snapshot. Each step does both again. Every field answer takes a full security snapshot. Full form discovery runs again after filling. `SNAPSHOT` already runs normalized validation, while `pre_submit` calls adapter validation again. Cursor motion evaluates viewport and all relevant frame geometry at each path point.
- **Cost:** browser-protocol round trips and repeated visible-text/style/layout scans. SmartRecruiters evaluates metadata, visibility, hidden ancestry and marker assignment separately per field. Generic dropdown discovery opens/searches/closes menus that filling then reopens.
- **Change:** immutable, per-inspection snapshots shared among classifiers at the same boundary; pure classification functions over one snapshot; incremental form metadata/commit receipts with explicit invalidation; bulk SmartRecruiters metadata collection that still traverses open shadow roots. Keep fresh security, geometry and attachment checks at mutation boundaries.
- **Benefit:** likely largest browser-side opportunity; no live DOM-scan timing claim yet.
- **Risk:** high if caching crosses a mutation/navigation/dialog/network change. Never cache ElementHandles or assume a changed form remains valid.

### 4. Repeated fill-only reconstruction doubles successful preparation

- **Files/functions:** `fill_batch.verify_reconstruction`, `scripts/fill_only_batch.py:run`, `scripts/restore_fill_only.py`.
- **Current behavior:** every ready application is navigated back to its canonical URL, upload tracker reset, answers replayed, resume reselected and upload validated, then checkpointed again and closed. `smoke_test` is recorded but does not gate this call to just the first ready application.
- **Cost:** a second complete fill/upload pass for each successful item, plus a third-or-later checkpoint. Necessary evidence for the current closed-tab recoverability claim, but expensive.
- **Change:** explicitly distinguish a saved-answer checkpoint from a verified recoverable draft and a currently live ready form. Default sequential preparation can save truthful checkpoints without claiming reconstruction is verified; only run reconstruction when that stronger guarantee is requested. If ready-for-immediate-manual-submit is required, stop with the one live tab instead of claiming several live ready forms.
- **Benefit:** can remove approximately one full preparation pass per item; actual throughput unmeasured.
- **Risk:** medium/high. Do not simply delete replay and preserve the old `reconstruction_verified` or readiness claims. One active application tab remains a hard limit.

### 5. Filtering and identity safeguards are inconsistent across execution modes

- **Files/functions:** `Database.ingest/claim/submission_conflict/bind_url`, `jobs.eligibility/ats_identity`, fill-batch `discover`, controlled runner candidate SQL.
- **Current behavior:** ordinary Engine checks full eligibility only after opening/navigating; the fill-only script checks saved metadata earlier. `submission_conflict` scans prior attempted/held applications late and also matches normalized company + title + ATS, so a different requisition with the same title can be blocked. Ingest dedup otherwise prefers exact identity. Three candidate-selection SQL copies drift in confirmation/retirement filters.
- **Change:** one data-only candidate policy plus a conservative preflight based on sufficiently complete saved evidence. Reject definite foreign geography/known incompatible requirements/protection/known closure before opening; retain unknowns for enrichment. Use canonical URL and extracted tenant/requisition IDs for conflicts; ambiguous legacy title matches should be a separately explained review signal, not silently considered the same requisition.
- **Benefit:** saves whole browser sessions for definite rejects and avoids false duplicate holds. Add URL/source/event indexes from section 7.
- **Risk:** medium/high. Incomplete repository cards must not cause irreversible ineligibility. A non-intern title alone can be insufficient when the missing description might explicitly allow students. Keep imported-history and ambiguous-submission protection.

### 6. Answer interpretation, policies and provenance have several representations

- **Files/functions:** `Config.profile`, `AnswerResolver.resolve`, `answers.concept/scope_for`, `field_mapping.*`, `dropdowns.*`, `Database.question/save_answer`, `Controller.answer`, SmartRecruiters preflight.
- **Current behavior:** a simple field reads profile YAML three times; dropdown/disclosure paths can read more. SmartRecruiters preflight resolves fields and then filling resolves them again, including usage/event side effects. Field mapping uses different normalization and contact-name aliases from the legacy concept/profile paths. A reproduced correctness gap: `UNKNOWN_FIELD` from conflicting mapper signals falls back to `concept(q.label)` and supplies a profile value anyway. Only semantic-key questions require the exact stored answer signature for legacy-memory reuse. `Question` semantic keys and `Answer.evidence` are not columns in persisted questions; replay retains source/confidence but loses structured evidence.
- **Change:** one immutable profile snapshot/revision per attempt or resume, a normalized field descriptor, pure resolution followed by explicit record/use, versioned exact signatures for every adapter, and structured provenance persisted alongside answers. Keep unknown/null distinct from false. Explicit user changes invalidate caches before continuation.
- **Benefit:** measured profile reads eliminated within a step; fewer queries/events; consistent factual answer reuse without AI.
- **Risk:** medium/high for precedence changes. Standing eligibility currently precedes exact memory; disclosures precede `REQUIRE_USER`; cached answered rows can bypass changed policy in the Engine. Characterize these cases and make policy precedence explicit before changing it.

The conflict-abstention defect is a correctness priority, not a reason to use AI for the field. Include a focused resolver guard/regression with the initial repair phase; broader precedence/schema consolidation remains Phase 5.

### 7. State, manual commands and retry ownership are only partly centralized

- **Files/functions:** `Database.transition/update_security/fail/recover`, `Controller.route/command`, `Engine.resume_manual/service_manual_requests`, `ManualHandoffManager`, scripts.
- **Current behavior:** transition has final-state/evidence guards but no allowed-transition graph. Status is duplicated in jobs, applications, application_state and history mappings; strings extend beyond enums. CLI/Discord command routes differ. Discord `resume` queues a bare `inspect` action; controlled sessions require tokenized JSON, so this path cannot reliably service controlled resume. `inspect-manual` is supported by `command` but omitted from `route`'s allowlist. Recovery/explicit retry use paths distinct from transient failure policy.
- **Change:** a small lifecycle service with typed commands/events and existing status projections; one tokenized manual command factory used by CLI and Discord; one failure classifier and retry decision per application attempt. Preserve separate application/security/verification dimensions.
- **Benefit:** fewer contradictory operator outcomes and recoverability errors, easier safe optimization.
- **Risk:** high. Add command parity/hold/intent tests before altering state schema or legacy aliases. Do not reset retry permission just to make a stuck item runnable.

### 8. Narrative overhead is narrow, but reuse and optional-field behavior need a policy

- **Files/functions:** `AIManager.draft`, `BrowserAIProvider.generate_response`, `CodexWritingProvider.generate_response`, `answers.written_reuse`, Engine narrative branch.
- **Current behavior:** two generation calls per new successful narrative, plus provider startup each time. Optional narrative failure also sets `missing=True` and holds the application. Verified writing reuse keys on exact question/company/title but not requisition, description or profile revision.
- **Change:** exact verified templates first; cache generated-and-audited prose only under a complete context/fact/policy hash and keep it unverified. Add explicit policy for optional narrative fields. Cache safe deterministic provider readiness for a short bounded interval, with failure invalidation.
- **Benefit:** fewer AI calls on unchanged context and fewer optional-field stalls, without factual generation.
- **Risk:** medium. Never reuse employer-specific text solely by title, promote generated claims to verified facts, or delete grounding checks for novel prose.

### 9. Incident operations and cleanup complicate the shared runtime

- **Files/functions:** `Engine.resume_manual` upload-module reload branch; `fill_batch.EXCLUDED`; `batch_approval.ApprovedDestinations`; `eligibility_repair.repair_5310`; special scripts; `Engine.close`, `Browser.close`, `_process_one` finally.
- **Current behavior:** engine can replace a live upload tracker's class through `importlib.reload`; batch approval requires exactly five destinations; exclusions are historical IDs. Normal worker/CLI both call Engine close. Browser close does not put Playwright stop in a finally if context close fails; an archive failure in `_process_one`'s finally can prevent page closure.
- **Change:** quarantine historical operations behind explicit maintenance entrypoints, preserve exclusion/retirement data durably, and give page/context/process cleanup one owner with nested try/finally. Keep diagnostic failures from skipping input cleanup or losing confirmation.
- **Benefit:** simpler runtime reasoning and fewer orphaned sessions.
- **Risk:** medium/high. Existing historical restrictions must remain at least as strict; moving code is not permission to reopen or alter any protected application.

## 4. AI audit: all meaningful paths

A source search of `autoapply/` and `scripts/` found two direct provider generation call sites, two application-level draft callers, and one injectable semantic-classifier hook. No LLM-based URL normalization, deduplication, eligibility, field-value lookup, date parser or dropdown selector is currently used.

| Path | Classification | Disposition |
|---|---|---|
| `Engine._process_one` -> `self.ai.draft` (`engine.py:422`) | Necessary narrative generation when rules/verified answers/reuse cannot supply a writing prompt | Remain as last resort; cache by full context; explicit optional-field policy. Ordinary factual prompts must never enter this route. |
| `Controller.draft` (`control.py:80`) -> AIManager; Discord AI button and `!ai` | User-requested narrative generation | Remain. Save proposal separately; acceptance uses normal verified user-answer flow. |
| `AIManager.draft` -> `provider.generate_response(request)` (`ai.py:137`) | Necessary novel narrative generation | Use verified template/reuse before invoking; not necessary for every application. Remain for bespoke prose. |
| Same function -> `generate_response(task='verify_grounding')` (`ai.py:147`) | Semantic classification/entailment audit of generated claims | Remain for novel model prose. Cache with the exact answer, source set, question and policy. Deterministic ID/quote/length checks supplement but do not prove entailment. It uses the same configured provider, despite the prompt calling it an independent auditor. |
| `BrowserAIProvider.generate_response` (`ai.py:44`) | Transport for either generation task | Potentially replace transport when an already authorized text provider is suitable. Each call opens/navigates/closes a page; up to 90 two-second polls, each with security/model health scans; >=3 stable comparisons before parsing. Not a separate field-answer AI path. |
| `CodexWritingProvider.generate_response/run` (`codex_writer.py:41,19`) | Text-only CLI transport for either generation task | Remain as supported option; each request runs `login status` and an ephemeral exec. Two requests mean two login checks and two execs. Login status is deterministic and potentially cacheable; generation is not. |
| `FieldMapper.map` -> optional `fallback(safe_metadata)` (`field_mapping.py:94`) | Semantic field classification, never applicant-answer generation | Dormant in production construction: SmartRecruiters creates `FieldMapper(name)` with no fallback or allowlist; only tests inject one. Keep deterministic mappings/aliases first. Do not introduce a classifier simply because the hook exists. Cache validated classifications if later explicitly configured. |

**Definitely removable AI calls:** none proved redundant as a live model call in the current code. Repeated unchanged narrative calls are potentially removable through precise caching. Deterministic provider health checks and exact answer lookup should stay outside AI. Label normalization, aliases, field signatures, full-clause regex, posting-date parsers, dropdown preference matching and exact verified memory already exist and should be consolidated rather than replaced with prompts.

`AIManager.draft` checks writing classification and verified facts, then attaches education/skills/interests and current listing context, reads style samples, filters provider tier/billing, validates structured output and word/character limits, checks supporting quotes/IDs, stores an unverified response plus grounding event, and returns `Answer(source='grounded_ai:...', confidence=1.0)`. That 1.0 is a program-assigned gate value, not measured model certainty. Generated prose never automatically enters `known_answers`; user acceptance does. Reuse also happens before the `ai.enabled` provider loop, so disabling AI generation does not disable verified writing reuse.

### Actual field/answer precedence

1. Adapter inventory derives field identity/type/options/scope. Generic uses `scope_for(concept(label))`; SR also adds mapped semantic_key. Known concepts generally get global scope; other questions get `application:<id>`, not an employer-wide memory scope.
2. Engine creates/refreshes the question signature. It may reuse a saved combobox answer or existing ANSWERED row before calling the resolver, validating the value against current question constraints. Replayed answers restore source/confidence, not structured `Answer.evidence`.
3. Resolver computes semantic key and field policy from repeatedly loaded profile YAML; DO_NOT_ANSWER stops resolver work. Full-clause standing eligibility assertions are then tried before exact verified memory.
4. Exact known_answers lookup checks normalized label + scope + verified, and checks a signature stored separately in settings. Generic unsigned legacy memory is still accepted when semantic_key is absent. Successful reuse updates usage_count/last_used_at.
5. Service/survey consent, employer-aware standing disclosures, then dropdown preferences are tried. Only afterwards does REQUIRE_USER stop further profile fallback. Exact employer-specific memory precedes standing disclosures, but not standing eligibility assertions.
6. Resolver maps canonical concepts to profile paths, overlays user-provided DB facts, handles bounded citizenship/sponsorship/full-name derivations, exact option fitting, configured locations/disclosure preferences, term-specific availability and scoped verified common_answers. Missing values remain unknown; matching never asks AI to invent a fact. Whole-profile normalization is not currently a separate load step.
7. If unresolved, Engine tries writing-bank reuse for textarea, then guarded AI narrative generation; otherwise required/unverified-prefilled fields become input requests. Optional ordinary empty fields can be skipped; failed optional narrative generation currently also pauses.
8. User answers arrive through Controller, receive validation, may update known_answers/verified_fact or verified written_responses, and may queue a resume only under current mode/session rules. Controlled holds require explicit session-bound continuation. Narrative source/audit events remain distinct from user verification.

Cache normalized descriptors, option aliases, exact resolution inputs and profile revisions; record memory usage/provenance separately after an answer is actually used. Never memoize a resolver that writes usage counters/events as though it were pure, nor cache across changed options, facts, field policies or employer scope.

Cache design should include canonical application/requisition scope, full question/type/options/limits, description hash, verified facts/profile revision, provider/model, rule/prompt version and output/audit hash. Template slots must draw only from verified facts and listing facts, with unresolved slots causing abstention. Fuzzy matching may rank candidate **field concepts or observed option aliases** with ambiguity rejection; it must not infer legal/eligibility answers, invent facts, or merge requisitions.

## 5. Browser performance and ATS audit

### Work inventory

| Operation | Current cost/evidence | Smallest safe improvement |
|---|---|---|
| Security inspection | `SNAPSHOT` walks visible text, ancestors/styles, class/id/script/frame markers and validation; called from browser, adapter, engine and cursor | Share one fresh result among readers at one boundary. Invalidate after UI/network/dialog/navigation changes; preserve a final security check immediately before input. |
| Form rediscovery | Generic extraction retags controls and reads body per relevant frame; post-fill extraction repeats it. Conditional-field loops can repeat up to `max_pages=12` | Separate stable metadata from current values/options; use an explicit step/form generation. Conditional fields must still be discovered and answered. |
| Generic combobox inventory | Opens, sometimes types, polls up to 50 x 100 ms for stable preferred choices, reads options, closes; commit opens/filters again | Pure metadata inventory and one observed option search at fill time. Verify exact committed label/value; preserve async options and multiple-menu scoping. |
| SmartRecruiters inventory | Per-field locator calls/evaluation, plus token mutations and emitted full inventory on rediscovery | Batch metadata in the page with open-shadow-root support. Emit inventory only when signature changes. Keep unsupported-required failures. |
| Dropdown commit | Shared `combobox.py`, fresh locators, exact option click once, up to 8-second state wait in 20 ms intervals; fallback reopening reads `aria-selected` | Keep fresh locators and one selection attempt. Reuse resolved locator descriptions, bound the whole operation by one deadline, prefer observable committed-state waits. |
| Validation | Every committed control is reread; attachments reread; full form/alerts inspected; classifier also calculates validity | One validated receipt per unchanged generation; independent final validation remains. Do not remove the late validation/upload checks merely because earlier ones passed. |
| Upload waits | Greenhouse can wait 180 seconds for attachment DOM; shared wait also allows 180 seconds, polls UI/security every 100 ms and requires 500 ms stability per call | One upload lifecycle deadline and evidence revision, event-driven network completion plus DOM state. Repeated ready checks need not restart a stable interval if no relevant evidence changed. Late failure/replacement invalidates immediately. |
| Resume file | Engine reads the PDF for magic header, again for SHA-256, then again before submit to detect replacement | One file snapshot/hash per version; retain final change detection and the intended exact path. Never treat selection alone as acceptance. |
| Next | Generic waits DOMContentLoaded + mutation marker up to 5 seconds; SR checks stable step, sleeps .15/.1/.2 seconds and samples up to 100 iterations | One shared next-step transition contract with ATS-specific signature. No repeated Next after uncertain delivery. |
| Cursor motion | 12–60 points; 250–1,000 ms default motion, viewport/frame-geometry work per point; up to four pre-press replans | Measure first; consolidate geometry generation checks, keep fresh hit testing and input cleanup. No anti-bot behavior or timing randomization. Final Submit already uses its dedicated single `mouse.click`, not this full motion loop. |
| Post-submit | Observe for configured 15 seconds even after confirmation; snapshot + security persistence roughly every 500 ms or DOM change | Keep the window for delayed verification, but avoid unchanged DB/history writes and redundant snapshot readers. Do not terminate observation early just for speed. |
| Navigation | No generic reload-on-error loop found. Extra pass is explicit fill reconstruction; BrowserJobSource redundantly runs `page_condition` after `navigate` | Remove same-boundary redundant inspection, not deliberate enrichment/external-link navigation. |
| Viewport | `normalize_zoom` reads identical before/after geometry without an intervening operation | One diagnostic read is enough; retain zoom mismatch failure. |
| Tabs | Normal workflow serializes application processing; fill-only also enforces max 1 context page and rejects popups. AI browser transport opens extra pages in the shared context; actual batch runner disables AI | Keep the one-active-application-tab invariant. Enforce policy centrally for any direct `Engine(fill_only=True)` caller; do not add parallel browsers or hide extra tabs behind an adapter. |
| Cleanup | Engine finally, handoff release, fill reconstruction, browser close, source/provider finally, CLI/run finally | One explicit page lease/ownership model and unconditional process cleanup, preserving held pages until explicit shutdown/release. |

The extra 60-second application delay is a configured pacing ceiling in normal `Engine.run`, not a hidden per-field sleep. Do not promise throughput beyond it without a deliberate policy/config change. The fill-only script overrides it to zero and does not use the daemon's scheduling gate. Worker loop is 2 seconds; manual command polling is .5 seconds. Discord uses 1-second delivery spacing, 5-second polling and a 55-second forbidden-delivery delay; these are independent service waits, not reasons to retry employer actions.

### Shared versus ATS-specific behavior

| Adapter | Actual specialization | Keep specific / consolidate |
|---|---|---|
| Greenhouse | Empty subclass; generic code detects React upload container, handles removed file input; UploadTracker checks successful S3 activity | Keep attachment selectors and S3 receipt interpretation in a GH upload policy; share answer resolution, control commits and lifecycle. Empty subclass itself is not a defect. |
| Lever | `begin()` clicks a unique Apply link | Keep entry behavior; all normal controls already shared. Add a dedicated fixture before changing it. |
| Ashby | Application tab/button entry; generic extractor skips autofill uploader and infers required radio metadata; tracker recognizes GraphQL upload metadata and Replace UI | Keep observed ATS semantics/operations; move them behind hooks only when editing this code, not for aesthetic uniformity. |
| SmartRecruiters | Whole-page/open-shadow inventory, deterministic FieldMapper, capabilities, exact Next signatures, carried same-session attachments; Engine contains many `adapter.name` branches | Keep discovery and step/receipt semantics. Expose `preflight`, `capabilities`, `advance`, upload evidence and event hooks so Engine need not know the name. Do not replace with first-form generic extraction. |
| Generic/other ATS | Generic first-form/main/body extraction; unsupported controls or ambiguous action pause. Recognition does not guarantee supported automation | Keep conservative fallback. Reuse proven capabilities; do not label every recognized ATS supported. |

ATS detection has two intentional responsibilities: `providers.detect_ats` recognizes broader host/DOM evidence; `jobs.ats_identity` extracts stable opportunity identity and also chooses the adapter. They disagree on supported domains/markers (e.g. recognition knows domains not handled by identity). Share a provider registry with separate recognition and requisition parsers; **do not** use a low-confidence DOM marker as a deduplication identity. The generic Greenhouse suffix check lacks the explicit dot boundary used by the other parsers; add boundary cases when consolidating URL identity.

## 6. State and retry architecture

### Current states and writers

| Dimension/store | Values or meaning | Writers |
|---|---|---|
| `applications.status` and mirrored `jobs.status` | DISCOVERED, QUEUED, CHECKING, CLOSED, INVALID, DUPLICATE, INELIGIBLE, READY, APPLYING, NEEDS_INPUT, AUTH_REQUIRED, MANUAL_REVIEW, ALREADY_APPLIED, SUBMITTING, SUBMITTED, FAILED, RETRY | Ingest; `Database.transition`; recovery/retry/fail; Engine, Controller, handoff, fill reconstruction, scripts through transition. Pinned eligibility repair performs direct status SQL. |
| `application_state` | DISCOVERED, OPENED, FILLING, READY_TO_SUBMIT, READY_FOR_MANUAL_SUBMIT, SUBMITTING, SUBMITTED, FAILED, MANUAL_REQUIRED, UNKNOWN, RATE_LIMITED, plus terminal listing/application outcomes | `STATE_MAP` in transition and explicit `update_security` calls. `ApplicationState` enum omits several persisted values, including READY_FOR_MANUAL_SUBMIT. |
| `security_state` | NONE, PASSIVE_PROTECTION_DETECTED, INTERACTIVE_CHALLENGE, RATE_LIMITED, SPAM_REJECTED, AUTOMATION_REJECTED, EMAIL_VERIFICATION, PHONE_VERIFICATION, IDENTITY_VERIFICATION, FRAUD_REVIEW, UNKNOWN_SECURITY_FAILURE | Engine inspection and history/schema migration; diagnostic/incident scripts. |
| `verification_state` | NOT_REQUIRED, PENDING, PASSED, FAILED, SKIPPED, UNKNOWN | Handoff request, Engine post-submit/manual resume, Controller reconciliation. |
| Listing state | ACTIVE, STALE, CLOSED, REMOVED, UNKNOWN; separate FRESH/STALE/UNKNOWN_DATE and `listing_active` | `Database.ingest`, `ListingStore.initialize_listings/cleanup_stale_listings/guard_listing/mark_listing_closed`. Application history remains durable. |
| Questions | PENDING, ANSWERED, SKIPPED | `Database.question/save_answer`; Engine pending/skip SQL; Controller answer/skip. Signature changes invalidate old answers. |
| Submission evidence | Intent timestamp, confirmation flag/text/URL/time, telemetry settings/events | Engine durable intent/confirm, SubmissionProbe, Controller reconciliation, migrations. Intent and confirmation are not equivalent. |
| Retry/hold | attempts, retry_at, retry_allowed, error_category, manual_action_required/reason, manual_resume_allowed | Database + Engine + handoff + Controller + scripts. No single object validates all combinations. |
| Live session | in-memory page/session maps, `session_preserved`, `manual_session:<id>` lifecycle/busy/token | Handoff, Engine manual service/finally/close, batch reconstruction, recovery. Persisted flags cannot restore a process. |
| Manual commands | `manual_requests` with legacy bare action or JSON token/action/id; `manual_ack:<id>` IN_PROGRESS/ACKNOWLEDGED/FAILED | Controller producers, Engine consumer; requests deleted before page access to prevent replay after crash. Crash after deletion may leave no final acknowledgement. |
| SR progress | `application_transaction:<id>` stage/step/next count/inventory | Engine adapter events. STEP_DISCOVERED, STEP_FILLED, UPLOAD_SELECTED, UPLOAD_READY, STEP_ADVANCED, READY_TO_SUBMIT, MANUAL_INTERVENTION_REQUIRED supplement core state. |
| Batch results | PREFLIGHT, PROCESSING, QUEUE_EXHAUSTED, FIVE_READY, global/smoke failures, checkpoint readiness | BatchReport and scripts; these are run summaries, not application outcomes. |

Ordinary SQL status changes largely go through `transition`; the direct production-package exception is `eligibility_repair.py:52–54`, explicitly limited to one historical input. Many non-status SQL writes bypass an application repository API: Engine stage/resume metadata, Controller eligibility overrides, URL binding, questions, and archive screenshot-path repair. These matter to transaction and history consistency even when they are not raw status changes.

`NEEDS_INPUT` often becomes `MANUAL_REVIEW` immediately when a live page is held, with `INPUT_REQUIRED` supplying the meaning. `READY` maps to READY_TO_SUBMIT even with automatic submission off; fill-only later overwrites the projection to READY_FOR_MANUAL_SUBMIT. `MANUAL_REVIEW` covers unsupported UI, missing facts, security, user stop, and submission ambiguity. `ALREADY_APPLIED` with affirmative confirmation can be represented as SUBMITTED by history normalization, so history and SQL status counts are not directly comparable. These are reasons for a centralized projection, not for flattening security and verification into one enum.

Recommended lifecycle service: typed operations such as `claim`, `record_input_hold`, `record_security_hold`, `record_ready`, `record_intent`, `record_confirmation`, `schedule_retry`, `request_manual_inspection`, `reconcile`. Each validates the combined evidence/hold state and writes one atomic mutation. Keep legacy status fields as projections during migration. Confirmation and retirement guards are monotonic; explicit evidence-backed reconciliation is the only route to clear an uncertain intent. Listing cleanup must never drive application transitions except an explicit attempt guard stopping untouched work.

### Retry/wait inventory and multiplication

| Layer | Current bound | Retry or observation? |
|---|---|---|
| `RetryPolicy.decide` + `Database.fail` | network/site/rate only; 60s exponential + <=5s jitter, cap 1,800s; attempts > max_retries stops | Central application retry already exists. Default 3 retries permits four claimed attempts. Any submit intent vetoes it. |
| `Engine._process_one` | one attempt; generic exceptions classified partly by class name/network flag | Routes to fail or hold; no separate whole-application inner retry loop. Timeout categories can conflate locator/site/network failures. |
| `Engine` form loop | `max_pages=12` including conditional rediscovery | Progress budget, not twelve allowed submissions. Repeated indistinguishable signatures stop. |
| Cursor `_arrive` | max_replans+1 = 4 before press | Target resolution/movement recovery. Each includes actionability, geometry, scroll and hit tests. Never multiply into post-intent click retries. |
| Geometry/point/scroll | 1.5s geometry sampling; 3s actionability calls; up to 10 point attempts; bounded scroll steps and three scroll mechanisms per container | Local recovery; nested waits can exceed an apparent per-action deadline. |
| Combobox | multiple individual 8s `expect`/click waits plus own 8s loop; discovery separately up to 5s | One option selection, repeated observations; nominal timeout is not one shared end-to-end deadline. |
| Upload | GH acceptance up to 180s, shared readiness up to 180s, shared check repeated before final action | Observations; not a blind reupload loop. Reupload occurs on full attempt reconstruction/new document. |
| SmartRecruiters Next | one click; <=100 transition samples | Observation only. Ambiguity holds, never auto-clicks Next again. |
| AI | sequential eligible providers, generation then audit; browser 90 x 2s; CLI per-process timeout default 180s | Independent text-service fallback, no employer action retry. |
| Archive | rename seven attempts, sleeps .05/.1/.2/.4/.8/1 seconds; history-lock acquisition .05s polling <=30s | Local I/O contention. Multiplied by synchronous export count. |
| Services | daemon 2s wakeups; manual .5s; Discord polling/delivery; source polling and 120s Git subprocess timeout | Service scheduling; distinguish from application attempts. |
| Windows launcher | Task Scheduler RestartCount 3, one-minute interval | Process restart invokes recovery. Pre-intent crash recovery does not consult RetryPolicy's budget. |

Potential multiplication is **application attempts × repeated pre-submit form work × cursor/local waits × filesystem exports**. There is no evidence of a controls × adapter × engine × worker chain each retrying final Submit. `SubmissionProbe.physical_click` is guarded to one call. Do not introduce a generic retry decorator around it.

Extend the existing RetryPolicy rather than add a competing one: typed failure with operation, stage, delivery (`not_started`/`possibly_delivered`/`observed`), retryability, attempt count and deadline. Application runner owns the one attempt budget; controls own bounded pre-action observation only. Recovery must explicitly account for attempts. Security, unknown input, controlled holds, ambiguous Next/Submit, and post-intent failures always require intervention/reconciliation. Do not use string matching on exception names to infer retry safety.

## 7. Database, discovery and queue improvements

### Concrete query/index work

| Query/path | Evidence | Proposed change and validation |
|---|---|---|
| Ingest/bind identity OR canonical URL | Measured full jobs scan | Nonunique `jobs(canonical_url)` enables the existing identity index's OR plan. Do not add UNIQUE without auditing legacy duplicates. Verify tracking aliases and distinct requisitions. |
| `events WHERE application_id=? ORDER BY id` in every export/inspect | Measured full scan | `events(application_id,id)` or the narrower rowid-backed equivalent after plan comparison. Compare mixed-app distributions and insert overhead. |
| `job_sources WHERE job_id=?`, bind source movement, cleanup max(last_seen) | Measured full scan; unique index is source-name/source-ID instead | `job_sources(job_id)`; retain the source identity uniqueness constraint. |
| Verified writing reuse | Measured scan; exact question/company/title/verified query | Partial index on `(question,company,job_title,id DESC) WHERE verified=1`; later extend context keys when fixing reuse semantics. Candidate plan verified, performance not yet measured on populated writing. |
| Exact answer lookup | Already indexed unique `(normalized_question,scope)` | Keep; no duplicate exact-answer index. Move signature/provenance from settings into a typed answer schema only with compatibility migration. |
| Queue priority selection | Current plans use active-listing/job-ID/settings indexes but sort | Compare a partial active-job priority index and runnable-application index with realistic ready/retry/terminal distribution. Do not assume either removes sort across joins; preserve due-time filtering. Add LIMIT/keyset pages to batch discovery rather than loading every candidate per item. |
| Submission conflicts | Fetch all attempted/held apps, normalize every URL/title, then scan history records | Exact indexed identity/URL conflict query; cached/incremental imported-history identity index. Retain unresolved aliases as review evidence. Current broad same-title rule needs behavior tests, not just an index. |
| Daily count | `substr` scan | UTC half-open timestamp range plus partial index on non-null intent timestamp. Preserve count of uncertain intents and timezone semantics. Benchmark before committing. |
| Outbox | pending notifications ordered by ID; all deliveries retained | Candidate partial `(id) WHERE delivered_at IS NULL`; verify pending/mostly-delivered distribution. No need to index tiny manual_requests beyond its existing primary key without evidence. |
| Recent/aggregate reports | ORDER BY updated_at / GROUP BY status; Python full-history statistics | Reuse one read model; benchmark updated_at index and incremental counters only if reporting remains material after export batching. |

No redundant explicitly declared index was proven removable. `app_queue`, `questions_pending`, `listing_active_idx` and `history_transition_app` serve different predicates; not every synthetic plan choosing one proves another useless. Existing unique constraints must remain. SQLite replacement has no supporting evidence.

### Writes and transactions

- `ingest` writes `listing_max_age_days` per listing, even in a batch; establish once per configuration revision/batch.
- `job_sources.last_seen`, listing timestamps and observation duplicate counts are useful provenance, but can be coalesced once per source batch without losing sightings. Do not treat duplicate events and unique observations as interchangeable counters.
- No-op cleanup updates all observations; apply a changed-value predicate, hoist `listing_days`, and age only candidates whose state could change.
- `inspect_security` writes `diagnostics_at` and `updated_at` each time; persist state changes immediately, and separate bounded observation diagnostics from history transitions.
- `save_answer` stores value/source/confidence but ignores `Answer.evidence`; explicit provenance storage should accompany resolver consolidation, including replay and archive round trips.
- `_initialize_history` validates the entire history even with `startup_maintenance=False`; that flag only suppresses listing cleanup. CLI status/helpful read commands that construct Database are consequently not pure reads. Provide an explicit maintenance path and a read-only status opener later; never instantiate current Database against production merely to benchmark it.
- `flush_history` intentionally locks DB before history. Preserve this lock order. An asynchronous exporter needs durable revisions so an older snapshot cannot acknowledge newer writes; do not simply remove the lock or drop dirty rows.

### Early-rejection matrix

| Check | Ordinary current timing | Target timing |
|---|---|---|
| Same source, canonical URL, ATS/requisition duplicate | Ingest, then bind after redirect | Keep; index and share identity implementation. |
| Already submitted/protected exact application | Queue status/intent/retirement filtering; stronger conflicts late | Shared data-only preflight before browser, plus final atomic conflict check. |
| Historical exclusion IDs | Fill-only `EXCLUDED`, separate from normal queue | Preserve as durable explicit protection, shared policy without reopening records. |
| Stale/unknown/future posting date | Ingest before ranking/application creation; claim/start/final guards | Already correct; reduce maintenance work, never weaken live checks. |
| Known closed | Ingest/cleanup, then page/HTTP checks | Keep early rejection; unknown closure still requires page inspection. |
| Wrong geography | After browser, except fill-only script | Reject only definite saved geography early; unknown/multi-country remains unknown. |
| Degree/graduation/authorization incompatibility | After listing extraction; fill-only preflight if saved description has it | Early only with sufficient verified requirement/profile evidence; rerun on enriched listing. Null is unknown. |
| Unsupported role | Eligibility after extraction | Use a conservative saved-data rule; no irreversible rejection from incomplete cards that might accept current students. |
| Unsupported ATS/widget | After navigation/form inspection | Recognition alone cannot establish capability; supported known limitations can produce a clear preflight hold, not false ineligibility. |

Discovery and execution already have separate modules but share one serial scheduling loop. Incrementally separate their due scheduling and data-only queue APIs; keep browser-based discovery serialized with the one application browser. No separate browser farm, message broker or replacement database is justified.

## 8. Duplicate/dead/incident code candidates

These are concrete consolidation candidates, not instructions to delete compatibility or protections without tests.

| Area | Findings / disposition |
|---|---|
| Normalization | `jobs.normalize`, `answers.question_text`, `field_mapping.normalize/semantic_key`, `standing.normalize_requirement`, source `clean`, cursor `normalized_origin`. Base Unicode/whitespace normalization can be shared; clause parsing, HTML cleaning, identity URLs and security-redacted URLs have different contracts and must remain distinct. |
| URL canonicalization | One primary `jobs.canonical_url` is already shared by DB/archive/sources. Repeated calls through `job_identity -> ats_identity -> canonical_url` can use a parsed identity object. `security.safe_url` intentionally removes identity-bearing details and must never replace it. |
| Identity/dedup | Ingest, bind_url, submission_conflict, history find_by_url/legacy merge, fill/controlled candidate copies. Consolidate exact identity and query policy; keep migration reconciliation distinct. |
| Dates | `freshness.py` is authoritative. `jobs.parse_date` is a compatibility wrapper referenced by tests; `jobs.freshness` also has the Controller legacy listing-date consumer. `history_statistics.timestamp` parses event timestamps, not freshness evidence. Do not merge these semantics blindly. |
| Eligibility | Main predicate is shared in jobs; Engine and batch duplicate timing/decision handling. `eligibility_repair.py` is incident-specific, not another normal eligibility engine. |
| Field concepts | `answers.LABELS/concept`, FieldMapper aliases/autocomplete/demographics, `dropdown_key`, preferences and controller verified_fact paths. Unify a canonical concept registry with profile-path compatibility. |
| Answer lookup | Engine answered rows + saved combobox replay + resolver + written_reuse; AIManager calls written_reuse again. Make precedence explicit; do not cache side effects. |
| Dropdowns | Commit already centralized in `combobox.py`; duplication is option discovery/preferences, SmartRecruiters post-commit checks, and two control-state readers, not four independent full dropdown engines. |
| Uploads | Shared UploadTracker and attachment_state are working consolidation. Browser retains a parallel pending_uploads/upload_failed view; inspect/remove duplicated state only after proving all consumers use tracker. Adapter-specific acceptance evidence must remain. |
| ATS | Registry duplicated between provider recognition and identity routing, with necessary different outputs. Unify registry metadata only. |
| Status mapping | `models` enums, `database.STATE_MAP`, `archive.STATES/LEGACY/record_state`, handoff headings and batch report labels. Centralize projection and document compatibility; preserve independently meaningful verification/security state. |
| Config loading | Core merge exists once, but CLI and several scripts repeat dotenv/config/DB construction and mutate config after validation. Provide one validated execution-policy builder. |
| Runtime hot reload | `Engine.resume_manual` `refresh_upload_protocol` branch performs module reload and live `__class__` replacement; explicit tests prove it is reachable. Move to controlled maintenance, do not call it dead. |
| Incident batch restrictions | `EXCLUDED` IDs, exactly-five approval, failed-batch retirement JSON and target count five belong to an operational run policy. Preserve them while extracting reusable batch execution. |
| One-off scripts | `audit_smartrecruiters_5310`, `repair_5310_once`, `smartrecruiters_5310_once`, `finalize_smartrecruiters_5310_hold` are historical utilities. Keep evidence/authorization restrictions; separate from normal startup. |
| Legacy listing-date branch | Controller still accepts `field_key='listing-date'`; no current discovery path creates that question because unknown dates now remain observations. Compatibility-only candidate; do not silently resolve pending legacy records. |
| Unused known-answer concept column | Schema defines `known_answers.concept`; current save/lookup paths do not populate/use it. Candidate for deprecation or purposeful migration, not another semantic-memory store. |
| Gmail integration | OAuth/extraction functions remain implemented and tested, but normal Engine has no call to `Gmail.verification`. Optional standalone utility, not a hot-path service to optimize or automatically activate. |
| Writing topic branches | `writing_topic` early why/interest return shadows the later company_interest rule and much of motivation; simplify only preserving stored legacy topics. |
| Export/build copies | Historical source exports and ignored `build/` are not imports used by normal package execution. Avoid auditing them as live duplicate implementations. |

No blanket dead-code purge is justified by a static call search alone. Public maintenance helpers and compatibility aliases can have external/manual users.

### Configuration and reporting

Precedence: repository example YAML -> private config YAML -> explicit `Config(..., override=...)`; scripts then mutate `config.data` directly. `Config.profile` reloads private YAML on every access. Verified factual overrides also live under DB `verified_fact:*`; exact memories and common_answers/standing assertions supply other sources. `.env` is loaded with `override=False`, so inherited environment wins for Discord credentials. Browser binary path uses `PLAYWRIGHT_BROWSERS_PATH` or private installed binaries; Codex executable uses configured path/PATH, inherits environment but removes API-key routing variables. No generalized environment-to-YAML override system exists.

Runtime DB settings override YAML auto_submit through `db.setting('auto_submit', yaml_default)`; an explicit stored null is not equivalent to a missing key. Paused, controlled target, daily attempt evidence, retirement guards, session controls, upload results, field mapping cache, provider cooldown, drafts and listing window are also stored there. Fill-only imposes separate hard policy and the script disables AI. Configuration is validated at construction but script mutations bypass that validation. Propose an immutable effective configuration with origin/provenance per setting and a separate live execution policy for pause/hold/submit permission. Changes need explicit cache revisions. Do not log secret values.

Status has overlapping producers: rotating log, events, SQL legacy-state counts, canonical history statistics, listing sidecar, handoff notifications, Controller explain, BatchReport, and script-generated run JSON. `Controller.status` does duplicate statistics work; `queue` orders by recent update while actual claim orders by priority. `Database.notify` suppresses `rate:`, `daily-cap:`, `question:` and `hold:` notifications and records suppression events instead; some caller text therefore never reaches Discord. Grouped handoff input notices coexist with legacy per-question views and direct batch DMs. Standardize a status snapshot with explicit listing/application/security/verification/hold/readiness dimensions and let each interface format it. Preserve delivery dedupe and report actual telemetry rather than leaving zero-initialized counters as the only proof no submission occurred.

## 9. Smallest useful target architecture

Keep the current package and introduce boundaries only where duplication causes cost or bugs:

1. **Pure policy/data:** parsed ListingIdentity, FreshnessDecision, immutable ProfileSnapshot, FieldDescriptor and AnswerResolution with provenance. Existing deterministic functions become their implementation.
2. **One application lifecycle:** existing Database-backed mutations plus typed transitions/retry/hold/manual commands. Keep compatibility projections, separate security/verification, and the exact intent-before-click boundary.
3. **One serial runner:** Engine coordinates candidate preflight, page ownership, adapter steps and outcomes. Discovery has due scheduling; it never creates parallel application browsers.
4. **Existing adapters with a small shared contract:** inspect listing, begin, inventory, commit field, upload evidence, validate, choose action, advance. Shared controls remain shared; selectors/step/receipt semantics stay ATS-specific. Add hooks in place before extracting whole modules.
5. **One page inspection/evidence layer:** live observations feed immutable snapshots; readers share them at a boundary; field/DOM/network generations invalidate cached work. Final action validation remains fresh.
6. **Durable storage and projections:** SQLite is authoritative for live control; history/imported evidence remains protected. Dirty revisions permit safe batching/export and one reporting projection. Avoid background export until crash/ack correctness is demonstrated.
7. **Narrow narrative service:** only after deterministic answers/templates/reuse fail, with grounded generation/audit and an exact-context cache. No AI for ordinary facts.

This does not require microservices, a new browser framework, a second queue, universal ATS schema, or replacing SQLite. Do not conflate safe deduplication, redacted diagnostics and display normalization in pursuit of one generic helper.

## 10. Independently testable implementation phases

Audit is Phase 1. The following seven implementation phases are proposed; none is implemented here. The baseline failures justify a small repair phase before the initially attractive maintenance optimization.

| Phase | Expected files | Behavior / deliverable | Before and after tests | Dependencies / risk |
|---|---|---|---|---|
| **2. Restore the regression baseline and mapping abstention** | `combobox.py`, `smartrecruiters.py`, `answers.py`; narrowly `applications.py`/`field_mapping.py` if a small shared helper is needed; test fixtures and dropdown/SR/retirement tests | Correct empty-menu committed-state detection, step-scoped receipts, fixture expansion contract and complete test construction; common installed-browser path; mapper conflict cannot be silently remapped to a fact | Before: the nine failing baseline cases and reproduced conflicting-field case. After: those cases, same-step mutation/late upload/security/retirement negatives, mapper-to-resolver abstention, full suite | None beyond audit. Medium; no large refactor or production-data operations. |
| **3. Bound maintenance and fix proven query scans** | `listing_store.py`, `database.py`, `engine.py`, `control.py`; new maintenance/query-plan tests; `test_freshness.py`, `test_history.py` | Due-driven cleanup, changed-only observation updates, one stats refresh, hoisted listing window, URL/events/sources indexes; unchanged final freshness guards | Before: exact freshness/controlled-target/history tests + offline counters. After: fake-clock expiry, no-op mutation counts, scan-not-due behavior, status single refresh, EQP checks, protected-state/history preservation, full suite | Phase 2 for reliable baseline. Medium; no browser/answer/submit redesign. |
| **4. Batch persistence and bound history overhead** | `database.py`, `archive.py`, `history_statistics.py`, Engine diagnostics/adapter events, `submission_probe.py` | Explicit DB-only mutation batches; no-op security updates; remove duplicate archive exports; history startup/read-mode separation. Revisioned deferred export only as a separately proven extension | Before: history/security/probe/controlled tests. After: crash between DB commit/export/ack, overlapping writers, Windows rename failure, imported fields/assets, monotonic confirmation, no await inside transaction, event count/latency counters, exact protected-history checks | Phase 3. Medium for batching, high for exporter revisions. |
| **5. One deterministic answer and candidate-preflight pipeline** | `config.py`, `answers.py`, `field_mapping.py`, `dropdowns.py`, `standing.py`, `disclosures.py`, `models.py`, question persistence, Engine, batch candidate code | Profile revision snapshot, canonical descriptor/signature, pure resolution/provenance, policy precedence; data-only early rejection where evidence is conclusive | Before: core/standing/disclosure/experience/dropdown/SR/freshness tests. After: null/false, Unicode aliases, scoped exceptions, changed options/policy/profile, evidence round-trip, zero AI for factual fields, preflight zero browser calls for definite rejects and no premature rejects for sparse cards | Phase 3; persistence hooks from 4 helpful, not required for pure pieces. Medium/high. |
| **6. Consolidate browser inspection and adapter step work** | `browser.py`, `security.py`, `applications.py`, `smartrecruiters.py`, `combobox.py`, `uploads.py`, limited cursor/scrolling code, Engine hooks | Shared boundary snapshots, pure dropdown inventory, batched shadow-aware metadata, unified transition/upload deadlines, explicit page ownership and reliable cleanup | Before: full browser/cursor/security/upload/probe/ATS suites. After: DOM/network invalidation, mutation between checks, async options, hidden duplicates, frames/shadow roots, attachment removal/late failure, one Next, cancellation/close failures, counters for scans/evaluations/selections | Phases 4–5. High; retain fresh final checks and existing physical-submit behavior. |
| **7. Central lifecycle, commands, retry and truthful batch readiness** | `models.py`, `database.py`, `retry.py`, `control.py`, `handoff.py`, `engine.py`, `fill_batch.py`, `batch_approval.py`, `discord_bot.py`, runners, maintenance location for incident code | Allowed lifecycle operations and shared status projection; tokenized command parity; typed failures/shared deadlines; exact identity conflict policy; optional reconstruction with honest checkpoint states; incident constraints preserved as policy | Before: recovery/retirement/manual-resume/fill-only/execution-approval tests. After: transition matrix, status dimension combinations, stale/duplicate commands, crash acknowledgement, pre/post-delivery retry budgets, two distinct requisitions, exact duplicate/imported history, one live tab, zero fill-only Submit/request/intent, closed-session checkpoint truthfulness | Phases 3–6 incrementally. High; compatibility migration and protected-history safeguards required. |
| **8. Narrative caching, documentation and performance acceptance** | `ai.py`, `codex_writer.py`, `answers.py`, written response schema, narrative/provider tests; README/DESIGN/SECURITY/handoff docs; audit harness | Verified templates and context-scoped audited cache; bounded provider readiness; explicit optional narrative policy; effective-config/status documentation; representative offline soak | Before: narrative failure/sensitive prompt tests. After: changed company/requisition/description/facts/limits cache miss, unverified stays unverified, grounding rejection, provider timeout/cancel, no extra application tab, no unexpected AI factual calls; full suite and before/after workload counters | Resolver/lifecycle/cache revisions from 5–7. Medium; keep uncached fallback available and audit safety properties. |

Every phase must preserve all user invariants: unknown values/null semantics; no invented facts; provenance; unverified generated prose; verified posting-date freshness; canonical URL plus ATS/requisition identity; protected submitted records; durable pre-click intent; no ambiguous retries; affirmative SUBMITTED evidence; manual security/auth/MFA/CAPTCHA handling without bypass; completed-upload evidence; one application tab; zero final Submit in fill-only; explicit controlled holds; reliable cleanup. Improvements to speed do not authorize relaxing any of these.

### Test strengths, fragility and missing coverage

Strong regression areas: freshness cutoff/provenance and non-posting dates (`test_freshness`); migration/history retention and Windows rename repair (`test_history`); negation/null/standing requirement distinction (`test_core`, `test_standing`, `test_llnl_standing`, `test_experience_distinction`, `test_disclosures`); security precedence/verification/intent recovery (`test_security`, `test_http_provenance`, `test_execution_approval`); physical delivery versus effect (`test_submission_probe`); local end-to-end form/resume/user input (`test_browser`, `test_narratives`); geometry, iframe, cancellation and input cleanup (`test_cursor`, `test_input_navigation`); SmartRecruiters shadow inventory and two-step fixture; controlled session tokens and permanent retirement; batch tab guards.

Important gaps before optimizing:

- No maintained workload budgets for SQL reads/writes, history exports, snapshot counts, provider invocations or browser actions per field/application.
- No shared CLI/Discord command parity test covering controlled `resume`, `inspect-manual`, stop scope and token construction. Current controlled tests directly call `command`, bypassing `route`.
- No complete matrix of persisted policy changes versus already-answered replay, semantic-key persistence, and provenance evidence round trips.
- Missing end-to-end real-browser sequential fill-only batch test asserting max one tab, zero final requests/intent, and the reconstruction claim. Existing tab tests mostly use fake page lists and direct guards.
- No broad tests of all combined status/hold/confirmation/retirement mutations; direct update_security permits values beyond the enum.
- Need failure-injection tests for context-close failure, Playwright-stop execution, archive failure in finally, and retained-page lifecycle after manual confirmation.
- Fixtures are synthetic, not a corpus of sanitized real variants. Only three committed HTML files; many cases build inline HTML. Add observed/reduced Lever, multiple Greenhouse variants, realistic Ashby upload/step cases, SmartRecruiters search widgets and changing-frame layouts when evidence becomes available. No live employer runs are required for this phase.
- The generic first-form extractor and SmartRecruiters inventory have different semantics; use a common behavioral adapter contract without insisting on identical DOM structure.
- Several browser tests import fixtures from `test_browser` instead of central fixture modules. `browser` marker is not uniformly applied to browser-using tests (for example many SmartRecruiters cases), so `-m 'not browser'` is not a reliable pure-unit selection.
- Tight three-second fixture timeouts, sleeps/short upload deadlines, exact event sequences and mocked private methods can be fragile under load. Historical docs record one such timeout; distinguish timeout diagnostics from business regressions. Test stable observable outcomes and budgets, with event order asserted where it is a safety invariant.
- The HTML/Python fixtures prove local behavior, not universal live ATS support. AI provider tests mock provider execution; they do not measure current service availability or model quality.

## 11. Exact recommended Phase 2 scope

**Restore the deterministic local browser regression baseline and honor conflicting field-map abstention in a narrowly scoped repair.** The audit initially pointed toward maintenance as the first optimization, but measured failures make baseline repair the safer first implementation. Maintenance is the next independent phase.

1. Centralize the existing repository-browser executable selection in the test fixture setup so every temporary-profile browser test uses the same available installation. Preserve explicit user/test environment overrides. No downloads, production profile changes or sandbox/security changes are needed.
2. Correct `combobox.committed_state` to distinguish an empty retained menu container from an actually open menu with selectable options. Require affirmative exact committed value/selected-label evidence and noninvalid state; preserve hidden duplicates, asynchronous options, one option click, stale-locator recovery and wrong-option rejection.
3. Scope SmartRecruiters `live_committed` control receipts to the observed form step. Retire prior-step control receipts only after `advance` observes a stable successful transition; retain stored answers/provenance and independent same-session upload receipts. Same-step disappearance or value changes must still fail validation. Do not simply clear receipts on every rediscovery or swallow KeyError.
4. Reconcile the synthetic SmartRecruiters City fixture with its intended supported control contract: expose accurate expanded/closed state and a real open/search trigger. If typing-first controls are an intended capability, add a separate behavioral fixture and bounded implementation; do not treat absent commitment evidence as success or infer live ATS behavior from this synthetic page.
5. Repair the retirement test's incomplete Engine construction so it exercises a valid Engine with controlled dependencies. Retain assertions that permanent retirement prevents browser/DB processing and final interaction; do not delete the assertion or regard an AttributeError as the expected guard.
6. Add a mapper-to-resolver behavioral test for conflicting signals, using the reproduced Email/given-name case. Prevent the legacy label fallback from overriding explicit mapping rejection; preserve exact user-confirmed answers and ordinary successfully mapped profile fields. Keep the larger precedence/provenance migration out of scope.
7. Run the nine baseline-failing cases first, then relevant browser/SR/combobox/security/upload/probe/retirement and mapping tests and the full suite. Record exact results and any remaining failure causes. Do not fold query optimization, state migration or AI changes into this repair.

Expected production files: `autoapply/combobox.py`, `autoapply/smartrecruiters.py`, `autoapply/answers.py`; `autoapply/applications.py`/`autoapply/field_mapping.py` only if small receipt/rejection helpers are necessary. Expected test files: `tests/conftest.py`, `tests/test_dropdown_browser.py`, `tests/test_stable_combobox.py`, `tests/test_smartrecruiters.py`, `tests/test_manual_submission_retirement.py`, `tests/fixtures/smartrecruiters-controls.html`. No database schema, private config, historical record, or final submission pipeline changes.

Acceptance criteria:

- All nine baseline-failing cases execute against the configured local browser and pass; the full suite is green, or a newly exposed failure is explicitly diagnosed before expansion of scope.
- Both committed-value regressions accept the exact chosen value with a truly closed/empty menu, without an extra selection click; visible uncommitted choices still block.
- The SmartRecruiters two-step local fixture retains accepted upload evidence, validates current-step controls, performs one simulated final submission and persists affirmative fixture confirmation. No stale earlier-step receipt lookup or hidden blanket skip.
- Missing/changed same-step controls, late upload failures, changed selections, security transitions and ambiguous Next/Submit continue to stop safely.
- Conflicting field signals produce an unresolved field instead of an automatically supplied profile value; explicit verified-answer and unambiguous factual-field tests remain passing.
- Retirement guards, one-tab fill-only behavior, zero real final Submit, pre-click intent, confirmation permanence and manual-session restrictions are unchanged.
- No live employer run, automatic-submission enablement, private-history maintenance, or Phase 3+ work included.

For Phase 3, the exact first performance targets are: zero observation updates on an unchanged maintenance pass, constant listing-window reads, no repeated full cleanup during idle ticks, one statistics refresh per status snapshot, and indexed URL/events/source lookups. Keep fresh claim/start/final guards even when maintenance is deferred. These are separately measurable and do not require a browser redesign.

Phase 2 remains a proposal. Only this audit and its offline supporting artifacts have been created.
