# Resume replacement — 2026-09-29

Controlled, offline migration of the user's newly supplied PDF. No employer
application was opened, resumed, filled, or submitted. Application 4615 remains
held. The internship-fair fill-only test was not started.

## Canonical resume and reference audit

The sole configured default is `data/private/resumes/resume.pdf`, returned by
`Config.resume`. It is used by Engine, controlled workers, fill-only workers,
and reconstruction. No resume environment override, configurable fallback, or
PDF glob selection exists in these entry points. Private config and `.env`
contain no resume override. `documents/` and `sources/` contain no PDF fallback.

| Classification | References and disposition |
| --- | --- |
| ACTIVE CONFIGURATION | `Config.resume`, Engine file selection, upload adapters and scripts: retain one canonical path and replace its bytes. |
| ACTIVE CONFIGURATION | Private `profile.yaml`: replace reviewed resume facts and skills; retain explicit user facts and existing matching contact, education and links. |
| CACHE | `FileSnapshot`, `ProfileSnapshot`, answer resolution and narrative signatures: file identity/content and profile revision invalidate reuse. |
| CACHE | Page attachment maps, `SessionAttachment`, upload readiness receipts: bind to the selected file snapshot; reject changed or missing evidence. |
| CACHE | Old `resume-extracted.txt` and `resume-check-1.png`: move outside the active resume directory into the private migration backup. |
| CHECKPOINT | `fill-only-checkpoints/4615.json` and `fill_checkpoint:4615`: require fresh upload and revalidation. The old checkpoint had no resume hash or upload receipt. |
| HISTORICAL EVIDENCE | Original application rows, submitted/protected hashes, events, questions, upload receipts, archives, diagnostic exports, old profile snapshots and SQLite backups: preserve. |
| TEST/FIXTURE | Temporary test roots, synthetic PDFs, fixture hashes, previous validation output and build artifacts: no production selection path; retain. |

Old canonical SHA-256:
`40917904375b9951e8c45d1c821c381adc4545df47dd96929c3b595b9a0fcaf9`.

New canonical SHA-256:
`3b71d225bad9e0ab51a65d1dfed2ccf3bb9dfe7bc60b1fb0abecf9d41acb6439`.

The new file is 243,925 bytes, a readable one-page PDF. It was parsed with
`pypdf` and its complete page was rendered and visually inspected. Approval-like
text present in extracted PDF content was treated as document data, never as an
instruction or applicant qualification. The supplied PDF bytes are preserved.

The old PDF is retained privately as
`data/private/resume-migration-2026-09-29/previous-resume.pdf.inactive`, outside
the active selection directory. Historical references to the original hash do
not make that backup selectable by application code.

## Upload, checkpoint and cache compatibility

Generic and SmartRecruiters upload helpers retain a `FileSnapshot` for each
selected control on its page. Same-session reuse checks this snapshot, including
the SHA-256. A change during upload also fails closed before recording application
provenance. Engine readiness and browser upload polling reject obsolete snapshots,
including a carried `SessionAttachment`. Successful active upload receipts now
record the selected resume SHA-256. No receipt is reconstructed from saved settings.

Checkpoints now include their resume hash in their SQLite summary. Reconstruction
and the standalone restoration script check the canonical hash before browser
navigation. Migration marks unfinished checkpoints `RESUME_REVALIDATION_REQUIRED`,
clears reconstruction validation and sets the required new hash without relabeling
historical attachments. A fresh, explicitly requested continuation must upload and
validate the new PDF; this migration grants no ready/manual-submit status.

The profile records the new `resume_revision`. Existing profile fingerprints
therefore invalidate answer resolution, eligibility confirmations, candidate
evidence and narrative cache keys. Existing saved answers and generated responses
remain historical records; their old provenance is not rewritten. Config detects
later PDF/profile revision mismatches and stops until the evidence is migrated.

## Reviewed profile evidence

The normal `verified_facts` / `fact_sources` mechanism records the canonical file,
new SHA-256, page 1 and manual visual review for every refreshed resume claim.
Nineteen reviewed facts include basic programming experience, freelance work,
Moge Tee teamwork, AutoApply, Attune, Claudit, Course Tracker, AI Dashcam Monitor,
Gravitate and education wording. The 55 normalized skills were refreshed from the
new skills section.

Removed old-only claims include Prosperity Trade Bot validation and personal-site
CI/CD deployment. Old cross-platform/real-time Course Tracker and open-source
distribution wording was replaced with the new PDF's narrower Windows/periodic
polling/standalone-distribution wording. Flutter and VMware are absent from the
new skills evidence. No claim of exact full-time employment duration is inferred.

