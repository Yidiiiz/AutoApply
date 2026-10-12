# Semi-auto operation from VS Code

The application loop and the development supervisor are independent. AutoApply
uses Python, SQLite, Playwright and Discord for everyday queue processing. Claude
Code supervises Codex only when you request engineering work. Neither CLI is
needed to recognize ordinary form questions or run a batch.

## Open and prepare the folder

Open `C:\Users\yzhao\Documents\GitHub\AutoApply` with **File → Open Folder**.
Choose **Terminal → New Terminal → PowerShell**. Keep your existing private
profile, canonical resume, `.env`, database, browser profile and config. Do not
run `init`, delete data, reset attempts or clear manual holds for this upgrade.

The existing virtual environment is ready. If installing on a new machine only:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\data\private\playwright"
.\.venv\Scripts\python.exe -m playwright install chromium
```

Use the absolute interpreter path in these commands; activation is optional.
For an existing installation with local Chromium, set the same browser path
before launch if your shell does not already set it.

In ignored `.env`, keep `DISCORD_BOT_TOKEN` and your personal numeric
`DISCORD_USER_ID`. Never put credentials into committed files or an LLM prompt.
The bot must share a server with you, and your account must accept its DMs.
In `data/private/config.yaml`, retain other settings and ensure:

```yaml
browser:
  headless: false
application:
  auto_submit: false
discord:
  enabled: true
```

Then run the explicit connectivity check once. This sends a private test message:

```powershell
.\.venv\Scripts\python.exe -m autoapply discord-check
```

## Start the application loop

```powershell
.\scripts\start-semi-auto.ps1
```

The equivalent command is `.\.venv\Scripts\python.exe -m autoapply semi-auto`.
Keep this terminal open. The worker validates Discord before employer work,
forces auto-submit off, selects up to five eligible applications by the existing
priority queue, and fills them **sequentially** while retaining up to five forms.
Freshness, eligibility, history, attempt budgets and holds still apply. Fewer
than five forms is normal when a listing is closed or infrastructure fails.
Discovery is separate: the batch does not run a new listing scan.

The batch summary reports actual verified fields and stops. Answer cards have
exact-option menus or a text modal; choices go through the supported controller,
are persisted, and are revalidated in the same live form when the input hold
allows it. Changed/unknown facts, legal declarations, credentials, durations and
personal interests still require your answer. Saving an answer never clears an
engineering or security hold.

Final actions belong to you:

| Discord control | Effect |
| --- | --- |
| `!batch` | Concise current batch summary |
| `!answer QUESTION_ID <answer>` or answer menu | Save an exact validated answer |
| **Review #ID** / `!review ID` | Bring the retained form forward and unlock your manual final action; automatic filling stops while review is armed |
| **Lock Submit / refill** / `!lock` | Relock final actions and allow supported same-form revalidation of saved answers |
| `!resume-manual ID` | Request revalidation; protected holds remain in force |
| `!submitted ID <employer confirmation or receipt>` | Permanently retire that application from automation using your report; this is distinct from observed ATS confirmation |
| **Next five** / `!next` | Process another priority batch after all current forms are closed |
| `!pause` / `!resume` | Pause/resume filling; auto-submit stays off |
| `!stop` or Ctrl+C | End after a safe point and retain checkpoints |

Review and correct the actual browser form. Click the employer's Submit button
yourself. No Discord button submits it. Save the employer confirmation, report
the outcome, and close the tab. A detected final click/request latches an
uncertain manual outcome and bars automatic replay. Closing a tab alone never
means submitted. No new batch opens while a current form remains open.

Use `!lock` **after finishing manual interaction** before requesting further
automatic filling. If the worker restarts or you close a form, no browser handle
survives; a checkpoint is saved progress, not a live ready-to-submit session.
The private report is `data/private/semi-auto-result.json`. Existing held
applications are not reopened by this command. Use the separately qualified
recovery workflow only when its evidence and budget allow it.

## Start the development supervisor

In a second VS Code terminal:

```powershell
.\.venv\Scripts\python.exe tools/agent_runner.py doctor
claude
```

Both official clients are installed on the inspected machine: Codex CLI
0.160.0 (native executable; the npm shim reports 0.160.1) and Claude Code
2.1.292. The doctor rechecks your actual versions, flags,
subscription login and provider overrides. Sign in interactively if needed:
`codex login` and Claude Code's `/login`. No API key is required. Remove provider
or API-key overrides explicitly if doctor reports them; their values are never
printed. Leave your saved model preferences in place; `agent-config.json` can
pin a model/reasoning effort after you choose one.

In the **same Claude Code conversation**, paste:

```text
Read docs/agent-supervisor.md. Use the project's serial Codex development runner.
Continue automatically through scoped implementation, report review and correction
until the goal is accepted or an enforced stop condition occurs. Preserve all
existing work and protected application history. Do not launch live application
workers or reset budgets without my explicit instruction.

Goal: [describe the engineering improvement here]
```

Or use the project command:

```text
/autoapply-loop [your engineering goal]
```

Run one tiny subscription smoke test before trusting background continuation:

```text
Read docs/agent-supervisor.md. Copy templates/agent-task.example.json into
.agent/tasks/T001.json. Run doctor, launch the task with the runner, inspect the
report and diff, persist your review decision, then explain the next action in
this same conversation. Do not launch live AutoApply work.
```

Workers never run recursively. The Python runner launches, contains, logs and
checks them; Claude decides acceptance and the next scoped task. Completion
returns through Claude's shell tool, so normal serial work needs no report
copying between chats. Keep the Claude conversation active for automatic serial
continuation. See the supervisor guide for cancellation, budgets, restart and
managed background limitations.

## Verification and research

New behavior is verified with local intercepted employer forms and fake Codex
workers; automated tests never make subscription model requests. Run focused
checks from the terminal:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_semi_auto.py tests/test_question_rules.py tests/test_agent_runner.py tests/test_integrations.py -q
```

See [question matching research](question-matching-research-2026-10-06.md).
Live ATS acceptance and signed-in Claude background delivery require separate
operator smoke tests. Passing local fixtures is not proof that Veeam's live
Country control or an employer's resume upload is fixed.
