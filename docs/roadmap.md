# Roadmap

Each milestone ends with something runnable, tested, and documented.

## Milestones

| # | Milestone | Status |
|---|---|---|
| 1 | Foundation: uv, config, model factory, Postgres, seeded shop, minimal ReAct graph | **Done** |
| 2 | Tools and state: the full read-only tool set, typed tickets, checkpointing, conversation memory | **Done** |
| 3 | Auth: bcrypt passwords, JWT access and refresh tokens with rotation | **Done** |
| 4 | ABAC: policy engine, audit log, principal injection into tools | **Done** |
| 5 | Web API and frontend shell: endpoints, SSE streaming, login, customer portal | **Done** |
| 6 | ShipTrack integration: signed REST client, webhooks, retries, circuit breaker, chaos | **Done** |
| 7 | Paywisp MCP: auth server, MCP server, service-account reads, allowlist, schema pinning | Planned |
| 8 | Delegated OAuth: Connections page, PKCE flow, token vault | Planned |
| 9 | Human-in-the-loop via web: approvals under the reviewer's token, idempotency | Planned |
| 10 | Security: injection guard, MCP threats, output sanitization, red-team set | Planned |
| 11 | Observability: cross-service tracing, token and cost accounting | Planned |
| 12 | Evals: dataset, scorers, LLM judge, two-phase runner, local model comparison | Planned |
| 13 | Anthropic switch: flip provider, rerun evals, compare | Planned |
| 14 | End-to-end: Playwright suite for the full flow | Planned |

## Milestone 1: Foundation

- [x] **1.1 Toolchain and Ollama.** Homebrew tools, native Ollama with a versioned start script, models pulled.
- [x] **1.2 Backend skeleton and config.** `.gitignore`, uv project, typed settings with per-role provider switch, unit tests, docs.
- [x] **1.3 Model factory.** Build a chat model per role; integration smoke tests of a real tool call, tool round trip, and token usage.
- [x] **1.4 Postgres and the shop.** Docker Compose, SQLAlchemy models, Alembic migration, deterministic seed data, `deskpilot db seed` CLI, integration tests on a separate test database.
- [x] **1.5 First tool and minimal ReAct graph.** `get_order` scoped to the acting customer, a hand-built loop with a step budget and tool-error handling, identity injected outside the model, `deskpilot ask`, and graph tests driven by a scripted model.
- [x] **1.6 Wrap-up.** Project README, `scripts/check.sh`, and a docs pass with every cross-link verified.

## Milestone 2: tools and state

- [x] **2.1 Tickets and checkpointing.** A `tickets` table, `AsyncPostgresSaver`, one LangGraph thread per ticket, multi-turn conversations that survive a restart, and `deskpilot ticket new / reply / list / show`.
- [x] **2.2 Policy knowledge base.** pgvector, markdown policy documents chunked at headings, embeddings with task prefixes, a `search_policy` tool, and `deskpilot policy index / status / search`.
- [x] **2.3 The rest of the read-only tools.** `get_customer`, which takes no arguments at all, and `list_orders` with a typed status filter and honest truncation.
- [x] **2.4 Classification.** A structured-output node ahead of the loop that labels each ticket once, feeds the label to the agent as a hint, and records it on the ticket row.
- [x] **2.5 Tool-selection evals.** A 16-case dataset with expected categories, required and forbidden tools, and answer checks; a concurrent runner that saves transcripts; `deskpilot eval list / run`.
- [x] **2.6 Wrap-up.** README refreshed, docs pass with every cross-link verified.

## Milestone 3: authentication

- [x] **3.1 Users and passwords.** A `users` table separate from customers, bcrypt hashing with the 72-byte limit enforced rather than hidden, a password policy, timing-equal login failures, and `deskpilot auth register / check / passwd`.
- [x] **3.2 Tokens.** Minimal-claim JWT access tokens with the algorithm pinned, opaque refresh tokens stored as digests, rotation on every use, family-wide revocation on reuse, and `deskpilot auth login`.
- [x] **3.3 Sessions.** A session saved under `~/.deskpilot` with owner-only permissions, transparent refresh, `deskpilot auth login / whoami / logout`, and `--as` demoted to an opt-in escape hatch.
- [x] **3.4 Wrap-up.** Account lockout with exponential backoff, and a docs pass.

## Milestone 4: authorization

- [x] **4.1 The principal and the policy engine.** Attributes on accounts, typed principals, actions and resources, deny-by-default rules in order, an exhaustive decision matrix, and `deskpilot auth grant / can`.
- [x] **4.2 The audit log.** An `audit_log` table written in its own transaction, denials always recorded and consequential allows too, auth events alongside decisions, and `deskpilot audit tail`.
- [x] **4.3 Principal injection.** `AgentContext` carries a `Principal`, every tool calls `guard`, and a cross-customer attempt is refused by the policy and recorded as evidence.
- [x] **4.4 Wrap-up.** Administration commands authorized, a bootstrap exception for the first account, and a docs pass.

## Milestone 5: the web API and the frontend shell

- [x] **5.1 The API and authentication over HTTP.** A FastAPI app, security headers, CORS, one error shape, the access token in the body and the refresh token in an httpOnly cookie, CSRF on refresh, rate limiting by source address, and `deskpilot serve`.
- [x] **5.2 Ticket endpoints.** Opening, replying, listing and reading, each asking the same policy engine the tools ask, with the agent graph built once for the process.
- [x] **5.3 Streaming.** Agent progress over SSE: category, tool calls, the answer a token at a time, and the ticket saved when the stream finishes.
- [x] **5.4 The React shell.** Vite with strict TypeScript, types generated from the OpenAPI document, an access token held in memory with single-flight refresh, a hand-written SSE reader, login and the customer portal.
- [x] **5.5 Wrap-up.** A test that the committed OpenAPI document matches the app, README refreshed, and a docs pass.

## Milestone 6: the ShipTrack integration

- [x] **6.1 The carrier.** A separate service with its own project, secret and data: HMAC-signed requests covering method, path, timestamp, nonce and body digest, replay protection, and chaos knobs for latency, errors and hangs.
- [x] **6.2 The client.** Signed requests from Deskpilot, a read timeout that survives a hang, retries only for what might succeed later, a per-service circuit breaker, a `track_shipment` tool, and contract tests against the real carrier.
- [x] **6.3 Webhooks.** ShipTrack calls back when a parcel moves, signed with a separate secret, verified before the body is parsed, replay-protected, and checked end to end against the real carrier.
- [x] **6.4 Wrap-up.** Fixed the carrier never reaching the API or the evals, added wiring tests, and a docs pass.

## Post-production backlog

Deliberately deferred. None of these block the main roadmap.

- **Keycloak as Paywisp's authorization server.** Validated by rerunning the auth server contract tests.
- **Dynamic client registration or client metadata documents.** Lets Deskpilot connect to MCP servers it wasn't pre-registered with.
- **Cedar policies via cedarpy.** Moves ABAC rules from Python code into reviewable data.
- **EdDSA-signed JWTs.** Only needed if other services must verify Deskpilot tokens without a shared secret.
- **Langfuse or OpenTelemetry with Jaeger.** A real trace UI alongside or instead of JSONL tracing.
- **Pre-call token estimation with Anthropic's `count_tokens`.** Enforces budgets before a call, not only after.
