# Native fields in open shadow roots

T073 adds the shared isolated-world `native-roots.js` traversal before credentials
and content, including toolbar reinjection. It visits at most 10,000 elements,
128 roots, and 32 nested open-root boundaries, excluding AutoApply sidebar and
Ask AI subtrees. Closed roots remain unobservable. A truncated search fails
closed for account privacy rather than assuming no authentication exists.

Native inputs, textareas, selects, radios, checkboxes and files use the existing
fill path. ID references resolve within their own tree. Choice groups cannot
merge across roots or unrelated containers with null forms. Native setters and
composed input/change events update synthetic host models. Occupied fields and
page/label guards remain authoritative; replacement controls are reacquired only
within the original connected root. A new root or page requires another scan.
Custom shadow roles are structural evidence, not permission to invent behavior.

The public label LinkedIn has a narrow extension API canonicalization to avoid
classifying its capitalization as a machine identifier. Only an explicit saved
link is used. Unknown social links and messages receive no invented answer.
File controls without an explicit resume/CV/cover purpose remain pending,
including the two identically labeled observed dropzones.

Visible shadow password controls activate account privacy across ordinary answers,
chat, learning, traces and structural captures. Unsupported shadow login stays
manual; the signed same-origin credential release path is unchanged. Manual
authentication values and identities remain private if the password disappears.
Visibility checks composed ancestors, including shadow hosts and slots, for
hidden/inert/aria-hidden state, display, visibility and zero opacity. Hidden
password templates remain private without blocking normal name/contact fields;
visible read-only passwords activate privacy but cannot be filled.

Read-only analysis can project an explicit saved fact as a text candidate to
diagnose an offered-choice mismatch. This probe returns only a diagnostic status;
actual answers still require a matching offered option, and missing facts and
scoped/legal unknowns continue to abstain without a writing provider.

Diagnostics retain bounded root IDs, parent root relationships, field membership
and composed host ancestry. The API validates bounds, parent ordering, duplicate
IDs and field membership. Worker evidence uses root-scoped opaque node aliases,
public selector tokens (including spl-input, spl-textarea and spl-dropzone), and
root trees, without values, selected answers or arbitrary selector prose.

`tests/test_shadow_fields.py` runs the unpacked MV3 extension against a real
loopback backend and synthetic explicit profile. The fixture preserves the
reported Oct 25 native IDs, labels and root membership, and tests host event
state, ambiguous uploads, duplicate IDs/groups, late inputs after icon roots,
visibility, replacement guards, privacy and capture-to-queue evidence. These
fixtures verify standard native open-shadow behavior. They do not establish
live SmartRecruiters behavior or reproduce undocumented widget internals.
A fresh user trace and independent review are needed for live completion.
