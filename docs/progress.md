# Progress — T048

Milestone: Correct Workday skill activation after exact-option discovery and
reconcile the supplied automation workflow with the existing serial runner.

Outcome delivered: Exact skill rows are reacquired after scrolling and before
activation. The actual inner checkbox/control receives the pointer/click sequence;
interrupted activation can reacquire a replaced row at most three times. Once a
click is issued it is not repeated while commitment may be pending. Existing pills
and checked rows are preserved, and existing choices are not counted as new adds.
Success requires a stable pill or checked accessibility state read from the current
DOM; a native checkbox property, detached row label or search text alone is
insufficient. Trace entries identify the target tag, automation ID and observed
commit evidence. The shared prompt fill path uses the same correction.

Actual files changed:

- `extension/content.js`
- `tests/test_extension.py`
- `tests/fixtures/extension/t048.html`
- `AUTOMATION_WORKFLOW.md`
- `docs/agent-supervisor.md`
- `docs/progress.md`

The original supplied workflow is retained verbatim, followed by repository
reconciliation. The supervisor guide now states the configured 20-launch budget
and 2,400-second worker deadline and documents startup in this checkout. Runner,
configuration, schemas, authentication, sandbox, budgets and runtime metadata
were not changed.

Checks executed and results (local fixture HTTP only; .venv Python):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extension.py -k t048 -q -p no:cacheprovider --basetemp .tmp/t048-regression-final
# 4 passed, 92 deselected (18.67s)
.\.venv\Scripts\python.exe -m pytest tests/test_extension.py -k "t046_document_popup or t046_uncommitted or skills_discovery" -q -p no:cacheprovider --basetemp .tmp/t048-existing-skills
# 3 passed, 93 deselected (18.30s)
.\.venv\Scripts\python.exe -m pytest tests/test_extension.py -k "t047 or t044_skills_action or t040_waits" -q -p no:cacheprovider --basetemp .tmp/t048-shared-prompts
# 5 passed, 91 deselected (34.96s)
node --check extension/content.js
git -c safe.directory=C:/Users/yzhao/Documents/GitHub/AutoApply-codex diff --check
```

The new regression proves the outer exact row is inert, uses the body-appended
Workday presentation/option/checkbox structure and opaque control IDs, scrolls
virtualized results, replaces the row during pointer activation and at commit,
preserves existing choices, and checks that no unrelated option is selected.
It covers pill and checked-label commits, native-checkbox-only failure, and a
detached checked label that must not count as success. An initial asynchronous
test invocation did not return captured output; only the direct completed checks
above are claimed. The runner should execute the full configured extension/diff
checks independently. Unchanged runner, fake-worker and sign-in suites were not
rerun for this documentation reconciliation.

Supervisor decision and reason: Pending independent report/diff/check review and
authorized live Expedia verification. Passing fixtures does not establish the
real Expedia outcome or signed-in/background supervisor continuation.

Rejected/deferred work: No second runner was built because the existing runner
already provides the workflow. T047 education behavior remains an independent
open issue. No live employer/Discord actions, real dashboard, submissions, worker
launches, API fallback, budget resets, staging, commits, pushes or deployment.

Worker budget remaining: Not read or changed in this scoped worker task; the
supervisor uses existing runner `status` rather than inferring subscription quota.

Next exact task: Supervisor reviews T048 and independent configured checks, then
performs authorized live Expedia skill verification: confirm AI Workflows has a
committed pill/checked label, existing skills remain selected, unrelated skills
remain unselected, and the trace identifies the inner checkbox activation. Keep
education follow-up separate. For development startup use doctor, status, then
`claude` and `/autoapply-loop [goal and acceptance criteria]` in one active
conversation, as documented in `AUTOMATION_WORKFLOW.md` and the supervisor guide.

## Supervisor review and integration — 2026-10-15

Run `e979fe45a51e4e33b4cfe95d963ca2d6` was independently reviewed and accepted
for local integration. Runner checks: **288 extension/API tests passed in
705.56 seconds**, plus `diff` passed; no out-of-scope changes. Supervisor read
the final diff and verified that the supplied workflow is a verbatim prefix of
the repository copy. Budget remaining: **17 of 20 launches**; no reset.

Live evidence used: Expedia skills trace from 2026-10-15T03:42:56Z finds the
exact AI Workflows row after scrolling but clicking its outer row leaves
`AI Workflows not checked`. The implementation now targets the inner checkbox.
The browser tool exposed no apps/tabs in this supervisor session, so successful
selection on the live Expedia form is **not yet verified**. Education remains
an independent open issue.

Next action: reload the unpacked extension and refresh the Expedia application
tab to load the new content script, run Autofill skills, then inspect the newest
skills trace for `target_automation_id: checkboxPanel`, `verified: true`, and a
checked option or selected pill. If it fails, use that observed trace for one
specific correction through the same bounded runner. Nothing is pushed.
