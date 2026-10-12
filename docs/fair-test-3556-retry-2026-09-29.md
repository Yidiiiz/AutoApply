# Application 3556 — single fill-only retry, 2026-09-29

Primary result: **FAIR_RETRY_SAFE_HOLD**

The repaired production `WorkdayAdapter` reached `SHELL_READY`, used its one
guarded application-entry click, and then exhausted the 15-second form-readiness
deadline without establishing `FORM_READY`. The application stopped with
`FORM_NOT_READY`. No applicant fields were filled, no file was selected, and no
upload or submission was attempted. The readiness failure was not classified as
a new upload failure.

| Required field | Observed result |
| --- | --- |
| Company | VIAVI Solutions (stored identity: `Viavi Solutions`) |
| Role | Software Engineering Co-Op |
| ATS | Workday; production `WorkdayAdapter` |
| Application ID | 3556; existing record reused |
| Application URL | https://viavisolutions.wd1.myworkdayjobs.com/careers/job/Germantown-MD-USA/Software-Engineering-Co-Op_260005140-1 |
| Workday readiness states observed | `LOADING` → `SHELL_READY` → `LOADING` → `SHELL_READY` → `LOADING` → `TIMEOUT_UNCONFIRMED` |
| Actual controls discovered | 0 applicant controls; shell observations recorded `fields=0`, `files=0`. Semantic applicant inventory was not authorized because `FORM_READY` was never reached. Saved checkpoint has one frame with zero fields. |
| Fields filled | 0 successful commits; zero new `LIVE_CONTROL_COMMITTED` events |
| Fields unanswered | No actual applicant questions inventoried; later questions remain unknown |
| Resume selection | None; the sole permitted file was `data/private/resumes/resume.pdf` |
| Resume SHA-256 | `3b71d225bad9e0ab51a65d1dfed2ccf3bb9dfe7bc60b1fb0abecf9d41acb6439`, verified before and after the run |
| Upload state | `UPLOAD_NOT_ATTEMPTED` for this retry; no fresh upload evidence, no attachment, no uploaded resume hash |
| Current application step | Entry dialog over the job listing; Engine stage `opening form`; hold `FORM_NOT_READY` |
| Final submission screen reached | No |
| Submission intents | 0 |
| Final submit clicks | 0 |
| Recognized final submission requests | 0; blocked final submission requests also 0 |
| Provider calls | 0; AI disabled, provider list empty, provider instances 0 |
| Peak application pages | 1; exactly one application page created; popup violations 0 |
| Live page retained | No; page and browser context closed |
| Checkpoint state | `MANUAL_REQUIRED`; legacy persistence label `FILLED_CHECKPOINT`, with `form_filled=false`, `upload_ready=false`, `validated_at=null`, `resume_sha256=null`, `reconstruction_verified=false` |
| Cleanup result | Successful; zero leases and active application pages at return, browser context closed, driver stopped, worker lock released and successfully reacquired, no cleanup exceptions |

## Execution and readiness evidence

Only `Engine.process_one(3556)` ran, exactly once. The existing attempt counter
advanced from 1 to 2 through production `begin_attempt`; no counter was manually
reset. No duplicate application was created, no other application was processed,
and no alternative fair link was opened. Other application and job rows matched
their pre-run snapshots.

The prior closed-session hold had `retry_allowed=0` and
`manual_resume_allowed=0`; ordinary retry and preserved-session resume could not
reopen it. After confirming the prior absence of input, upload, submission,
security, and uncertain-delivery evidence, the one-shot operational harness
recorded this user's explicit authorization and used the existing lifecycle
`release_hold` and guarded `transition(..., 'RETRY')` operations. All subsequent
browser work, readiness checks, interactions, and checkpointing used production
Engine code. No production source was changed or monkeypatched.

Fresh run events are 8174–8186. Readiness timestamps below are UTC:

| Time | Observation |
| --- | --- |
| 14:26:11.087936 | `LOADING`, shell inspection |
| 14:26:12.881773 | `SHELL_READY`, zero applicant fields/files |
| 14:26:13.848353 | `LOADING`, application-entry boundary |
| 14:26:14.474334 | `SHELL_READY`, zero applicant fields/files |
| 14:26:16.173182 | `LOADING`, form boundary following the single guarded entry action |
| 14:26:31.189336 | `TIMEOUT_UNCONFIRMED` |
| 14:26:31.403397 | `MANUAL_REVIEW` with `FORM_NOT_READY` |
| 14:26:32.020711 | Fresh checkpoint persisted |
| 14:26:33.052090 | Cleanup finished; worker lock released |

The final saved screenshot shows a dialog over the loaded listing. Production
screenshot masking obscures its contents, so this report does not infer the
dialog's choices or an authentication requirement. Security diagnostics reported
`NONE` and HTTP 200. No second entry click or intermediate transition was made.

This live run verifies bounded readiness gating and safe failure classification.
It does **not** qualify downstream form filling, upload compatibility, or
multi-step traversal, because the applicant form was never ready.

## Upload and checkpoint limitations

There are no fresh upload selection, upload request, or upload-readiness events
in this retry. `UPLOAD_NOT_ATTEMPTED` above describes that evidence; it is not a
new persisted upload verdict. The old `upload_result:3556` setting still contains
the prior erroneous `UPLOAD_FAILED` result and is copied into the checkpoint's
legacy `upload` member. It remains historical data and was not used as evidence
of upload readiness or as the reason for this hold. The new application error
category is `FORM_NOT_READY`, and both new checkpoint readiness flags are false.

The four saved user-confirmed answers remain unchanged. They are reusable answer
records, not evidence of four questions appearing or being filled in this run.
No unknown answer or narrative was generated.

The old checkpoint was preserved before the attempt at
`data/private/fair-retry-3556-2026-09-29/checkpoint-before.json`. The fresh
checkpoint is `data/private/fill-only-checkpoints/3556.json`. It is not a live
session or a verified reconstructable application.

## Cleanup and isolation

- Page leases at Engine return: **0**; at final return: **0**.
- Active application pages at Engine return: **0**; at final return: **0**.
- Browser context closure: **confirmed**; Playwright driver stopped.
- Production `Engine.close()` completed; a repeated `Browser.close()` also
  completed, exercising the repaired idempotent path.
- Worker lock: **released**, with successful reacquisition after cleanup.
- Cleanup exceptions: **0**.
- Final settings: `paused=true`, `auto_submit=false`; automatic retry disabled.
- All recorded `autoapply/` and `scripts/` source hashes match their pre-run
  values. Existing unrelated workspace edits were preserved.

Private execution evidence: `.tmp/fair-test-3556-retry-evidence.json`.
Operational harness: `.tmp/fair_retry_3556.py` (one-shot replay guard).
Final screenshot:
`data/private/application_history/manual_required/3556_Viavi Solutions_Software Engineer Co-op/screenshots/attention.png`.

## Questions for Yidi to complete manually

None could be identified: no actual applicant question was exposed in the
verified inventory before the readiness hold. Later questions are unknown, so
no speculative questions are listed.

Stopped after application 3556. No additional retry is authorized by this run.
