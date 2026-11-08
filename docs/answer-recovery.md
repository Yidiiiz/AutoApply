# Autonomous answer recovery

Autofill uses the existing answer resolver and document-bound repair capability
to recover ordinary unfamiliar questions without a separate Analyze action.
Automatic recovery uses the same capability when automatic analysis is enabled.
It fills grounded answers, checks that they remain selected, and distinguishes
missing applicant facts from scanner or widget defects. Recovery does not submit
applications or turn on automatic submission.

## Executed five-step plan

1. **Reconcile the controls independently.** The scanner and passive inventory
   recover a nearby question for native radio and checkbox groups rather than
   accepting a broad “Application questions” heading or the option “Yes” as the
   question. Transparent native choices with a visible associated label can be
   clicked even when the native radio/checkbox has a `readonly` attribute; disabled,
   hidden, inert and explicitly ARIA-readonly controls remain protected. Repair
   binds the document, URL, form/heading nodes and application step. Async menu or
   hidden-mirror mounts do not cancel unrelated fields. Trusted edits protect the
   edited controls, while independent untouched controls can continue. Existing
   `labelAtScan`, choice-question, writable-control and current-option guards
   remain at the write boundary. Closing the sidebar or cancelling revokes repair.

2. **Match saved facts, then retrieve bounded grounding.** The first answer pass
   uses `allow_ai:false`. Unknown ordinary wording can use a small whitelist of
   relevant identity, contact, education, skills, languages and public-link facts.
   At most six ordinary facts, 2,000 serialized characters per fact and 6,000 in
   total are retrieved. Existing specific mappings retain their own limits.
   Relevant verified `USER_PROVIDED` memory is limited to three answers, each
   bounded to 1,500 serialized characters, with matching scope, profile revision,
   stored signatures, field kind and compatible options. Generated answers never
   become verified facts merely by being filled. Provider input contains bounded
   question metadata and fact IDs, not the full profile, credentials or HTML.
   An eligible ordinary question with no relevant fact can receive an explicit
   grounded abstention. Legal, medical, account, employment, relationship,
   government, financial-interest and consent assertions require their existing
   explicit mapping/preference or verified scoped answer. In particular,
   government/auditor history no longer defaults to “No” when the fact is absent.
   `REQUIRE_USER`, `DO_NOT_ANSWER`, optional demographic decline, rejected labels,
   honeypots and credential exclusions still apply. Writing uses its existing
   grounded drafting path.

3. **Observe exact offers, write and verify.** Eligible unresolved choices are
   probed before reasoning, including choices with no initial offers. An empty
   previous observation can be reopened on a later bounded attempt. Each recovery
   pass makes one fast batch and at most one grounded provider batch, capped at
   20 questions with an eight-second provider timeout. Evidence IDs, exact option
   spelling, type, cardinality, length and current fact revision are validated.
   A saved decline preference matches a complete offered decline phrase such as
   “Do not wish to identify,” with or without “self.” Substantive identity denials
   and arbitrary parenthetical qualifiers are not equivalent. Required checkbox
   groups receive a list containing the exact offered decline; optional
   demographics stay blank when no decline is offered. Multiple equivalent
   offers remain ambiguous. Native option buttons with
   `type="button"` are allowed, including an `aria-controls` popup appended to
   the body; links and submit-capable options are rejected.

   Combobox verification compares the selected offered label over a stable
   interval. A popup's hidden search/mirror input is excluded from backing-value
   checks. Actual backing inputs still have to retain a value. Closed finite
   input comboboxes can verify their exact selected input value; remote search
   text is not promoted to a selected choice. Accessible selection text also
   participates in exact verification. A different selection, an empty backing
   value, or a delayed reversion fails verification. Completion requires the
   fill routine's verified result plus retained equality, never mere occupancy.
   Failed writes stay unresolved in repair results, subsequent scans and passive
   diagnostics, so an occupied unverified widget cannot disappear from both
   pending and completed lists. Successful verified answers retain their source
   and appear in Answers.

