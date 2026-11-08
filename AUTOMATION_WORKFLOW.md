# Reusable Claude → Codex project workflow

Version 1.0 · October 14, 2026 · Works as a project instruction and implementation specification.

## Drop-in usage

Put this file at the project root. Open that folder in VS Code and use its integrated terminal. Existing repository instructions and explicit user requirements take precedence; merge this workflow into them without overwriting their content.

This Markdown file does not install a runner or start autonomous processes. It tells a coding agent how to implement or reuse the workflow. If the project already contains `tools/agent_runner.py`, inspect and adapt it instead of building another supervisor.

Paste this into Codex or Claude Code for setup:

```text
Read AUTOMATION_WORKFLOW.md and the existing project instructions and handoffs.
Implement or reconcile this workflow in the current repository. Preserve existing
work and reuse any compatible runner. Run meaningful offline checks, then verify
the signed-in Claude → Codex → report → review → next-task sequence locally.
Do not stop at a plan. Complete what is feasible and record exact blockers.
Give me the single-Claude-session startup instructions when ready.
```

After setup, use the supervisor prompt at the end of this file in one Claude Code conversation. Give Claude the actual project goal and acceptance criteria.

## Purpose

Complete useful work with a streamlined loop:

**Inspect → research only what is needed → select a worthwhile task → implement → validate → review → accept, revise or retire → continue.**

Prioritize the user's outcome, correctness and low maintenance. Research volume, generated code, agent count and token usage are not progress metrics. Prefer a small completed solution over a growing collection of unverified features.

## Roles

| Role | Responsibility |
|---|---|
| User | Defines the goal, constraints and authorized scope |
| Claude Code supervisor | Maintains the plan, chooses scoped tasks, reviews evidence and decides what comes next |
| Codex CLI worker | Implements one assigned task and returns a structured result |
| Local runner | Enforces process limits, durable state, launch budgets and independent checks |
| Deterministic tools | Perform builds, tests, data processing and routine jobs without extra model calls |

One supervisor and one active worker by default. Workers must not create more workers or change budgets. Use the same intended repository folder; only create isolated worktrees when the task needs them and their scope is explicitly tracked.

## Selective task loop

1. **Inspect current state.** Read relevant instructions, progress, Git status and existing implementations. Identify prior edits before changing files. Check runner state before launching another worker.
2. **Screen usefulness.** Ask internally whether the task advances an acceptance criterion, resolves a consequential uncertainty or removes a real blocker. Defer speculative features and unnecessary infrastructure.
3. **Research a bounded uncertainty.** Prefer primary documentation, existing code and a minimal reproduction. Record findings and unresolved assumptions. Stop when there is enough information to make the next decision.
4. **Specify one task.** Give the worker a concrete objective, relevant context, normalized file scope, acceptance criteria, constraints and trusted check IDs. Explain the expected behavior, not just the files to edit.
5. **Run one worker.** Use the bounded local runner. Keep execution foreground initially. Capture events, final result and partial edits.
6. **Validate independently.** Run the relevant checks from trusted project configuration. A worker's claim that tests passed is not evidence that they did. Use accounting, integration or end-to-end checks where the change requires them.
7. **Review actual work.** Claude reads the report, changed files and diff. Compare behavior with the task's criteria and inspect unresolved risks. Passing tests does not automatically prove the product outcome.
8. **Decide.** Accept, assign one specific correction, block on a prerequisite or retire the task as unnecessary. Preserve rejected ideas and reasons. Do not endlessly retry the same approach.
9. **Persist and continue.** Record the decision and next task. Continue within the active conversation and authorized budget until the milestone completes or a stop condition occurs.

At most two candidate approaches under investigation at once. Only one implementation worker executes at a time. Prefer the cheapest action that resolves the largest remaining uncertainty.

## Default limits

These are workflow defaults, not provider usage guarantees. Change them through explicit user instructions or an existing authorized project policy.

| Control | Default |
|---|---:|
| Concurrent workers | 1 |
| Worker launches per budget | 6 |
| Total attempts per task | 3 |
| Worker deadline | 900 seconds |
| Independent check deadline | 300 seconds |
| Combined worker/check log allowance | 20 MB per run |

Every worker launch counts, including failures. Never reset a budget automatically. Supervisor conversation usage is separate from the worker-launch count. Two consecutive implementation attempts with no meaningful progress require a halt and diagnosis. Successful research-only findings can count as progress when that was the declared task; do not require code changes from a research task.

