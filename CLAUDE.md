# CLAUDE.md

Working notes for Claude Code on this repo. Read `docs/` for detail; this file is
only the rules and the current state.

## What this is

Deskpilot: a production-style AI support agent for Acme Gear, a fictional online
shop. The agent investigates tickets with tools, and later will propose actions that
a human approves before any money moves. The project is built milestone by
milestone, deliberately, as a learning exercise. **Favour the clear, correct,
well-documented version over the quick one.**

Full design: `docs/architecture/overview.md`. Why anything is the way it is:
`docs/decisions.md`.

## Current state

- **Milestone 1: complete.** Config, model factory, PostgreSQL, seeded shop,
  a hand-built ReAct loop, and the `get_order` tool.
- **Milestone 2: in progress.** Step 2.1 (tickets and checkpointing) is written but
  **not yet applied to this working tree**. See "Applying step 2.1" below.
- Next after that: 2.2 pgvector policy search, 2.3 more read-only tools,
  2.4 classification, 2.5 tool-selection evals, 2.6 wrap-up.

`docs/roadmap.md` is the authoritative checklist. Update it when a step lands.

## Layout

```
backend/          uv project: all Python. Run uv commands from here.
  src/deskpilot/
    config.py     typed settings; every knob lives here
    llm.py        the ONLY module that imports a model provider
    db/           SQLAlchemy models, sessions, seed data, checkpointer
    graph/        agent state, context, prompts, the loop, the runner
    tools/        what the agent can call
    tickets.py    ticket lifecycle
  tests/
    unit/         fast, no services
    graph/        the loop, driven by a scripted model, no LLM
    integration/  real Postgres and real Ollama; marked, skipped by default
  migrations/     Alembic
docs/             guides, reference, architecture, decision log
scripts/          check.sh, ollama-serve.sh
vendors/          fake ShipTrack and Paywisp services (milestone 6+, not built yet)
```

## Commands

```bash
./scripts/check.sh          # lockfile, ruff, format, mypy, fast tests
./scripts/check.sh --all    # the above plus integration tests
```

From `backend/`:

```bash
uv run pytest                       # unit + graph
uv run pytest -m integration        # needs Ollama + docker compose up -d
uv run alembic upgrade head
uv run deskpilot --help
```

Infrastructure from the repo root: `docker compose up -d` (PostgreSQL).
Ollama runs natively, not in Docker: `./scripts/ollama-serve.sh`.

## House rules

These are settled decisions. Don't quietly reverse one; if a change needs to,
say so and add an entry to `docs/decisions.md`.

1. **Identity never enters graph state or messages.** The acting customer travels in
   `AgentContext`, passed via `graph.ainvoke(..., context=...)` and injected into
   tools as `ToolRuntime`. This is what makes prompt injection unable to reach
   another customer's data. (D-020)
2. **Providers only in `llm.py`.** Everything else calls `build_chat_model(role)`.
   Never import `langchain_ollama` or `langchain_anthropic` elsewhere. (D-005)
3. **Services don't manage transactions.** Functions in `tickets.py` read and mutate;
   the command or request handler commits. A `session.begin()` inside a service
   crashes after any prior SELECT. (D-031)
4. **Alembic must not touch LangGraph's tables.** `checkpoints`, `checkpoint_blobs`,
   `checkpoint_writes`, `checkpoint_migrations` are filtered out in
   `migrations/env.py`. If a generated migration contains `drop_table("checkpoints")`,
   the filter is broken — do not apply it, it destroys every conversation. (D-029)
5. **Money is integer cents. Timestamps are timezone-aware.** Enums are strings with
   CHECK constraints, never native PostgreSQL enums. (D-017)
6. **Relationships use `lazy="raise"`.** Load explicitly with `selectinload(...)`.
7. **A tool answers the same for "doesn't exist" and "isn't yours".** Ownership is
   enforced in the SQL query, not after loading. (D-021)
8. **Untrusted customer text stays away from the model** until spotlighting exists
   in milestone 10. `orders.notes` and `tickets.subject` are not shown to it. (D-022)
9. **No secrets in code, logs, or docs.** Settings only, typed `SecretStr`.
10. **Deterministic logic gets deterministic tests.** Loop behaviour goes in
    `tests/graph/` with `ScriptedChatModel`, not through a real LLM.

## Gotchas that have already bitten

- **Message id collisions.** `add_messages` merges by id, so two messages sharing one
  become one. This silently caps the agent loop or makes a reply vanish. If a turn
  seems to disappear, check ids first.
- **Ollama truncates silently.** Exceeding the context window drops the oldest
  tokens with no error, and the system prompt goes first. `num_ctx` is always
  explicit.
- **`steps` resets each turn, token counters don't.** Different questions, different
  reducers. (D-030)

## Definition of done for a step

1. `./scripts/check.sh --all` passes.
2. Docs updated in the same commit: the relevant guide, the configuration reference
   if a setting changed, the roadmap checkbox, and a `docs/decisions.md` entry if a
   design choice was made.
3. Cross-links in `docs/` still resolve.
4. `git status` shows no `.env` or other secrets staged.
5. Commit with Conventional Commits, e.g. `feat(tickets): add multi-turn conversations`.

## Applying step 2.1

Step 2.1 adds tickets, PostgreSQL checkpointing, and multi-turn conversations. The
files are in `deskpilot-step-2.1.zip`. Apply in this order — step 5 must land before
step 6, or autogenerate writes `DROP TABLE` for LangGraph's tables.

1. `cd backend && uv add langgraph-checkpoint-postgres`
2. `db/models.py`: add `TicketStatus` above `OrderStatus`, add the `tickets`
   relationship to `Customer`, add the `Ticket` class before `Product`.
3. New file `db/checkpointer.py`.
4. New file `tickets.py`.
5. `migrations/env.py`: add `LANGGRAPH_TABLES` and `include_name`, and pass
   `include_name=include_name` in both `context.configure(...)` calls.
6. Migration `0002_add_tickets.py`, then `uv run alembic upgrade head`.
   Verify first: `grep -c drop_table migrations/versions/0002_add_tickets.py` → 0.
7. `graph/state.py`: `steps` loses its reducer, `escalated` is added.
8. `graph/agent.py`: `checkpointer` parameter, `steps: state["steps"] + 1`,
   `escalated: True` in `over_budget`, `compile(checkpointer=checkpointer)`.
9. Replace `graph/runner.py` (adds `run_turn`).
10. Replace `cli.py` (adds the `ticket` command group and `db setup`).
11. `tests/support.py`: add `run_id` and use it in both generated ids.
12. `tests/integration/conftest.py`: call `setup_checkpointer` in `test_database`.
13. `tests/graph/test_agent_graph.py`: three `escalated` assertions.
14. `tests/integration/test_agent_end_to_end.py`: `answer_question` → `run_agent`.
15. New tests: `tests/graph/test_multi_turn.py`,
    `tests/integration/test_tickets.py`.
16. Copy the `docs/` tree.
17. `uv run deskpilot db setup && uv run deskpilot db seed --reset`, then
    `./scripts/check.sh --all`. Expect 53 fast tests and 28 integration tests.

When it passes, update this file's "Current state" and tick 2.1 in
`docs/roadmap.md`.

## Keeping this file useful

Update "Current state" whenever a step lands, and add a house rule only when a
decision is genuinely repo-wide. Detail belongs in `docs/`; this file should stay
short enough that reading it costs nothing.
