# Future task: safe same-session manual intervention for controlled workers

Incident: Together AI #6401, reconciled as user-manually-submitted on 2026-09-24.
The custom `.tmp/restart_6401.py` worker serviced its own command files, not the project's normal `manual_requests` queue / `Engine.service_manual_requests`. Email verification therefore could not reliably resume the same live workflow through normal controls.

Controlled production workers should support a safe same-session manual-intervention resume mechanism.

Implement and test separately before relying on it for future applications:

- WAITING_FOR_MANUAL_INTERVENTION -> user completes action -> MANUAL_INTERVENTION_COMPLETE -> same worker inspects/reconciles its existing page.
- Preserve worker and page identity, acknowledge commands exactly once, and expose idle/in-progress state.
- Reconcile affirmative confirmation before considering any further action. An inspection command must never implicitly submit, refill, upload, or reconstruct a browser.
- Fail closed if the session is gone; honor permanent user-submitted guards before page access, and reject stale/duplicate commands.
- Test normal queue delivery, live-session continuity, missing sessions, repeated commands, retirement during a hold, and zero employer mutations during inspection.

Do not resurrect #6401 for implementation or testing. Use isolated tests and a different application for future authorized controlled testing. #6401 is finished from AutoApply's perspective.

Standing demographic/self-identification mappings are unchanged and remain applicable to semantically equivalent future options.

## Implemented 2026-09-24

`scripts/controlled_once.py` selects one fresh queued application and uses the
normal Engine, answer resolver, upload, validation and submit pipeline. It
retains the worker lock and services the normal manual request queue indefinitely.
It requires the ordinary Windows-user context and records isolation snapshots.

Holds publish `manual_session:<id>` with a session token, lifecycle state and
busy flag. Commands bind to that session and receive one durable acknowledgement;
stale or repeated commands cannot touch the employer. Controlled missing sessions
fail closed. Ordinary non-controlled durable input reconstruction is retained.

- `python -m autoapply inspect-manual ID`: inspect and reconcile only; never fill,
  upload, submit or reconstruct a browser.
- `python -m autoapply resume-manual ID`: explicit completion signal; inspect the
  same page first, then revalidate through the normal flow only before submit intent.
- Existing submit intent always restricts continuation to reconciliation.
- Saved answers alone do not restart a controlled hold. Existing attachments are
  retained and freshly validated, without reselecting the file.

Tests: `tests/test_controlled_resume.py` plus existing security, browser, submission
probe and input-navigation regression suites. The protected application is never
used for live testing.
