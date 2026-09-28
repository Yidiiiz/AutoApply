# Final acceptance and release-readiness audit — 2026-09-27

Scope: offline/local acceptance of the completed Phases 1–8 implementation. This report does not authorize live applications, authentication, provider use, messages, or submission. No Phase 9 or production repair is included. Audit execution crossed into September 28 UTC; the requested report date is retained.

**Decision: PASS — internally ready to enter a separately authorized operational qualification stage.** All ten objective gates pass within the documented offline scope; no concrete blocker was found. Production source and the existing baseline tests are unchanged by this audit. This is not live operational acceptance or submission authorization.

## 1. Repository state

The original [Phase 1 audit](audit-2026-09-25/AUDIT.md) and Phase 2–8 reports were read before the source audit. Current architecture was checked against source, not inferred from historical descriptions.

- Branch: `main`; HEAD: `f39b2346da7d203507fae4554b52841c2051d3e4` (the Phase 8 pre-edit baseline).
- The tree was **not clean**. Existing modified files: `DESIGN.md`, `README.md`, `autoapply/{ai,answers,codex_writer,control,database,engine}.py`, `tests/test_narratives.py`. Existing untracked files: `autoapply/narratives.py`, `tests/test_phase8_narratives.py`, `docs/phase8-2026-09-27.md`, and `docs/phase8-2026-09-27/`.
- These are the Phase 8 deliverables, not audit edits. All **93/93** normalized source/test hashes in Phase 8's `validated-tree.json` match. Its saved exact final baseline contains **1,023** unique IDs. Phase 8's preservation manifest also matches the prior artifacts/protected modules. No unrelated publishable modification was identified.
- The Git ownership check required `git -c safe.directory=C:/Users/yzhao/Documents/GitHub/AutoApply ...`. Global Git settings were not changed. Git reported unreadable user-global ignore configuration and several old ignored temporary directories; private/ignored storage was not inspected to overcome those warnings.
- Ignored/generated paths reported by Git: `.env`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `.tmp/`, `.venv/`, `autoapply.egg-info/`, `build/`, `data/`, `logs/`, and Python `__pycache__/` directories. Browser executables already installed under the repository are reused by existing fixtures; their production profiles/history are not opened.
- Audit additions are this report and `docs/final-acceptance-2026-09-27/`. Raw console/JUnit output is in `.tmp/final-acceptance-2026-09-27/`. No existing phase evidence is overwritten. No commit, branch change, staging, cleanup, or private configuration edit was performed.

[Repository manifest](final-acceptance-2026-09-27/repository.json) records the source hashes and final status. This audit concerns the uncommitted, hash-identified Phase 8 tree, not HEAD alone. A later release must bind its commit to this tree or revalidate changed code.

## 2. Current architecture ownership map

| Responsibility | Authoritative decision/source | Boundary |
| --- | --- | --- |
| Job discovery | `sources.scan_github`, `BrowserJobSource`; `Engine.scan`; `Database.ingest` | Serial source polling, source sightings and ingest; audit uses synthetic ingest only. |
| Listing freshness | `freshness.py`; `ListingStore.guard_listing`, `maintain_listings` | Posting evidence/UTC age policy is authority; scheduling and ACTIVE caches do not grant execution. |
| Candidate preflight | `candidate_policy.evaluate/preflight` | Shared by ordinary/controlled Engine and fill-only selection; conclusive reject, protected hold, or enrichment. |
| Application identity | `jobs.canonical_url/ats_identity/job_identity`; `conflicts.identity_match/submission_conflict` | Canonical URL or recognized opportunity identity; incomplete legacy similarity is a review hold. |
| Application lifecycle | `Lifecycle` in `lifecycle.py` | Atomic status, job projection, dimensions and events. Database transition/claim/fail/retry/recover facades delegate. |
| Retry decisions | `RetryPolicy.decide`, applied by `Lifecycle` | Intent, delivery uncertainty, holds, retirement and persisted attempt budget veto retry. |
| Submission intent | `Lifecycle.record_submission_intent` | Conflict/hold checks and durable SUBMITTING transition before physical interaction. |
| Submission confirmation | `SubmissionClassifier`, `Engine.confirm`, `Lifecycle.record_confirmation` | Affirmative observed evidence; operator reconciliation is a separately recorded evidence path. |
| Manual commands | `ManualCommand`, `ManualCommands`; shared Controller parsing | Durable command IDs, token envelopes, claim/ack ledger, crash failure rather than blind replay. |
| Controlled authorization | `manual.validate_session_token`, Controller target guards, `Engine.resume_manual` | Persisted target/token plus actual in-memory owned session; inspection does not grant submission. |
| Profile facts | `Config.profile_snapshot`, `ProfileSnapshot`, explicit verified DB overrides | Immutable revisioned factual view; unknown/null does not become false. |
| Field mapping | `concepts.py`, `FieldMapper`, `field_mapping.describe` | One descriptor/signature; REJECTED_FIELD blocks weaker sources. |
| Answer resolution | `AnswerResolver.resolve_result`, standing/disclosure/dropdown policies | Pure deterministic resolution; exact scoped verified evidence has defined precedence. |
| Narrative generation | `AIManager.draft`, `narratives.py` | Admission, deterministic sources/templates, verified writing, exact proposal cache, generation plus grounding audit. |
| History persistence | `Database.transaction/flush_history`; `ApplicationHistory` | SQLite authoritative; transactional dirty queue, serialized file export and acknowledgement. |
| Browser ownership | `Browser`, `PageLease`; Engine/handoff owner maps | Dedicated persistent context, serialized page creation, cleanup and one application page in fill-only. |
| ATS mechanics | `applications.adapter_for`, Generic/Lever/Ashby/Greenhouse and `SmartRecruitersAdapter` | Inventory, exact field commitment, action selection and step evidence; no independent submit policy. |
| Security classification | `SecurityDetector`, fresh `PageInspection`, `SubmissionClassifier` | Fresh boundary observations; blocking challenge leads to durable hold. |
| Upload evidence | `UploadTracker`, `SessionAttachment`, `Browser.uploads_ready`, `FileSnapshot` | Revision-scoped receipt plus current UI/network/security and file identity. |
| Fill-only checkpoints | `fill_batch.checkpoint/finish_preparation`, `FillOnlyInvariant` | Atomic checkpoint file then durable metadata/event; release removes live-ready projection. |
| Reconstruction | `fill_batch.verify_reconstruction`; lifecycle reconstruction/restore guards | Explicit, budgeted fresh replay on the owned page; checkpoint alone is not replay proof. |
| Final submission | `Engine._process_one` → lifecycle intent → `SubmissionProbe.physical_click` | One physical mouse click; observation yields confirmation or protected uncertainty. |

