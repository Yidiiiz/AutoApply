# Offered-choice reliability (T072)

Finite dropdowns collect the actual offered labels before answer resolution. Searchable demographic/status menus are finite choices too; they never receive a canonical profile string as a search query. Observations are reused during a pass and invalidated by page identity, URL, label or input/change revision. Activation rechecks the current offered labels and the original control state. A click alone is insufficient: the exact displayed choice and any hidden submission value must remain committed. Native select/radio/checkbox choices also undergo retention checks.

The sanitized captured Greenhouse labels are covered by the real resolver and unpacked MV3 fixture tests:

| Question | Explicit fact | Offered label selected |
| --- | --- | --- |
| Veteran Status | Not a veteran | I am not a protected veteran |
| Gender | Not declared | Decline To Self Identify |
| U.S. WORK AUTHORIZATION | `work_authorization.us_authorized: true` | Yes |
| Full captured EXPORT CONTROLS status heading | Uncontradicted `citizenship.us_citizen: true` | A United States citizen or national |

The export rule recognizes the captured heading and offered status, never a general export agreement or security-clearance question. Authorization alone does not establish citizenship. Decline variants are a closed semantic family; arbitrary parenthetical qualifications, opposite booleans and veteran prefixes cannot establish equivalence. Unknown, empty, contradictory and ambiguous choices remain pending.

## Location preferences

`locations.preferred_states` is an optional, ordered tag list editable through Dashboard Answers and `PUT /api/answers`. Full U.S. state names normalize to postal abbreviations; DC is supported. Empty means no state preference. Validation rejects foreign/unknown states, malformed values and lists over 51 entries. `locations.ordered_preferences` is also editable as up to 100 `City, State` tags. Editing states does not replace the existing city list. This task does not read or change the real applicant profile.

With an explicit `preferred_states: [CA]`, saved California cities retain their saved order (for example San Francisco, then San Jose). Other offered California cities precede saved cities outside California. Remaining saved cities retain their order (for example New York, Seattle, Austin). No California preference is assumed for other profiles. An unranked city outside preferred states is never an arbitrary fallback. Multiple unranked cities tied within one state remain ambiguous.

?Top? and ?primary? location preference mean rank one; first/second/third and ordinal forms keep their ranks. Ranking uses distinct offered city identities. A trailing `(HQ)` or `(headquarters)` annotation is incidental, so the captured `Costa Mesa, CA (HQ)` is returned verbatim when California is explicitly preferred. Foreign country/province suffixes and geographic parentheticals are not stripped. `Toronto, Ontario, CA` and `Toronto, CA, Canada` cannot mean California.

## Preserved search and education behavior

Remote school/skill vocabulary searches retain the profile-first path and bounded full/prefix queries. Exact known-school matches still win among campuses; ambiguous generic campus prefixes do not select the first result. Failed searches clear search text and may expose the unrelated offered first page as truthful pending metadata without committing it.

The existing caller-authorized degree correction compares selection and displayed state with their initial values, permitting the unchanged original selection to be replaced. Occupied controls still require explicit caller force; force cannot bypass page/label/option changes or a new edit during activation. Controlled bachelor, month, phone-type aliases and highest language proficiency remain supported.

Legacy tests now expect finite option collection rather than zero collection time, truthful post-failure search metadata, and abstention for a phone-type select with no options. Their filled values, hidden values, dates, uploads, scan timing, search cleanup, pending state and no-submit assertions remain intact.

## Verification and remaining limits

Focused checks use synthetic profiles, local fixture HTTP and the actual unpacked MV3 extension. They inspect offered options in resolver requests, exact clicked labels, retained form/hidden values, pending reasons and traces, with no submission or run advancement. They also exercise forced replacement, changed options, page/label changes, disabled/user-edited controls, duplicate choices, uncommitted activation and observation reuse.

Final focused validation passed across selected runs: 45 dropdown/editor cases, 32 choice-reliability cases, six existing education/Greenhouse/search cases, and four phone API cases (87 total). JavaScript syntax checks and `git diff --check` passed. The full extension suite was not run. Final rescans can clear pre-fill option metadata after input/change invalidation; tests therefore inspect the actual resolver request and trace rather than assuming observations persist in the final scan. Nested section/autofill traces can share an event, so the bounded-opening assertion counts distinct timestamps.

These checks establish fixture behavior, not a live-employer pass. The current live employer DOM and its deployed extension version were not inspected. Without explicit California preference, the captured Costa Mesa/Ashville menu correctly remains blank because neither city overlaps the saved city list. With explicit California preference the fixture commits the exact Costa Mesa label. Missing citizenship/authorization facts, absent options and unresolved ambiguity remain legitimate blank reasons. Separate SmartRecruiters shadow capture work is outside T072.