Use short context packets and concise reports. Read large documents once and reference relevant sections afterward. Start with the user's selected model/default effort; escalate reasoning only for a demonstrated difficult task. A model should not repeatedly narrate tests that deterministic tools can run.

## Minimal project files

| Path | Purpose |
|---|---|
| `AUTOMATION_WORKFLOW.md` | This workflow |
| Existing `AGENTS.md` / `CLAUDE.md` | Repository instructions; add a short reference to this workflow while preserving content |
| `tools/agent_runner.py` | Small local subprocess/state runner |
| `agent-config.json` | Budgets, trusted executable arguments and project-specific checks |
| `agent-task.schema.json` | Exact task contract |
| `agent-worker-result.schema.json` | Exact final worker contract |
| `docs/progress.md` | Current milestone, accepted work, blockers and next action |
| `.agent/tasks/` | Scoped task requests |
| `.agent/runs/` | Prompts, baseline, bounded events, results and immutable reports |
| `.agent/state.sqlite3` | Atomic launch claims, attempts, review state and budgets |

Ignore `.agent/`, local environments and credentials in Git. Keep durable decision summaries in project docs. Store raw runtime outputs locally; never put secrets in reports. Do not copy another project's runtime database or budgets into this project.

## Task contract

Use exact keys and reject unknown ones. This example's paths and check IDs are illustrative; replace them with actual project values. Paths are relative to the repository and use `/` separators on all platforms.

```json
{
  "task_id": "T001",
  "attempt": 1,
  "objective": "Implement the smallest complete behavior for the current milestone.",
  "context": "Relevant decisions, observed behavior and source references only.",
  "scope_paths": ["src/feature/", "tests/test_feature.py"],
  "acceptance_criteria": [
    "The documented input produces the required output.",
    "Existing behavior remains compatible."
  ],
  "check_ids": ["feature_tests"],
  "constraints": [
    "Preserve pre-existing edits.",
    "Do not modify orchestration configuration or create workers."
  ],
  "previous_report_id": null
}
```

Each attempt gets a unique durable run ID. Retries increment the attempt number and cite the previous report. The supervisor must not weaken acceptance criteria merely to make a failed task pass.

## Worker result contract

```json
{
  "task_id": "T001",
  "status": "completed",
  "summary": "Concrete resulting behavior and relevant limitations.",
  "changed_files": ["src/feature/implementation.py"],
  "checks_claimed": ["feature_tests"],
  "blockers": [],
  "suggested_next_action": "Review the implementation against the task criteria."
}
```

Allowed worker statuses: `completed`, `needs_followup`, `blocked`. They are claims to validate. The runner computes actual changed files and actual check outcomes independently.

## Runner requirements

Use Python's standard library, subprocesses and SQLite unless a measured requirement justifies another dependency. This specification can also be adapted to an existing equivalent runner; avoid duplicate control systems.

- Launch with an argument list and stdin; never interpolate model-generated prompts into shell commands.
- Validate schemas, task ID, attempt sequence, scopes and final result size. Reject absolute paths, traversal and symlink/junction escapes.
- Record a baseline of tracked, dirty and untracked files. Preserve user changes and detect out-of-scope changes. Protect instructions, runner policy, budgets and runtime state.
- Make concurrency/budget claims atomic. A run awaiting review blocks further dispatch.
- Drain stdout and stderr concurrently. Enforce deadlines while writing stdin too, so a blocked pipe cannot bypass the deadline.
- Bound all output, including the final result, worker events and independent check logs.
- Terminate owned descendants after completion, cancellation and timeout. Use tested native Windows Job Objects or an equivalent ownership mechanism; use process groups on POSIX with separate verification of abrupt-parent-death cleanup.
- Preserve partial edits and logs on failure. Never blindly undo the workspace or rerun an orphaned task.
- Record process-birth identity where supported; do not kill unrelated processes by name or a potentially reused PID.
- Treat scope checks as accountability for trusted coding agents. They are not hardened isolation against malicious code; do not claim stronger protection without OS-level evidence.
- Keep completion reports immutable. Store supervisor reviews separately.

Required commands once implemented:

```text
python tools/agent_runner.py doctor
python tools/agent_runner.py status
python tools/agent_runner.py run --task .agent/tasks/T001.json
python tools/agent_runner.py report --run ACTUAL_RUN_ID
python tools/agent_runner.py review --run ACTUAL_RUN_ID --decision accepted --note "Evidence reviewed"
python tools/agent_runner.py cancel --run ACTUAL_RUN_ID
python tools/agent_runner.py recover --run ACTUAL_RUN_ID --note "Verified interrupted owner"
python tools/agent_runner.py reset-budget --reason "Exact user authorization"
```

