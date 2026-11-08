# Application 3556 — browser-patience live run, 2026-09-29

Outcome: **FAIR_RETRY_ABORTED_SAFELY**.

The production browser could not create an application tab. Its exact error was
`BrowserContext.new_page: Protocol error (Target.createTarget): Failed to open a new tab`.
No employer page was reached, so this run provides no live qualification of the
Workday readiness or upload changes. No submission occurred.

| Requested measure | Result |
| --- | --- |
| Workday states observed | None in this run |
| Apply Manually to FORM_READY | Not applicable; neither occurred |
| Meaningful progress observations | No employer-page progress; a separate blank-page diagnostic recovered after 10.17 seconds, but the live continuation failed |
| Main Apply clicks | 0 |
| Apply Manually clicks | 0 |
| Next/Continue clicks | 0 |
| Actual applicant controls | 0 |
| Fields filled | 0 |
| Fields unanswered | No applicant questions exposed |
| Resume selected | No; configured canonical PDF hash verified |
| Upload state | UPLOAD_NOT_ATTEMPTED; no fresh selection, request, or attachment |
| Upload wait duration | Not applicable |
| Current step | Browser startup, before canonical-URL navigation |
| Final screen reached | No |
| Submission intents | 0 |
| Final submit clicks | 0 |
| Recognized submission requests | 0; blocked requests also 0 |
| Provider calls | 0; AI disabled and no provider instances |
| Peak application pages | 0 |
| Live page retained | No page was created |
| Checkpoint | Fresh startup-failure checkpoint; form_filled=false, upload_ready=false, validated_at=null, resume_sha256=null |
| Cleanup | All contexts closed, driver stopped, zero leases, worker lock released; no cleanup exceptions |

Only application 3556 was processed, using its existing record and stored canonical
URL. The canonical resume is `data/private/resumes/resume.pdf`, SHA-256
`3b71d225bad9e0ab51a65d1dfed2ccf3bb9dfe7bc60b1fb0abecf9d41acb6439`.
The four saved user-confirmed answers remained available but were never entered.

## Recovery and evidence

Two initial Engine calls failed before any page existed. Read-only blank-page
diagnostics isolated the creation error; one bounded diagnostic recovered. A final
Engine continuation added narrowly scoped operational recovery around that exact
tab-creation error, requiring zero context pages and zero leases before restart.
It used the centralized 8-second grace, 30-second normal window, and 5-second
extension policy. It did not retry navigation or any applicant interaction.

The final continuation observed four explicit tab-creation failures at
20:19:57.325611, 20:20:06.405295, 20:20:15.323796, and 20:20:24.471018 UTC.
The last observation was 27.87 seconds into the window; the runner waited out the
remaining normal window before returning. No meaningful progress justified an
extension. Engine returned at 20:20:28.074168 UTC and released the worker lock at
20:20:28.420835 UTC.

Three Engine calls advanced the production attempt counter from 3 to 6. The two
continuations used process-local retry budgets of 4 and 5 under the user's explicit
operational-recovery instruction; attempts were never reset. Final automatic
retry is disabled, paused=true, and auto_submit=false. The Engine's generic
SUBMISSION_UNKNOWN classification was corrected to BROWSER_START_FAILED using
the affirmative zero-page, NOT_STARTED, zero-submission evidence.

Production files in `autoapply/` and `scripts/` were unchanged. Other application
and job rows matched each execution's baseline. No other application was processed.

Private evidence files:

- `.tmp/fair-test-3556-patience-retry-evidence.json`
- `.tmp/fair-test-3556-patience-recovery-evidence.json`
- `.tmp/fair-test-3556-patience-live-evidence.json`
- `.tmp/fair-3556-browser-start-diagnostic.json`
- `data/private/fill-only-checkpoints/3556.json`
- `data/private/fair-patience-live-3556-2026-09-29/startup-checkpoint.json`

The fresh checkpoint excludes historical Workday URLs, old screenshots, and stale
upload results from its current-run claims. The legacy FILLED_CHECKPOINT persistence
label is not evidence of a filled form; its readiness flags are explicitly false.
Prior canonical checkpoints were backed up before each execution.

## Questions Yidi still needs to answer

None were exposed by a form during this run.
