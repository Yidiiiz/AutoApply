# Automation audit — 2026-10-07

Evidence: two live semi-auto batches on 2026-10-07 (10 applications) and follow-up diagnostics in throwaway browser profiles. Full offline suite after all changes: **1,709 passed**.

## Fixed, ranked by impact on unattended completion

| # | Blocker | Fix | Where |
|---|---|---|---|
| 1 | Cookie-consent backdrops covered Apply / Workday entry buttons; cursor hit-test failed (`No safe clickable point`) | Decline-only consent dismissal before every guarded click; never clicks Accept | `autoapply/consent.py`, `cursor/__init__.py`, `workday.py` |
| 2 | Requirement-confirmation questions re-asked every revalidation (6 per listing) | Operator affirmative rule: skill/willingness/requirement questions answered Yes; legal, identity and demographic topics excluded | `autoapply/affirmative.py`, `answers.py`, `eligibility_questions.py` |
| 3 | Removed Workday listings served as HTTP 403 were held as security failures | `REMOVED_PAGE` text → closed; Workday CXS API `403 S22` → removed | `freshness.py`, `security.py`, `liveness.py` |
| 4 | Batches picked listings that were already closed | Pre-selection liveness probe; dashboard sweep every 30 min | `semi_auto.py`, `liveness.py`, `dashboard.py` |
| 5 | Workable "Scroll progress" bar (role=progressbar, 0 px wide) read as loading forever | Ignore zero-area and scroll/reading progress bars | `applications.py` |
| 6 | Discovery blocked the event loop (Discord heartbeat warnings); history scanned once per candidate | `ConflictIndex` built once per pass (1,255 candidates in ~1.2 s, 0 mismatches vs old policy on 150 apps); discovery runs in a thread | `conflicts.py`, `fill_batch.py`, `semi_auto.py` |
| 7 | Missed controls: ARIA radio groups, switches, contenteditable, button+listbox selects, open shadow roots, same-origin iframes | Detection, fill and validation support with local fixtures | `applications.py`, `form_controls.py`, `combobox.py`, `form_validation.py` |
| 8 | Chat widgets / newsletter modals over targets | Close only input-free, close-only dialogs | `consent.py` (`dismiss_overlay`) |
| 9 | Workday sign-in wall reported as "entry mode unresolved" | Surface the real `AUTH_REQUIRED` outcome | `workday.py` |
| 10 | #4611 missing from semi-auto exclusions | Shared exclusion set covers 3556, 4522, 6397, 4611 | `fill_batch.py`, `semi_auto.py` |

Six unreferenced helpers were removed by the T006 worker after grep showed no references from `autoapply/`, `tests/`, `scripts/` or `tools/`.

## Remaining blockers (not fixed)

1. **Workday account walls.** Most Workday tenants require Create Account / Sign In after "Apply Manually". Needs a per-tenant credential policy decided by the operator; currently an `AUTH_REQUIRED` hold.
2. **Identity verification / interactive CAPTCHA pages.** Intentionally held for the user.
3. **Facts the program cannot know** (sponsorship, authorization, GPA, start dates, salary). Must be answered once in Discord; saved answers are reused for compatible questions.
4. **Retry budget.** Entry failures consume attempts (max 3); operator reruns use `semi-auto --rerun ID --source "..."`.
5. **Live worker restart.** Code changes apply only to newly started workers; a restart closes retained forms (checkpoints are kept).
