# Policy knowledge base

How Acme Gear's published policies reach the agent.

> Last verified against: milestone 6 (complete).

## How it fits together

```
backend/policies/*.md  ──chunk──►  passages  ──embed──►  policy_chunks table
                                                              │
                          customer question ──embed──►  cosine search  ──►  search_policy
```

The markdown files are the source of truth. The table is derived from them and can
be deleted and rebuilt at any time ([D-032](../decisions.md#d-032-policy-documents-are-markdown-files-and-the-index-is-derived)).

## The documents

| File | Covers |
|---|---|
| `returns.md` | Return windows, item condition, what cannot be returned, who pays return postage |
| `refunds.md` | When and how much is refunded, partial refunds, method and timing, approval limits |
| `shipping.md` | Delivery times, tracking, delayed and lost parcels, wrong deliveries |
| `warranty.md` | What is covered, what is not, how a claim is handled, repair versus replace |

They are written the way a real shop would write them, including the awkward cases:
gold tier exceptions, who pays return postage, what happens when tracking says
delivered but nothing arrived. Those edge cases are what the refund policy engine
and the eval suite will lean on later.

## Commands

```bash
uv run deskpilot policy status    # what is indexed, and what needs rebuilding
uv run deskpilot policy index     # index anything new or changed
uv run deskpilot policy index --force
uv run deskpilot policy search "how long do I have to return a tent?"
```

`search` runs exactly the query the agent's tool runs, but prints distances instead
of handing passages to a model. It is the fastest way to tell a retrieval problem
from a prompting one, and how you choose `MAX_DISTANCE` for your embedding model.

`index` skips unchanged documents, so editing one file re-embeds that file only.
`--force` re-embeds everything, which is what you want after changing the chunking
rules, since the file digest would not have changed.

Rebuild the index whenever you edit a policy file, change the embedding model, or
change the prefixes. `status` will tell you when it is needed.

## Chunking

Each markdown section becomes one passage, and every passage repeats its heading
path ([D-033](../decisions.md#d-033-passages-are-split-at-headings-not-at-a-fixed-size)):

```
Returns and refunds > Return window

Customers may return most items within 30 days of delivery for a full refund...
```

Two reasons for the repetition. Retrieved on its own, "within 30 days of delivery"
does not say what it is about, and the model has to guess. And the heading words go
into the embedding, which measurably improves matching.

A section longer than `MAX_CHUNK_CHARS` is split further, but only between
paragraphs. A single paragraph longer than the limit is left over-long, because half
a sentence embeds badly and reads worse.

## Task prefixes

`nomic-embed-text` is trained to be told what a piece of text is for. Documents are
embedded as `search_document: ...` and questions as `search_query: ...`.

This matters more than it looks. Omitting the prefixes is not an error. Nothing
fails, no warning appears, and every result is just slightly worse than it should
be — the hardest kind of bug to notice. `PrefixedEmbeddings` in `llm.py` applies
them, and both sides are configurable because other models want different prefixes
or none at all.

## Staleness

Each passage stores two things beyond its text:

| Column | Purpose |
|---|---|
| `source_sha256` | Digest of the whole source file, so a changed file is spotted without re-embedding anything to find out |
| `embedding_fingerprint` | `model｜document_prefix｜dimensions`; any change makes existing vectors incomparable |

A document is **current** only when every one of its passages carries the same
digest and fingerprint. A mixture means a half-finished rebuild, which counts as
stale ([D-036](../decisions.md#d-036-a-document-is-current-only-when-all-of-its-passages-agree)).

| State | Meaning |
|---|---|
| `current` | Indexed and up to date |
| `missing` | On disk, never indexed |
| `stale` | The file or the embedding settings changed since indexing |
| `orphaned` | Indexed, but the file is gone from disk; the next `index` removes it |

## Search

`search_policy` embeds the question, finds the nearest passages by cosine distance,
and drops anything further away than `MAX_DISTANCE`.

That threshold is the important part. A vector search always returns its k nearest
neighbours, relevant or not. Without a cut-off, asking about something the policy
does not cover hands the model the closest passage anyway and invites it to answer
from it. Instead the tool says the policy does not cover the question, and the
prompt tells the model to offer a colleague rather than reason its way to an answer
([D-035](../decisions.md#d-035-search-results-below-a-distance-threshold-are-discarded)).

### Tuning

| Setting | Effect |
|---|---|
| `DESKPILOT_POLICY_SEARCH__TOP_K` | How many passages the model sees. More context, more tokens, more chance of it answering from the wrong one. |
| `DESKPILOT_POLICY_SEARCH__MAX_DISTANCE` | Lower is stricter. Too low and real questions get "not covered"; too high and unrelated passages leak through. |
| `DESKPILOT_POLICY_SEARCH__MAX_CHUNK_CHARS` | Larger passages carry more context but match less precisely. |

Cosine distance runs from 0 (identical) to 2 (opposite). With `nomic-embed-text` a
good match usually lands well below 0.5 and an unrelated passage above 0.6, which is
where the default came from. Check the real numbers on your own machine:

```bash
uv run pytest -m integration -k retrieval -v
```

## Tests

| Level | Covers |
|---|---|
| `tests/unit/test_chunking.py` | Heading paths, paragraph splitting, the digest |
| `tests/unit/test_embeddings.py` | That documents and questions get different prefixes |
| `tests/unit/test_tools_policy.py` | How passages are rendered for the model |
| `tests/integration/test_policy_index.py` | Indexing, staleness, orphan cleanup, and retrieval quality |

The retrieval-quality tests are the interesting ones. They ask questions in wording
that deliberately does not appear in the documents — "how long do I have to send
something back?" against a section titled "Return window" — and assert the right
section comes back. That is what embeddings are for, and it is the part that would
silently regress if the prefixes, the chunking, or the model changed.

If one fails, it is a finding rather than a broken test. Look at the distances the
search returned, and decide whether the fix is the threshold, the chunking, or the
wording of the policy itself.
