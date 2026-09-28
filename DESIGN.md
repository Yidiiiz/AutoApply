# AutoApply implementation

## Data flow

```text
Git repositories / opt-in browser sources
  -> dated Listing records
  -> canonical URL + tenant/requisition identity
  -> SQLite jobs + all source references + one application
  -> freshness / priority queue
  -> persistent browser / listing description
  -> deterministic eligibility or per-application question
  -> semantic form inspection
  -> verified answers / confirmed writing / manual verification
  -> validation / pause controls / submission ceiling
  -> durable SUBMITTING intent
  -> positive confirmation or MANUAL_REVIEW
  -> SQLite audit + private readable archive
```

One asyncio worker owns browser application processing. Discord handles interactions concurrently on the same event loop. Blocking Git and Gmail work runs through `asyncio.to_thread`; SQLite stays on the event-loop thread. SQLite uses WAL, foreign keys, a schema version and short explicit transactions. An OS-backed file lock prevents two workers from using the persistent profile. CLI status/control commands can use separate SQLite connections while the daemon is running.

## Core invariants

- `null` profile data is unknown, never implicitly false. Examples are blank; actual profile data is ignored.
- Personal answer matching uses exact normalized questions and narrow semantic concepts, not unconstrained fuzzy similarity. Immigration negation and compound questions require review.
- Arbitrary questions and written answers are application-scoped; recognized personal facts can be reused globally. A changed field signature/options invalidates prior answers.
- Existing prefilled text without verified provenance is not accepted merely because the browser shows it. Optional blank fields can be skipped; checked/prefilled unknown fields require review.
- Dates retain original source evidence. Relative dates use commit time. Missing dates are not treated as newly posted. Reposts with a new requisition identity remain separate.
- No title-only ingestion deduplication: identical titles can represent different requisitions. Tenant-qualified IDs avoid merging employers' Workday IDs. Final URL collisions link source references to an existing canonical job. Before submitting, unresolved or submitted matching employer/title/ATS aliases conservatively require review.
- Required fields, browser validation, question resolution, confidence, resume digest, global pause and auto-submit state are checked before submission. Unknown selectors/layouts stop the affected application.
- Submit intent is persisted before the click. Confirmation is required to mark success. A crash after intent requires explicit reconciliation, even when this conservatively holds an application whose click never happened.
- Captchas, login forms, security blocks and usage limits produce holds. No solver, stealth plugin, security bypass, credential cycling or email sending is implemented.
- New generated writing is stored unverified with provider and evidence IDs. Only a user-confirmed answer becomes trusted memory.
- Private archives are supporting records; queue decisions query SQLite. Windows archive paths are bounded, sanitized and prefixed with numeric IDs.

## Modules

| Module | Responsibility |
| --- | --- |
| `config`, `models` | YAML/profile access and shared data types |
| `database` | Schema, queue, source provenance, questions, audit and recovery |
| `jobs`, `sources` | Identity, dates, location, eligibility and source parsing |
| `browser`, `applications` | Persistent Playwright lifecycle, detection and form adapters |
| `answers`, `narratives`, `ai`, `codex_writer` | Verified fact matching, deterministic writing templates, immutable narrative signatures, separate unverified proposal cache, and session-owned configured providers |
| `gmail` | Read-only OAuth and scoped verification extraction |
| `control`, `discord_bot` | Shared CLI/DM controls, pending questions and durable outbox |
| `engine`, `runtime` | Worker orchestration and exclusive process lock |
| `archive`, `privacy` | Private archives and Git-index safety checks |
| `providers`, `security` | ATS/provider recognition, multi-layer inspection, pre/post-submit classification |
| `retry`, `handoff` | Explicit retry categories, preserved manual tabs, diagnostics and resume |
| `cursor` | Serialized physical input, target geometry, planning, ownership and cancellation |

Schema v2 preserves queue states and adds independent application, security and verification dimensions. A confirmed submission is immutable across later verification failure. The worker owns manual tabs in memory; durable resume requests connect CLI/Discord to the same session. A restart retains the hold but does not claim to recover live form contents. See [SECURITY.md](SECURITY.md) for the state mapping, provider matrix, retry policy and operator workflow.

## Validation and remaining live work

