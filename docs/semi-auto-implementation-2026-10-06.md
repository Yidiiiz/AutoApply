# Semi-auto and development-runner delivery

Implementation is local and preserves the pre-existing working tree. Start with
[VS Code instructions and prompts](semi-auto-vscode.md). The supplied handoff was
used as design reference, with the actual user request defining the two loops.

## Application runtime

`semi-auto` uses the existing priority selection, eligibility checks, controller,
adapter pipeline, canonical document checks and durable checkpoints. It retains
up to five forms while filling one at a time. It requires a fresh dedicated
owner and visible browser. Existing holds and attempt budgets remain in force;
the historical one-tab fill command keeps its original limit.

Discord sends a batch review summary and deduplicated unresolved-question cards.
Answer menus/modal responses are owner-authorized, exact-option validated and
persisted through Controller. Supported input holds can revalidate their existing
page. Refilling has a separate phase so Review cannot race an automatic writer.
An unexpected unowned page stops execution even when fewer than five tabs exist.

Review is explicit: the owner brings a form forward through Discord, reviews it,
and performs the employer's final action. No bot control clicks Submit. Automated
processing cannot run while review is armed. Final click/request evidence latches
an uncertain manual outcome; a reported receipt retires automation and is kept
distinct from observed ATS confirmation. Closing a form does not imply submitted.
After all forms are closed, Next five selects another batch. Shutdown checkpoints
progress and returns paused with auto-submit off.

## Recognition and answer reuse

Existing typed mapping, canonical facts, verified memory and revision caches
remain in place. New small grammars recognize compatible ordinary experience
Yes/No questions and reuse explicit user choices across equivalent subjects.
Negation, durations, additional qualifications, special legal/personal wording,
changed profile facts, different options and conflicting field metadata abstain.
Broad example-based and combined claims have separate keys from narrow claims.
Explicit REQUIRE_USER/DO_NOT_ANSWER policy always wins. Recognition needs no
model request. Optional grounded narrative drafting retains its provider policy.

Research and tradeoffs are in [the matching note](question-matching-research-2026-10-06.md).
No end-to-end live throughput or ATS repair is claimed from local tests.

## Development supervisor

The Claude project command `/autoapply-loop` loads the supervisor contract. Claude
creates scoped tasks, launches one Codex CLI worker through the Python runner,
reviews authoritative reports/diffs/independent checks, persists the decision and
dispatches another task in the active conversation. Default limits are six
launches, three attempts per task, 900 seconds per worker and 20 MB of logs.

The runner validates contracts/paths, snapshots tracked/staged/untracked edits
and protected applicant files, rejects duplicate claims, and records durable
SQLite lifecycle/budget state. Windows uses a gated kill-on-close Job Object;
Linux process groups supply owned-tree containment. Logs and partial edits are
preserved on cancellation, timeout, quota/authentication stop and interruption.
Tasks select trusted check IDs instead of supplying shell commands. No tokens
are copied and no API fallback is enabled.

Doctor verified Codex's native 0.160.0 client and Claude Code 2.1.292, supported
flags and existing subscription sign-ins without inference. The npm Codex shim
reported 0.160.1; executable discovery uses the native client when available.
Model preferences are preserved. Actual signed-in Claude background report
delivery/automatic next-turn dispatch is a user-run smoke test, not a unit-test
claim. The foreground report-return path is the supported default.

## Files

- Runtime: `autoapply/semi_auto.py`, `question_rules.py`, `discord_batch.py`,
  with integration in Engine, Controller, AnswerResolver, DiscordBot and CLI.
- Runner: `tools/agent_runner.py`, `agent_containment.py`, `agent_process.py`.
- Contracts: `agent-config.json`, `agent-task.schema.json`,
  `agent-worker-result.schema.json`, `templates/agent-task.example.json`.
- Launch/prompt: `scripts/start-semi-auto.ps1`,
  `.claude/commands/autoapply-loop.md`, `docs/agent-supervisor.md`.
- Tests: new `test_semi_auto.py`, `test_question_rules.py`, `test_agent_runner.py`.
  All pre-existing test files remain byte-identical to the intake snapshot.

## Verification and preservation

The final focused implementation run passed 76 cases. A separate regression run
passed 121 cases covering legacy Discord/controller behavior, resume revalidation,
protected continuations and checkpoint recovery. Local fixtures demonstrated
five retained forms, verified contact fields and fresh fixture upload readiness;
no final automatic click or submission intent was created. Fake workers exercised
the runner without subscription model usage.

The complete 1,515-case frozen suite was interrupted when the operator requested
terminal instructions and no further execution. It had recorded no failures at
the interruption, but complete acceptance is **not claimed**. The remaining
offline acceptance run is an operator/development-supervisor action.
The evidence directory is `.tmp/semiauto-2026-10-06/`. Earlier restricted-temp/socket
failures, the initial CIM failure, interrupted acceptance runs and a transient
unchanged readiness-test failure are preserved rather than overwritten.

The read-only production audit used raw SQLite rather than Database initialization.
Applications, questions, events, manual commands, requests and settings match the
intake snapshot. Production is paused, auto-submit false, controlled target null,
with no browser owner and no queued commands. Protected checkpoint hashes match.
#3556 remains PREPARED, Tyler #4522 is unchanged, Netsmart #6397 is unchanged,
and Veeam's exhausted attempt budget has not been reset. No employer navigation,
live filling, Discord sends, real Codex worker inference, commit or push occurred
as part of this implementation.

## Existing live-trial applications

This development delivery made no new employer attempts. All five stored
checkpoints still report zero committed fields and no accepted resume upload.

| Application | Preserved stopping point | Missing input or next prerequisite |
| --- | --- | --- |
| Veeam #4611 | Country adapter capability failure; four attempts used | Resume #171 needs fresh canonical ATS upload acceptance; ordinary retry budget is exhausted |
| APEX Data Analyst #5320 | Input checkpoint hold | SQL answer is saved; supported checkpoint revalidation still required |
| APEX Data Engineering #5321 | Application entry unresolved | Experience answers #251/#252 are saved; duplicate Apply entry still needs supported adapter repair |
| Lunar Outpost #6127 | Input checkpoint hold | #238 remains unanswered: “Can you confirm this requirement? Verify qualification: Fueled by a passion for space exploration and the boundless possibilities beyond our world” (Yes/No) |
| CACI #6418 | Security/access hold following HTTP 403 | Supported access resolution; no bypass |

The next concrete operation is the tiny development-supervisor smoke task,
followed by a fresh eligible semi-auto batch. Those operations do not authorize
reopening these held applications or widening their retry budgets.

## Offline qualification addendum (2026-10-07)

Supervisor tasks T001 (semi-auto batch/review/next contract fixes, new
`tests/test_semi_auto_qualification.py`) and T002 (Windows `process_inventory`
UTF-8 decoding; new `tests/test_process_inventory_encoding.py`) were accepted.
Acceptance run v4 exposed an order-independent environmental defect: PowerShell
CIM output was decoded as cp1252, a 0x81 byte killed subprocess's reader thread,
stdout became `None`, and browser startup/cleanup failed closed (1 failure, 10
errors). After T002, run v5 passed **1,545/1,545** with all 1,439 prior IDs
retained, original test files unchanged and frozen source unchanged
(`.tmp/semiauto-2026-10-06/acceptance-audit-v5.json`, 49 min wall time). The
read-only production audit (`final-readonly-audit-v5.json`) still shows paused,
auto-submit false, #3556 PREPARED and protected checkpoints unchanged. No
employer navigation, Discord sends, commits or budget resets occurred.
