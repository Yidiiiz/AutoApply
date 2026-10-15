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

The sidebar is a bounded flex column in its actual shadow iframe, up to 400px
wide. Long company/job names truncate visually with full title/accessibility text;
gear, minimize and close never shrink. Active-run controls appear only when
relevant. Tabs and content have independent scroll space. Autofill and Next page
share the persistent bottom row, with an attached Auto switch and a final tiny
Send page structure utility. Auto retains the per-profile preference and all
existing validation, cancellation and final-submit guards. Actionable sign-in,
wait, cancellation, retry, upload and writing errors stay visible.

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
one wrapping filter/layout/selection/Run bar; More contains discovery and listing
checks. Run is disabled with a selection hint when empty. Sorting, pagination,
manual review, applied undo, history, settings and answers remain accessible.
Reload feedback is shown while busy with an absolute completion time on hover;
there is no per-second idle timestamp update. Answers and Settings remain lazy.
No queue backend, cache, request schedule or local skill planner was rewritten.

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