No competing ordinary runtime authority was found. Dedicated standing/disclosure grammars and ATS evidence rules are subordinate specialized policies, not competing lifecycle or fact stores. The pinned 5310 repair is an explicit historical exception, described in section 5. Low-level SQL and dimension APIs are trusted internal mechanisms, not a sandbox against arbitrary malicious Python callers.

## 3. End-to-end scenario results

The matrix uses actual production components through existing deterministic tests. It does not claim every scenario is a single giant end-to-end test: cross-layer flows, local browser tests and injected crash tests are identified separately. `tests/` prefixes below are literal source paths; parameterized variants are individually retained in the exact outcome record.

| Scenario | Production path / expected outcome | Primary evidence |
| --- | --- | --- |
| Ordinary successful fill | Synthetic discovery → freshness/selection → claim reservation → preflight → begin attempt → browser → factual fill/upload → validation → checkpoint → release. Final intents/clicks/requests **0**, peak application pages **1**. Actual Engine reserves before its second preflight; reservation consumes no attempt. | `test_phase7_fill.py::test_sequential_checkpoint_batch_local_forms[False]`; `test_listing_maintenance.py::test_attempt_start_rechecks_after_claim`; `test_phase5_candidates.py::test_fill_only_shares_policy_keeps_sparse_and_historical_exclusion` |
| Unknown fact | Resolver abstains; no provider factual inference; required field becomes input hold/checkpoint without ready projection. | `test_phase7_fill.py::test_held_local_form_is_checkpoint_not_ready`; `test_phase5_resolution.py::test_abstention_is_structured`; audit adversarial-label cases |
| Narrative | Deterministic resolver/receipt → verified writing → admitted proposal → real local browser commitment → saved provenance/use; proposal remains unverified. | `test_narratives.py::test_production_fills_narrative_and_skips_optional_demographic`; `test_generate_grounded_narratives`; `test_phase8_narratives.py::test_browser_rerender_failure_reuses_proposal` |
| Cached narrative | Same compatible request/new manager/new connection reuses proposal; zero additional provider generation. | `test_phase8_narratives.py::test_cache_retries_restart_and_pure_consideration`; `test_persistent_cache_new_connection` |
| Changed narrative context | Profile, wording, employer, requisition, policy/provider/model/writing/context changes cannot use old signature. | `test_phase8_narratives.py::test_invalidation`; `test_saved_generated_receipt_cannot_bypass_narrative_revision`; `test_revision_change_during_provider_await_refuses_output` |
| Security challenge | New challenge invalidates safe observation; fresh mutation boundary stops; hold disables automatic retry. | `test_phase6_browser.py::test_snapshot_invalidation_and_fresh_boundaries`; `test_phase7_lifecycle.py::test_holds_override_retry`; `test_security.py::test_cleared_spam_banner_does_not_enable_automatic_submission` |
| Upload failure/removal | Previously accepted receipt invalidates on removal/replacement/late failure/busy/warning; readiness false until current evidence passes. | `test_phase6_browser.py::test_upload_receipt_reuse_and_invalidation`; `test_transient_upload_mutation_requires_new_stability`; `test_submission_probe.py::test_failed_upload_is_not_successful_completion` |
| Ambiguous Next | Durable possibly-delivered transition; one interaction; bounded unconfirmed observation; no blind repeat or inherited resume permission. | `test_phase6_browser.py::test_next_ambiguity_is_one_click_with_bounded_observation`; `test_phase7_lifecycle.py::test_ambiguous_next_cannot_inherit_prior_resume_permission` |
| Submission uncertainty | Durable intent precedes click; absent affirmative evidence preserves UNKNOWN/manual hold and bars retry/resubmit until reconciliation. | `test_persistence_browser.py::test_intent_is_committed_before_final_interaction`; `test_security.py::test_no_confirmation_stays_unknown_and_cannot_resubmit`; `test_phase7_lifecycle.py::test_crash_after_possible_delivery_holds` |
| Crash before begin_attempt | CHECKING reservation recovers with no attempt consumed. | `test_phase7_lifecycle.py::test_restart_budget_counts_work_not_startups` |
| Crash after begin_attempt | Exactly one attempt counted; persisted max_retries controls recovery; repeated acted crashes exhaust budget. | Same test; `test_resolved_answers_cannot_manufacture_retry_budget` |
| Crash after checkpoint | File/metadata survive; restart clears prior session claim and reports reconstructable checkpoint, never a live page. | Audit `test_lifecycle_commit_export_failure_restart[checkpoint]`; `test_phase7_lifecycle.py::test_persisted_flags_do_not_prove_live_readiness` |
| Crash after intent | Committed intent and dirty export survive abrupt close; restart exports then protects uncertainty. | `test_persistence.py::test_critical_commit_survives_export_failure_and_restart`; audit intent/confirmation cases |
| Controlled execution | Correct target and session token permitted; wrong/missing/stale tokens rejected before fact resolution/employer access. | `test_phase7_lifecycle.py::test_controlled_token_parity`; `test_controlled_missing_token_does_not_resolve_facts`; `test_controlled_resume.py::test_invalid_session_never_accesses_employer` |
| Submitted/protected application | Maintenance does not reopen; candidate, work start, retry, recovery and manual continuation retain protection. Exact alias blocks; distinct requisition remains distinct. | `test_listing_maintenance.py::test_cleanup_preserves_protected_history_and_target`; `test_phase5_candidates.py::test_mode_restrictions_and_protection`; `test_history.py::test_imported_submission_blocks_duplicate`; `test_controlled_resume.py::test_post_submit_never_repeats`; retirement and lifecycle tests |

Final pass/fail counts for these tests come from section 13, not the previous phase's green total.

## 4. Safety-invariant matrix

