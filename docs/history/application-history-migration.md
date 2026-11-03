# Application-history migration

Implemented on September 21, 2026. This maintenance work did not run live
applications or submit any employer forms.

## Storage and migration

The previous history consisted of flat `<id>_<company>_<title>/` bundles containing
`application.json`, listing/source/question/answer/generated-response/status/event/
confirmation JSON, `job_description.txt`, and optional screenshots. The inventory
contained 5,217 application bundles and one empty directory; SQLite contained
6,841 applications. The new implementation refactors that existing archive.

Bundles retain their names and contents under the current state directory:

```text
data/private/application_history/
  discovered/<id>_<company>_<title>/application.json
  opened/                  filling/
  ready_to_submit/         submitting/
  submitted/               manual_required/
  failed/                  unknown/
  rate_limited/            closed/
  invalid/                 duplicate/
  ineligible/              already_applied/
  statistics.json
  migration_report.json
```

Each application record contains a stable `application_id`, canonical
`application_state`, and `status_history` with timestamps and available
reason/source/security/verification metadata. Existing IDs take precedence;
missing IDs are derived from URL or job metadata. Duplicate copies of one ID are
merged, with combined transitions, affirmative confirmation preserved, and
conflicting files retained under `preserved/`. Malformed records retain their raw
bytes and receive an UNKNOWN recovery record.

The complete original tree was copied to
`data/private/application_history_migration_backup/` before moving records. The
empty directory contains no application and is excluded from totals. A temporary
Windows directory lock interrupted the first migration; the preserved record was
recovered and bounded retries were added. Migration then resumed from the mixed
flat/status layout using the original backup.

Final inventory and preservation verification are recorded privately in
`data/private/history_migration_result.json` and
`data/private/history_verification_result.json`.

All **5,217 existing bundles were migrated**, and **1,624 previously unarchived
database applications were exported**, giving **6,841 canonical applications**:

| State | Count |
| --- | ---: |
| DISCOVERED | 1,624 |
| CLOSED | 3,138 |
| INVALID | 2,077 |
| MANUAL_REQUIRED | 2 |
| OPENED, FILLING, READY_TO_SUBMIT, SUBMITTING, SUBMITTED | 0 each |
| FAILED, UNKNOWN, RATE_LIMITED, DUPLICATE, INELIGIBLE, ALREADY_APPLIED | 0 each |

No duplicate IDs required merging and no ambiguous records required UNKNOWN.
The original empty directory remains in the backup. The resumed migration report
counts 5,173 moves because 44 bundles had already moved before it resumed; the
original 5,217-bundle inventory was fully covered. No flat application bundles
remain. The resumed migration reported no additional warnings.

The backup comparison checked all 5,217 original records and **31,313 sidecar or
attachment files**. Of those files, **25,962 were unchanged**. Current database
metadata refreshed 5,208 source sidecars and 143 listing sidecars; their values
were verified against SQLite. Application records also received 5,215 existing
database discovery-stage values and 143 updated posting dates. The exact previous
snapshots remain in the backup. No unexpected answer, description, confirmation,
event, or screenshot changes were found.

Every database ID occurs exactly once in the organized tree, internal states
match directories, and the durable export queue is empty. The protected
manual-review application remains unconfirmed with retries disabled. Statistics
record one submission attempt, zero confirmed submissions, two manual
interventions, and one recorded spam rejection.

An independent statistics rebuild from all 6,841 canonical records matched every
stored metric, excluding only the generation timestamp. Repeated validation
required no moves or duplicate repairs and reported no warnings.

## Statistics and APIs

`statistics.json` is derived from canonical application records and includes:

- Counts for every canonical status and totals by established ATS, company,
  location, and recorded source.
- Submission/failure/manual/unknown rates and percentages, plus a separate
  completed-attempt success rate using SUBMITTED + FAILED.
- Recorded submission attempts, confirmed submissions, failed submissions,
  manual interventions, security states and security providers.
- UTC daily/weekly/monthly activity and 366 calendar days of daily detail.
- Discovery-to-submission average/median, filling-to-submission average,
  completed manual-episode average, and oldest/recent application identifiers.

Missing timing evidence produces null. Closed or invalid listings are separate
from failures. Processing retries are not submission attempts. Multi-source
applications contribute once to each recorded source. No role or work-location
classification is invented.

