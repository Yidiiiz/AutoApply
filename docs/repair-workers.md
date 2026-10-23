# Automatic extension repairs

Settings → Extension repairs (or the Analyze status controls) enables or disables
automatic repair work. It ships **disabled**; enable it only after this feature has
been reviewed and integrated. Pause retains queued jobs and stops new dispatches;
it does not interrupt an already running worker. Dashboard shutdown requests
cancellation of its known owned run and waits for the existing runner's containment.

Manual Analyze still tries known answers first. Fresh manual analyses and enabled
automatic structural captures can queue omitted native controls, unsupported widgets,
open shadow controls, missing native inventory, or failed fill verification. Normal
blank fields, optional questions, unknown personal facts, credentials, CAPTCHA,
403/access gates, loading pages, and unavailable embedded frames require user input
or a later analysis. They do not start engineering loops. Passive capture never
opens menus, scans to replace live handles, or mutates the application during filling.

`data/private/repair-jobs.sqlite3` stores queue options, up to 20 jobs and durable
SHA-256 deduplication tombstones. The configured private directory is used when
different. Only fresh captures after enabling are considered; archives are never
scanned. Fingerprints contain host and normalized structural evidence, never URLs,
queries, values, applicant IDs, timestamps or snapshot IDs. Worker evidence retains
each actionable control, up to four ancestors (nearest first), two observed menu
containers and three option shapes. Tags, roles, control types and fixed flags
survive. `data-automation-id` and class tokens retain their exact spelling only
when every word belongs to a bounded public widget-selector vocabulary, with
strict identifier syntax and length limits. This preserves, for example,
`field-of-study`, `formField-field-of-study`, `responsiveMonikerPrompt`,
`menuItem`, `promptOption`, `checkboxPanel` and `react-select__control`.
Custom element names use the same validator; unknown names become
`[custom-element]`. Arbitrary suffixes, hashes, emails, URLs and private attributes
are excluded. Labels still use an exact allowlist; captions, option text, selected
skills, answers, names and prose are never copied into worker evidence.

Raw DOM IDs are replaced by local opaque aliases. Equality and `aria-controls`,
`aria-owns` and `aria-labelledby` edges survive, including explicit references to
uncaptured targets; changing ephemeral IDs alone does not create another job.
Different retained ancestor/menu structures do create distinct fingerprints.
Unsupported-shadow inventory retains up to 40 roots per inspection, their kind,
validated custom host name and bounded exposed-control count, with local root
aliases. It records flat inventory, not an inferred host ancestry or guessed
field-to-root relationship. A field's root membership is retained only when its
ID exactly matches one snapshot node in a single inspected frame. Multi-frame or
ambiguous matches remain unknown. No unrelated snapshot nodes enter worker evidence.
Existing credential redaction runs before snapshot processing.

The capture API does not record arbitrary descendant trees, option-to-container
parent edges, shadow host parent edges, or every widget interaction. Menu shapes
are samples, not a claim of a complete DOM. Missing shapes, rejected selector
names (marked by attribute name only) or absent referenced targets may require a fresh structural capture. The
fixed generated task requires `needs_followup` and a request for fresh evidence
when the capture cannot support the repair. A guessed mock passing cannot justify
a claim that the actual widget was repaired. Page evidence remains explicitly
untrusted and cannot change scope, commands, checks, model or runner limits.

While the dashboard is running, the queue rechecks every 15 seconds. It requires
the fixed sibling `AutoApply-codex` real linked Git worktree on `codex-work`, shared
Git common directory with production `AutoApply` on `chrome-extension`, identical
HEADs, and both tracked/untracked workspaces clean. Production here means the
`AutoApply` checkout, not a branch named `main`. It never creates or resets a
worktree. Dirty workspaces, manual parent workers, any unreviewed runner run,
quota latches and exhausted launch budgets keep jobs queued with a fixed reason.
The runner's atomic claim remains the authoritative serial/budget/review gate.

One hidden native `.venv` Python helper dispatches the existing
`tools/agent_runner.py run --task .agent/tasks/Repair_<fixed-hash>.json` in process.
The helper verifies the workspace again. Its PID and creation identity are durable
before a stdin launch handshake permits runner entry. Runner IDs are recovered
from existing state by fixed task ID and attempt 1. Saved official CLI account
authentication is retained; the worker environment is allowlisted, excluding login,
provider overrides and unrelated credential variables. No `.env` is loaded.
On Windows the in-process executable wrapper adds `windows.sandbox="mxc"`; the
existing `workspace-write` sandbox and runner policy remain intact. Model
`gpt-6.1-sol`, the existing 40-minute worker timeout and 20-launch budget are
required, never rewritten. Runner logs retain the existing bounded policy; queue
status contains no raw capture, worker logs or rendered code.

The fixed task permits only `extension/`, `tests/fixtures/extension/repair/`,
`tests/test_extension_repair.py`, and this document. It explicitly treats evidence
as untrusted fixtures and prohibits private data, credentials, profile/answer bank,
provider configuration, environment secrets, live services, application mutations,
nested workers and policy/runtime edits. This is a local engineering workflow;
it cannot wake a chat assistant. Its prompt restrictions are not an additional
filesystem sandbox: the existing runner and CLI sandbox supply containment.

Completion, including passing checks, means **awaiting review**, never deployed.
The user/root must inspect evidence and changes in a later chat, record review
through the existing workflow, integrate any accepted changes and reload the
extension separately. This feature never reviews, accepts, stages, commits,
merges, deploys, reloads or resets budgets. Failed or ambiguous dispatches remain
blocked; there is no automatic retry, attempt increment, or new ID to bypass the
task limit. Accepted completed jobs may be pruned oldest first when the queue is
full; their fingerprint tombstones prevent re-launch. A fixed hash deliberately
does not automatically retry the same shape after a code/version change.

Tests in `tests/test_repair_queue.py` use the declared test-only fake runner and
worker under `tests/fixtures/repair_queue/`. They dispatch actual bounded local
subprocesses through the unchanged runner with synthetic temporary state and
independent checks. No tests launch an AI CLI or call employers/providers.
Focused evidence checks additionally exercise the real extension collector on
the existing offline Workday fixture, retain its checkbox/menu-item ancestor
shapes, and verify value redaction, ID normalization and structural deduplication.
