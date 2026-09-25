# SmartRecruiters repair and controlled #5310 operation

## Outcome

Reusable support has been implemented and tested locally. **#5310 is not submitted.**
Production verification is blocked by a genuine DataDome access restriction.
No attempt was made to solve a challenge, change browser fingerprints, rotate
network routes, or repeatedly reload the restricted page.

The original worker used a private debugging pipe and could not accept repaired
code from a new process. The one authorized reconstruction was used for #5310
only. The restriction appeared before application form entry. A later process
check found that both the replacement worker and its dedicated browser had exited;
the cause was not established. Its inspection-only registration command was never
acknowledged and has been cancelled. **There is no live session to resume.** The
database and canonical history were corrected to reflect that fact. No further
reconstruction is authorized by this run.

## Implementation

- `autoapply/smartrecruiters.py`: dedicated adapter, page-wide native/custom control
  inventory, open-shadow-root discovery through locators, root-scoped labels,
  required/validation metadata, radio grouping, optional unsupported controls,
  required capability failures, fresh visible action resolution, stable transition
  checks, and rediscovery. No applicant or employer-specific answers/selectors.
- `autoapply/field_mapping.py`: canonical semantic aliases over the existing profile;
  Unicode, punctuation and whitespace normalization; weighted deterministic signals;
  conflicts fail to `UNKNOWN_FIELD`. Sanitized bounded classifier interface accepts
  only allowlisted concepts and confidence, with a field/options/type cache key.
  **No external semantic classifier has been configured for this run.** Unknowns
  remain unknown; the classifier never generates applicant facts.
- `autoapply/answers.py` and `database.py`: existing verified answer memory and
  grounded narrative system retained; newly verified memories receive an exact
  question/type/options signature. SmartRecruiters does not trust unsigned legacy
  exact-memory records as an exact options match. Per-field autofill policies are
  supported. Demographic categories remain distinct.
- `autoapply/engine.py`: per-step profile completeness checks, capability matrix,
  sanitized inventory and filled-field provenance, durable semantic step progress,
  and fresh eligibility required before SmartRecruiters final submission. Missing
  mandatory capabilities cannot be compensated by a numeric support score.
- `autoapply/uploads.py`: zero selected files can never produce `UPLOAD_READY`;
  an empty control inventory is not an attachment. SmartRecruiters requires a
  filename entry and uses its own document-upload lifecycle label. A same-session
  accepted attachment may survive an observed step transition; that receipt cannot
  be restored into a new browser. Late failures and busy/warning states still block.
- Next events are separate from all final Submit events. A failed or ambiguous
  Next is not retried automatically. The existing final physical-mouse Submit probe,
  fresh geometry/hit testing, no locator-click fallback and no-retry rules remain.

The ten demographic standing answers from the request were saved in the private
canonical profile with a backup and separate provenance record. No demographic
answers were sent to the employer in this run.

## Evidence and limits

The prior false upload readiness has a confirmed code cause: aggregation started
with `attached=True` for an empty control collection, and the verdict did not
require a selection. That path is now covered by negative tests and the old live
readiness setting is explicitly invalidated without deleting historical evidence.

The generic extractor chooses a single first matching form/container. Local
fixtures reproduce missed controls outside that root, and the new adapter covers
that case plus open shadow roots. **The exact original live discovery failure and
`TargetUnavailableError` cause remain unverified**: the original incident did not
retain enough target diagnostics, and the restricted site prevented inspection
of the actual form DOM. Hidden duplicates, stale targets and rerenders are tested
failure classes, not claims about the original incident.

The saved Easy Apply screenshot informed a synthetic structural fixture. It is
not represented as a captured live DOM fixture. Real SmartRecruiters upload markup,
custom widgets and final confirmation still require production verification once
access is restored. Unsupported or ambiguous controls stop with exact evidence.

## #5310 production result

| Item | Result for this run |
|---|---|
| Fields inventoried / mapped / filled | None: restricted before form entry |
| Mapping sources and confidence | No production mappings; local tests exercise deterministic and bounded fallback paths |
| Demographics filled | None |
| Unresolved controls | Actual form inventory unavailable behind restriction |
| Intermediate Next interactions / transitions | 0 / 0 |
| Resume control discovered in this run | No; visible in the prior saved screenshot |
| Resume selections / upload lifecycle | 0 / not started |
| Employer-side attachment evidence | None |
| `UPLOAD_READY` | False; prior false setting invalidated |
| DataDome | Active access restriction, no longer passive-only |
| Manual action / resume | No restoration observed; replacement worker/browser exited; same-session resume unavailable |
| Fresh final eligibility / validation | Not established under current restricted access |
| `READY_TO_SUBMIT` | False |
| Final Submit calls / observed clicks | 0 / 0 |
| Submission request / response | None / none |
| Affirmative confirmation | None |
| Classification | Not submitted; security hold, session unavailable |

The corrected eligibility parser was retained and its regressions passed. No new
ineligibility conclusion was drawn from the access restriction.

## Verification

- Broader relevant regression set: **312 passed** (eligibility, standing answers,
  security, navigation, manual sessions, browser behavior and submission probes).
- Final targeted set: **48 passed**, including the synthetic two-step normal Engine
  workflow with one intermediate Next, retained verified attachment, exactly one
  final submission and affirmative confirmation; negative receipt and discovery tests;
  upload invariants; all ten demographic categories; and refresh safety checks.
- Final inspection-only session-registration tests: **9 passed**.
- Python compilation passed. Ruff is not installed in the project environment.

Private evidence lives under `data/private/smartrecruiters-5310/`. `audit.json`
records protected application comparisons, previous submitted-history hashes,
live security state, upload readiness and submission counters. Test XML and logs
are under `.tmp/smartrecruiters-*`.

The final audit found **only #5310 changed**. #6401 was not processed again;
#6415 and #6416 were unchanged; unrelated applications changed: **0**; previous
submitted canonical-history files unexpectedly changed: **0**. Final Submit calls,
observed clicks, submission requests, intermediate Next clicks and resume
selections were all **0**.

The run remains scoped to #5310, with processing paused and retries disabled.
Restoring access does not authorize bypassing DataDome, another reconstruction,
or replaying a final Submit. Future live continuation requires restored access and
authorization for a replacement session, followed by actual DOM verification and
normal gated completion. The reusable same-session path is tested, but cannot
resume a process that has exited.
