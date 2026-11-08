# Eligibility parser correction — 2026-09-29

This is an offline development repair following Operational Qualification 1B.
Application 4615 was not opened, resumed, answered, or modified. Question 206 was
not read from or written to the live database. No employer application was opened,
no real form was filled, and no applicant facts were changed. Validation uses
temporary profiles/databases, saved public evidence, local browser fixtures, and
the existing offline network/provider guards.

## Root cause

`jobs.eligibility()` recognized very few section endings. After `Requirements`,
benefits and later pay, EEO, privacy and attestation prose remained in its hard
requirement scope. A keyword list then treated unrelated occurrences of
“experience” and “knowledge” as qualifications. The same list missed requirements
without those keywords, including comfort working with APIs. Inline preference
handling could also discard a required clause or fail to distinguish “preferably”.

The adapter's `get_text("\n")` placed a boundary at every HTML text node, including
inline formatting. That could separate a qualification from its “preferred”
modifier. The standing matcher understood written/verbal communication but did
not recognize the shared noun in “Excellent written, verbal and communication
skills”; its comma-first composition also split otherwise complete clauses.

## Parser and boundaries

`applications.description_text()` now keeps block/heading/list/form-label
boundaries and preserves text across inline formatting. Both ordinary HTML and
JobPosting JSON-LD descriptions use it. Scripts, styles and HTML comments are
excluded. Formatting newlines inside a paragraph do not split qualifications.

`requirements.extract_requirements()` provides one required/preferred extractor
for both live eligibility and candidate preflight. It normalizes heading case,
punctuation, hyphens, whitespace and ampersands. Whole-heading grammars cover
Benefits, Compensation, Pay Range, Salary, What We Offer, Perks, Equal Opportunity,
EEO, Privacy, Application Information/Questions, Voluntary Self-Identification,
Job Alerts and Talent Community, including common prefixes/suffixes. Later
required or preferred headings reopen the corresponding section. Same-line
headings followed by a colon are supported.

Ordinary qualifications such as “Experience with privacy software” and
“Knowledge of compensation systems” do not end a section. Recognizable
promotional/pay/attestation boilerplate is excluded even if a heading is missing.
Outside qualification context, generic skill words do not create requirements.
Explicit gates such as mandatory residence, citizenship, authorization and
sponsorship restrictions remain discoverable after section boundaries. Ordinary
form questions such as “Will you require sponsorship?” are left to form resolution.

Preferred, preferred qualifications/skills, nice-to-have and bonus headings are
nonblocking, even when their subordinate text says “required”. Inline preferences
remain nonblocking. A required degree followed by “preferably in …” keeps its
degree/standing requirement and records the major preference separately.
Semicolon-separated mandatory/preferred clauses and “required, … preferred”
clauses remain distinct.

Every extracted mandatory clause must have deterministic support or remain
unknown. Complete-clause checks prevent a verified fragment from satisfying an
unknown added qualifier. Existing citizenship, authorization, sponsorship, GPA,
degree and graduation checks remain conservative. Missing profile facts stay
unknown. The existing nullable eligibility result is preserved; its read-only
`status` property names this state `UNKNOWN_REQUIRES_USER`.

## Standing and technical evidence

Communication now accepts narrowly enumerated written/verbal communication
wording. Composition tries complete clauses before splitting conjunctions or
commas; every component must independently match a verified standing assertion.
German fluency, Python proficiency, clearance, professional-experience thresholds,
negations and other unsupported additions still prevent a match.

Verified independent work is separated from the unsupported limited-direction,
pace and motivation clause. Familiarity with Word/Excel/PowerPoint does not prove
Windows/Microsoft Office proficiency.

Basic API/scripting/system-integration comfort has a closed evidence rule: an
explicit API skill plus explicit scripting and system-integration skills, or
source-backed positive implementation excerpts establishing both scripting and
server/client integration. The saved profile's verified Python polling and
server/client implementation evidence is represented by sanitized synthetic
excerpts in the test. The rule reads no application-specific ID, project name,
unverified project prose or AI-generated inference. Removing the API skill or
either required evidence/source component leaves the requirement unknown.
Stronger expertise, language, years and added-qualification wording is not
satisfied by this basic-comfort rule.

## Availability

Explicit full-program availability requirements and a stated program schedule
produce a duration confirmation. A start date alone cannot satisfy it. This
repair does not synthesize a day from a month range or infer an end date.

The saved public posting says: “The program takes place from June – August 2027
(10-week program).” It does **not** give exact calendar dates or separately say
“full-duration availability required”. The parser conservatively requests
confirmation of participation throughout that stated schedule; the fixture does
not invent a stronger quotation or exact start/end days. An explicit user
confirmation is scoped to the complete current listing and profile revision.

## Sanitized 4615 regression

