# Rolling-age filter and listing cleanup

Implemented on September 21, 2026. The default and maximum listing age are now
30 elapsed days. A stricter configured window is allowed; values above 30 fail
configuration validation. Freshness, listing availability and application state
are independent.

## Root cause and filtering

Repository discovery parsed entire supported README tables without a result cap.
Ingest then created a job and application for every parsed row, including old,
closed and undated entries, and calculated priority before rejecting them. The
browser adapters previously read only one page per search URL: the historical
archive growth was not caused by an existing unlimited browser pagination loop.
Date parsing also discarded time-of-day precision and treated ambiguous ages too
loosely.

The new flow extracts basic card/table metadata, resolves source/URL/ATS identity,
normalizes posting evidence, checks age and known closure, and only then creates
and ranks a new application. Rejected sightings retain lightweight identity/date
observations for deduplication and metrics, without a new application or archived
description. Unknown, malformed and future dates fail closed. UTC timestamps
preserve the exact inclusive cutoff; 30 days plus one second is stale.

## Date evidence and discovery bounds

The centralized extractor prefers structured `datePosted` (including a single
JSON-LD JobPosting), API posting metadata, ATS posting metadata, explicit posted-on
dates, machine-readable HTML dates, and reliable relative posting text. It records
source and confidence. Generic updated/modified fields are excluded. Contradictory
or malformed higher-priority evidence is not masked by a fresher fallback.

Repository relative ages are anchored to the saved source commit timestamp;
browser relative ages are anchored once to observation time. Repeated discovery
does not refresh a known posting date. Earlier posting evidence wins across
duplicates. An explicit genuine repost may supply a new effective posting date,
while the original date remains separately recorded. Generic modification or
promotion is insufficient. Naive/date-only dates mean midnight UTC; ambiguous
month ages are unknown, and `30+ days` / `over 30 days` are definitely stale.