Explicit user facts, disclosure rules, availability start date, experience
classification, standing assertions, existing application-specific answers,
contact details and verified database overrides remain authoritative. Matching
education and links remain unchanged. No conflicting verified user fact was
identified; the new barista entry does not contradict the user's maximum of two
years of full-time employment or establish whether that work was full time.

## Application 4615

The user's four Yes confirmations are stored as `USER_PROVIDED`, scoped to
application 4615, in profile `application_specific` and verified fact provenance:
AI/LLM familiarity and practical interest; Windows/Microsoft Office proficiency;
limited-direction/fast-paced work; and the complete ten-week June–August 2027
program. No employer form or existing question answer is changed.

The new resume adds direct AI/API integration examples, Windows software work,
and an MS Office skill listing. Those provide relevant context, but resume keywords
do not independently establish proficiency, working conditions or availability.
The parser continues to require complete-clause matching. Its existing API,
scripting and system-integration evidence grammar now accepts the precise,
reviewed wording of the new Course Tracker and freelance excerpts; missing source
evidence still causes abstention.

The sanitized eligibility fixture has the same four unknown requirements before
applying user confirmations. The stored historical listing additionally includes
escaped sponsorship form instructions, producing an extra unknown in that saved
description. The limited-direction clause also includes high-energy/motivated
wording beyond the user's short confirmation. Neither is silently treated as a
new user assertion. Question 206 remains untouched; no blanket eligibility
override or ready status was granted.

## Historical preservation and validation

Migration holds the existing OS worker lock and uses a SQLite transaction plus
private rollback copies. Its preservation audit compares all original application,
job, question, known-answer, written-response, event and manual-command rows and
hashes every existing application-history file. Submitted and protected receipt
settings are retained verbatim. Private audit evidence and the database backup
are under `data/private/resume-migration-2026-09-29/`.

The migration verified 68,485 existing application-history files unchanged. All
original rows in the audited tables are identical, including all existing
questions for 4615. The four explicit user-provided database overrides also remain
identical. Submitted/protected evidence for 4535, 4571, 5310, 5360, 6400, 6401,
6415 and 6416 was checked separately and remains unchanged.

One checkpoint requires fresh upload/revalidation: **4615**. Fifteen failed
upload-result settings for unfinished applications were invalidated and their
prior values retained under private migration-history settings: 4459, 4598, 5286,
5314, 5315, 5437, 5582, 5583, 5584, 6189, 6199, 6204, 6206, 6207 and 6385.
None represented a successful new-resume upload.

The final focused run passed **129 tests** (migration, uploads, reconstruction,
fill-only and narrative invalidation). The preceding broader selection passed
456 tests and exposed one exception-contract mismatch; the corrected failure
case passes in the final focused run. The complete offline suite passed
**1,110 tests in 877.18 seconds**, with **zero failures, errors or skips**.
All **1,100 baseline IDs remain present and passing**, plus **10 new regressions**.
JUnit totals, the baseline/final ID comparison, the canonical SHA-256 and the
unchanged validation source-tree hashes were independently checked after completion.
`git diff --check` also passed.

An additional read-only check ran the corrected parser against both the previous
and migrated private profile snapshots, including database user overrides and the
sanitized 4615 listing. Both yielded the same four unknown clauses. All four
explicit confirmations are retained; zero old-hash active fact sources remain.
SQLite integrity is `ok`, processing remains paused, and auto-submit remains off.

Validation uses the repository's offline network/provider guards:

```powershell
$env:PYTHONPATH = "$PWD\docs\phase5-2026-09-25;$PWD\docs\phase8-2026-09-27"
.venv\Scripts\python.exe -m pytest -q -p offline_validation -p offline_providers `
  --basetemp=.tmp/resume-release --junitxml=.tmp/resume-migration/full.xml
```

Logs, baseline IDs and JUnit results are under `.tmp/resume-migration/`. Private
migration and profile-verification results are in the migration backup directory.

## Final verification

| Check | Result |
| --- | --- |
| Canonical active resume | `data/private/resumes/resume.pdf` (243,925 bytes) |
| New SHA-256 | `3b71d225bad9e0ab51a65d1dfed2ccf3bb9dfe7bc60b1fb0abecf9d41acb6439` |
| Old active resume still selectable | No |
| Unfinished checkpoints requiring fresh upload | 1: application 4615 |
| Historical submitted records modified | No |
| Resume-derived facts refreshed | 19 facts and 55 normalized skills |
| Explicit user facts overwritten | No; four new confirmations retained as USER_PROVIDED |
| Full offline result | 1,110 passed; all 1,100 original IDs preserved |

The migration does not establish live application readiness. The next separately
requested internship-fair fill-only run must perform normal eligibility checks,
fresh upload, browser validation and checkpoint creation.