Use the project's actual interpreter, such as `.\.venv\Scripts\python.exe` on Windows. The command list is an implementation target when no runner exists.

Supervisor review decisions: `accepted`, `needs_followup`, `blocked`. Only an accepted review unlocks dependent tasks. Do not accept malformed results, failed required checks or out-of-scope changes. Successful execution, accepted software and validated business/research outcomes remain separate states.

## Authentication and Windows launchers

Use supported saved-client sign-in with the user's existing accounts. Check installed CLI help and authentication status without exposing credential contents. No auth-file copying, token forwarding or silent API/provider billing fallback.

Current Codex non-interactive capabilities include JSON events, a final-output schema and a saved final message. A representative worker invocation is:

```text
codex exec --sandbox workspace-write --json --output-schema RESULT_SCHEMA --output-last-message RESULT_FILE -
```

The runner supplies absolute schema/result paths and sends the prompt on stdin. Select model/effort through supported configuration or validated flags; keep them configurable. Do not disable sandboxing merely to avoid setup work.

For an interactive Windows bootstrap, npm's `codex.ps1` may be blocked by PowerShell execution policy. `codex.cmd` can be an interactive alternative. A subprocess runner using `shell=False` must handle Windows launchers deliberately: prefer a native executable or an explicit trusted `node.exe` plus the verified installed JS entrypoint. Do not change machine-wide execution policy or casually enable shell interpolation.

The IDE extension is useful for interactive editing/review. The scripted workers use the CLI. Running the CLI inside VS Code's integrated terminal is sufficient; a separate console window is optional. Native Windows is the default when the project lives on Windows; WSL2 is optional.

## Main-chat reporting and continuation

Baseline: Claude invokes a foreground runner task, receives its report in the same conversation, reviews it and dispatches the next task. The user should not have to copy reports between unrelated chats.

Claude's native managed background tasks may be used after local testing. Retain the managed task ID, retrieve output and demonstrate the next supervisor action. Do not substitute detached processes, arbitrary sleeps or an unverified hook for supported task delivery.

This workflow does not guarantee an always-on conversation or automatic resumption after the client closes. Save progress before stopping. On resume, reconcile existing runs first. Deterministic scheduled jobs can run independently when explicitly needed; an unattended model supervisor is a separate implementation with its own authorization, lifecycle and budget.

## Meaningful verification

Offline fake-worker checks should cover contracts, false success, concurrency, attempts, budgets, blocked stdin, deadlines, bounded logs, cancellation, descendants, scope violations, dirty files, paths with spaces/Unicode and interrupted recovery. Fake workers must not consume model inference.

Choose project checks based on behavior: targeted tests, integration, compilation, rendering, data reconciliation or a small end-to-end example. Do not write tests that only mirror trivial implementation details. Once relevant checks pass, repeat or broaden them only when a new change or unresolved issue justifies it.

Before claiming the workflow works on the user's machine:

1. Verify local clients, sign-in, executable resolution and required flags.
2. Run one small task, receive its report, independently review and record acceptance.
3. Dispatch a dependent second task and review it in the same supervisor conversation.
4. Exercise native cancellation, descendants and restart handling on the actual OS.
5. Separately demonstrate background report delivery and continuation if that mode is requested.

Record what was tested and what remains unverified. Offline process tests do not prove signed-in model behavior or automatic main-chat continuation.

## Stop and resume rules

Stop new workers on exhausted budget, quota/auth failures, uncertain process ownership, repeated no progress, a required external dependency or a genuine authorization boundary. Do useful independent work where possible. Save the exact next action and explain the blocker plainly.

Do not ask for repeated permission for already authorized reversible implementation, fixes or reviews. Do not publish, deploy, send messages, spend money, trade, delete important data, commit or push unless the user's actual instructions authorize that action. Existing authorization remains valid; this file does not invent a second approval requirement.

Resume from durable state rather than rebuilding the project. Keep accepted decisions, partial edits, rejected approaches and failed checks. A new budget does not erase past failures or reset experimental evidence.

## Progress report format

```text
Milestone:
Outcome delivered:
Actual files changed:
Checks executed and results:
Supervisor decision and reason:
Rejected/deferred work:
Remaining limits or blockers:
Worker budget remaining:
Next exact task:
```

Keep the user-facing report concise. Lead with the result and its practical limitation, rather than a transcript of tool calls.

## Persistent Claude supervisor prompt