`tests/fixtures/eligibility-4615-sanitized.html` contains relevant public posting
excerpts from the locally saved `.tmp/qualification-1b-listing.html`. The source
HTML SHA-256 recorded by Qualification 1B is
`14418638f960d96df0088c025be25e449e520670543f2f9bb2446fd38aa1605f`.
Employer names are replaced; role responsibilities, external links, scripts,
widgets, tracking, hidden fields and applicant controls/answers are omitted.
Qualification wording and the public residence constraint are retained. A few
administrative prompts are shortened. The source was not fetched again.

The fixture classifies benefits, compensation, nondiscrimination, privacy,
attestation, alerts, community and upload instructions as nonrequirements.
Programming/Python and the CS/software/data-science major are preferred only.
Student standing/degree, communication, AI/LLM interest, API/scripting/integration,
Windows/Office, independent work with limited direction/pace, U.S. residence and
program attendance remain actual eligibility considerations.

With the verified synthetic equivalent profile, the exact remaining uncertainties
are:

1. **Availability:** Confirm full-program availability for “The program takes
   place from June – August 2027 (10-week program).”
2. **AI/LLM:** Familiarity with AI and large language model concepts, and
   demonstrated curiosity for applying them to real problems — coursework,
   personal projects, and hackathons all count.
3. **Windows/Office:** Proficiency in Windows and Microsoft Office.
4. **Work environment:** Ability to work with limited direction in a fast-paced
   environment; must be a high-energy, motivated self-starter.

No answer to any of these four is supplied. Removing technical evidence adds an
API/scripting/integration uncertainty; removing residence, enrollment or standing
evidence likewise keeps the corresponding fact unresolved.

## Focused questions and lifecycle compatibility

`eligibility_questions` creates one required Yes/No question for each unresolved
clause using the existing question store and manual-input flow. Stable keys and
application scopes include a digest of the listing and requirement. Confirmations
must be explicit, verified `user_confirmed` answers with matching question
signature and canonical profile revision. Listing edits or profile changes
invalidate their reuse, including ordinary answer-memory reuse.

One Yes does not resolve other requirements or set `eligibility_override`. A No
uses the existing INELIGIBLE transition. Pending parser-owned questions that are
no longer unresolved are marked SKIPPED; legacy and employer-form questions are
untouched. The engine's initial and final eligibility gates consult the same
confirmed assessment. Synthetic confirmation fields always require user input.
No schema migration, Phase 5 resolver redesign, Phase 7 lifecycle redesign or
live-question migration is included.

The local browser regression traverses the ordinary fill-only Engine path and
checks four pending questions, INPUT_REQUIRED/manual hold, no filled fields and
no submission intent or confirmation.

## Validation

Existing behavioral tests and assertions are unchanged.

| Validation | Result |
| --- | --- |
| Broad focused regression selection | 440 passed, 258.12 seconds |
| Parser/standing/core/preflight selection after final requirement refinements | 242 passed, 27.40 seconds |
| Supplemental accepted safety-audit contracts | 12 passed, 7.29 seconds |
| Complete final offline suite | 1,100 passed, 778.71 seconds; zero failures or skips |
| Exact accepted baseline comparison | All 1,023 accepted test IDs retained and passing; 77 new regression cases |

The final complete run also covers the last HTML-comment exclusion refinement.
Its focused regression selection contains 447 passing cases. Comparison against
`docs/final-acceptance-2026-09-27/test-results.json` found no missing or nonpassing
baseline cases. All 100 recorded source/test/fixture hashes remained unchanged
during validation. The comparison results are retained in
`.tmp/eligibility-validation-results.json`; the JUnit suite duration is 778.42
seconds, while the pytest terminal summary reports 778.71 seconds.

The focused and complete runs use:

```powershell
$env:PYTHONPATH = "$PWD\docs\phase5-2026-09-25;$PWD\docs\phase8-2026-09-27"
.venv\Scripts\python.exe -m pytest -q -p offline_validation -p offline_providers
```

The broad focused selection was:

```text
tests/test_eligibility_parser.py tests/test_eligibility_repair.py
tests/test_standing.py tests/test_llnl_standing.py tests/test_experience_distinction.py
tests/test_core.py tests/test_phase5_candidates.py tests/test_phase5_resolution.py
tests/test_phase7_lifecycle.py tests/test_phase7_fill.py tests/test_fill_batch.py
tests/test_controlled_resume.py tests/test_browser.py
```

The final full invocation adds
`--basetemp=.tmp/eligibility-release --junitxml=.tmp/eligibility-release.xml`,
with stdout/stderr retained in `.tmp/eligibility-release.log`. It includes every
test under `tests/` without skips or exclusions. The source/test/fixture hashes
are recorded in `.tmp/eligibility-validation-tree.json` for consistency checks.

Early validation caught the existing ampersand heading variant, a form-question
versus eligibility-constraint distinction, and missing UTF-8 metadata in the new
browser fixture. Those were corrected without weakening prior assertions. The
first exploratory full run was interrupted after diagnosing the form-question
regression. Subsequent in-progress full runs were stopped when source refinements
required a fresh run. None is represented as a passing complete run.

Development stops after offline implementation and validation. This report does
not authorize resuming application 4615 or answering question 206.
