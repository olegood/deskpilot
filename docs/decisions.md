# Decision log

Significant design decisions, newest last. To reverse a decision, add a new entry
that references the old one rather than editing it.

---

### D-001: Domain is a support agent for a fictional shop

**Date:** 2026-09-11

**Decision.** Deskpilot handles customer support tickets for Acme Gear, a fictional e-commerce shop.

**Why.** The risks are real rather than contrived: ticket text comes from strangers (prompt injection), refunds cost money (human-in-the-loop), and carrier APIs are slow and flaky (timeouts). Everything can be simulated locally, and outcomes like "refunded the correct amount" are measurable, which makes evals meaningful.

---

### D-002: Single tenant

**Date:** 2026-09-11

**Decision.** One shop only. No `tenant_id` column anywhere, not even reserved.

**Consequences.** Isolation is between customers of the same shop, enforced by ownership and ABAC. Deskpilot has one ShipTrack key pair and one Paywisp merchant account. Adding multi-tenancy later would be a real project: a schema migration plus a new attribute on every ABAC policy.

---

### D-003: Ollama runs natively on the host, not in Docker

**Date:** 2026-09-11

**Decision.** Ollama runs on macOS via `scripts/ollama-serve.sh`. Docker Compose runs everything else.

**Why.** Docker on macOS runs containers in a Linux VM with no access to the Apple GPU. Ollama in a container falls back to the CPU and is many times slower.

**Consequences.** Settings live in a versioned script instead of the Ollama desktop app. Ollama binds to loopback only, because it has no authentication. A containerized backend would reach it at `host.docker.internal:11434`.

---

### D-004: Separate model settings per role; embeddings have their own provider

**Date:** 2026-09-11

**Decision.** The agent, injection guard, and eval judge each have their own model settings and switch provider independently. Embeddings are configured separately and always use Ollama.

**Why.** The roles have different needs: the guard wants short, fast answers, and the judge should come from a different model family than the agent to reduce self-preference bias. Anthropic has no embeddings API, so embeddings can't follow the LLM switch.

---

### D-005: Explicit model factory instead of `init_chat_model`

**Date:** 2026-09-11

**Decision.** The model factory constructs `ChatOllama` and `ChatAnthropic` directly.

**Why.** Some settings exist for only one provider, such as Ollama's `num_ctx`. Explicit construction makes these differences visible and type-checked instead of passing loosely typed keyword arguments.

---

### D-006: PostgreSQL instead of SQLite

**Date:** 2026-09-11

**Decision.** PostgreSQL for application data and LangGraph checkpoints, via SQLAlchemy async, Alembic, and `AsyncPostgresSaver`.

**Why.** The web app has concurrent users and background agent runs. SQLite allows only one writer at a time.

---

### D-007: `bcrypt` and PyJWT directly

**Date:** 2026-09-11

**Decision.** Hash passwords with the `bcrypt` library (cost factor 12) and handle tokens with PyJWT.

**Why.** passlib is unmaintained and breaks with bcrypt 4.x. python-jose is poorly maintained. Bcrypt silently truncates input at 72 bytes, so longer passwords are rejected explicitly instead of risking collisions.

---

### D-008: In-house ABAC engine

**Date:** 2026-09-11

**Decision.** ABAC policies are typed Python functions, deny by default, with every decision written to an audit log.

**Why.** Building it teaches more than configuring a library, and pure functions are easy to test exhaustively. Cedar is in the backlog as an upgrade path.

---

### D-009: Two external vendors with different protocols and authentication

**Date:** 2026-09-11

**Decision.** ShipTrack (shipping) is a REST API authenticated with HMAC request signing, and sends signed webhooks back. Paywisp (payments) is an MCP server protected by OAuth: client credentials for the agent's read-only access, authorization code with PKCE for each reviewer's delegated write access.

**Why.** Together they cover the main ways a production agent authenticates to the outside world. Read-only scopes for the autonomous agent mean a hijacked agent cannot move money.

---

### D-010: Hand-built Paywisp authorization server with Authlib

**Date:** 2026-09-11

**Decision.** Build a small OAuth 2.1 authorization server with Authlib instead of using Keycloak.

**Why.** It makes every step of OAuth visible, and the security stakes of a fake vendor are low.

**Consequences.** Deskpilot discovers all endpoints through metadata and the MCP server validates tokens via JWKS, so swapping in Keycloak later is a configuration change. Keycloak is in the backlog.

---

### D-011: The payment vendor is named Paywisp

**Date:** 2026-09-11

**Decision.** The fictional payment vendor is called Paywisp.

**Why.** The previous working name matched a real payment processor. In a public repository, an obviously invented name avoids confusion and trademark concerns.

---

### D-012: The MCP client lives in Deskpilot's backend

**Date:** 2026-09-11

**Decision.** Deskpilot connects to MCP servers itself, using `langchain-mcp-adapters` and the MCP Python SDK, even after switching to Anthropic.

**Why.** It keeps the integration provider-agnostic, and vendor credentials never leave Deskpilot.

---

### D-013: Evals run in two phases

**Date:** 2026-09-11

**Decision.** First the agent processes every eval ticket and saves transcripts. Then the judge model scores them.

**Why.** With 48 GB of unified memory, the agent model and judge model can't be loaded at the same time. As a bonus, old transcripts can be re-scored with a new judge or rubric without rerunning the agent.

---

### D-014: Each service is its own uv project

**Date:** 2026-09-11

**Decision.** The backend and each fake vendor are separate uv projects with their own lockfiles, not members of one uv workspace.

**Why.** The vendors simulate separate companies. Separate projects mean no shared dependencies, code, or secrets, which keeps the integration honest.

---

### D-015: Thinking mode is always explicit and off by default

**Date:** 2026-09-11

**Decision.** `reasoning` is a boolean on every model role, defaulting to `false`. There is no "use the model's default" option. Enabling it for an Anthropic role is rejected until the Anthropic switch milestone.

**Why.** When a thinking model runs through langchain-ollama without an explicit setting, its raw `<think>` blocks can end up in the response content. In Deskpilot that content can become a reply to a customer. Explicit settings also make latency and token use predictable.

**Consequences.** Whether the agent benefits from thinking is decided by evals in milestone 12. Anthropic's thinking uses a different mechanism (token budgets, temperature constraints) and gets designed with the Anthropic switch.

---

### D-016: psycopg 3 is the only PostgreSQL driver

**Date:** 2026-09-11

**Decision.** SQLAlchemy connects with `postgresql+psycopg` (psycopg 3, async), not asyncpg.

**Why.** LangGraph's `AsyncPostgresSaver` is built on psycopg 3. Using the same driver for application data and checkpoints means one set of connection behaviors, errors, and settings to understand.

---

### D-017: Schema conventions

**Date:** 2026-09-11

**Decision.** Money is integer cents. Timestamps are timezone-aware. Enums are stored as strings with CHECK constraints, not native PostgreSQL enums. Constraint names come from a naming convention. Relationships use `lazy="raise"`. Orders have an internal integer `id` and a public `number`.

**Why.** Floats can't represent money exactly. Native enums need awkward migrations to change. Deterministic constraint names keep migrations reliable. In async SQLAlchemy an implicit lazy load fails at runtime, so `lazy="raise"` turns a subtle bug into an immediate, clear error. Public order numbers keep internal IDs out of conversations with customers and the model.

---

### D-018: Integration tests use a separate database, migrated from scratch

**Date:** 2026-09-11

**Decision.** Integration tests run against `deskpilot_test`, created by a Compose init script. Each test run downgrades it to an empty schema and upgrades it to the latest revision, and `alembic check` verifies that models and migrations match.

**Why.** Tests never destroy development data, every migration is exercised in both directions, and a model change without a migration fails the build.

---

### D-019: No default database password in code

**Date:** 2026-09-11

**Decision.** `DESKPILOT_DATABASE__PASSWORD` has no default. Startup fails with a clear error if it's missing. Compose likewise refuses to start without `POSTGRES_PASSWORD`.

**Why.** A default password in a public repository tends to end up in places it shouldn't. Requiring it costs one line in each `.env` file.

---

### D-020: Identity travels in the graph context, never in state

**Date:** 2026-09-12

**Decision.** Who the agent acts for is carried in `AgentContext`, passed to `graph.ainvoke(..., context=...)`. It is not part of graph state, not part of the message list, and not checkpointed. Tools receive it through LangGraph's `ToolRuntime`. The model supplies only business arguments, such as an order number.