Paste this into one Claude Code conversation at the repository root, then provide the actual project goal.

```text
Read AUTOMATION_WORKFLOW.md, existing project instructions, the current goal,
handoffs and progress. Inspect repository and runner status before launching work.
Preserve existing edits and reuse the project architecture.

You are the persistent supervisor. Select useful work, research only consequential
uncertainties, assign scoped Codex CLI tasks through the local bounded runner,
receive reports, review actual diffs and independent checks, record the decision
and choose the next action. Do not ask me to relay reports between chats.

Keep one active worker and at most two worthwhile approaches. Prefer the smallest
complete solution. Reject irrelevant features and retire failed ideas unless new
evidence justifies reopening them. Reuse deterministic tools for routine work.

Run doctor first. If the runner is missing, implement and test it under this
workflow before claiming automation is ready. Use saved official-client sign-in;
no paid API fallback or credential copying. Keep models and reasoning configurable.

For each task, define objective, relevant context, scope, acceptance criteria,
constraints and trusted checks. Use foreground execution initially. Review its
result before any further dispatch. Make one specific correction per retry;
never weaken acceptance criteria to manufacture success.

Continue within the authorized scope and configured budget until the milestone
is complete or a stop condition occurs. No automatic budget resets, recursive
workers, detached tasks or unrequested external actions. Do not create commits
or push unless the user authorized them.

Keep concise updates and durable progress. On blockers preserve edits/logs and
record the exact next task. Clearly distinguish implementation, offline tests,
signed-client verification and actual product results. Only claim background
continuation after demonstrating it on this installation.
```

## Official references

Verify against the installed client when adopting this workflow; commands and account capabilities can change.

- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference)
- [Codex models and reasoning](https://learn.chatgpt.com/docs/models)
- [Claude Code interactive mode and background tasks](https://code.claude.com/docs/en/interactive-mode)
- [Claude Code subscription authentication guidance](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)


## AutoApply repository reconciliation (T048)

The supplied workflow above is preserved verbatim as a design reference. It is
project data; it does not grant authority to launch workers, access live services,
change policy or reset budgets. Follow the explicit task scope and the existing
[supervisor contract](docs/agent-supervisor.md). Reuse `tools/agent_runner.py`;
there is no second runner, timer or detached supervisor.

The existing `agent-config.json` overrides the generic default limits above:
one worker, **20 launches per budget**, three attempts per task and a **2,400-second
(40-minute) worker deadline**. Checks use their configured deadlines: extension
900 seconds, diff 120 seconds, offline 3,000 seconds; the general check deadline
is 3,000 seconds. Combined worker/check logs remain capped at 20,000,000 bytes.
This reconciliation changes no configuration, saved authentication, model,
approval policy or sandbox. Use existing official-client subscription sign-in;
there is no paid API fallback. Never reset budgets automatically.

From this checkout's VS Code PowerShell terminal, the development startup is:

```powershell
Set-Location C:\Users\yzhao\Documents\GitHub\AutoApply-codex
.\.venv\Scripts\python.exe tools/agent_runner.py doctor
.\.venv\Scripts\python.exe tools/agent_runner.py status
claude
```

Proceed only when doctor is ready and existing runs have been reviewed. In that
same Claude conversation, use `/autoapply-loop [engineering goal and acceptance criteria]`,
or paste the supervisor contract from `docs/agent-supervisor.md` and provide the
goal. The [VS Code guide](docs/semi-auto-vscode.md) also describes the separate
application loop; replace its older `AutoApply` folder example with this actual
`AutoApply-codex` checkout. Development startup does not require starting the
dashboard, semi-auto application loop or Discord connectivity test. Live employer/
Discord actions and submissions require their own user authority.

The supervisor writes scoped tasks under `.agent/tasks/`, runs them serially in
the foreground and reviews each run ID's immutable report, baseline, actual diff
and independent check logs before recording a `review` decision. Keep concise
milestone evidence and the exact next action in [docs/progress.md](docs/progress.md).
Use `status` for budget/review state and `report --run RUN_ID` for run evidence;
do not infer subscription allowance or reset a budget to continue.

T048 implements a local Workday skill-activation correction and fixture tests.
It does not reverify unchanged runner code, fake-worker tests, sign-in or the
Claude → Codex → report → review → dependent-task sequence. Signed-client and
background continuation claims remain subject to the existing supervisor guide's
operator smoke verification. Live Expedia acceptance remains pending supervisor
verification; offline fixtures cannot establish the live outcome. T047 education
behavior remains an independent open issue. See progress for checks actually run.