All tests named here are behavioral checks unless an instrumentation/source constraint is expressly described. Generated [safety-matrix.json](final-acceptance-2026-09-27/safety-matrix.json) expands these references to exact executed IDs and outcomes.

| ID | Invariant / production enforcement | Primary regression test(s) | Secondary defense / limit |
| --- | --- | --- | --- |
| S1 | Unknown facts never invented: `AnswerResolver`, field policy and narrative admission abstain. | `test_phase5_resolution.py::test_abstention_is_structured`; `test_phase7_fill.py::test_held_local_form_is_checkpoint_not_ready` | Required field hold; adversarial-label audit. Arbitrary model entailment is not formally proved. |
| S2 | Null != false: `ProfileSnapshot`, fact lookup and explicit bool override parsing. | `test_phase5_resolution.py::test_snapshot_null_false_original_and_immutable`; `test_null_authorization_and_sponsorship_not_derived_from_citizenship` | No citizenship-to-authorization fallback. |
| S3 | Factual fields invoke zero AI: `is_writing_question`, manager admission and Engine ordering. | `test_phase8_narratives.py::test_factual_provider_calls_are_zero`; `test_phase5_resolution.py::test_factual_fields_never_enter_provider` | Audit checks both provider transports for requested labels. |
| S4 | REJECTED_FIELD cannot reach weaker fallback: descriptor/resolver hard stop. | `test_phase5_resolution.py::test_rejected_field_never_uses_profile_or_narrative` | Exact compatible verified answer exception is explicit; manager repeats rejection. |
| S5 | Generated prose remains unverified: manager proposal, writing insert `verified=0`, structured receipts. | `test_phase5_resolution.py::test_generated_provenance_stays_unverified`; `test_phase8_narratives.py::test_user_correction_outranks_cache_and_preserves_proposal` | Explicit user acceptance separate from generation/use. |
| S6 | Incompatible narrative scope not reused: full field/narrative signature and revisions. | `test_phase8_narratives.py::test_invalidation`; `test_explicit_employer_scope_still_obeys_phase5_requisition_signature` | Answer hash, post-await revision check; whole-profile invalidation is conservative. |
| S7 | Stale listing cannot start application work: `guard_listing` at claim/start/final. | `test_listing_maintenance.py::test_claim_never_trusts_cached_active`; `test_attempt_start_rechecks_after_claim` | Pure freshness filter/preflight and final listing guard. |
| S8 | Protected identity prevents duplicate execution: `submission_conflict`, preflight, retirement/lifecycle guards. | `test_history.py::test_imported_submission_blocks_duplicate`; `test_security.py::test_unresolved_duplicate_protects_new_alias` | Final intent conflict check; claim reservation is not browser execution. |
| S9 | Same-title distinct requisitions distinct: `identity_match`, ATS opportunity keys. | `test_phase5_candidates.py::test_same_title_distinct_requisitions_and_exact_alias`; `test_phase7_lifecycle.py::test_exact_identity_distinct_tenants_and_requisitions` | Incomplete legacy title similarity is explicitly ambiguous, never exact. |
| S10 | Durable intent before final interaction: Engine ordering and lifecycle atomic commit. | `test_persistence_browser.py::test_intent_is_committed_before_final_interaction` | Export failure aborts before click; physical probe itself is an internal transport, not an independent intent authorizer. |
| S11 | SUBMITTED requires affirmative evidence: classifier plus lifecycle nonempty confirmation guard. | `test_security.py::test_confirmation_requires_affirmative_evidence`; `test_submission_probe.py::test_effect_without_confirmation_remains_unknown` | Confirmation recorded before optional screenshot I/O. Lifecycle cannot independently authenticate operator evidence. |
| S12 | Ambiguous final delivery cannot auto-retry: durable intent and `RetryPolicy`. | `test_security.py::test_no_confirmation_stays_unknown_and_cannot_resubmit`; `test_phase7_lifecycle.py::test_crash_after_possible_delivery_holds` | Manual reconciliation requires evidence and preserves prior intent event. |
| S13 | Manual/security/controlled holds block retry: lifecycle policy plus session checks. | `test_phase7_lifecycle.py::test_holds_override_retry`; `test_execution_approval.py::test_execution_approval_hold_does_not_request_applicant_answers` | Target and in-memory token verification. |
| S14 | Retry budget survives restart: persisted attempts/max_retries and begin_attempt accounting. | `test_phase7_lifecycle.py::test_restart_budget_counts_work_not_startups`; `test_resolved_answers_cannot_manufacture_retry_budget` | Resume/reconstruct/reconcile consult remaining budget. |
| S15 | Persisted session does not imply live page: `status.snapshot`, lifecycle recovery/release. | `test_phase7_lifecycle.py::test_persisted_flags_do_not_prove_live_readiness` | Actual owned, open page required; checkpoint crash audit. |
| S16 | Fill-only creates no intent: Engine early return and lifecycle fill-only guard. | `test_phase7_fill.py::test_sequential_checkpoint_batch_local_forms`; `test_whole_run_intent_violation_is_detected` | Whole-run field plus SUBMITTING-event detection. |
| S17 | Fill-only makes no final click/request: Engine early branch; probe blocks; observer/route latch. | `test_phase7_fill.py::test_actual_final_click_is_observed_and_stops_run`; `test_final_request_is_blocked_and_latched`; `test_sequential_checkpoint_batch_local_forms` | Recognized endpoints/labels are finite; the route classifier is not a universal arbitrary API classifier. |
| S18 | Fill-only max application tabs one: central Browser page limit/lease policy. | `test_phase6_browser.py::test_direct_fill_only_prestarted_context_enforces_owned_tab_limit`; `test_phase7_fill.py::test_sequential_checkpoint_batch_local_forms` | Unexpected popup closed and violation latched; a popup can exist transiently before callback closure. |
| S19 | Stale snapshots cannot authorize mutation: generation reads, fresh locators/options/security and validation. | `test_phase6_browser.py::test_snapshot_invalidation_and_fresh_boundaries`; `test_changed_dropdown_options_cannot_replay` | Cursor fresh hit test; no claim of atomicity against arbitrary browser changes between awaits. |
| S20 | Upload receipt invalidates on relevant change: tracker revision, UI/control/network/session evidence. | `test_phase6_browser.py::test_upload_receipt_reuse_and_invalidation`; `test_transient_upload_mutation_requires_new_stability` | Final upload recheck and fresh file hash; ATS-specific stronger acceptance rules. |
| S21 | Final lifecycle states monotonic: transition/confirmed-dimension/retirement guards. | `test_phase7_lifecycle.py::test_forbidden_transitions`; `test_security.py::test_confirmed_submission_then_persona_keeps_submission` | Guarded historical 5310 eligibility repair is an explicit non-submission exception, not ordinary reopen. |
| S22 | Reconciliation preserves evidence: lifecycle writes prior timestamp/outcome/source before clearing uncertain intent. | `test_phase7_lifecycle.py::test_reconciliation_retains_intent_evidence`; `test_submission_probe.py::test_user_reconciliation_preserves_prior_intent` | Imported unknown fields/assets retained by archive merge. |
| S23 | Manual commands durable/idempotent: ledger, unique ID, atomic claim/ack, interrupted-command failure. | `test_phase7_lifecycle.py::test_claim_crash_has_evidence_and_never_replays`; `test_malformed_legacy_request_has_durable_failure` | Audit export-failure/manual-command case. |
| S24 | Dirty-history durability survives export failure: mutation commit + dirty trigger; export ack rollback. | `test_persistence.py::test_critical_commit_survives_export_failure_and_restart`; `test_atomic_replace_failure_keeps_old_file_and_pending_work` | Audit repeats five lifecycle operations; existing DB-before-history lock protocol. |
| S25 | Validation requires no external network/provider: explicit offline plugins and synthetic configuration. | `test_phase8_narratives.py::test_offline_validation_blocks_external_services_and_providers` | **Validation-only enforcement**, not a production network ban. Python socket/DNS guards, Chromium route/DNS restrictions, blocked real transports, temporary credential metadata. |

