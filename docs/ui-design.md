# AutoApply UI design

Dashboard, application sidebar and pairing options share the same CSS tokens.
Use local system fonts; no remote fonts, assets or build step.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| bg | #f6f5f1 | #151c27 | Warm page / navy page |
| surface | #ffffff | #1c2635 | Cards, header, inputs |
| text | #223047 | #e7edf5 | Primary ink |
| muted | #59677b | #aab8cb | Supporting text |
| line | #dce1e7 | #354356 | Borders |
| accent | #285bc5 | #3b6ed8 | Primary actions, focus, links |
| link | #285bc5 | #9bbaff | Readable text links |
| hover | #edf1f7 | #28364a | Hover and selected controls |
| ready | #20754c | #74c69d | Confirmed success |
| answers | #97601c | #e5bc76 | Attention / missing input |
| failed | #b33838 | #ff9a9a | Actionable errors |
| security | #74519b | #c0a6e0 | Security holds |

Typography: Segoe UI/system sans, 14px body at 1.5 line height; 12–13px
supporting text; 16px sidebar brand and 22px dashboard page title. Utility links
may use 10–11px. Weights 400/500/600. Numbers and timestamps use tabular figures.

Spacing follows 4/8/12/16/24/32px increments. Cards use 8px radii and subtle
1px borders. Inputs/buttons use 6px radii and a 32px minimum height; compact
header icons are 28–30px. Focus is a visible 2px blue outline with 2px offset.
Disabled controls remain recognizable with reduced opacity and a disabled cursor.
Primary buttons have white text; hover changes surface or brightness. Respect
system light/dark and reduced-motion preferences. Hidden or empty status lines
reserve no space. Errors retain text and do not rely on color alone.

Dashboard content is centered within 1200px; header and content share aligned
horizontal padding. Queue is the default task. List rows and headers use matching
columns, hiding secondary metadata together at intermediate widths. Filtering,
sorting, pagination and selection remain available. Settings and Answers load on
demand. Rare worker controls are under a secondary Worker tab and a closed
Legacy worker controls disclosure. Listing-check details are collapsed.

The sidebar uses a bounded flex column: compact job identity and conditional run
icons, one Autofill action, Fill/Answers/Chat tabs, scrollable content and a fixed
footer area. Skills is a small disclosure; selected chips, other skills and manual
entry expand only on demand. Add all precedes Type to add skills. Auto-advance
sits beside Next page. Send page structure is the last, tiny text-only footer
utility. Traces and internal Workday step detection remain automatic without
visible narration or saved-trace notices. Connection uses an accessible status
indicator with a connected name/title. Settled results use compact field/skill
totals; completed writing and cover attachment do not add status narration.
Sign-in instructions, waiting/cancellation/retry guidance, warnings and errors
remain visible. Completed Answers and actual page values/uploads retain outcomes.

T050 local screenshots in .tmp/review/t050/ cover the materially changed sign-in,
cancelled wait, actionable error and settled summary states at 360px in light and
dark. Tests check accessible state, overflow, footer bounds and JavaScript errors.

Local screenshot evidence for T049 is in .tmp/review/t049/: dashboard at
1440/800px and sidebar at 360/400px, each in light and dark. Tests check horizontal
overflow, list-cell overlap, footer bounds and JavaScript errors. These fixtures
do not establish appearance or behavior on a live employer page.