Deterministic tests cover canonical URLs, requisitions, dates, locations, eligibility, answer matching, option changes, queue transitions, independent pending applications, archives, privacy, configuration, process locking, Gmail ambiguity, Discord authorization and unpaid AI enforcement. Chromium tests exercise complete native forms with resume upload and server-observed submission data, user-answer resume, auto-submit off/on, no-confirmation holds, pause immediately before clicking, browser cookie persistence and common site conditions.

Public repository snapshots were used to validate all three parser formats and default-branch discovery. Discovery can run without the user's private contact details, resume, Discord token or employer sessions. Real employer submission, authenticated social sources, live Discord delivery, Gmail OAuth and AI providers require the user's missing setup and subsequent site-specific validation. Successful fixture tests do not establish universal compatibility.

Workday dominates the discovered open queue, but it commonly requires authenticated, tenant-specific flows. Its generic adapter deliberately holds unsupported forms instead of claiming complete Workday support. Greenhouse/Lever/Ashby share the tested semantic handler with small entry-point specializations. Add concrete adapter behavior and captured synthetic regression fixtures when authenticated real layouts reveal a need.

Known conservative boundaries: unknown graduation sub-year restrictions and complex required skills/availability ask the user; no automatic academic-calendar inference; no automatic employer account creation/password storage; required extra documents need manual preparation; no unrestricted application chatbot; Gmail extraction remains read-only and protected verification requires manual completion; social internal dialogs use the generic handler and are not certified end to end. These do not get silently marked successful.

## Cursor subsystem

`CursorController` owns a complete interaction lock and an explicit state machine.
Callers capture an operation generation before waiting for the lock, so cancellation
invalidates queued work as well as the active path. Normal clicks cannot interleave
movement, press or release. Low-level `press` / `move_to_point` / `release` retain
ownership by asyncio task; use `drag` for an atomic drag. Other tasks cannot borrow
held buttons. Cancellation and manual handoff invalidate immediately, then release
only controller-owned buttons and modifiers. Cleanup is idempotent. Page teardown
discards input ownership along with the destroyed browser context.

Target identity remains a Locator. Handles are transient and disposed after frame
resolution/hit testing. Actionability checks cover strict cardinality, connection,
visibility, enabled state, stable geometry and event reception. Playwright trial
clicks are intentionally avoided: they move the mouse, which breaks persistent
position and continuous path ownership. Ancestor frames scroll into view before
the child target. Bounding boxes and physical input always use main-frame viewport
CSS pixels, independent of device scale factor. Frame-local coordinates are used
only for recursive hit tests, including occluding elements over ancestor iframes.
Axis-aligned positive frame scaling is supported; rotated, skewed or perspective
frame transforms are rejected conservatively.

Geometry observers track DOM mutations, scrolling, resize, visual viewport changes
and layout shifts in each frame. A changed generation stops an element path before
its next sample. Long paths additionally recheck actionability and target geometry.
Bounded replanning starts at the last dispatched physical position. Every press
rechecks geometry and the actual arrival point. Pointer capture during drag uses
held-button movement without requiring the source to remain under the pointer;
viewport changes still abort the drag. Navigation cancels active stale interactions.

The pure planner uses bounded cubic control points, configurable easing, 12–60
samples by default, 250–1000ms planned duration, and 4–50ms planned sample spacing.
Execution uses cumulative monotonic deadlines; browser latency may extend real
duration. Seeded owned/test profiles add smooth sparse noise, optional bounded
overshoot and timing/curve variation. Endpoints remain exact. Restricted mode
rejects those settings. Exact origin checks apply to the page and target frame chain.

`InputStateManager` claims input before dispatch because an exception may occur
after delivery. Both backends preserve button, modifier and click-count semantics.
CDP emits mouse movement, down and up through one session; Playwright emits all
three through its mouse API. Native selects/uploads are explicit framework
operations under the same interaction lock, never retries after physical clicks.

Diagnostics include an interaction ID, state/generations, backend, positions,
boxes, path parameters, validation/replan events, input options and outcome.
They deliberately omit selectors, element text, URLs, form values and exception
messages. The controller keeps a bounded 200-event buffer and logs at DEBUG.
Submission classification and durable submission intent remain the engine's
responsibility: a released button does not establish application success.

