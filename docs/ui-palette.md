# Shared UI palette

Dashboard and extension use the same moon mark and neutral tokens. Dark surfaces are #111111 / #191919 with #eeeeee text; light surfaces are #f3f3f3 / #ffffff with #202020 text. Muted text, hover fills and monochrome accents use the matching CSS custom properties in dashboard_ui.py and extension/shared.css.

Ordinary buttons are transparent and borderless, with hover/pressed fills, reduced disabled opacity and visible keyboard focus outlines. Primary actions retain a filled accent. Inputs, switches, table separators and grouped toggle edges keep their purposeful borders. Close and run cancellation use accessible X icons and 30-32px hit targets.

The Queue Applied heading and row checkboxes share the same action-column grid. A job title remains a normal link; row backgrounds also activate that link. Interactive descendants, selected text and selection gestures are excluded. No URL produces no navigation target.

Current Chrome run context stays inline with queue controls and truncates with a tooltip. Completed results remain tracked by the backend, with a completion toast instead of a Last run section.

The extension header exposes persistent Overlay / Docked controls. Overlay is the default. Docked reserves 400px only while open and at viewport widths of at least 900px; narrower windows use overlay and show the effective pressed state. A scoped root attribute and stylesheet apply the reservation without editing employer inline properties or priorities. Closing, minimizing, switching modes and removal release the reservation; resize, root replacement and toolbar recovery re-evaluate it. This is a bounded in-page layout, not a guarantee for every employer fixed-position header or viewport-sized child.

T076 screenshots and bounding-box checks use offline synthetic forms and the actual unpacked MV3 extension. Artifacts are under .tmp/t076-visuals. No live employer layout was verified.