S25 intentionally has no production-wide prohibition: production is a network application. This is the only invariant here whose relevant enforcement is validation infrastructure. Provider quality and arbitrary live ATS behavior remain qualification questions; they are not inferred from mocks. No runtime safety invariant above depends solely on a test assertion without a corresponding production enforcement path.

## 5. Bypass-path audit

[bypass-inventory.json](final-acceptance-2026-09-27/bypass-inventory.json) classifies each of **643** matching source lines across `autoapply/`, `scripts/`, and `tests/`; it includes reads, schema defaults, callers and writes, not just mutations. Search terms include all requested lifecycle/security/verification/intent/session/reconstruction fields, transition/fail/retry/draft calls, direct application SQL, raw clicks, generation, reload and class inspection. The file/function context below supplies the reasoning rather than treating a textual match as a bypass.

| Occurrence group | Classification | Finding |
| --- | --- | --- |
| `Lifecycle` application SQL / transitions / dimensions / attempt / delivery writes | AUTHORIZED OWNER | Central operations; final, intent, hold, retry and confirmed-evidence guards reviewed. `dimensions` is a trusted dimension setter, not a general command API. |
| Database transition/claim/retry/recover/fail/update_security | COMPATIBILITY FACADE | Delegate to lifecycle. Legacy transition relaxes graph compatibility, not final/intent/retirement/fill-only/evidence protection. |
| Database migration `attempt_started`, initial schema/status projection, history-dirty timestamp preservation | AUTHORIZED OWNER | One-time additive migration/initialization, not an alternate runnable workflow. |
| Listing ingestion/maintenance | AUTHORIZED OWNER | Initial creation/listing evidence belongs here. Expiry changes eligible unfinished applications through transition; excludes intent/manual protection. Never reopens submitted state. |
| Archive screenshot-path UPDATE | AUTHORIZED OWNER | Repairs filesystem relocation metadata under export ownership; no state/permission mutation. |
| Engine, Controller, handoff, fill checkpoint release/status projection | AUTHORIZED OWNER | Call lifecycle or its facades; no independent status SQL. Session/projection writes do not bypass claim/preflight/final checks. |
| Controller `auto-submit` setting | AUTHORIZED OWNER | Explicit operator command; fill-only separately requires false and blocks intent/click. Not called by this audit. |
| `SubmissionProbe.physical_click` | AUTHORIZED OWNER | Sole normal physical final mouse click, one-call latch, current security/geometry/hit-test, retirement and fill-only guards. Caller establishes durable intent. |
| `applications.py` control/radio/checkbox clicks; combobox open/selection/collapse; cursor backend input | AUTHORIZED OWNER | Context is field input or nonfinal navigation. Shared Next records possibly-delivered intent and never blindly repeats. No second final-submit locator fallback found. |
| Probe/browser/upload request listeners | AUTHORIZED OWNER | Observe or abort/classify requests; do not post application payloads directly. Source polling/Gmail/Discord/provider network routines are separate explicitly configured integrations, unused here. |
| `AIManager` two `generate_response` calls | AUTHORIZED OWNER | Admitted novel narrative and its existing grounding audit. Engine and Controller are the only application-level draft callers. |
| `FieldMapper.fallback` | AUTHORIZED OWNER | Dormant, allowlisted semantic classification hook; production constructors do not install a provider. |
| `eligibility_repair.repair_5310` raw status reset | MAINTENANCE/INCIDENT TOOL | Exact ID, URL, description hash, old defect, stage, attempt count and event sequence; rejects submission/resume/confirmation/session/activity/retirement. Preserves repair evidence. Not general terminal reopen and not executed. |
| `smartrecruiters_5310_once.py` `importlib.reload` | MAINTENANCE/INCIDENT TOOL | Explicit incident runner remains isolated. Ordinary Engine resume has no reload or live `__class__` replacement. Not executed. |
| `discord_bot.py` `exc.__class__.__name__` | AUTHORIZED OWNER | Exception-name check for user-facing error handling, not class mutation. |
| Controlled/fill-only/restore/finalize scripts | MAINTENANCE/INCIDENT TOOL | Explicit runners use current lifecycle, target, token, destination and retirement guards. Some intentionally set operational auto-submit; none run in this audit. |
| Synthetic direct SQL, fake provider methods, deliberately triggered final clicks/endpoints | TEST/FIXTURE | Exercise forbidden mutations and defense-in-depth detection; not production routes. |

