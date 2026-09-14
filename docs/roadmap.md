# Roadmap

Each milestone ends with something runnable, tested, and documented.

## Milestones

| # | Milestone | Status |
|---|---|---|
| 1 | Foundation: uv, config, model factory, Postgres, seeded shop, minimal ReAct graph | **Done** |
| 2 | Tools and state: the full read-only tool set, typed tickets, checkpointing, conversation memory | In progress |
| 3 | Auth: bcrypt passwords, JWT access and refresh tokens with rotation | Planned |
| 4 | ABAC: policy engine, audit log, principal injection into tools | Planned |
| 5 | Web API and frontend shell: endpoints, SSE streaming, login, customer portal | Planned |
| 6 | ShipTrack integration: signed REST client, webhooks, retries, circuit breaker, chaos | Planned |
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
- [ ] **2.2 Policy knowledge base.** pgvector, embeddings, and a `search_policy` tool.
- [ ] **2.3 The rest of the read-only tools.** `get_customer` and `list_orders`.
- [ ] **2.4 Classification.** A node ahead of the loop that tags each ticket and shapes which tools the agent gets.
- [ ] **2.5 Tool-selection evals.** A small scripted dataset with expected tool calls, as a dry run for milestone 12.
- [ ] **2.6 Wrap-up.** Docs pass and a demo.

## Post-production backlog

Deliberately deferred. None of these block the main roadmap.

- **Keycloak as Paywisp's authorization server.** Validated by rerunning the auth server contract tests.
- **Dynamic client registration or client metadata documents.** Lets Deskpilot connect to MCP servers it wasn't pre-registered with.
- **Cedar policies via cedarpy.** Moves ABAC rules from Python code into reviewable data.
- **EdDSA-signed JWTs.** Only needed if other services must verify Deskpilot tokens without a shared secret.
- **Langfuse or OpenTelemetry with Jaeger.** A real trace UI alongside or instead of JSONL tracing.
- **Pre-call token estimation with Anthropic's `count_tokens`.** Enforces budgets before a call, not only after.