**Why.** Anything in state or messages is text the model reads and could, in principle, be talked into rewriting. Identity that lives outside both cannot be forged by prompt injection, because no sequence of tokens can reach it.

**Consequences.** This is the seam the ABAC milestone extends: the context grows into a full principal with attributes, and every tool checks it. A tool can still choose to put identity in its output; the guarantee is that the model has no other way to obtain it.

---

### D-021: "Not found" and "not yours" give the same answer

**Date:** 2026-09-12

**Decision.** `get_order` returns one message, "No order with that number was found for this customer", both when an order does not exist and when it belongs to somebody else.

**Why.** Different answers would turn the agent into an oracle for which order numbers are real. The ownership check is part of the SQL query, so another customer's row is never loaded into memory at all.

---

### D-022: Untrusted customer text is withheld from the model for now

**Date:** 2026-09-12

**Decision.** `orders.notes`, which customers type at checkout, is deliberately left out of the tool's output.

**Why.** It is attacker-controlled text. It reaches the model only once spotlighting and the injection guard exist in the security milestone, and there is no reason to expose it before then. A unit test asserts it stays out.

---

### D-023: The loop is hand-built; tool plumbing is not

**Date:** 2026-09-12

**Decision.** The ReAct loop, its router, and the step budget are written by hand with `StateGraph` rather than using `create_react_agent`. Tool execution uses LangGraph's `ToolNode`.

**Why.** The loop is where every later milestone attaches: an injection guard before it, a policy check after it, an approval interrupt before side effects. A prebuilt agent would have to be unpicked. `ToolNode` is the opposite case: it injects the context, runs parallel tool calls concurrently, and turns unknown tool names and malformed arguments into messages the model can react to. Reimplementing that adds risk and teaches nothing.

---

### D-024: The agent loop is bounded by a step budget

**Date:** 2026-09-12

**Decision.** A run may call the model at most `DESKPILOT_MAX_AGENT_STEPS` times (6 by default). When the budget runs out, the run ends with a fixed message telling the customer a colleague will follow up.

**Why.** A model that keeps calling tools would otherwise loop until something else broke, spending tokens and time. Ending with a plain message is also how escalation will behave later.

**Consequences.** Detecting repeated identical calls, per-call timeouts, and retries arrive with the resilience milestone. This is the floor, not the finished behaviour.

---

### D-025: Tool exceptions never reach the model verbatim

**Date:** 2026-09-12

**Decision.** When a tool raises, the model receives a fixed instruction to report the failure and not retry it. The exception is logged server-side only.

**Why.** Exception text routinely contains hostnames, connection strings, and table names, and anything the model receives can end up in a reply to a customer. A fixed message also avoids the pattern where an unfamiliar error tempts the model into retrying the same call.

---

### D-026: Graph behaviour is tested with a scripted model

**Date:** 2026-09-12

**Decision.** A third test suite, `tests/graph/`, drives the real graph with a `ScriptedChatModel` that returns prepared responses. It runs by default alongside the unit tests. Real-model behaviour stays in the integration suite.

**Why.** Loop control, routing, error handling, and the step budget are deterministic logic and deserve deterministic tests that run in milliseconds. Testing them through a real LLM would be slow and flaky, and a flaky test on a security-relevant path is worse than no test.

**Consequences.** The fake must return a fresh message with unique ids each turn. Reusing one message object makes `add_messages` treat the second turn as an edit of the first, which silently caps the loop and hides off-by-one errors.

---

### D-027: A ticket is a LangGraph thread; the conversation is not duplicated

**Date:** 2026-09-13

**Decision.** The `tickets` table holds only what has to be queried or listed: a reference, the customer, a subject, a status, and a `thread_id`. The messages live in LangGraph's checkpoint tables under that thread id, and nowhere else.

**Why.** Two copies of a conversation drift apart. Keeping one copy means a ticket resumed after a restart is exactly the state the agent last saw, including tool results and token counters, not a reconstruction of it.

**Consequences.** Reading a conversation means reading a checkpoint, which `deskpilot ticket show` does directly through the checkpointer, with no model and no graph involved. Anything the UI needs to filter or sort by has to become a column.

---

### D-028: The thread id is separate from the public reference

**Date:** 2026-09-13

**Decision.** A ticket has a human-readable `reference` (`TCK-0007`) and a separate `thread_id` (a UUID).

**Why.** The reference is a presentation choice and could reasonably change: per-year numbering, a different prefix, a merge of two tickets. The thread id is the key a conversation is stored under, and changing it orphans the conversation. Separating them means one can never break the other.

---

### D-029: Alembic is told to ignore LangGraph's tables

**Date:** 2026-09-13

