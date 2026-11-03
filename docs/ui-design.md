# AutoApply UI design

Dashboard, sidebar, fallback pairing options and the minimized control use a
restrained lunar palette: grayscale surfaces, one small inline crescent, system
fonts, and no remote assets or build step. Dark is the base; OS light preference
uses the gray light variant. Status text and accessible names carry meaning.

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| bg | #111111 | #f3f3f3 | Page |
| surface | #191919 | #ffffff | Header, inputs |
| text | #eeeeee | #202020 | Primary text |
| muted | #b0b0b0 | #606060 | Supporting text |
| line | #383838 | #cccccc | Essential dividers |
| accent | #dedede | #303030 | Primary action, focus, checked state |
| on-accent | #151515 | #ffffff | Primary foreground, checkbox mark |
| link | #e0e0e0 | #333333 | Links |
| hover | #292929 | #e8e8e8 | Hover, selected controls |
| ready / answers / security | #cccccc / #d0d0d0 / #bcbcbc | #404040 / #484848 / #505050 | Status indicators |
| failed / neutral | #eeeeee / #888888 | #202020 / #777777 | Errors / idle |

Tokens live in extension/shared.css and dashboard_ui.py. The isolated collapsed
control and inline Ask AI button use matching grayscale values inside their shadow
roots. Body text is 14px, supporting text 12–13px and utilities 10–11px. Spacing
uses 4/8/12/16/24px; controls are 32px, header icons 30px, helper subtabs 28px.
Focus uses a visible 2px outline. Checkboxes share checked, mixed, focus and
disabled styles. Few dividers replace nested card outlines.

Ordinary buttons are transparent and borderless, with hover/pressed fills and
reduced disabled opacity. Primary actions retain a filled accent. Inputs,
switches, table separators and grouped toggle edges keep purposeful borders.
Close and run cancellation use accessible X icons and 30-32px hit targets.

The sidebar is a bounded flex column in its actual shadow iframe, up to 400px
wide. Long company/job names truncate visually with full title/accessibility text;
gear, minimize and close never shrink. Active-run controls appear only when
relevant. Tabs and content have independent scroll space. Autofill and Next page
share the persistent bottom row, with an attached unlabeled auto-advance switch and a final tiny
Analyze page utility with an accessible automatic-diagnostics switch. Auto-advance retains the per-profile preference and all
existing validation, cancellation and final-submit guards. Actionable sign-in,
wait, cancellation, retry, upload and writing errors stay visible.

The header exposes persistent Overlay / Docked controls; Overlay is the default.
Docked reserves 400px only while open at viewport widths of at least 900px.
Narrower windows use overlay and show the effective pressed state. A scoped root
attribute and stylesheet apply the reservation without editing employer inline
properties or priorities. Closing, minimizing, switching modes and removal
release it; resize, root replacement and toolbar recovery re-evaluate it. This
bounded in-page layout does not guarantee fit for every employer fixed-position
header or viewport-sized child.

Settings replaces the assistant view in place. Back/Escape restores the same tab,
conversation, draft and helper history. Embedded and fallback settings share
pairing.js: disabled inputs until storage initializes, masked key, URL validation,
Save and Test connection. Opening Settings creates no tab. Minimize shows a
draggable crescent control with a separate dismissal X; dismissal keeps it hidden
until the Chrome action/keyboard reopens it. Restore supports keyboard activation.

Skills is a compact disclosure; automatic matched filling, exclusions, Add all,
manual entry and busy cancellation remain available. Managed skill search is
excluded from pending/completed Answers; actual questionnaire answers remain.
Chat/helper use compact subtabs. Empty helper shows question/Generate; result,
versions, copy/fill, count and refinement appear only after an answer exists.
History remains per application/question. Chat keeps its scrolling log and composer
above the footer at short heights.

Dashboard navigation and content share a centered 1200px width. Settings is a
32px gear at the top right and remains in the tab keyboard sequence. Queue uses
one wrapping filter/layout/selection/Run bar; Find new listings is a direct titled
icon button, with listing checks in a compact disclosure. Run is disabled with a selection hint when empty. Sorting, pagination,
manual review, applied undo, history, settings and answers remain accessible.
Reload feedback is shown while busy with an absolute completion time on hover;
there is no per-second idle timestamp update. Answers and Settings remain lazy.
No queue backend, cache, request schedule or local skill planner was rewritten.

The Queue Applied heading and row checkboxes share the action-column grid. Job
titles remain normal links; row backgrounds also activate those links, excluding
interactive descendants, selected text and selection gestures. Rows without a
URL have no navigation target. Current Chrome run context stays inline with queue
controls and truncates with a tooltip. Completed results remain tracked by the
backend, with a completion toast instead of a Last run section.

