# Application entry testing

The harness opens a listing and calls its existing adapter's `begin()` entry path.
It observes readiness, declines cookie banners when a reject/decline control is
available, closes input-free dialogs containing only close-like controls, follows
Apply links/buttons, and chooses Workday **Apply Manually**. It never fills fields,
uploads documents, creates accounts, advances application steps, or submits.
An Accept-only banner may produce `entry_failed`; acceptance is never a fallback.

`probe_entry(page, url)` returns `EntryResult` with `outcome`, `steps`,
`field_count`, `ats`, and `error`. Outcomes are:

| Outcome | Meaning |
| --- | --- |
| `form_ready` | The entry adapter finished and visible form controls are ready. |
| `account_required` | Sign In/Create Account or password fields block further entry; this is a correct terminal. |
| `closed` | Explicit removed/closed text or HTTP 404/410. |
| `security_challenge` | A security/verification control requires operator attention. |
| `entry_failed` | Entry/readiness could not be established safely, or a final request was blocked. |

Reports contain control/transition steps and error categories, without field values
or page text. The context-level request guard aborts every request recognized by
`batch_approval.is_final_submission`, including body-based actions. It remains
active until the context closes. Callers must create contexts with
`service_workers='block'` and use a fresh page for each listing.

## Offline fixtures

`tests/fixtures/entry/` covers a OneTrust backdrop, Accept-only consent, Workable
scroll progress, an inline Greenhouse form, a Lever Apply link, Workday manual
entry behind consent, Workday account creation, newsletter and chat dialogs,
removed listings, a security challenge, an in-modal application form, scripted
submission attempts, and a misleading submit button labelled Apply.
The tests serve files over loopback HTTP. Synthetic ATS URLs are intercepted and
rewritten to that server so the real adapter selection and cursor are exercised
without contacting employer hosts. They assert zero Accept/submit clicks, empty
fields/files, and zero mutation requests received by the fixture server.

Run from the repository root, using the environment's installed Chromium:

```powershell
python -m pytest -p no:cacheprovider --basetemp .tmp/entry-tests tests/test_entry_harness.py tests/test_workday_entry_mode.py tests/test_workday_readiness.py tests/test_browser_patience.py tests/test_autonomy_fixes.py
```

## Opt-in live entry check

Only run this command when you want to contact public employer application pages:

```powershell
python -m autoapply entry-test --limit 5
python -m autoapply entry-test --ids 101 102 --limit 2 --headed
python -m autoapply entry-test --help
```

`--ids` means **job/listing IDs**, not application IDs. The default limit is five.
Selection reads active/open listings in descending stored priority order, with ID
as the tie breaker; requested IDs still must be active. The SQLite connection is
read-only. There is no discovery, liveness refresh, database migration, application
row update, worker startup, lock access, messaging, or budget change.

The command uses a new throwaway temporary Chromium profile with no applicant
cookies. It closes every page and the context and deletes the temporary profile
afterwards, including on failure. It never uses `data/private/browser_profile`.
Account requirements may therefore be more common than in a logged-in session.
It writes only `data/private/entry-test-report.json` and prints the same JSON,
including each listing's outcome and steps. An empty selection writes an empty
report without starting a browser. Auto-submit stays off.
