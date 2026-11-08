# Fast question recognition with explicit reuse boundaries

Recommendation: use a deterministic cascade, then abstain. Existing AutoApply
already has typed DOM metadata, canonical concepts, exact verified memory,
profile/fact revision tracking and bounded classifier caches. Replacing these
with an LLM on each question would add latency and make identical inputs harder
to audit. This upgrade adds small full grammars for ordinary experience Yes/No
questions and keeps controller persistence, option validation and hold checks.

The cascade is: trustworthy control/autocomplete metadata → exact canonical
concept → exact scoped verified answer → narrow equivalent-subject grammar →
ask the user. Caches bind complete descriptors and fact/profile revisions.
Narratives alone may use an explicitly configured grounded writing provider;
unknown factual or legal wording is not sent to a model to invent an answer.

Rules use normalized tokens, complete phrase matching and curated aliases.
For example “Do you have experience with Git?” and “Experience using Git version
control” refer to the same ordinary boolean. A user-selected
Yes or No can be reused when options remain exactly Yes/No and the profile
revision and rule version remain current. Changed answer memory invalidates
the resolver through its existing database revision trigger.

Broader example-based prompts ("version control tools such as Git") and combined
requirements ("SQL and database management systems") have distinct subject keys.
A Yes to a broader claim does not prove a narrower one, and a No to a conjunction
does not identify which part is false. Those answers never transfer to plain
Git/SQL questions. Conflicting or differently mapped field metadata also rejects
keyword memory before it can override the existing mapper's decision.

“Three years of Git experience”, “Git and Python”, negated questions, certified
credentials, proficiency levels, production experience, legal statements and
personal interests do not match that grammar. A keyword such as Python cannot
alone authorize answering a mixed qualification. Explicit REQUIRE_USER and
DO_NOT_ANSWER policies always override reusable grammar memory. Existing broad
experience policy records are preserved; this upgrade does not widen their scope.

spaCy's [rule matching documentation](https://spacy.io/usage/rule-based-matching)
describes matching structured token patterns with optional validation. For this
small vocabulary, Python's standard library regexes and bounded caches supply
the needed behavior without loading an NLP model. This is an engineering
inference from the documented technique, not a measured spaCy comparison.
Embeddings or fuzzy search could rank suggestions for review in a future
version; they should not grant automatic answer authority.

Discord's [interaction protocol](https://docs.discord.com/developers/interactions/receiving-and-responding)
requires a prompt initial response and supports deferred completion. Answer
menus now defer before controller work; owner authorization and disabled
mentions remain in place. Routine command acknowledgements are removed in semi-auto mode. One
batch summary and deduplicated unresolved-question cards replace repeated
generic input/failure notices in semi-auto mode. The durable outbox preserves
undelivered messages, but Discord DMs do not provide an end-to-end exactly-once
delivery transaction: a disconnect after send but before local acknowledgement
can still duplicate a message.

Playwright [pages](https://playwright.dev/python/docs/pages) support multiple
tabs in one context. This upgrade retains up to five while keeping one engine
writer, which avoids five concurrent DOM interactions, account session races
and simultaneous question discovery. Five retained tabs is a convenience for
human review; it does not multiply the filling concurrency.

Speed claims are deliberately bounded: typed recognition performs no model
request and repeated compatible descriptors use existing caches. End-to-end
time remains dominated by employer page loads, uploads, validation and human
review. No live ATS throughput or accuracy improvement is asserted from local
fixtures alone.
