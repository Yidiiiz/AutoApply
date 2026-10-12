# Application 3556 — live application-entry retry, 2026-09-29

Outcome: **FAIR_RETRY_SAFE_HOLD**

The real Workday chooser was recognized, **Apply Manually was clicked exactly once**, and production telemetry recorded a confirmed entry transition. The subsequent form gate reported `APPLICATION_ENTRY_CHOICE` again and stopped with `APPLICATION_ENTRY_MODE_UNRESOLVED` (“Entry chooser still present; form input forbidden”). `FORM_READY` was never established. No applicant input, resume selection, upload, or submission occurred.

| Required field | Observed result |
| --- | --- |
| Company | VIAVI Solutions; stored identity `Viavi Solutions` |
| Role | Software Engineering Co-Op |
| ATS | Workday; production `WorkdayAdapter` |
| Application ID | 3556; existing record reused |
| Application URL | https://viavisolutions.wd1.myworkdayjobs.com/careers/job/Germantown-MD-USA/Software-Engineering-Co-Op_260005140-1 |
| Workday readiness states | `LOADING` → `SHELL_READY` → `LOADING` → `SHELL_READY` → `LOADING` → `APPLICATION_ENTRY_CHOICE` → `LOADING` (entry transition) → `LOADING` (form) → `APPLICATION_ENTRY_CHOICE`; no `FORM_READY` |
| Entry chooser detected | Yes |
| Entry mode selected | `WORKDAY_ENTRY_MODE_SELECTED = applyManually` |
| Entry transition | `WORKDAY_ENTRY_TRANSITION_CONFIRMED`, recorded after the sole manual-mode click |
| Main Apply clicks | **1** |
| Apply Manually clicks | **1** |
| Autofill clicks | **0** |
| Use Last Application clicks | **0** |
| Actual applicant controls inventoried | **0**; production semantic inventory was never reached; fresh checkpoint also has zero fields |
| Fields filled | **0**; no fresh `LIVE_CONTROL_COMMITTED` events |
| Fields left unanswered | No applicant questions were exposed in the verified inventory; later questions remain unknown |
| Resume selected | **No**; fresh upload tracker selection count 0 |
| Resume SHA-256 | `3b71d225bad9e0ab51a65d1dfed2ccf3bb9dfe7bc60b1fb0abecf9d41acb6439`; canonical local file verified before and after execution |
| Upload state | `UPLOAD_NOT_ATTEMPTED` for this run; zero upload requests/events and no fresh upload result or attachment evidence |
| Current application step | Engine stage `opening form`; manual-entry route reached, applicant form not ready |
| Final submission screen reached | **No** |
| Submission intents | **0** |
| Final submit clicks | **0** |
| Recognized final submission requests | **0**; blocked final requests also 0 |
| Provider calls | **0**; AI disabled, provider configuration empty, provider instances 0 |
| Peak application pages | **1**; one page created, zero popup violations |
| Live page retained | **No**; page and context closed |
| Checkpoint state | `MANUAL_REQUIRED`; persistence label `FILLED_CHECKPOINT`, with `form_filled=false`, `upload_ready=false`, `validated_at=null`, `resume_sha256=null`, `reconstruction_verified=false` |
| Cleanup result | Successful; zero remaining leases/pages, browser context closed, driver stopped, worker lock released and successfully reacquired, zero cleanup exceptions |

## Fresh execution evidence

Only `Engine.process_one(3556)` ran, exactly once. The existing lifecycle `release_hold` and guarded `transition(..., 'RETRY')` recorded this explicit authorization. Production `begin_attempt` advanced attempts **2 → 3**; the attempt counter was not manually reset. The canonical URL was reused, and the manual-entry URL was reached by the guarded click, never direct guessed navigation.

The production adapter retained its unique-option, origin/semantics, visibility, connected-element, security, geometry, and cursor actionability checks. The operational harness added read-only click and end-page observations and inventory profiling; no production source or interaction method was monkeypatched. Both observed navigation clicks were trusted browser events. No alternate mode or non-final step control was clicked.