T076 screenshots and bounding-box checks use offline synthetic forms and the
actual unpacked MV3 extension; artifacts are under `.tmp/t076-visuals`. No live
employer layout was verified.

Local T051 evidence is in .tmp/review/t051/: actual in-page iframes at 360×800,
400×800 and 360×600; active controls, embedded settings, empty/generated helper,
Chat, Answers, Skills, minimized/dismissed states; dashboard at 1440/800/500px
in both grayscale variants with unselected/mixed/all checkbox states and Settings.
Tests check click hit-testing, bounds, overflow and JavaScript errors. These local
fixtures do not establish appearance on a live employer page.


T054 keeps company and 30px icon centers on the first header row, with the
single-line title and connection indicator on the second. Main tabs are centered;
run controls keep their conditional row. Settings uses the same outlined gear on
the dashboard and sidebar. The canonical crescent path also supplies locally
rendered 16/32/48/128px manifest PNGs in extension/icons; no new permission or
asset dependency is required. Collapsed hover clips to its rounded container.

Cover-letter generation is a small text utility. Structure feedback is a transient
inline Sent immediately before its utility; errors keep that footer height and
provide actionable detail in the existing alert. Checked Auto shows an internal
lock. The stored preference survives final review, where Auto is disabled and the
footer offers an explicit manual Submit. Automatic navigation never calls Submit.

A manual final action reacquires a visible, enabled button and verifies URL, step,
validation, and idle state. A click shows pending feedback, never success. Fresh
visible employer confirmation produces a compact crescent/status, with reduced
motion support. Tracking failure or an unmatched application is separately named,
with Retry applied tracking. One Application submitted banner contains the moon and
inline Undo tracking after successful tracking; no separate applied-success text or
global progress success is shown. Undo retains site confirmation and explains that
only tracking changed. Duplicate confirmation does not repeat success or retry a
failed tracking write; retry remains explicit.

Evidence is in .tmp/review/t054, using isolated local fixtures only. The dashboard
favicon uses the same SVG path, with a narrow img-src data: allowance. Browser
verification decodes and paints the declared image under the actual response CSP
and records no policy violations. Other policy directives are preserved. The
existing manifest PNGs remain required integration assets at 16/32/48/128 pixels;
their headers, dimensions, manifest paths and visual sheet are checked.


T056 main Fill/Answers/Chat tabs are 14px and 36px high; helper subtabs retain
12px/28px sizing. Settled field totals appear only inside the rerunnable Autofill
button. Scans show Filling immediately; navigation clears previous totals. The
Next page switch keeps its accessible name, focus outline and saved preference;
its on-state lock is centered in the knob, with no visible Auto label. Final
review still disables auto-advance and requires an explicit Submit click.

The small switch beside Analyze page defaults on and persists locally.
The background captures settled unknown/failed fields, including after writing,
using passive structure reads. Automatic success says Sent; manual success says Analyzed,
inline left of Analyze page, fading after 2.5 seconds and clearing at 2.8.
Stable capture events and session seen markers prevent rerenders, saved-signature
reruns and preference changes from replaying success. Automatic failure stays as
compact Failed with accessible retry detail; manual recovery clears the error.
Turning automatic capture off hides its feedback; manual Analyze remains available.
There is no separate above-footer success line. These
snapshots await the next active review session; they never wake a supervisor or
start automatic code fixes. Local fixture evidence is in .tmp/review/t056/.


T057: Hide legacy application status segments and Worker navigation from both layout and keyboard focus. Queue remains the default, and legacy hashes redirect to visible panels. Fixture screenshots at 1440px and 500px verify navigation and horizontal fit. Toolbar actions restore the sidebar rather than toggling it closed; minimized and dismissed states remain separate controls. Chat replacement requires an explicit user request and one eligible field, preserves the full draft, and shows success only after verified replacement. Narrow chat evidence is fixture-only; actual employer acceptance is pending review.

T063: Analyze page keeps the small footer text action and passive automatic-capture switch. Explicit Analyze immediately shows Analyzing with aria-busy, fills only supported empty current controls using grounded saved answers, refreshes choices and retries a failed widget once, then verifies retained values. The compact result above the footer says "Filled N · M need attention"; expandable details show current unresolved issues, excluding optional skips and inactive responsive copies. Busy conflicting operations ask for wait/retry. Unknown facts, unsupported widgets, files and writing remain for the user. Navigation and changed form structure clear stale results. No modal, extra fill control, automatic navigation, upload or section creation is introduced. The note remains "Details saved for review." Autofill retains its broader existing workflow.
