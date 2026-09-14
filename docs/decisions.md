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