4. **Retry transient conditions within bounds.** The automatic scheduler waits
   16 seconds between attempts, outliving the backend's 15-second provider-failure
   cache. It permits at most three recovery attempts for a stable page/fact
   revision, coalesces pending signals and does not repeat settled stable work.
   Transport calls inside an attempt retain their existing three-call bound.
   Provider unavailability, stale facts, missing offers and loading can rearm a
   retry; a stable missing fact does not continually call the provider. Scheduler
   signature, fact revision, attempt count, deadline and settled state live in
   extension session storage. On MV3 restart, pending deadlines rearm timers;
   subsequent form signals also respect the saved deadline/count. A browser
   suspension can delay a timer; this is not an exact wall-clock alarm.

   Grounded proposals and abstentions have a five-minute per-question cache bound
   to profile/answer revision, scoped descriptor, exact label, exact offers and
   supplied facts. Rebinding a control key or adding a late-options field to the
   batch does not repeat a stable missing-fact request. Changing exact offered
   spelling invalidates reuse. Identical concurrent batches share provider work.
   Navigation, document replacement, cancellation, sidebar closure, user edits,
   disabled analysis and changed fact revisions retain their existing guards.
   Matching, reasoning, retrying and worker-queued statuses replace stale input
   prompts while recovery is active.

5. **Queue genuine defects through the existing queue and verify behavior.**
   Unsupported controls, scanner omissions and known-answer verification failures
   produce sanitized structural evidence for the existing repair queue. Missing
   facts, intentional skips, user-edited controls and access/loading/account
   states are distinct from engineering defects. The queue retains its existing
   deduplication, pause/enable, capacity, workspace ownership, budget and supervisor
   review rules. Evidence strips answer values and arbitrary page prose and uses
   structural aliases. A control can have more than one legitimate cause: an
   unsupported range control omitted by the scanner records both
   `omitted_by_scanner` and `unsupported_widget`.

## Exact choices and missing facts

The synthetic observed-form fixture retains the public choice wording from the
sanitized PayPal/Eightfold evidence. Its race checklist includes “Do not wish to
identify”; a saved `race=decline` selects only that checkbox, without inferring a
race or asking a provider. Discovery-source offers such as Glassdoor, HireEZ,
Indeed, Moseeker, RiseSmart and Zhiphin require a matching saved answer.

Distinct privacy-statement and nondisclosure consent options do not authorize a
blanket “Yes.” Board/business interests and relationship details need actual
facts; absence never establishes “No.” A truncated diagnostic option prefix is
not treated as the full offered set. Finite input comboboxes with owned body
popups are supported when their exact selection passes retention verification.

Verified values must appear in the completed list and filled count with their
answer source. An occupied widget that fails verification remains pending.
Delayed offers may trigger another bounded probe while stable per-question
results prevent duplicate provider work.

## Validation and limits

Offline tests use synthetic facts, in-process backend calls, stub providers and
Node fixtures executing shipped code. They cover exact decline matching and
negative counterexamples, evidence validation, cache invalidation, independent
field recovery, selection retention, cancellation and bounded retries. Queue
tests check sanitized evidence, deduplication and missing-fact separation.

Run the focused offline checks from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_answer_recovery.py tests/test_t085_answer_repair.py tests/test_question_keywords.py tests/test_question_groups.py tests/test_choice_reliability.py -m "not browser" -q -p no:cacheprovider
git diff --check
```

The actual unpacked-MV3 gate uses fixture-only HTTP, synthetic facts and a stub
provider. It checks DOM selections, completed values and counts, answer sources,
single-call caching, trusted edits, delayed reversions, label changes, sanitized
queue causes and cancellation without submission:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extension_recovery.py -m browser -q
```

Fixture verification does not establish the live PayPal outcome. The live
applicant form was not connected for this release verification. Custom or
unsupported widgets and missing applicant facts can still require manual input.
