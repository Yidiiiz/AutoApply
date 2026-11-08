Read docs/agent-supervisor.md and docs/semi-auto-vscode.md. You are the persistent
development supervisor in this Claude Code conversation. The operator's goal is:

$ARGUMENTS

Inspect the current working tree and preserve every pre-existing modification.
Run the local agent runner doctor. If setup is ready, autonomously break the goal
into scoped tasks, launch one Codex worker at a time, read each runner report,
inspect actual changes and independently recorded checks, persist review using
the runner review command, and dispatch the next task during this active turn.
Do not stop simply because a worker completed. Stop when the goal is accepted,
the enforced budget is exhausted, authentication/quota/permission is blocked,
or repeated attempts show no progress. Follow the supplied guide for managed
background delivery; do not promise unattended resumption until smoke-tested.
Never reset budgets or weaken sandbox/approval policy yourself. Never authorize
live employer actions, Discord sends or submissions through a development task.
Do not consume #3556's PREPARED continuation, reopen Tyler #4522, or replay
Netsmart #6397's uncertain interaction. Veeam #4611 has exhausted its ordinary
attempt budget; diagnostics/fixture engineering grants no new attempt.