`tests/test_cursor.py` covers pure planning and seeded-policy bounds, physical
event ordering with both backends, persistence, button/modifier semantics,
drag capture, scrolling, child-frame occlusion, hover changes, viewport changes,
detachment, serialization, cancellation, security holds and manual synchronization.
All browser fixtures are local/intercepted; they do not validate live employer
layouts or certify compatibility with every browser/widget.

Cursor validation on this Windows checkout: **207 tests passed**, including
**65 cursor tests** (full suite: 386.41 seconds). Both Playwright and Chromium CDP
input passed physical event-order, button/modifier, pointer-capture and DPR 2
checks. The suite also passed scaled/cross-origin iframe targeting, cancellation,
manual recovery and the existing submission/retry-safety regressions. Ruff syntax
and undefined-name checks passed across the application/tests, broader `F` checks
passed for cursor code/tests, and Mypy with untyped-body checking passed for all
six cursor modules. Bytecode compilation, wheel build, CLI help and the Git privacy
check passed. Validation did not submit any live employer application.

Application-history storage now preserves the original multi-file bundles beneath
status directories. Each canonical `application.json` is self-contained for
statistics (identity, job/source metadata, timestamps, transition/security history).
Database triggers enqueue affected IDs and capture state/security changes inside
the same transaction as the live mutation. Export runs after commit, under a DB
write lock and an OS history lock; failed exports leave the queue pending. A
restart replays committed updates. This also covers existing direct SQL mutations
and avoids publishing rolled-back transitions.

A bundle move writes and fsyncs the canonical record before renaming the directory
on the same filesystem. An interruption can leave a record in the wrong status
folder; startup repairs it using its internal state. Statistics replacement is
atomic, but the database, directory rename, sidecars, and statistics cannot form
one filesystem transaction. The durable queue and explicit rebuild provide
recovery. Callers must reacquire the current bundle path after a transition.

History maintains a process-local record cache and notices statistics revisions
from other processes. Statistics are recomputed from cached canonical records on
each committed mutation; explicit rebuild/startup validates on-disk records.
This favors correctness at the current scale. Very large histories may warrant
incremental aggregates; external manual edits require validate/rebuild to refresh
the cache. Previous sidecar paths inside historical diagnostic events remain
historical evidence; the current application's screenshot path is relocated.

## Listing freshness and lifecycle

`freshness.py` owns the maximum 30-day policy, UTC normalization, evidence
priority and closure signals. Relative ages resolve once against a source
revision or observation timestamp, never against each subsequent poll. Generic
updated timestamps do not reset age. Source IDs, canonical URLs and ATS IDs are
resolved before scoring; earlier posting evidence wins unless a source explicitly
supplies a genuine repost. A changed URL for a rejected source ID cannot evade
the age policy. Unrelated requisitions are not merged by fuzzy company/title.

Ingest stores only dated, fresh, open opportunities in `jobs`/`applications`.
Rejected discoveries use `listing_observations` and source aliases without an
application or full description. `ListingStore` adds lifecycle metadata to jobs,
an `active_listings` view, startup/discovery cleanup, queue guards and statistics.
Existing jobs remain durable historical/deduplication records; culling removes
active membership rather than deleting rows referenced by application history.
Cleanup never transitions applications. An attempted application that is no
longer eligible stops with a distinct stale/closed/unknown reason, without an
automatic retry. Existing submission intent/manual evidence is preserved.

Eligibility is time-dependent: the persisted active view is refreshed by startup,
discovery and queue access; a read of that view alone is not a submission permit.
Claims, application start, AI drafting and the point immediately before durable
submission intent revalidate age. Page availability and security are checked
again before submission, with authentication/security holds taking precedence
over ambiguous page text. There is no history-wide network closure sweep.

Source discovery is bounded by pages and results. A stale-page early exit requires
an explicit newest-first guarantee and a complete nonempty stale page; none of
the present adapters promises that ordering. Batches commit once, then flush the
durable history queue and update statistics, avoiding an archive/statistics
rewrite for every card. DB-to-history lock ordering is retained. Listing stats
can be rebuilt from observations; history statistics merge them from a private
sidecar. Unique observations, duplicate sighting events and current eligibility
are separate quantities, not additive stages of a funnel.