No unresolved POTENTIAL BYPASS was established. The inventory's broad owner classification includes read-only references; it does not mean every line independently enforces the invariant. Arbitrary external SQL could defeat application-level rules; the documented serial trusted-process model does not claim protection against that threat.

## 6. Final submission path proof

Source trace: `Engine._process_one` lines 495–630, `Lifecycle.record_submission_intent` lines 126–132, `SubmissionProbe.physical_click` lines 108–142. Current line numbers are bound by the source manifest.

1. Current fields are filled with exact readback; uploads are checked; rediscovered question signatures must match before action selection. Adapter validation and fresh security classification run. Next uses its separate one-interaction transition protocol.
2. A final action requires inspectable fields, applicable eligibility/resume rules and no unresolved earlier-step question. **Fill-only exits here** (lines 537–553) after eligibility/upload/conflict checks and ready checkpoint ownership. Auto-submit-off also returns before probe construction.
3. Probe preparation resolves current button visibility, enabled state, geometry and hit testing. Pre-submit screenshot/telemetry are saved. Fresh security, interruption/paused/status, auto-submit and daily-ceiling checks follow.
4. Resume bytes are reread and hashed against the uploaded hash. Fresh page closure/auth/security checks and listing guard follow; fresh pre-submit validation and upload readiness run again.
5. The **probe is armed before durable intent** (`await probe.arm()`, line 611). This is the actual safe ordering; instrumentation is not a physical interaction. Target/session authorization was established at controlled claim/resume, and execution-denial observations lead to holds. The ordinary path uses explicit auto-submit policy and current interruption checks; it does not claim a universal external destination authorization system. The specialized fill-only runner has its additional approved-destination policy.
6. `record_submission_intent` rechecks protected identity, manual/security hold and delivery state, then atomically writes SUBMITTING, timestamp, possibly-delivered state, retry prohibition and event. Mutation commit precedes history flush. If export fails, the already durable intent survives and the call raises **before any final click**.
7. `probe.intent()` records observation metadata; browser observation is reset. `physical_click` checks fill-only/retirement/one-call latch, fresh target geometry, fresh security and hit test. It marks the call and issues **one** `page.mouse.click`, with no locator fallback or retry.
8. Immediate post-click observation stores affirmative confirmation **before optional screenshot I/O**. Subsequent bounded observation retains security/verification evidence. Network effect or click delivery alone is insufficient for SUBMITTED; absent affirmative evidence creates a protected hold/uncertainty. Recovery cannot blindly resubmit.

The requested conceptual ordering is satisfied, with the intentional probe-arm-before-intent detail above. These are successive fresh checks, not a database transaction across browser awaits or an atomic guarantee that a remote page can never change between checks. Local/intercepted submission-probe and persistence-browser tests verify physical count, request count, confirmation timing, uncertain effects and export-failure zero-click behavior. No employer was visited.

## 7. Identity/duplicate audit

