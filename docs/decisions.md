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