LinkedIn queries use its date-posted filter and retain stricter saved filters. The
supported day/week/month options are documented in [LinkedIn Help](https://www.linkedin.com/help/linkedin/answer/a507441/filter-and-sort-job-search-results?lang=en).
The adapter selects an option no longer than the configured local window. GitHub
README discovery has no posting-date search parameter. Handshake keeps saved
search URLs because a portable recency parameter has not been verified. Local
date checks remain authoritative for every adapter.

Browser queries default to five pages and 250 unique visible results. Pagination
follows only actual next-page links on the same host, detects loops, and reports
why it stops. A stale-page early exit requires a source's explicit newest-first
guarantee and a nonempty page containing only definitively stale dates. Current
adapters make no such guarantee, so mixed/ranked pages continue to the bounded
limit. Main and off-season repository READMEs each have a 250-result cap; main is
processed first and historical archive files are not scanned.

## Lifecycle, deduplication and submission safety

Listing metadata includes posting provenance/confidence, original/repost/update
dates, last-seen/checked times, lifecycle, freshness, active membership, and
closure/stale/cull timestamps. The active view excludes stale, unknown, closed and
removed listings. Historical jobs remain durable deduplication records, including
those referenced by applications. Culling means removal from active membership,
not deleting application history.

Source plus job ID, canonical URLs and ATS requisition IDs resolve identity before
ranking. Tracking parameters such as `utm_source`, `ref` and `source` do not create
new identities. Lightweight rejected sightings also retain source-ID aliases, so
a new tracking URL cannot rejuvenate an old rejected job. Company/title/location
fuzzy merging is deliberately avoided when requisition identity is available.

Startup, discovery boundaries and queue access perform idempotent cleanup. Claims,
application start, AI drafting and the final pre-submission step recheck
eligibility. Known closure, explicit page closure text and definitive HTTP 404/410
stop processing; authentication, security blocks, rate limits and server errors
are not evidence of closure. Existing already-applied evidence takes precedence
over closure text. Closed-before-submit records receive a distinct reason and are
not counted as failed applications or retried. Cleanup does not transition any
application; processing guards may stop an untouched pending attempt. Prior
submission intent, manual holds and confirmation evidence are protected.

## Stored-data audit

The migration runs offline under the worker lock. Before changes, it backs up the
SQLite database and private configuration, fingerprints every application row,
question, event and transition, and fingerprints application-history records and
evidence assets. Listing metadata is allowed to change; application history is
not. The private policy is updated from 14 to 30 days. Previously final invalid
applications are preserved and are not automatically reopened by this change.

| Migration category | Count |
| --- | ---: |
| Existing listings examined | 6,841 |
| Fresh dates within 30 days, including closed listings | 3,181 |
| Verified older than 30 days | 426 |
| Unknown exact posting date / ambiguous records | 3,234 |
| Confirmed closed from stored source evidence | 3,138 |
| Open listings marked stale | 366 |
| Old closed listings culled from active storage | 60 |
| Previously pending applications removed from processing queue | 0 |
| Application records retained | 6,841 |
| Duplicate records merged | 0 |
| Active fresh listings after migration | 2,838 |
| Existing queued applications still eligible | 1,624 |

All 3,138 known closed listings are inactive. The 60 cull count is specifically
the subset with a verifiable date older than 30 days. The 3,234 unknown-date
records use abbreviated month ages (`1mo` through `8mo`); the previous parser's
month-to-30-day approximation was discarded. None enters the new-processing
queue. The zero pending-removal count is expected: the existing application
queue was already restricted by the former 14-day policy, although the historical
store was not. Fresh date eligibility does not automatically reopen final
application outcomes.

Preservation verification passed for all **6,841 application-history records**
and **54,730 supporting files**. Application rows, questions, events and transition
tables have identical pre/post fingerprints. Every canonical history record has
identical non-listing fields after the archive's existing empty-field
normalization; every checked evidence/answer/status/confirmation asset is byte
identical. The initial raw-export comparison detected restored empty fields;
startup validation normalized them and the exact original-baseline comparison
then passed. No historical status entries or nonempty application values were
changed. The protected unconfirmed manual-review record is unchanged with retry
still disabled.

Application outcomes remain 3,138 CLOSED, 2,077 INVALID, 2 MANUAL_REVIEW and
1,624 QUEUED. A second cleanup changed zero listings and culled zero additional
records. Rebuilding statistics from canonical history preserved the listing
metrics, and the durable history-export queue is empty.

Private audit artifacts are `listing_freshness_backup.sqlite3`,
`listing_freshness_config_backup.yaml`, `listing_preservation_baseline.json`,
`listing_migration_report.json`, and `listing_verification_result.json` beneath
`data/private`. They are ignored by Git. The migration script is retained locally
in `.tmp/migrate_listing_freshness.py`; the completed baseline verification is in
`.tmp/verify_listing_freshness.py`.

## Statistics

Application-history statistics now include `listing_stats`: unique discovered
identities, current fresh eligibility, too-old/unknown-date counts, closed and
stale counts, culled closed-stale records, duplicate sighting events, average and
median age at first discovery, and fresh discoveries today/this week (UTC).
Categories overlap: a stale closed job appears in both too-old and closed counts.
Duplicate sightings are events rather than unique opportunities. Migration cannot
recover duplicate sightings that were not historically recorded.

Discovery logs summarize raw parsed results, already-known identities, records
older than the configured window, unknown dates, closure, fresh eligibility, new
storage, existing fresh updates, scanned pages and browser stop reasons. Repository
scans count source snapshots as pages; README result caps are logged separately.
Cleanup, date rejection and deduplication emit explicit diagnostic logs. Listing
statistics can be rebuilt and are merged into history statistics without changing
application outcomes.

## Files changed

| Files | Responsibility |
| --- | --- |
| `autoapply/freshness.py` (new) | Canonical age/date/closure policy and stale-page boundary |
| `autoapply/listing_store.py` (new) | Additive schema, backup/migration, lifecycle, cleanup, guards, statistics |
| `autoapply/database.py` | Early ingest filtering, aliases, batching, claim/retry guards |
| `autoapply/models.py`, `autoapply/jobs.py` | Posting metadata and timestamp-aware compatibility helpers |
| `autoapply/sources.py` | Source provenance, recency filter, bounded discovery and pagination |
| `autoapply/engine.py`, `autoapply/browser.py` | Discovery maintenance, application/pre-submit guards, reliable closure |
| `autoapply/control.py`, `autoapply/__main__.py` | Active queue/pending views, AI guard, status and maintenance commands |
| `autoapply/archive.py` | Listing-statistics integration and damaged-sidecar recovery |
| `autoapply/config.py`, `config/config.example.yaml` | Enforced maximum and discovery caps |
| `tests/test_freshness.py` (new) | Date, migration, history, deduplication, pagination and queue regressions |
| `tests/test_browser.py`, `tests/test_core.py`, `tests/test_history.py` | Local pre-submit safeguards and updated filtering expectations |
| `README.md`, `DESIGN.md`, this report | Operation, architecture, audit and limits |

## Validation

- Full suite: **306 passed** in 270.38 seconds, including local Chromium,
  submission/retry safety, cursor and history tests.
- Subsequent focused verification: **76 passed** for freshness and browser
  conditions, including two new precedence cases; **127 passed** for freshness,
  core and history after queue/notification changes; final freshness suite
  **73 passed** after adding non-posting-relative-text regressions.
- Tests cover exact 30 days and 30 days plus one second, UTC/DST, future/malformed
  dates, missing dates, relative/lower-bound dates, reposts and generic updates,
  no ranking/application creation for rejected records, duplicate identity/date
  conflicts, migration backups, submitted/manual-history preservation, idempotent
  cleanup, statistics rebuilding, caps and ordered versus mixed pagination,
  expired queued work, and final pre-submit changes to stale/closed/unknown.
- Ruff syntax/undefined-name checks, scoped `F` checks, bytecode compilation,
  wheel build and isolated CLI cleanup/statistics smoke tests passed.
- The publishable-content privacy check returned no findings. `git diff --check`
  passed; most repository files are currently untracked, so this command has
  limited coverage until they are staged. No commit or push was made.

Reproduction commands:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=.tmp/freshness-full-suite.xml
.\.venv\Scripts\python.exe -m pytest -q tests/test_freshness.py tests/test_browser.py -k 'freshness or browser_conditions'
.\.venv\Scripts\python.exe -m pytest -q tests/test_freshness.py tests/test_core.py tests/test_history.py
.\.venv\Scripts\python.exe -m ruff check autoapply tests --select E9,F63,F7,F82
.\.venv\Scripts\python.exe -m ruff check autoapply/freshness.py autoapply/listing_store.py tests/test_freshness.py --select F
.\.venv\Scripts\python.exe -m compileall -q autoapply
.\.venv\Scripts\python.exe -m pip wheel . --no-deps --no-build-isolation --wheel-dir .tmp/wheels
.\.venv\Scripts\python.exe -m autoapply --root .tmp/freshness-cli listings cleanup
.\.venv\Scripts\python.exe -m autoapply --root .tmp/freshness-cli listings stats
.\.venv\Scripts\python.exe -c "from pathlib import Path; from autoapply.privacy import privacy_check; print(privacy_check(Path('.')))"
git -c safe.directory=C:/Users/yzhao/Documents/GitHub/AutoApply diff --check
```

## Remaining limits

- Migration uses stored posting and closure evidence; it does not navigate every
  historical employer page to establish current availability. Active jobs are
  checked when processing reaches their page, then again before submission.
- Source date accuracy and layout stability remain external dependencies.
  Unsupported dates fail closed. Date-only UTC interpretation may conservatively
  exclude part of the oldest calendar day.
- Ranked sources can contain fresh jobs beyond the configured safety cap. The cap
  bounds work and is configurable; it does not promise exhaustive search coverage.
- Existing closed identities are not silently reopened even with a newer date.
  A new requisition is distinct; manual review is needed for ambiguous reopening.
- Cleanup retains durable history and lightweight deduplication observations.
  It reduces active workload, not the size of permanent application history.
- The active view reflects the last cleanup. All processing entry points and the
  final submission guard re-evaluate time, so a cached flag alone grants no right
  to submit.
- Browser tests use local fixtures. No live source scan or employer submission
  was used to validate this change.