Status updates use `Database.transition` and `Database.update_security`.
Transactional triggers capture history and enqueue exports; post-commit export
moves the bundle and refreshes statistics. Pending exports survive failures.
Explicit deletion preserves the bundle outside the active tree before removing
it from aggregates.

Central lookup APIs are `db.history.get_application(id)`,
`find_by_job_id(id)`, `find_by_url(url)`, and
`list_applications(status=None)`. Existing `archive_application` callers still
receive the current bundle path. Imported history participates in duplicate
submission checks.

Maintenance commands:

```powershell
.\.venv\Scripts\python.exe -m autoapply history migrate
.\.venv\Scripts\python.exe -m autoapply history validate
.\.venv\Scripts\python.exe -m autoapply history stats
.\.venv\Scripts\python.exe -m autoapply history rebuild-stats
```

Equivalent standalone functions are `migrate_application_history`,
`validate_application_history`, and `rebuild_application_statistics` in
`autoapply.archive`. Startup validates and repairs the layout automatically.

## Files changed

| File | Change |
| --- | --- |
| `autoapply/archive.py` | Refactor archive, migration, preservation, lookup, atomic writes, directory moves and export |
| `autoapply/history_statistics.py` | Rebuildable record-derived aggregates |
| `autoapply/database.py` | Durable export queue, transition capture, automatic synchronization and imported-history conflict lookup |
| `autoapply/__main__.py` | History maintenance commands |
| `tests/test_history.py` | Migration, transitions, statistics, preservation and failure recovery regressions |
| `tests/test_browser.py` | Updated status-directory archive lookup |
| `README.md`, `DESIGN.md` | Storage/API/metrics/recovery documentation |
| `.gitignore` | Exclude temporary local build artifacts |
| `HISTORY_MIGRATION.md` | Implementation and migration report |

Repository-wide searches covered history readers/writers, duplicate detection,
control/Discord paths, engine/manual handling and tests. Existing workflow readers
use SQLite; archive writers use the central archive API. The one test assuming a
flat archive was updated.

## Validation

- Earlier full suite: **231 passed** in 461.34 seconds before the final Windows
  retry and deletion-recovery refinements.
- Final suite coverage: **234 passed across batches**. The final full invocation
  passed 91 tests before a cursor fixture's local page navigation exceeded its
  three-second setup timeout. That test immediately passed in isolation; rerunning
  it and all remaining collected tests passed **143 tests** in 83.31 seconds.
  No test timeout settings or cursor behavior were changed to obtain this result.
- Latest history suite: **27 passed**, including those refinements and malformed
  non-UTF-8 preservation. Existing tests also cover browser submission and retry
  safety; no live application was used for validation.
- Ruff syntax/undefined-name checks passed across `autoapply` and `tests`; broader
  `F` checks passed for archive/statistics/history tests.
- Bytecode compilation, wheel build, history CLI rebuild smoke test and
  publishable-content privacy scan passed.

Commands used:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --disable-warnings --maxfail=1
.\.venv\Scripts\python.exe -m pytest tests/test_history.py -q --disable-warnings --maxfail=1
.\.venv\Scripts\python.exe -m ruff check autoapply tests --select E9,F63,F7,F82
.\.venv\Scripts\python.exe -m ruff check autoapply/archive.py autoapply/history_statistics.py tests/test_history.py --select F
.\.venv\Scripts\python.exe -m compileall -q autoapply
.\.venv\Scripts\python.exe -m pip wheel . --no-deps --no-build-isolation --wheel-dir .tmp/wheels
.\.venv\Scripts\python.exe -c "from autoapply.privacy import privacy_check; print(privacy_check('.'))"
```

## Limits

SQLite owns live workflow state; canonical history records independently support
statistics rebuilding. Database commits, sidecar writes, bundle moves and
statistics cannot be one filesystem transaction. Durable export replay and
startup validation repair interruptions. Persistent filesystem failures remain
visible and require fixing the underlying access/storage problem.

Distinct explicit application IDs remain distinct. Matching missing IDs to
existing IDs requires unambiguous metadata; uncertain identity is not silently
collapsed. Historical diagnostic paths remain historical evidence, while current
screenshot pointers follow moved bundles. Missing old transition timestamps
cannot be reconstructed. External manual edits require validation/rebuild to
refresh cached records. Statistics recompute over cached records per mutation;
very large datasets may require incremental aggregates later.
