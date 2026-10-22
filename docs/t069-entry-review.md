# T069 entry and sidebar review

Combined T069 changes are limited to `extension/background.js`,
`extension/content.js`, the focused browser check and T069 fixtures/report.
This correction modifies only `content.js`, the check, the new synthetic hydration
fixture and this report; prior entry/diagnostic changes are preserved.
Existing out-of-scope dirty files, including T067, were verified byte-for-byte
unchanged against this attempt's initial hashes.

## Public evidence (2026-10-22)

The preserved first-attempt report records inspection of the four supplied URLs
using the installed Playwright full Chromium,
fresh anonymous contexts and GET-only routing. Every non-GET request was aborted,
including employer telemetry and SmartRecruiters POST requests. No entry link,
OAuth control, login or submission was activated on a public site. Frame challenge
tokens and page bodies were not saved as fixtures.
This correction attempt made no live requests. The observations below are prior
evidence, not fresh personal verification by this attempt.

| Supplied page | Observed public response and DOM |
| --- | --- |
| [Anduril Greenhouse](https://job-boards.greenhouse.io/andurilindustries/jobs/5271672007) | HTTP 200. Ordinary document inputs for names, email, phone, resume and questions; no open shadow roots found. The public Apply button coexists with the actual application controls. |
| [Keysight](https://jobs.keysight.com/external/jobs/55110) | HTTP 403, title/body `403 Forbidden`. Exact entry-button structure could not be verified. The reported `button role=link` shape is tested synthetically, with Apply and Apply now captions; this is not a live success claim. |
| [SmartRecruiters landing](https://jobs.smartrecruiters.com/LinkedIn3/744000156616169) | HTTP 200. Exact `I'm interested` anchors target `/oneclick-ui/company/LinkedIn3/publication/001359e4-c46c-45cb-a388-bc79014089ec?dcr_ci=LinkedIn3`. A second GET confirmed two eligible canonical anchors, another canonical sticky anchor above the document origin, and two `display:none` alternatives targeting `www.smartr.me`. The reduced fixture anonymizes company/job/publication IDs and retains these branches. Open shadow roots belonged to location/icon/toaster components; no exposed form inputs were observed there. |
| [SmartRecruiters OneClick](https://jobs.smartrecruiters.com/oneclick-ui/company/LinkedIn3/publication/001359e4-c46c-45cb-a388-bc79014089ec) | HTTP 403 and a `geo.captcha-delivery.com` verification iframe. No application inputs were available to inspect. This does not establish whether the real application uses shadow DOM, frames or ordinary controls. |

## Changes

The pre-fix sidebar race was reproduced in actual offline MV3 Chromium by holding
the old `sidebar-ready` storage read, explicitly opening through the toolbar's
handler function, and releasing the old `closed` restore. The sidebar opened and
then closed. The same new regression check run against temporary copies of the
HEAD extension files fails specifically at `delayed closed restore cannot close
explicit open`.

Sidebar intents now acquire a revision before asynchronous reads; writes and
injections are serialized. Restores retain their original revision and consult
the latest explicit state, including an open that has not yet reached storage.
Close/minimize persist, a newer open wins, and reinjection reuses one host.

The supervisor subsequently exercised the actual MV3 extension against public
Anduril Greenhouse. As recorded in
`.agent/t069-supervisor-public-evidence.json`, the first run showed one visible
open host at DOMContentLoaded, then no host within 500 ms and for six seconds.
A second run stayed open, demonstrating timing-dependent hydration removal.
The supervisor also reported that explicitly removing the open host left it
absent after one second. These are supervisor observations, not live tests
performed by this correction attempt. They establish an additional DOM-removal
failure beyond the asynchronous restore race.

An existing open/minimized host now watches only direct child changes on the
document and current document root. A coalesced one-shot repair reattaches that
same host after removal, including replacement of the entire root; it reads the
latest state and preserves the host, iframe element and minimized position.
Closing disconnects the watcher and cancels pending repair. No watcher is created
before a sidebar exists, and no polling or application-subtree observation is
added. Repairs are capped at five within a one-second burst; a renderer that
continually removes the host disables recovery until another explicit sidebar
intent rearms it. This prevents a feedback loop. The synthetic hydration fixture
is not a capture of the employer page, and a live post-fix retest is still pending.

Entry retains exact Apply/Apply for this job and guarded Workday manual/Continue
behavior, adds Keysight-scoped Apply now and SmartRecruiters-scoped Interested
anchors to the same company's OneClick route. Repeated identical anchors count as
one destination. Distinct eligible targets/buttons, form-contained actions,
submit controls, active inputs, sign-in/review steps and credential-bearing URLs
are rejected. Apply with LinkedIn remains manual. Existing run advancement code
is unchanged.

Analyze captures fixed action captions and sanitized public targets, root IDs,
open shadow roots, frame accessibility and document loading state. Queries,
fragments, credentials, arbitrary target paths and input values are excluded.
Discovery is capped at 40 roots/10,000 visited elements and 20 iframe summaries
per document, with existing snapshot node/byte limits retained. Closed shadow
roots are explicitly unobservable. No speculative shadow fill implementation
was added: exposed shadow inputs produce an unsupported-shadow explanation;
blocked, loading and unavailable embedded content have separate actionable
messages instead of a categorical no-form summary.

## Verification

` .venv/Scripts/python.exe tests/test_extension_entry.py ` passes 60 actual
Chromium checks, including all 46 preserved checks. It loads the real unpacked extension,
uses no HTTP server process, resolves no employer hosts, fulfills all browser
navigations locally and stubs the extension's local API in its service worker.
It does not import the applicant/answers backend or rely on the dirty T067 files.
The check also runs as a pytest test, but the standalone command avoids repository
conftest/backend imports. No backend answers-engine validation is claimed.
The same 60 checks also pass in a temporary snapshot containing clean `main`
backend files (`f7306e1`) and the current extension/check/fixtures; this verifies
that the browser checks do not depend on the dirty T067 answers implementation.

Coverage includes both restore races, ready during a pending explicit open,
close/minimize/reopen and one host, public-DOM-derived repeated entry anchors,
open/minimized host removal, entire-root hydration and watcher retargeting,
closed/dismissed/no-initial-host non-recovery, queued repair versus newest intent,
host/frame/position identity, unrelated subtree churn and bounded hostile removal,
synthetic Keysight/Workday/negative entry cases, native scan/labels/fill and
preserved edits, actual Analyze messages and payloads, private-value/URL
redaction, diagnostic caps, and three real run ticks holding the current claim.
Progress reports remain allowed; terminal release and submission are forbidden.
`node --check` passes for both modified JavaScript files; scoped and full
`git diff --check` pass. The full extension suite was intentionally not run.

Remaining verification limitation: Keysight and OneClick require a later usable
public page/user capture. Their real inputs and successful live entry/fill remain
unverified. The new diagnostics make that capture actionable without asserting
that the form has no controls.

## Legacy test compatibility (attempt 3)

Updated only `test_t057_toolbar_entrypoint_missing_script_and_restricted_pages`
to extract from `const sidebarVersions` through `globalThis.autoApplySidebar=`,
retain all registered navigation listeners, and drain queued asynchronous work.
The restore setup records minimize through the shipped API before simulating a
new document, keeping persisted state and intent versions consistent. All prior
toolbar, missing-script, restricted-page and navigation-restore assertions remain.
The focused pytest test passes (1 test), and the standalone offline Chrome check
passes all 60 checks. No production changes or live service access in this attempt.