Fresh database events: **8187–8204**. Times below are UTC on 2026-09-29:

| Time | Evidence |
| --- | --- |
| 18:51:39.454439 | `SHELL_READY`, fields 0, files 0 |
| 18:51:41.908777 | `SHELL_READY` at the entry boundary |
| 18:51:43.926790 | Observed main Apply click |
| 18:51:44.310177 | `APPLICATION_ENTRY_CHOICE` |
| 18:51:46.517903 | Observed Apply Manually click |
| 18:51:46.532031 | `WORKDAY_ENTRY_MODE_SELECTED`, `applyManually` |
| 18:51:47.816431 | `WORKDAY_ENTRY_TRANSITION_CONFIRMED` |
| 18:51:48.398421 | Subsequent form gate reports `APPLICATION_ENTRY_CHOICE` |
| 18:51:48.968868 | `MANUAL_REVIEW`; `APPLICATION_ENTRY_MODE_UNRESOLVED` |
| 18:51:49.890879 | Fresh checkpoint recorded |
| 18:51:50.874726 | Worker lock release verified |

The complete requested sequence did **not** occur: chooser recognition, one manual selection, and transition confirmation succeeded, but stable form readiness and downstream filling/upload remain unqualified. The actual production hold category was `APPLICATION_ENTRY_MODE_UNRESOLVED`, rather than a form-readiness timeout (`FORM_NOT_READY`) or an unconfirmed transition. This report preserves that distinction; no state was relabeled to imply a timeout or successful form entry.

The final observed URL was:

https://viavisolutions.wd1.myworkdayjobs.com/en-US/careers/job/Germantown%2C-MD-USA/Software-Engineering-Co-Op_260005140-1/apply/applyManually

The saved final screenshot shows a mostly empty Workday shell with loading dots, cookie controls, and a header Sign In link. The final read-only observation found no visible input controls. Security diagnostics reported `NONE`, HTTP 200; a header Sign In link alone does not establish a login challenge. These later observations do not establish why the earlier form gate re-detected the chooser. No further interaction or retry was performed to investigate.

## Resume, answers, and checkpoint

Only `data/private/resumes/resume.pdf` was configured. Its hash matched the required value before and after the run. No file control was reached, so upload readiness was not claimed and no new upload failure was classified.

The checkpoint's legacy `upload` member still contains the historical `UPLOAD_FAILED` value from an earlier attempt. It is not fresh evidence: this run's tracker has `selected=0`, no requests, no events, and an empty result. The new hold concerns application entry, not upload failure.

The four existing USER_PROVIDED answer records and their provenance are unchanged. They were not broadened, presented as observed questions, or committed to applicant controls. No answer or narrative was generated.

Fresh checkpoint: `data/private/fill-only-checkpoints/3556.json`, timestamp `2026-09-29T18:51:49.886796+00:00`. Prior checkpoint preserved at `data/private/fair-entry-retry-3556-2026-09-29/checkpoint-before.json`. The fresh checkpoint is a durable hold record, not a filled form or verified reconstruction.

## Isolation and cleanup

- Final settings: `paused=true`, `auto_submit=false`; application `retry_allowed=0`, `manual_resume_allowed=0`, `session_preserved=0`.
- All other application and job rows match their pre-run snapshots. No other application or fair link was processed.
- All recorded `autoapply/` and `scripts/` source hashes match their pre-run values. Existing workspace edits were preserved.
- Production cleanup and an idempotent repeated browser close completed without errors. No live application page remains.
- Private evidence: `.tmp/fair-test-3556-entry-retry-evidence.json`.
- One-shot operational harness: `.tmp/fair_entry_retry_3556.py`; its evidence-file guard prevents replay.
- Final screenshot: `data/private/application_history/manual_required/3556_Viavi Solutions_Software Engineer Co-op/screenshots/attention.png`.

## Questions for Yidi to complete manually:

None were actually exposed by an applicant form during this retry. No speculative questions are listed.

The single authorized retry is complete. Stopped after application 3556 without submission.