`canonical_url` strips tracking, normalizes URL components and Lever apply aliases; `ats_identity` parses recognized requisitions (including Ashby's `/application` alias); `job_identity` falls back to canonical URL hashing. `conflicts.identity_match` compares canonical URL or opportunity key. Lever/Ashby/SmartRecruiters keys carry tenant; Workday carries host and requisition suffix; Greenhouse uses its job ID/`gh_jid` convention rather than a universal tenant column.

| Boundary | Compatible policy verified |
| --- | --- |
| Candidate preflight | Shared `submission_conflict` and protected/retired state before browser work. |
| Claim/start | Claim excludes protected status/intent/retirement and rechecks freshness; Engine preflight runs before begin_attempt/new_page. Claim alone is only a reservation and does not run a second whole-history scan. |
| Final submission | Lifecycle rechecks shared conflict policy immediately when recording intent. |
| URL binding/ingest | Canonical/opportunity keys and protected imported history guard aliases. |
| History import | Preserved protected records participate in the same conflict matcher; malformed/unknown evidence is retained. |
| Reconciliation | Does not rewrite identity; preserves old intent evidence and requires explicit outcome/source; known submission remains protected. |
| Fill-only selection | Shared pure preflight plus exact history, retirement and historical exclusion checks. |
| Controlled execution | Target restriction adds to, rather than replaces, Engine preflight and final conflict guard. |

Same-company/title distinct known opportunities are not exact duplicates. Incomplete identities may cause `ambiguous_legacy_identity` holds; this is a conservative accepted limitation. Provider detection is broader than identity extraction and does not establish identity merely by recognizing a page marker. The legacy broad Greenhouse suffix/`gh_jid` parsing and unknown/custom URL conventions are not a universal origin-authentication mechanism; supported destination/identity conventions need qualification before wider deployment. No protected-identity execution bypass was demonstrated under the documented recognized-identity fixtures.

## 8. Persistence/recovery audit

The Phase 4/7 interaction remains explicit: mutation and dirty triggers commit first; `flush_history` obtains `BEGIN IMMEDIATE`, reads pending work, exports bundles/statistics under the history lock, and acknowledges only after complete success. File failure rolls back acknowledgement, not the earlier state commit. Startup retries dirty work. Graceful close attempts export and still closes the connection on failure. Lock order remains DB → history.

Audit-only tests add the previously less-direct cross-phase evidence: production manual enqueue, retry decision and actual `checkpoint` persistence under injected export failure followed by abrupt connection close/restart. The same parameterized harness also exercises intent and confirmation as controls. Checkpoint uses an empty synthetic page inventory to isolate persistence; existing local browser tests independently prove field capture/upload/fill/release. The harness does not pretend to emulate OS power loss or browser reconstruction.

| Operation | Durable state before simulated exit | Restart / recovery expectation |
| --- | --- | --- |
| Submission intent | SUBMITTING, intent timestamp, attempt count, dirty row | Export restored; UNKNOWN/manual protection; no claim. |
| Confirmation | SUBMITTED, affirmative evidence, prior intent, dirty row | Confirmed final state retained; no claim. |
| Manual command | PENDING ledger and compatibility request plus supporting event | Remains durable; same ID cannot enqueue a duplicate. Separate existing test covers crash after claim → FAILED/no replay. |
| Retry decision | RETRY, deadline, budget, exactly one decision event | Same deadline/budget/decision retained; restart is not another failure. |
| Checkpoint | Atomic file then setting/event; ready projection and dirty row | File/metadata exported; no live page; prior session cleared, reconstructable readiness retained. |

Rollback-before-commit, atomic file replacement failure, concurrent-connection acknowledgement isolation, imported metadata/assets and probe callback durability remain covered by the original persistence/history suite. No new concurrency is introduced; an existing second-connection test is a deterministic durability check. Export still holds a SQLite write lock during filesystem work. That is a performance limitation, not evidence loss, and was not redesigned.

## 9. Browser ownership audit

`Browser` owns its dedicated persistent context and `PageLease` objects. It rejects foreign-context leases. `new_page` is serialized and cleanup runs on setup failure/cancellation. Engine uses nested `finally` and shielded release so telemetry/history/checkpoint errors do not bypass page cleanup; intentional manual/handoff/retained pages retain an explicit owner. Shutdown attempts context close and Playwright stop even when cleanup fails.

Fill-only centrally limits the application page to one, including a prestarted context. Only dedicated-context blank startup pages are removed. Unexpected popups are closed and leave a latched violation; the popup callback cannot guarantee no transient second page ever existed. Successful sequential fixtures independently assert peak **1**. No personal/foreign context is manipulated.

Checkpoint-only preparation releases the page. Explicit reconstruction uses the sole page and a new budgeted navigation pass; only successful replay earns RECONSTRUCTION_VERIFIED. `status.snapshot` requires an actual live open page to report LIVE_READY_FOR_MANUAL_SUBMIT. SQLite session flags or historical reconstruction evidence do not manufacture that page.

## 10. AI boundary audit

The Phase 8 admission map still accounts for every production provider entry: Engine and Controller call `AIManager.draft`; manager has precisely two generation calls, novel narrative and grounding audit; BrowserAIProvider/CodexWritingProvider implement transports. No direct provider call was found in factual resolution, eligibility, identity, retry, uploads or submission. `providers.py` is ATS detection. Mapper fallback remains unconfigured in production.

Manager repeats admission even for direct callers, consults the deterministic resolver, current field policy, verified writing and exact templates, then compatible unverified cache. Missing facts stay unresolved. Cache keys bind descriptor/question/limits/employer/requisition/profile/fact/writing/policy/provider/context revisions. Cache payload hashes and post-await revision checks supplement this. Generated text remains `verified=False`; use/provenance is recorded only after accepted browser commitment, not merely consideration. Cache eviction preserves historical proposal provenance.

The audit explicitly exercises: **Describe your citizenship; Tell us your visa status; Describe your criminal history; Explain your disability; Tell us your gender; Describe your GPA; Tell us your graduation year.** With an empty temporary profile, each must remain unresolved and direct draft must reject before either provider transport. No sensitive fact is supplied or inferred. This exact-label check extends the existing broader factual adversarial parameter set.

Model grounding checks validate IDs/exact source quotes and deterministic constraints but do not prove arbitrary semantic entailment. Provider mocks demonstrate policy, provenance, cache and failure handling, not real model accuracy or authenticated service health. Optional-narrative failures intentionally continue to request input rather than silently skip.

## 11. Configuration/secrets audit

Config reads public defaults plus root-relative private config; profile snapshots read the canonical private profile; browser state and resume paths stay below that root. Test fixtures construct temporary roots, disable Discord/Gmail/AI except fake-provider cases, and use synthetic PDF/profile/history. This audit did not instantiate `Config()` against the repository's private root, read `.env`, load private history, or authenticate anything.

CLI provider metadata invalidation calls `Path.stat()` on `auth.json` (timestamps/size/inode/path plus settings fingerprint), not `read_text` or JSON credential parsing. Real CLI execution is blocked by the validation plugin, API-key variables are removed from the fixture environment, and credential metadata points to temporary storage. Production CLI transport also removes OpenAI/Codex API-key routing variables and sanitizes transport errors.

Gmail credentials are read only by its explicit enabled service path; disabled fixtures never invoke it. Discord tokens are environment inputs; normal validation blocks real transport. Source review found no token/key logging on these paths. Security diagnostics use URL/text redaction, request telemetry excludes payloads/headers and masks opaque IDs, and Engine records exception type/stage rather than raw exception messages. Existing redaction tests exercise credentials and upload metadata.

The existing Git-index privacy checker and an additional current publishable-file pattern scan found **zero findings**. The latter includes untracked Phase 8/audit artifacts and suppresses matching content if a finding occurs. See [publishable-secret-scan.json](final-acceptance-2026-09-27/publishable-secret-scan.json). This confirms no detected committed/index/current publishable key patterns; it is not a proof over every historical Git blob or every possible secret encoding. Ignored private configuration is deliberately unread, not certified secret-free.

Profile facts are not copied wholesale into narrative cache/provenance: stored signatures/revisions/source IDs/quote hashes reference facts; answer text lives in the writing record. Answer/history sidecars and fill checkpoints intentionally contain answers and therefore remain private operational data; they are not anonymous. Audit artifacts contain synthetic cases and source metadata only.

## 12. Test-quality review

- No `pytest.skip`, `skipif`, `xfail`, flaky rerun marker/plugin configuration or exclusion was found in the repository test suite/configuration. Words such as `skip` in manual-command behavior are functional coverage, not skipped tests.
- The complete baseline is run without selection/exclusion using both offline plugins. Exact JUnit IDs, duplicates, missing IDs and nonpassing outcomes are checked, rather than accepting only the printed count. Saved Phase 1–7 exact ID sets are compared as well.
- Phase 8's changed `test_narratives.py` assertions were inspected: duplicate top-level context fields became one context object; lookup-time use events became an asserted absence before save and an asserted provenance event afterward; raw supporting text became source IDs/quote hashes. The provider request still contains the actual grounding source, verification remains false, and submission assertions were not weakened.
- Earlier documented intentional changes are consistent: distinct known requisitions no longer fail title-only dedup; standing consideration is pure; rate-limit is a security hold; attempt accounting begins at work; checkpoint readiness no longer claims live/replayed state; normal resume no longer reloads live classes. Retained behavioral tests exercise the stronger contracts. Exact-ID retention alone would not prove equivalent assertions, so these changes were also reviewed.
- Mock scope is material: fake providers cannot validate writing quality; fixture DOMs cannot certify arbitrary live ATS; the audit checkpoint persistence case mocks page inventory only. Local browser tests separately run real discovery, input/readback, attachment UI, Next, final physical input, requests, cleanup and fill-only counters. Source/implementation-count tests supplement these; they are not the only safety evidence.
- Tight pre-existing 3-second local browser timeouts and prior phase timeout reruns are documented. No timeout or assertion is weakened for this audit. Any failure is retained in the final validation history rather than hidden by a rerun plugin.
- Offline guard limits: Python DNS/connect guards apply during fixtures; browser routes permit later synthetic fulfillment; Chromium DNS/background restrictions provide another layer. This is not a universal OS firewall, import-time sandbox, or arbitrary subprocess network interceptor. Tests do not require those unsupported behaviors; real writing subprocess entry is separately blocked. The external-services/provider negative test exercises the current guard.

No baseline test was deleted/renamed/edited by the audit. The **12** audit-only cases cover the exact seven requested labels and five export/restart operations; their concrete gap and mock boundary are described above. They are not silently counted as part of the 1,023 Phase 8 baseline.

## 13. Final validation results

The complete guarded suite passed **1,023/1,023** exact Phase 8 IDs: **0 failed, 0 errors, 0 skipped**, with no missing, duplicate or unexpected IDs. Pytest reported **885.66 seconds**. This was one full run, with no failure rerun or timeout adjustment. Saved Phase 1–7 sets (631, 655, 690, 725, 815, 851 and 934 IDs) are all retained and passing. See [test-results.json](final-acceptance-2026-09-27/test-results.json).

The **12 additional audit-only cases passed**. After their first green run, assertions were tightened to require the explicit factual-admission rejection reason and to compare the retry deadline before/after restart; all 12 passed again. Both raw runs are retained as `audit.xml`/`audit.log` and `audit-final.xml`/`audit-final.log` under the ignored audit output directory. [audit-results.json](final-acceptance-2026-09-27/audit-results.json) records the final test source's results. These are supplemental cases, not changes to the 1,023-ID baseline.

All **25** safety-matrix rows have passing primary regression evidence. The measurement runs below all exited **0**; the 9 browser and 83 lifecycle/fill cases overlap the baseline and are not counted as additional unique coverage.

| Harness | Final result and compatibility evidence |
| --- | --- |
| Phase 3 unchanged maintenance | At 100 and 1,000 listings: **9 SELECTs, 0 UPDATEs, 0 observation writes, 0 statistics refreshes**, 2 SQLite changes. Maintenance counts match Phase 7. The verified-writing query now uses Phase 8's `verified_writing_lookup` index instead of the older pre-index scan; this is the sole non-timing comparison difference. |
| Phase 4 persistence | All workload counts match: separate 10 events use 20 commits/10 exports/20 rewrites; batch uses 2/1/2. Unchanged security has 10 short transactions, zero writes/exports. Probe observations defer export until the final flush. |
| Phase 5 factual resolution | Both workloads: 1 profile read, 3 SELECTs, zero writes/events/AI. Twenty repeated first-name requests compute once; 300 representative requests compute 15 resolutions. All counts match. |
| Phase 6 browser work | **9 passed in 48.56s**. Every non-timing counter matches; repeated generic inventory uses 1 full inventory, Greenhouse 2, SmartRecruiters 1. These ordinary adapter fixtures include the context's blank page; their context-page count is not the fill-only application-tab invariant. |
| Phase 7 lifecycle/control | Claim, begin-attempt, failure and recovery counts match, each with 1 application update/1 history sync; failure/recovery each decide retry once. **83 passed in 104.40s** under control instrumentation. Sequential batch: 3 opens/3 processing passes/9 protected-set scans; explicit reconstruction: 3/6/15. Successful fill-only batches retain zero final clicks, submission requests and intents. All recorded control counters match. |
| Phase 8 narrative/provider | Representative: 4 proposals, 3 input holds, 8 fake-provider calls, 1 provider construction/readiness check. Repeated 20: 19 cache hits/1 miss, 2 calls (generation plus audit), 1 prompt/context build, 1 profile read, zero use events. All counters match, including 27 SELECTs/3 writes for repeats. |
| Phase 8 architecture ratio | Matches: 10 deterministic facts, 3 input holds, 1 deterministic narrative template, 2 generated proposals, 4 fake-provider calls. No writing-quality or real-provider readiness claim. |

[measurement-comparison.json](final-acceptance-2026-09-27/measurement-comparison.json) contains every non-timing difference against named preserved baselines. [harness-runs.json](final-acceptance-2026-09-27/harness-runs.json) records commands, exit status and elapsed time; adjacent `phase3.json` through `phase8.json`, `control-work.json` and `ratio.json` preserve current samples. Phase 4 was compared against Phase 7's saved persistence rerun, which Phase 8 also retained compatibly. No regression was found and no optimization was performed.

Final source/protected-artifact hashes match Phase 8; Git whitespace validation passed. Current publishable artifacts pass the secret-pattern scan. [integrity.json](final-acceptance-2026-09-27/integrity.json) binds this report and the audit evidence to the final source verification.

Reproduction (PowerShell, repository root). The runner executes checks sequentially and supplies both offline guards to pytest, including its browser/control subprocesses. Preserve this audit directory and its raw output before reproducing, because these commands replace this audit's evidence; prior phase evidence is never overwritten.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'docs/phase5-2026-09-25') + ';' + (Join-Path (Get-Location) 'docs/phase8-2026-09-27')
.\.venv\Scripts\python.exe -u -m pytest -p offline_validation -p offline_providers -q --tb=short --junitxml=.tmp/final-acceptance-2026-09-27/full-suite.xml
.\.venv\Scripts\python.exe docs/final-acceptance-2026-09-27/evidence.py .tmp/final-acceptance-2026-09-27/full-suite.xml
.\.venv\Scripts\python.exe -u docs/final-acceptance-2026-09-27/run_checks.py
.\.venv\Scripts\python.exe docs/final-acceptance-2026-09-27/compare.py
.\.venv\Scripts\python.exe docs/final-acceptance-2026-09-27/matrix.py
.\.venv\Scripts\python.exe docs/final-acceptance-2026-09-27/evidence.py
```

All workloads are synthetic/local/intercepted; measurements are compatibility counts and single timing samples, not real employer throughput. Existing intercepted submission fixtures set submission policy only in disposable databases; the audit never enables production auto-submit.

## 14. Remaining risk classification

| Class | Finding / disposition |
| --- | --- |
| BLOCKER | None found in the source audit, complete guarded suite, supplemental crash/admission cases or compatibility harnesses. |
| PRE-OPERATIONAL QUALIFICATION | Supported ATS variant/redirect/frame/upload/confirmation behavior needs controlled evidence for the intended destinations. Local fixtures do not prove live compatibility. |
| PRE-OPERATIONAL QUALIFICATION | Actual environment permissions, approved destinations, dedicated profile ownership, human handoff/session continuity and operator reconciliation must be qualified before live trust. No such authorization is supplied here. |
| PRE-OPERATIONAL QUALIFICATION | If narratives will be enabled, authenticated provider/model readiness, prompt privacy and human-reviewed output correctness need separate evidence; offline fake transport cannot supply it. |
| PRE-OPERATIONAL QUALIFICATION | Freeze/identify the reviewed uncommitted Phase 8 source in a release commit before operational rollout; maintain exact-source/evidence linkage. This is packaging/provenance work, not a source safety flaw. |
| DEFERRED OPTIMIZATION | Export under the SQLite write lock, constructor history initialization, full dirty statistics, coarse invalidation and narrow fixture corpus remain documented. No observed regression requires redesign. |
| ACCEPTED LIMITATION | Serial execution, bounded supported controls, conservative holds, incomplete legacy-identity ambiguity and optional-narrative failure holds are intentional. |
| ACCEPTED LIMITATION | Grounding audit is not formal entailment; generic upload evidence is not universal remote receipt proof; finite endpoint/click classification is supplemental; unexpected popups may briefly exist before closure. |
| ACCEPTED LIMITATION | Existing identity parsers implement provider conventions rather than general origin authentication; unknown/custom/malformed identities require conservative review and qualified destination scope. |

## 15. Deferred-work assessment

| Deferred item | Assessment | Reason |
| --- | --- | --- |
| Revisioned history snapshot/ack exporter | Safe to defer | Current lock/dirty-ack scheme preserves durability; changing it would create new correctness work. |
| Read-only Database construction | Safe to defer | Constructor repair/export is documented; pure reporting optimization is not an internal safety gate. |
| Incremental history statistics | Safe to defer | Current aggregate semantics and compatibility counts retained. |
| Broader ATS fixture coverage | Safe to defer before **entering** qualification; targeted evidence required during qualification before trusting additional variants | Existing supported fixtures are the internal scope, not proof of every employer form. |
| Finer-grained profile/writing invalidation | Safe to defer | Conservative invalidation may miss cache reuse; it does not reuse incompatible facts. |
| Cross-requisition narrative sharing | Not recommended in current release | Broadens factual/context compatibility risk without acceptance need. |
| Additional deterministic templates | Safe to defer | Existing grammars abstain when unsupported; new grammars require explicit factual evidence/tests. |
| Optional-narrative skip behavior | Safe to defer | Current input hold is intentional; changing completion policy needs separate authorization. |
| Provider transport changes | Safe to defer | Existing transport boundaries suffice internally; actual authenticated behavior belongs to qualification. |
| Concurrency | Not recommended in current release | Serial browser/lifecycle/persistence assumptions are deliberate; parallelization is out of scope. |

No listed optimization is required before entering a separately authorized qualification stage. Qualification itself must produce destination/environment/provider evidence appropriate to the intended operational scope.

## 16. Objective readiness gates

| Gate | Result | Objective basis |
| --- | --- | --- |
| G1 All tests green | **PASS** | 1,023 baseline and 12 audit-only cases passed; 9 browser and 83 control measurement reruns passed; no failures/errors/skips. |
| G2 All Phase 8 baseline IDs retained | **PASS** | Exact 1,023-ID comparison: no missing, duplicate, renamed or unexpected IDs; all earlier saved baseline sets retained. |
| G3 No unresolved submission-safety bypass | **PASS** | Source ordering and intercepted tests establish intent before one physical final interaction, affirmative confirmation or protected uncertainty; no alternative final path found. |
| G4 No retry/recovery bypass | **PASS** | Shared lifecycle policy, durable budget, delivery/hold guards and pre/post-begin crash cases pass. |
| G5 No factual-AI bypass | **PASS** | All provider callers accounted for; factual admission, REJECTED_FIELD and seven exact adversarial-label checks pass with zero provider use. |
| G6 No duplicate/protected-state bypass | **PASS** | Shared identity/conflict guards, imported-history protection, final-state/retirement controls and distinct-requisition regressions pass within recognized identity scope. |
| G7 No fill-only submission path | **PASS** | Early return plus lifecycle/probe/route defenses; successful serial fixtures have zero intents/clicks/requests and one application page; deliberate violations latch and stop. |
| G8 Persistence crash semantics remain valid | **PASS** | Five export-failure/restart operations preserve authoritative SQLite state; dirty work is retried; baseline rollback/acknowledgement/browser-order tests pass. |
| G9 Offline validation credential/network independent | **PASS** | Complete suite uses existing socket/browser/provider guards, temporary credentials and local/intercepted fixtures; no real service or private history required. Guard scope is stated in section 12. |
| G10 No concrete BLOCKER remains | **PASS** | No concrete correctness/safety defect found; remaining qualification needs, accepted limits and deferred work are separately classified. |

**The repository meets the internal gate for entering separately authorized operational qualification.** These PASS results are bounded by the examined source, supported contracts and offline evidence; they do not certify arbitrary ATS forms, model quality, production credentials or operational destinations. No live stage was started.

## 17. Recommended next stage

With all internal gates passing, the recommended next stage is **separately authorized operational qualification** with a frozen source identity, explicit intended destinations/mode, dedicated temporary or approved browser profile, measured stop/hold/recovery behavior, and evidence retention. Begin with bounded fill-only qualification if separately authorized; any provider authentication, external messaging or final submission needs its own applicable authorization. Do not treat this audit as that authorization.

This audit stops with the report and offline evidence. No Phase 9, live application, credential change, external provider call, Gmail/Discord message or production submission is performed.