**Decision.** `migrations/env.py` passes an `include_name` filter that excludes `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, and `checkpoint_migrations`. They are created by `deskpilot db setup`, which calls `AsyncPostgresSaver.setup()`.

**Why.** Those tables are not in our SQLAlchemy metadata, so autogenerate treats them as unknown and writes `DROP TABLE` for each one. The first generated migration did exactly that, and applying it would have deleted every conversation. LangGraph also migrates its own schema between versions, so it must stay the owner.

---

### D-030: The step budget resets each turn; token counts do not

**Date:** 2026-09-13

**Decision.** `steps` has no reducer, so each turn overwrites it with 0 and gets a fresh budget. `input_tokens` and `output_tokens` keep summing across the whole ticket.

**Why.** They answer different questions. A budget protects against one runaway turn, so it has to reset or the second message on a long ticket would start already exhausted. Token counts answer "what has this ticket cost", which is inherently cumulative and is what a per-ticket budget will need in the observability milestone.

---

### D-031: Services do not manage transactions

**Date:** 2026-09-13

**Decision.** Functions in `tickets.py` read, add, and mutate, but never `begin()` or `commit()`. The command or request handler owns the transaction.

**Why.** Found the hard way: `set_status` opened its own transaction and crashed with "a transaction is already begun on this session", because the preceding `SELECT` had already started one implicitly. Beyond that, one command needs to create a ticket, run the agent, and record the resulting status as a single unit; a service that commits halfway makes that impossible.

**Consequences.** `seed()` is the exception. It is a standalone bulk load rather than a service call, and being atomic on its own is the point of it.

---

### D-032: Policy documents are markdown files, and the index is derived

**Date:** 2026-09-14

**Decision.** The policies live as markdown under `backend/policies/`. The
`policy_chunks` table is built from them by `deskpilot policy index` and can be
deleted and rebuilt at any time.

**Why.** A policy is written and reviewed by people, so it belongs in files that
diff well in a pull request. Making the database derived means there is one source
of truth, and a wrong answer can always be traced back to a line in a file.

**Consequences.** The index can be out of date with respect to the files, so
staleness has to be detectable — hence the digest and fingerprint columns.

---

### D-033: Passages are split at headings, not at a fixed size

**Date:** 2026-09-14

**Decision.** Each markdown section becomes one passage. Over-long sections are split
further, but only at paragraph boundaries. Every passage repeats its heading path.

**Why.** A policy document is already organised around the questions people ask
("Return window", "Lost parcels"), so the author's structure beats any window we
could slide over the text. Repeating the heading matters twice over: retrieved
alone, "within 30 days of delivery" is ambiguous, and the heading words measurably
improve the embedding match.

**Consequences.** The chunking depends on the documents being well structured. A
policy file with no headings produces no passages, and indexing says so rather than
silently indexing nothing.

---

### D-034: Embeddings carry a fingerprint, not just a model name

**Date:** 2026-09-14

**Decision.** Each passage stores `model|document_prefix|dimensions`. Any change to
that string marks every affected document stale.

**Why.** `nomic-embed-text` is trained with task prefixes: documents are embedded as
`search_document: ...` and queries as `search_query: ...`. Applying a different
prefix, or none, produces different vectors for the same text and quietly degrades
every result. That is worse than an error, because nothing fails. The model name
alone would not have caught it.

---

### D-035: Search results below a distance threshold are discarded

**Date:** 2026-09-14

**Decision.** `search_policy` returns only passages within
`DESKPILOT_POLICY_SEARCH__MAX_DISTANCE`, and otherwise tells the model the policy
does not cover the question.

**Why.** A vector search always returns its k nearest neighbours, whether or not
anything is relevant. Handing the model the closest passage to "what is the atomic
mass of tungsten?" invites it to answer from a refund policy. Saying "not covered"
is a better answer than a confident wrong one, and it is the behaviour the eval
suite will measure.

---

### D-036: A document is current only when all of its passages agree

**Date:** 2026-09-14

**Decision.** `index_status` treats a document as current only if every one of its
passages carries the same digest and fingerprint.

**Why.** Found by a failing test. The first version recorded one digest per document
and let the last row win, so a document whose passages disagreed — a half-finished
rebuild — reported itself as current. Comparing the whole set makes a partial state
visible instead of hiding it.

---

### D-037: A tool takes no argument it can get from the context

**Date:** 2026-09-14

**Decision.** `get_customer` has no parameters. The schema the model sees is an empty object; the account it describes comes entirely from `AgentContext`.

**Why.** The obvious signature, `get_customer(email)`, would put the identity back under the model's control and undo [D-020](#d-020-identity-travels-in-the-graph-context-never-in-state). A parameter the model must never choose should not exist.

**Consequences.** The rule generalises: before adding an argument, check whether the context already answers it. It also makes the tool impossible to misuse by accident, which matters more once ABAC arrives and a wrong argument would be a policy violation rather than a wrong answer.

---

### D-038: A truncated list says that it is truncated

**Date:** 2026-09-14

**Decision.** When `list_orders` returns fewer orders than exist, it appends a line saying how many of how many were shown.

**Why.** A model handed ten of fourteen orders has no way to know the list was cut, and will tell the customer they have ten. The tool knows, so the tool says. Silent truncation turns a display limit into a false statement to a customer.

**Consequences.** Any future tool that caps its output owes the model the same disclosure.

---

### D-039: Output caps are settings, not tool arguments

**Date:** 2026-09-14

**Decision.** `max_orders_listed` lives in `ToolSettings`, reaches the tool through `AgentContext`, and is not exposed to the model.

**Why.** A limit exists to protect the context window from the tool's own output. Letting the model raise it defeats the purpose, and Ollama truncates a too-long prompt silently, so the failure would be invisible. Passing it through the context rather than reading a global also lets a test vary it, which is how the truncation behaviour is tested.

---

### D-040: Enum arguments are typed, not free text

**Date:** 2026-09-14

**Decision.** `list_orders(status: OrderStatus | None)` takes the enum, so the JSON schema the model sees lists the five valid values.

**Why.** A string parameter invites "in transit", "on its way", or "shipped?" and turns a typo into an empty result the model then explains away. With an enum, an invalid value is rejected before the tool runs and the model gets a correctable error instead of a plausible wrong answer.

---

### D-041: Classification runs once per ticket, not once per turn

**Date:** 2026-09-14

**Decision.** A `classify` node sits between START and the agent loop. A conditional edge skips it when the state already holds a category, so it runs on the first turn only.

**Why.** What a ticket is about is a property of the ticket, not of each message. "So what happens now?" on the third turn classifies as nothing useful on its own. Running once also means the cost is paid once, and the label stays stable for reporting.

**Consequences.** A ticket that changes subject mid-conversation keeps its original label. That is acceptable while the label is advisory; it would not be if the label gated behaviour.

---

### D-042: The category is advisory, and does not decide which tools the agent gets

**Date:** 2026-09-14

**Decision.** The category is appended to the system prompt as a hedged hint and recorded on the ticket row. It does not filter the tool set. The earlier plan was for it to select tools; that was reversed before it was built.

**Why.** Filtering tools by category turns a wrong classification into a wrong answer the agent cannot recover from: a refund question mislabelled as shipping would leave the agent without `search_policy` and no way to notice. With four read-only tools, the gain from filtering is small and the failure mode is silent. It also matters that the classifier reads untrusted customer text, so a label is something an attacker can influence; a hint they can nudge is survivable, a capability switch they can flip is not.

**Consequences.** Filtering becomes worth revisiting when there are many more tools, and especially in the human-in-the-loop milestone, where keeping refund tools away from a shipping ticket is a real safety gain rather than a small prompt saving. By then the label will need to be trustworthy enough to gate on.

---

### D-043: The category is stored in state as a string, not as an enum

**Date:** 2026-09-14

**Decision.** The `category` channel is typed `str | None`, and `parse_category` converts at the edges.

**Why.** Found by a crash on the second turn: `'str' object has no attribute 'value'`. A checkpoint is serialized, and a `StrEnum` written to it comes back as a plain `str`. Typing the channel as the enum would have been a lie on every turn after the first.

**Consequences.** It generalises to anything else put in state. A checkpointed channel holds what the serializer can carry, and rich types have to be reconstructed on the way out. `parse_category` also turns a value from an older enum into "unclassified" rather than an exception.

---

### D-044: Structured output keeps the raw message

**Date:** 2026-09-14

**Decision.** `with_structured_output(Classification, include_raw=True)`, and the classifier returns its token usage alongside the category.

**Why.** Without `include_raw`, only the parsed object comes back and the underlying `AIMessage` is discarded, taking `usage_metadata` with it. Classification would then appear free in the ticket's totals, which is exactly the kind of quiet under-counting that makes a cost budget useless.

---

### D-045: A failed classification degrades, it does not raise

**Date:** 2026-09-14

**Decision.** A model error, a malformed answer, or an empty message all produce `OTHER`, logged at warning level. The run continues.

**Why.** The category is a hint. Losing it costs a little answer quality; raising from the classify node would cost the customer their reply entirely. The failure is worth recording, not worth stopping for.

---

### D-046: Evals score behaviour, not prose

**Date:** 2026-09-14

**Decision.** A case states an expected category, tools that must be called, tools that must not be called, and substrings the answer must or must not contain. Nothing judges how well the answer is written.

**Why.** These checks are deterministic, fast, and unarguable: either `search_policy` was called or it was not. Answer quality needs a second model and its own biases, and that belongs in the evals milestone. Starting with the mechanical half means the suite is trustworthy from the first run.

---

### D-047: Required tools are a floor; extra calls are reported, not failed

**Date:** 2026-09-14

**Decision.** `requires` lists tools that must be called. Anything else the agent called is reported as an extra, and does not fail the case. `forbids` is where hard expectations live.

**Why.** There is usually more than one reasonable route to a correct answer: checking the customer's tier before quoting a return window is thorough, not wrong. Pinning the exact tool set would make the suite fail every time the agent got better. Where a call really is wrong, `forbids` says so explicitly.

---

### D-048: Some failures are critical, and the report says which

**Date:** 2026-09-14

**Decision.** Calling a forbidden tool or leaking a forbidden string is critical and printed in red. A wrong category or a missing phrase is an ordinary failure, printed in yellow. The summary counts both.

**Why.** They are different kinds of problem. A mislabelled ticket is a quality regression to look at when convenient. Another customer's tracking number in an answer is a defect to stop for. A single pass rate hides that difference precisely when it matters most.

---

### D-049: A case that crashes is a failed case, not a stopped suite

**Date:** 2026-09-14

**Decision.** `run_case` catches every exception and records it as a failure with the exception text. The suite always finishes and always reports.

**Why.** A suite that stops on the first timeout tells you almost nothing, and local models time out. Fifteen results and one crash is a useful report; one crash and no results is not.

---

### D-050: Every case runs in a fresh thread with no checkpointer

**Date:** 2026-09-14

**Decision.** Each case gets a unique thread id, the graph is built without a checkpointer, and cases run concurrently behind a semaphore.

**Why.** Cases must not see each other's conversations, and re-running the suite must not need a reset. It also keeps the eval database clean: nothing is written, so the suite can run against a seeded database repeatedly.

**Consequences.** Concurrency defaults to 2, matching `OLLAMA_NUM_PARALLEL`. Going wider does not make a local model faster; it makes every case wait longer.

---

### D-051: Runs are saved as JSONL so they can be re-scored

**Date:** 2026-09-14

**Decision.** Each run writes `evals/runs/<timestamp>.jsonl`: one header line with the models and summary, then one line per case including the full answer.

**Why.** Scoring is cheap and running the agent is not. Saving the transcripts means the judge model in the evals milestone can score old runs without paying for them again, and two runs can be compared long after the fact. The header records which models produced the run, because a report without that is not comparable to anything.

---

### D-052: The dataset validates itself in the normal test suite

**Date:** 2026-09-14

**Decision.** Fast unit tests check that every case names a real customer and real tools, that ids are unique, that no case both requires and forbids the same tool, and that every tool is exercised somewhere.

**Why.** A rotten dataset is worse than no dataset. A case naming a renamed tool fails for ever and gets written off as a model problem. Catching it in the normal test run means a tool rename breaks the build immediately, rather than quietly degrading the suite.

---

### D-053: Users are a separate table from customers

**Date:** 2026-09-14

**Decision.** `users` holds people who can log in. `customers` holds people who bought something. A customer account carries a nullable `customer_id` linking the two.

**Why.** They are genuinely different populations that mostly overlap. A reviewer logs in and has never bought anything; a customer who never registered still has orders and tickets. Merging them would mean either staff rows sitting in the customer table or orders hanging off accounts that do not exist.

**Consequences.** Registration links by email when a matching customer exists. From the ABAC milestone the principal comes from the user, and `customer_id` is what lets a login see that person's orders.

---

### D-054: A password longer than 72 bytes is rejected, not truncated

**Date:** 2026-09-14

**Decision.** The policy rejects any password whose UTF-8 encoding exceeds 72 bytes, with a message saying so.

**Why.** bcrypt hashes at most 72 bytes and silently ignores the rest, so two different long passwords can produce the same hash. Truncating quietly weakens a password the user believed was strong. Pre-hashing with SHA-256 first is the other common fix and does work, but it adds a scheme to get wrong for a case almost nobody hits.

**Consequences.** The limit is counted in bytes, not characters, which surprises people: forty accented characters are eighty bytes. A test pins that specifically.

---

### D-055: Every login failure looks and costs the same

**Date:** 2026-09-14

**Decision.** An unknown address, a wrong password, and a disabled account all raise the same message. The unknown-address path verifies against a throwaway hash so it takes comparable time.

**Why.** Either channel enumerates accounts. A distinct "no such account" message does it in one request; a faster response for unknown addresses does it just as well without a message, because bcrypt at cost 12 takes long enough to measure over a network.

**Consequences.** Support gets a vaguer error to debug. The logs carry the real reason, which is where it belongs.

---

### D-056: The bcrypt cost is configurable, and tests lower it

**Date:** 2026-09-14

**Decision.** `DESKPILOT_AUTH__BCRYPT_ROUNDS` defaults to 12, and tests pass 4 explicitly.

**Why.** Twelve rounds is roughly a quarter of a second per hash by design. A test suite that registers and authenticates dozens of accounts would spend most of its time proving that bcrypt is slow, which it is, on purpose. The property being tested is the algorithm and the flow, not the cost.

**Consequences.** The cost is recorded inside every hash, so raising the default later does not invalidate existing passwords; they are rehashed on next login when that is added.

---

### D-057: A password change revokes every session

**Date:** 2026-09-14

**Decision.** `set_password` bumps `token_version`, which invalidates every token the user holds.

**Why.** Changing a password is usually a response to it being compromised. Leaving existing sessions working means the person who took it keeps their access, which makes the change theatre.

---

### D-058: The CLI never takes a password as an argument

**Date:** 2026-09-14

**Decision.** `auth register`, `auth check`, and `auth passwd` prompt with echo off. There is no `--password` flag.

**Why.** An argument lands in shell history and is visible in the process list to every other user on the machine for as long as the command runs.

---

### D-059: Expected failures are one list, shared by both CLI runners

**Date:** 2026-09-14

**Decision.** `EXPECTED_ERRORS` names every exception a command can raise in normal use, and both `run` and `run_sync` catch that tuple.

**Why.** Found by a bug: adding `AuthError` to what looked like the right handler missed both, because the two `except` clauses had quietly diverged and neither matched the text being edited. A wrong password then printed a traceback instead of one red line. One list cannot drift from itself.

---

### D-060: An access token carries identity and nothing else

**Date:** 2026-09-14

**Decision.** The claims are `sub`, `jti`, `ver`, `iat`, `exp`, `iss`, and `aud`. No role, no email, no approval limit.

**Why.** A claim baked in at login is a snapshot of a permission that may since have been taken away. A reviewer whose approval limit was lowered would keep the old one until their token expired. Reading attributes from the database at the moment of the decision costs a query and removes a whole class of stale-permission bug. This is the concrete form of "the token proves who you are; it does not decide what you can do".

---

### D-061: The algorithm is pinned at decode, and issuer and audience are checked

**Date:** 2026-09-14

**Decision.** `jwt.decode` is given `algorithms=["HS256"]`, an expected issuer and audience, and a list of claims that must be present.

**Why.** Trusting the token's own `alg` header is how `alg: none` gets accepted, and how an HMAC-signed token gets verified against a public key an attacker already has. Checking the audience stops a token minted for another service being replayed here — the same rule that will apply to the Paywisp integration, in the other direction. Requiring claims explicitly means a token missing `ver` is refused rather than defaulting to something.

**Consequences.** Every one of these is a test: forged key, tampered payload, `alg: none`, wrong audience, wrong issuer, missing claim, and assorted rubbish.

---

### D-062: Refresh tokens are opaque and stored as SHA-256 digests

**Date:** 2026-09-14

**Decision.** A refresh token is 256 random bits. The database holds only its SHA-256 digest.

**Why.** Opaque rather than a JWT, because a refresh token needs to be revocable individually, which means a database row either way — and if there is a row, there is no reason to sign anything. The digest means a database dump cannot be used to mint sessions. SHA-256 rather than bcrypt because the input is already random: there is no dictionary to run, no benefit to being slow, and this is on the lookup path for every refresh.

---

### D-063: Refresh tokens rotate, and reuse revokes the family

**Date:** 2026-09-14

**Decision.** Every refresh retires the old token and issues a new one in the same family. Presenting an already-retired token revokes every token in that family.

**Why.** Rotation alone narrows the window a stolen token is useful for. Reuse detection closes it: once both the thief and the owner hold tokens from the same family, whichever refreshes second is caught. Which of the two is the thief is unknowable, so both lose the session and the owner logs in again — a minor annoyance that turns a silent compromise into a visible one.

**Consequences.** Each login starts its own family, so signing out a laptop does not sign out a phone. The retire and the issue happen in one transaction, so two concurrent refreshes cannot both succeed.

---

### D-064: The database is consulted even after the signature verifies

**Date:** 2026-09-14

**Decision.** `authenticate_access_token` verifies the signature and then loads the user, checking `token_version` and `is_active`.

**Why.** A signature proves the token is ours and unmodified. It cannot know that the account was disabled or signed out a minute ago. Without the lookup, "revoke all sessions" would mean "revoke all sessions within fifteen minutes", which is not what anybody means by it.

**Consequences.** Every authenticated request costs one query. That is the price of instant revocation, and it is the right trade for a fifteen-minute token.

---

### D-065: Logout is silent about tokens it does not recognise

**Date:** 2026-09-14

**Decision.** `log_out` revokes the family when the token is known and does nothing otherwise, without reporting which happened.

**Why.** A logout that says whether a token existed is an oracle for testing stolen tokens. There is also nothing useful a caller could do with the failure.

---

### D-066: The CLI session lives in a file, and the trade-off is documented

**Date:** 2026-09-15

**Decision.** The session is saved to `~/.deskpilot/session.json`, written `0600` inside a `0700` directory, and `load` refuses to read a file that others can read.

**Why.** It is a refresh token sitting in a file, which is worth being uncomfortable about. The alternatives are worse for a local tool: a keychain drags in a platform-specific dependency, and keeping it in memory means logging in for every command. Every command-line tool that does not make you log in every time works this way. The honest thing is to write it carefully and say so, rather than to pretend the problem is not there.

**Consequences.** The file is created with the right mode from the start, using `os.open`, because writing first and `chmod`-ing afterwards leaves a window where it is world-readable. `__repr__` is overridden so a traceback cannot carry the tokens. The path is under the home directory rather than the repository, so a checkout cannot commit one.

---

### D-067: `--as` survives, as an opt-in escape hatch

**Date:** 2026-09-15

**Decision.** `--as` still works, but only when `DESKPILOT_AUTH__ALLOW_IMPERSONATION` is true, which it is not by default. Every use logs a warning. `.env.example` turns it on for local development.

**Why.** A flag that lets one person act as another is precisely what this milestone exists to remove, so it cannot be the default. But the CLI is the only interface until the web milestone, and logging in as each of eight seeded customers to try something would make the project tedious to work on. Off in code, on in the local environment file, is the shape that keeps both properties: the insecure default never ships, and the developer never notices.

**Consequences.** The refusal message names the exact command to log in and the exact variable to set, because an unexplained refusal is how a good default gets deleted in frustration.

---

### D-068: Every ticket command goes through one resolver

**Date:** 2026-09-15

**Decision.** `current_customer` decides who a command acts as, and every ticket command calls it.

**Why.** Found by writing this step: `ticket list` had its own `--as` handling and did not consult the session at all, so it listed every customer's tickets to anybody who ran it. It had been that way since the tickets step. One resolver is one place to get it right, and a command that forgets to call it now fails to compile rather than quietly leaking.

---

### D-069: The access token is refreshed where it is used, not where it is issued

**Date:** 2026-09-15

**Decision.** `active_session` checks whether the saved access token has expired and refreshes it before use, with thirty seconds of skew.

**Why.** Access tokens last fifteen minutes and the CLI is used in bursts hours apart, so almost every command would otherwise fail on a stale token. Refreshing at the point of use makes a long-running shell stay usable without the user noticing. The skew stops a token that is valid at the check from expiring during the request it was fetched for.

---

### D-071: Account lockout with exponential backoff, and why that is a trade

**Date:** 2026-09-15

**Decision.** Five consecutive failures lock an account for a minute. Each further failure doubles the lockout, capped at an hour. A success clears the counter. Expiry is by timestamp, so nothing has to run to unlock an account.

**Why.** Password guessing is the attack bcrypt slows down but does not stop. Doubling matters more than the starting value: it makes sustained guessing cost exponentially more, while one fat-fingered evening costs a minute.

**Consequences.** This is a genuine trade, not a free win. Anybody who knows an email address can lock its owner out by failing on purpose. That is why the first lockout is short, and why the real answer — rate limiting by source address — waits for the web milestone, where there is a source address to limit by. Until then an account can be locked, but only briefly and only one at a time.

---

### D-072: A lockout is not announced

**Date:** 2026-09-15

**Decision.** A locked account raises the same message as a wrong password and an unknown address, and spends the same time doing it.

**Why.** "Locked until 14:32" confirms the account exists, which is the enumeration channel [D-055](#d-055-every-login-failure-looks-and-costs-the-same) closed. It also lets an attacker watch their own lockout tick down and time the next attempt.

**Consequences.** A legitimate user who locked themselves out is told only that the credentials do not match, which is worse for them. The log says exactly what happened. With per-address rate limiting in the web milestone there will be a safe way to be more helpful, because a lockout can be reported to a session that has already proved it owns the address.

---

### D-073: A failed login must be committed

**Date:** 2026-09-15

**Decision.** `authenticate` mutates the failure counter and does not commit, like every other service. Callers commit in a `finally`, so the attempt is recorded even though the login raised.

**Why.** The natural shape — commit on success, roll back on failure — silently disables the whole lockout, because the only thing worth recording happens on the failure path. It would have looked like it worked: the code is all there, and the counter would sit at zero for ever.

---

### D-074: A NOT NULL column added to a populated table needs a server default

**Date:** 2026-09-15

**Decision.** `users.failed_logins` is declared with both a Python `default` and a `server_default`.

**Why.** Found by the migration failing: `column "failed_logins" of relation "users" contains null values`. Autogenerate writes `nullable=False` with no default, which cannot work on a table that already has rows. A `server_default` on the model makes autogenerate emit it, keeps `alembic check` quiet, and also covers any insert that bypasses the ORM.

**Consequences.** It generalises. Any non-nullable column added to a table that already has data needs a server default, or a three-step migration: add nullable, backfill, then enforce.

---

### D-075: A policy is a pure function of principal, action, and resource

**Date:** 2026-09-15

**Decision.** Every rule takes a frozen `Principal`, an `Action`, and a frozen `Resource`, and returns allow, deny, or `None` meaning "not my business". No rule performs I/O, loads a row, or reads a setting.

**Why.** It makes the policy enumerable. The whole rule set can be tested as a matrix — every action against every resource for every kind of actor — rather than through a handful of scenarios. A policy you cannot enumerate is a policy you are guessing about. It also means the caller decides what to load, so a rule can never be slow or fail.

**Consequences.** The caller assembles the resource, which is a real cost: forgetting to put the region on a `Ticket` silently changes the answer. [D-008](#d-008-in-house-abac-engine) already chose an in-house engine; this is the shape of it.

---

### D-076: Deny by default, and the denial says the default was reached

**Date:** 2026-09-15

**Decision.** Rules are tried in order and the first to answer decides. When none answers, the request is denied with the reason "no rule allows this action on this resource", and the recorded rule is `default`.

**Why.** An action nobody wrote a rule for is not an oversight to route around; it is an action that has not been thought about. Distinguishing "a rule denied this" from "nothing covered this" matters when reading an audit log, because the second is a gap in the policy rather than a user doing something wrong.

---

### D-077: Denials are ordered before allows, and separation of duties is a denial

**Date:** 2026-09-15

**Decision.** The rule order is: disabled accounts, separation of duties, ownership, then region and limit. Reading `policies.py` top to bottom is reading the policy.

**Why.** First-match wins, so position is meaning. A rule that stops somebody approving a refund on their own ticket is worthless if a later rule can allow it because their limit is high enough and they cover the region. Putting it among the denials makes it unconditional, and a test asserts that an unlimited supervisor covering every region still cannot approve their own.

---

### D-078: Ownership is a rule about the resource, not about the role

**Date:** 2026-09-15

**Decision.** `a_customer_reads_their_own_things` matches on `principal.customer_id == resource.owner_customer_id`, and never on `role == CUSTOMER`.

**Why.** A reviewer is also a person who buys tents. Keying on role would mean staff lose access to their own orders, or gain a second path to them, and both are wrong. Ownership is a property of the pair, so the rule should be about the pair.

---

### D-079: The refusal a caller sees carries no reason

**Date:** 2026-09-15

**Decision.** `Forbidden` says only "You are not allowed to do that." The real reason travels on the exception and into the log.

**Why.** "You may not approve above 500.00" is useful to a colleague and equally useful to somebody mapping out where the limits are. The audit log is the right audience for the detail, because reading it is itself an authorized action.

---

### D-080: Regions are an array column, and unknown values narrow rather than raise

**Date:** 2026-09-15

**Decision.** `users.regions` is a Postgres array of strings. `Principal.from_user` drops values that are not in the `Region` enum.

**Why.** A short list of labels read on every decision and never queried on its own does not earn a join table. Dropping unknown values means removing a region from the enum takes access away, which is the safe direction; raising would break every request the account makes, including the ones that had nothing to do with that region.

---

### D-081: `deskpilot auth can` exists so a refusal can be understood

**Date:** 2026-09-15

**Decision.** A CLI command asks the engine one question and prints the answer, the rule that produced it, and the attributes that were weighed.

**Why.** Deny-by-default makes "it says no" the common experience, and "it says no" is useless on its own. Being able to see that `a_reviewer_approves_within_their_limit` refused because 90000 is above 50000 turns a mystery into a fact. It is also the quickest way to check a rule change did what was intended before wiring it into anything.

---

### D-082: An audit entry is written in its own transaction

**Date:** 2026-09-15

**Decision.** `record_decision` and `record_event` open their own session, insert, and commit, independently of whatever the caller is doing.

**Why.** The entry worth having most is the one for an action that was refused, and a refused action rolls back. Sharing the caller's transaction would roll the record back with it, so the log would contain only the things that succeeded — which is the opposite of an audit log. This is the same lesson as [D-073](#d-073-a-failed-login-must-be-committed), which is why it is worth stating twice: the interesting record is almost always on the failure path.

**Consequences.** Two round trips instead of one on the audited paths. Acceptable, because the audited paths are refusals and consequential actions, not the routine reads.

---

### D-083: Recording never raises

**Date:** 2026-09-15

**Decision.** `_write` catches every exception, logs it, and returns.

**Why.** A logging failure must not turn a working request into a broken one. The trade is real and is stated rather than hidden: this is an accountability record, not a ledger that has to balance. A system where the audit log is load-bearing enough to fail closed would need a different design, and would say so.

---

### D-084: Every denial is recorded; only consequential allows are

**Date:** 2026-09-15

**Decision.** A refusal is always written. An allow is written only when its action is in `ALWAYS_AUDITED`: approvals, edits, rejections, viewing any ticket, viewing traces, managing accounts.

**Why.** A customer reading their own order happens on every turn of every conversation. Recording it would bury the entries somebody would actually want to find, and a log nobody can read is not evidence of anything. The rule is about consequence, not about volume: the audited allows are the ones where "who did this, and when" is the question.

**Consequences.** The set is a policy decision, so it is asserted in a test rather than assumed. A new action that moves money must be added to it.

---

### D-085: The audit log stores text, not foreign keys

**Date:** 2026-09-15

**Decision.** `event` is a string, not an enum column. `resource` is a rendered description rather than a reference. Only `actor_user_id` is a key, and it is `ON DELETE SET NULL`.

**Why.** An entry has to stay readable after the world around it changes. An enum column would force old entries to be rewritten or deleted when the enum changes, and rewriting history is exactly what an audit log must not do. A resource row may be deleted, and the record of what happened to it should outlive it.

---

### D-086: Decisions and authentication events share one log

**Date:** 2026-09-15

**Decision.** One `audit_log` table, with a `kind` column distinguishing `auth` from `authz`.

**Why.** The question people ask is "what did this account do", and that answer spans both: a login, then a refusal, then an approval. Two tables would mean interleaving them by hand every time. The `kind` column keeps them separable when only one is wanted.

---

### D-087: The engine stays pure; a separate layer remembers

**Date:** 2026-09-15

**Decision.** `decide` knows nothing about a database. `guard` calls `decide`, records the outcome, and raises. The rest of the application calls `guard`.

**Why.** [D-075](#d-075-a-policy-is-a-pure-function-of-principal-action-and-resource) is what makes the matrix tests possible, and putting a write inside `decide` would end that. Keeping recording in a separate layer means the policy stays enumerable and testable with no database at all, while every real call still leaves a trace.

---

### D-088: The agent context carries a principal, not an email

**Date:** 2026-09-15

**Decision.** `AgentContext.customer_email` is replaced by `AgentContext.principal`. `customer_email` survives as a read-only property for messages and logs, and decides nothing.

**Why.** An email is an identifier; a principal is an identifier plus the attributes a policy weighs. Tools that compare emails encode one policy each, in eight places, and they drift. One principal means one policy, in one file, that can be enumerated.

**Consequences.** Everything that builds a context now has to build a principal: the CLI from the signed-in user, the impersonation hatch and the eval suite from a seeded customer. [D-020](#d-020-identity-travels-in-the-graph-context-never-in-state) is unchanged — the principal still travels outside state and outside the message list, so the model cannot read or forge it.

---

### D-089: A row is loaded first and judged second

**Date:** 2026-09-15

**Decision.** `get_order` now queries by order number alone, builds a resource from the row, and asks the policy. This narrows [D-021](#d-021-not-found-and-not-yours-give-the-same-answer), which said the ownership check lived in the SQL so another customer's row was never loaded at all.

**Why.** With the filter in the query, a cross-customer attempt was indistinguishable from a typo: the same empty result, and no trace of either. Loading the row and judging it means the policy is the single place ownership is decided, and the refusal becomes evidence in the audit log. What the customer is told does not change — "not found" either way, so the tool is still not an oracle for which order numbers are real.

**Consequences.** Another customer's row is briefly in memory. It never leaves the function: the tool returns the fixed not-found message on refusal, and there is a test that a denial's recorded reason contains no order number. The trade is a moment of exposure inside one function, in exchange for the attempt being recorded at all.

---

### D-090: A list is authorized by scope, not row by row

**Date:** 2026-09-15

**Decision.** `list_orders` asks one question — may this principal read the orders of the customer they are — and then filters the query by `principal.customer_id`.

**Why.** A per-row decision on a list is either a query the policy cannot express or a judgement per row that does not scale. The scope question is the honest one, and it has a useful side effect: a member of staff, who owns no customer record, is refused before any query runs rather than shown somebody else's list.

---

### D-091: A principal may have no user id

**Date:** 2026-09-15

**Decision.** `Principal.user_id` is `int | None`. `Principal.for_customer` builds one with no user id and no staff attributes.

**Why.** Found by a foreign key violation. The impersonation hatch and the eval suite act for a customer that nobody signed in as, and a placeholder id of 0 pointed at no row in `users`. Recording that entry failed, and because audit writes swallow exceptions ([D-083](#d-083-recording-never-raises)) it failed silently. `None` is simply true: there is no actor.

**Consequences.** An audit entry for an unauthenticated action records no actor, which is what the nullable column already expected. It also confirmed the risk in D-083: swallowing means a schema mistake is invisible, so tests that assert an entry exists are doing real work.

---

### D-092: Building a principal demands a loaded relationship

**Date:** 2026-09-15

**Decision.** It raises a `ValueError` naming `selectinload(User.customer)` when the relationship is unloaded, rather than letting `lazy="raise"` fire.

**Why.** A customer's home region is one of the attributes a policy weighs, so the relationship is genuinely required. Left to `lazy="raise"`, the failure surfaces deep inside an unrelated call with a message that does not say what to do. Checking it at the boundary turns it into one sentence with the fix in it.

---

### D-093: Reading the audit log is itself audited

**Date:** 2026-09-15

**Decision.** `audit.view` is its own action, restricted to administrators, and listed in `ALWAYS_AUDITED`.

**Why.** Somebody who can read the record of what everyone did should leave a record of having read it. Until this step `audit tail` was the one command with no authorization at all, which is an odd gap in a milestone about authorization.

**Consequences.** It is deliberately separate from `trace.view`, even though the same people read both today. A trace is debugging material that can be sampled or thrown away; an audit entry is evidence. They deserve separate answers.

---

### D-094: An empty installation may create one account of any role

**Date:** 2026-09-15

**Decision.** Anybody may register themselves as a customer. Creating a member of staff needs `user.manage`, which only an administrator has — except when the `users` table is empty, where one account of any role is allowed and the event is recorded as a bootstrap.

**Why.** The bootstrap problem: the first administrator cannot be created by an administrator. The alternatives are worse. A seeded default admin means a known account in a public repository. A separate privileged command is the same exception wearing a hat. An environment variable that disables the check is a switch somebody leaves on.

**Consequences.** The exception is the narrowest one that still lets the system be set up, and it closes the moment the first account exists — which a test asserts. The bootstrap is written to the audit log, so the first thing the record ever says is how the first account came to exist.

---

### D-095: A password is prompted for before authorization is checked

**Date:** 2026-09-15

**Decision.** `auth register` asks for the new account's password, then checks whether the caller may create it.

**Why.** Not a decision so much as a consequence of where typer prompts. It is recorded because it is visible: somebody who may not create a staff account is asked to type a password first, then refused. Nothing is leaked - the refusal is the same either way - but it is poor manners, and it is worth knowing the order is that way round rather than assuming the check comes first.

**Consequences.** Worth revisiting when the web API arrives, where the check happens before any input is collected.

---

### D-096: The access token goes in the body, the refresh token in an httpOnly cookie

**Date:** 2026-09-15

**Decision.** `/api/auth/login` returns the access token as JSON, for the SPA to hold in memory. The refresh token is set as an httpOnly, SameSite=strict cookie scoped to `/api/auth`, and never appears in a response body.

**Why.** The two have different lifetimes and different threats. An XSS bug can read anything the page can reach, so the thing it can reach should expire in fifteen minutes. The thing that lasts a fortnight goes somewhere script cannot look. Putting both in `localStorage` is the common shortcut and hands an attacker a fortnight; putting both in cookies means every request carries the long-lived one.

**Consequences.** The SPA loses its access token on refresh of the page and has to call `/refresh` on load. That is the cost, and it is small.

---

### D-097: The refresh endpoint checks a double-submit token

**Date:** 2026-09-15

**Decision.** `/api/auth/refresh` requires an `X-CSRF-Token` header matching a readable `deskpilot_csrf` cookie. Nothing else does.

**Why.** It is the only endpoint authenticated by a cookie, so it is the only one a browser could be tricked into calling from another site. `SameSite=strict` already prevents that in every browser that honours it; the double submit is a second lock on the same door and costs one header. Every other endpoint is authenticated by a header the browser will not attach on its own, so CSRF does not apply to them and adding a check would be ritual.

**Consequences.** Logout deliberately has no check. Being signed out against your will is an annoyance, not a compromise.

---

### D-098: Rate limiting by source address, in memory, and honest about it

**Date:** 2026-09-15

**Decision.** A fixed window of failed logins per client address, held in process memory.

**Why.** Account lockout ([D-071](#d-071-account-lockout-with-exponential-backoff-and-why-that-is-a-trade)) stops somebody guessing one password. It cannot stop one password tried against a thousand accounts, because that never trips any single account's counter. This is the counter that catches it, and the HTTP layer is the first place with an address to count against.

**Consequences.** Per process, and forgotten on restart. A shared store is the real answer and belongs where there is more than one process to share it. The client address is `request.client.host` and deliberately not `X-Forwarded-For`: taking that header unconditionally would let anybody choose their own identity and skip the limit entirely. Trusting it is a deployment decision, made where the proxy is.

---

### D-099: One place turns exceptions into responses

**Date:** 2026-09-15

**Decision.** Exception handlers are registered once on the app. `Forbidden` becomes 403 with the flat refusal, `AuthError` and `TokenError` become 401 with their own already-vague messages, and anything unhandled becomes 500 with "Something went wrong."

**Why.** The services are careful about what they say; a route that formats its own error can undo that in one line. Registering the mapping once means a new route inherits it. The 500 handler matters most: an exception message is written for a developer and routinely contains hostnames and table names, so it is logged in full and reported as nothing.

---

### D-100: The interactive docs are not served

**Date:** 2026-09-15

**Decision.** `docs_url` and `redoc_url` are `None`. The OpenAPI schema is still generated, for the frontend's typed client.

**Why.** A browsable explorer of every endpoint, with a form for each, is a target rather than a feature. The schema is what the project actually needs, and it can be written to a file at build time without serving anything.

---

### D-101: The graph is built once for the process

**Date:** 2026-09-15

**Decision.** The checkpointer and the compiled agent graph are created in the app's lifespan and shared by every request.

**Why.** Building a graph per request would rebuild the model client and its connection pool every time. `run_turn` was split out from `run_agent` in milestone 2 exactly so a long-lived caller could build once and run many turns; this is the caller it was split out for.

**Consequences.** The lifespan has to run for the state to exist, which `ASGITransport` does not do on its own. Tests enter `app.router.lifespan_context` themselves.

---

### D-102: A dependency must not refuse on its own

**Date:** 2026-09-15

**Decision.** There is no dependency that rejects a principal without a customer record. Endpoints build a resource whose owner is an id nothing owns, and let `guard` refuse.

**Why.** Found by a test. The first version raised `Forbidden` from a dependency, which returned the right status and left no audit entry at all — a refusal with no trace, in a project whose whole argument is that refusals are recorded. Routing every refusal through `guard` means one path, one record, and one definition of who may do what.

**Consequences.** A staff account asking for its own tickets is denied by whichever rule reaches it first, which may be the region rule rather than the default. That is the policy's business, and the API test asserts only that the question reached the engine and left a trace.

---

### D-103: Another customer's ticket is 404, not 403

**Date:** 2026-09-15

**Decision.** Reading or replying to a ticket that belongs to somebody else returns 404 with "no ticket was found for this customer".

**Why.** The same reason `get_order` answers identically for "does not exist" and "not yours" ([D-021](#d-021-not-found-and-not-yours-give-the-same-answer)). A 403 would confirm the reference exists, turning the endpoint into an oracle for valid ticket references. The audit log still records which of the two actually happened.

---

### D-104: The conversation the API returns leaves out the agent's working

**Date:** 2026-09-15

**Decision.** `GET /api/tickets/{reference}` returns customer and agent turns only. Tool calls and their results are available to the CLI and not over HTTP.

**Why.** Two reasons, and the second is the serious one. Tool calls are the agent's working rather than the conversation, so a customer has no use for them. And a tool result is raw text from a database row or a policy document that nothing has sanitised, heading for a browser. The security milestone is where rendering untrusted text is dealt with properly; until then it does not leave the server.

---

### D-105: The status code is decided before the first byte

**Date:** 2026-09-15

**Decision.** Authentication, authorization, validation, and loading the ticket all happen in the handler, before the streaming response is returned. Only the agent run is inside the generator.

**Why.** Once a 200 and the headers have gone out, a refusal cannot become a 403. It can only be an event inside a stream the client already accepted, which every HTTP client in the world will treat as a success. A refused stream has to be a plain 403, and there are tests that assert exactly that for 401, 403, 404, and 422.

**Consequences.** The handler does the work twice over in a sense - it loads the ticket, and the generator loads it again at the end to save it. That is the price of the split, and it is worth paying.

---

### D-106: A streaming handler cannot use the request's database session

**Date:** 2026-09-15

**Decision.** The generator opens its own session to record the outcome when the turn finishes.

**Why.** FastAPI closes a dependency's session when the handler returns, and a streaming handler returns immediately - the generator runs afterwards. Using the request's session inside it would be using a closed session, and the failure would be confusing rather than obvious.

---

### D-107: Every SSE payload is JSON

**Date:** 2026-09-15

**Decision.** The `data:` field always contains a JSON object, even when it holds a single string.

**Why.** A raw newline ends a `data:` field, and model output and customer messages are full of newlines. Sending text directly would split one event into two at the first line break, and the second half would arrive as a malformed event. JSON escapes them, and a test asserts every `data:` line parses.

---

### D-108: Two stream modes, and the saved state wins

**Date:** 2026-09-15

**Decision.** `astream` is called with both `updates` and `messages`. `updates` supplies the category and the tool calls; `messages` supplies the token chunks. When the turn ends, the answer is read back from the checkpoint rather than from the accumulated tokens.

**Why.** Token chunks make the answer appear a word at a time instead of after twenty seconds of nothing, and node updates are what let the customer see that something is happening at all. The final answer comes from the saved state because that is what the conversation actually contains: the tokens are a view of it, and if the two ever disagree the saved one is the truth.

---

### D-109: No keepalive, for now

**Date:** 2026-09-15

**Decision.** No periodic comment line. `X-Accel-Buffering: no` is set, which is a different problem.

**Why.** Sending a keepalive needs a second task racing the real stream, and the gaps here are a model thinking - seconds, not minutes. The complexity is not earned yet. Recorded because the absence is deliberate: a deployment behind a proxy that closes idle connections sooner than a turn takes is exactly when to add it.

---

### D-110: The frontend's types are generated from the backend's schema

**Date:** 2026-09-16

**Decision.** `deskpilot openapi --out frontend/src/api/openapi.json` writes the schema; `pnpm types` turns it into `schema.d.ts`, which is git-ignored and regenerated. `src/api/types.ts` aliases the few shapes the app uses.

**Why.** A renamed field on the server should be a TypeScript error, not an `undefined` in a browser three weeks later. Hand-written interfaces drift silently, and the drift is only visible to the user.

**Consequences.** `scripts/check.sh` regenerates the types before type-checking, because a stale generated file hides exactly the drift it exists to catch. The alias module keeps the generated paths out of the rest of the code.

---

### D-111: The access token lives in a module variable, not in localStorage

**Date:** 2026-09-16

**Decision.** The token is held in a closure in `api/client.ts`. Nothing writes it to storage.

**Why.** An XSS bug can read `localStorage`; it cannot read a variable it has no reference to. That is a smaller win than it is usually presented as — a script running on the page can call the API directly — but it means the token is not written down somewhere a later bug can find it, and it does not survive the tab.

**Consequences.** A page reload loses it, so the app calls `/refresh` before deciding anybody is signed out, and shows a loading state until that finishes. Skipping that step would sign the user out on every reload.

---

### D-112: A 401 triggers exactly one refresh

**Date:** 2026-09-16

**Decision.** `refreshSession` keeps the in-flight promise in a module variable. Every caller that needs a refresh awaits the same one, and it is cleared in a `finally`.

**Why.** This is the bug the refresh-token design creates for the client. Five requests failing together would become five refreshes, and four of them would present a token the first one had already rotated — which is precisely what the server's reuse detection treats as theft, revoking the family and signing the user out of everything. A test fires three parallel requests and asserts one refresh.

**Consequences.** Clearing in a `finally` matters as much as the single flight: a failed refresh that left the promise in place would wedge every later one. That has its own test.

---

### D-113: The SSE reader is hand-written

**Date:** 2026-09-16

**Decision.** About thirty lines reading the fetch body stream, rather than a dependency.

**Why.** The browser's `EventSource` cannot set an `Authorization` header and cannot POST, and this API needs both. Given that, a library would be wrapping the same loop. The part worth getting right is that a network chunk has no relationship to an event boundary: one event can arrive in three chunks and three in one, so the reader buffers and only emits on a blank line. Tests cover both directions, including a payload containing an escaped newline.

---

### D-114: Model output is rendered as text

**Date:** 2026-09-16

**Decision.** Every message is rendered as a text node. Nothing is parsed as markdown or HTML, and `dangerouslySetInnerHTML` appears nowhere.

**Why.** It is model output, derived from a ticket somebody else wrote. Rendering it as markup is how an image tag pointing at another host turns a conversation into an exfiltration channel. Making it pretty is worth doing after the security milestone has dealt with sanitising untrusted text, and not before.

---

### D-115: The committed OpenAPI document is checked against the app

**Date:** 2026-09-16

**Decision.** `frontend/src/api/openapi.json` is committed, and a fast unit test compares it against what `create_app().openapi()` produces.

**Why.** The frontend's types are generated from that file, so a stale copy means the types describe an API that no longer exists — which defeats the whole reason for generating them ([D-110](#d-110-the-frontends-types-are-generated-from-the-backends-schema)). Committing it makes the contract reviewable in a diff; testing it makes the contract true. It is the same idea as `alembic check`.

**Consequences.** Changing a response model now fails the backend test suite until the schema is regenerated, which is the right moment to notice. The test passes silently when the file is absent, so a backend-only checkout still works.

---

### D-116: Logging out does not invalidate the access token

**Date:** 2026-09-16

**Decision.** `POST /api/auth/logout` revokes the refresh family and clears the cookie. An access token already in the client's hands stays valid until it expires.

**Why.** Killing it immediately means bumping `token_version`, and that signs the person out of every other device as well. That is what "sign out everywhere" is for; it is not what "sign out" means on one laptop. The exposure is bounded by the access token's lifetime, which is fifteen minutes and short for exactly this reason.

**Consequences.** Recorded because it looks like a bug when you first notice it. A deployment that cannot accept a fifteen-minute window needs a denylist of token ids, which is a shared store and a lookup on every request — the cost that short tokens exist to avoid.

---

### D-117: The carrier is a separate service, not a mock

**Date:** 2026-09-16

**Decision.** ShipTrack is its own uv project, its own container, its own settings, and its own test suite. Deskpilot reaches it over HTTP and shares nothing with it but a secret.

**Why.** [D-014](#d-014-each-service-is-its-own-uv-project) said the vendors would be separate projects; this is what that buys. A mock patched into the client tests the client against the author's belief about the carrier. A service tests it against something that can disagree - that can be slow, return a 500, hang, or refuse a signature the client thought was fine.

**Consequences.** Two `uv sync`s, two test suites, and `scripts/check.sh` walks `vendors/*` so a change to one cannot be forgotten.

---

### D-118: The signature covers method, path, timestamp, nonce, and a body digest

**Date:** 2026-09-16

**Decision.** The signed string is those five joined by newlines, HMAC-SHA256 with a shared secret.

**Why.** Each piece closes a hole, and a test asserts that changing each one changes the signature. Without the **method and path** a captured read is replayable as a write, or against another customer's parcel. Without the **timestamp** a capture works for ever. Without the **nonce** it works repeatedly inside the window. Without the **body digest** the body can be swapped under a valid signature, which is the one people leave out.

The newlines matter too: concatenating without a separator lets a path ending in `1` with nonce `23` produce the same string as a path ending in `12` with nonce `3`. That has its own test.

---

### D-119: The nonce is recorded only for a request that was otherwise valid

**Date:** 2026-09-16

**Decision.** Replay checking happens after the signature check, not before.

**Why.** Recording the nonce of a request that failed for another reason lets anybody burn nonces they never legitimately used - send a million unsigned requests carrying guessed nonces and the real client starts being refused. Checking it last means only a caller who already proved they hold the secret can spend one.

---

### D-120: Every refusal reads the same and the reason goes to the log

**Date:** 2026-09-16

**Decision.** Unknown key, wrong signature, stale timestamp, reused nonce and missing header all return the same 401 body. The real reason is logged.

**Why.** The same rule as Deskpilot's own login ([D-055](#d-055-every-login-failure-looks-and-costs-the-same)), applied in the other direction. "Unknown key id" versus "signature does not match" tells a prober which half of the credential they have right.

**Consequences.** Chaos is applied after authentication for the same reason: if a failing carrier refused bad signatures differently from good ones, the failure rate itself would leak.

---

### D-121: The chaos knobs exist so the resilience can be tested

**Date:** 2026-09-16

**Decision.** ShipTrack can add latency, return 500s, and hang, by configuration or through a signed endpoint at runtime.

**Why.** A supplier that always answers in five milliseconds teaches nothing about timeouts, retries, or circuit breakers, and a client's resilience that has never been exercised is a claim rather than a property. The hang is the important one: a 500 is obvious, while a connection that stays open looks like a slow response until somebody's timeout decides otherwise - and a client without one waits for ever.

**Consequences.** The randomness is seedable, so a test can arrange a failure and get the same one twice.

---

### D-122: The client re-implements the signing scheme

**Date:** 2026-09-16

**Decision.** `deskpilot/integrations/shiptrack/signing.py` is a second implementation of the same scheme, not an import from the vendor package.

**Why.** ShipTrack is another company. In life its scheme arrives as a document and you write the code. Sharing a helper would also make the contract test meaningless: a shared implementation cannot disagree with itself, so it can never catch the case where one side changes and the other does not.

**Consequences.** The duplication is the point and should not be refactored away. A test asserts that nothing under `src/deskpilot` imports `shiptrack`, so the shortcut cannot be taken by accident.

---

### D-123: Retries only for failures that might not happen again

**Date:** 2026-09-16

**Decision.** Timeouts, connection errors, and 5xx are retried. A 4xx is not, and neither is a 404 or a 401.

**Why.** Retrying a 401 is asking the same question and expecting a different answer. It also matters for the breaker: a wrong key is our problem, and letting it trip the circuit would stop every later request for a reason that has nothing to do with the carrier's health. A test asserts a bad secret leaves the circuit closed.

**Consequences.** Every attempt signs afresh, with its own timestamp and nonce. Reusing them would make a retry fail as stale or as a replay — a failure that has nothing to do with why the first attempt failed.

---

### D-124: A retried call is one failure, not several

**Date:** 2026-09-16

**Decision.** The breaker records one failure per logical call, after the retries are exhausted.

**Why.** Counting each attempt would trip a five-failure circuit on the second bad request, which is the opposite of what retries are for. The breaker is asking "is this service healthy", and one call that failed three ways is one piece of evidence.

---

### D-125: The three layers only work in order

**Date:** 2026-09-16

**Decision.** A read timeout, then retries, then a circuit breaker.

**Why.** Each one is useless without the one before. Without the **timeout** nothing ever fails, so retries never fire and the breaker never trips — a hung connection just waits. Without the **breaker** the retries make an outage worse: every request spends its whole timeout budget three times over, so a dead supplier becomes a slow application, and the retries arrive exactly as the supplier tries to recover. The jitter on the backoff is part of that: without it every client that failed together retries together.

---

### D-126: The tool takes an order number, not a tracking number

**Date:** 2026-09-16

**Decision.** `track_shipment(order_number)` looks the tracking number up from an order the customer owns, after asking the policy engine.

**Why.** A tool that accepted a tracking number would report on any parcel in the carrier's system to anybody who could guess one — and tracking numbers are sequential. The identifier the model is allowed to supply is the one whose ownership can be checked.

---

### D-127: A carrier failure is a sentence, not an exception

**Date:** 2026-09-16

**Decision.** `track_shipment` catches `CarrierUnavailable` and returns a fixed line telling the agent to say the tracking information is temporarily unavailable.

**Why.** The agent's job at that moment is to say something true to a customer, and "we cannot reach the carrier" is true. Letting the exception through would make the model interpret a stack trace, and the failure would end the turn instead of producing a reply.

---

### D-128: An in-process transport cannot test a timeout

**Date:** 2026-09-16

**Decision.** The hang test runs against a real TCP listener that accepts and never answers. Everything else uses httpx's `ASGITransport`.

**Why.** Found by the test failing. `ASGITransport` calls the application directly in the same process, and an httpx timeout is a network timeout — there is no socket to give up on, so a handler that sleeps simply sleeps and the client waits with it. The one property that most needed testing was the one the convenient transport could not test.

**Consequences.** The listener releases its handler on teardown through an event. Left to sleep, closing the server waits for it, and the test paid thirty seconds on the way out instead of one and a half.
