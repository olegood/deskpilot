# Setup guide

Sets up a development machine from zero. Follow the steps in order.

> Last verified against: milestone 2, step 2.2.

## Target machine

Developed on macOS with Apple Silicon (M4 Pro, 48 GB unified memory). The default agent model needs roughly 24 GB. Machines with 32 GB or less should use the smaller model described in [the Ollama guide](ollama.md#smaller-machines).

## 1. Install tools

Install [Homebrew](https://brew.sh) if you don't have it, then:

```bash
brew install git uv ollama
```

Install **Docker Desktop** or **OrbStack** (lighter on macOS). It runs PostgreSQL now and the fake vendors later. In its settings, limit memory to about 6 GB, so the local model has room.

For the frontend you also need Node and pnpm:

```bash
brew install node
npm install -g pnpm
```

## 2. Clone the repository

```bash
git clone git@github.com:olegood/deskpilot.git
cd deskpilot
```

## 3. Start Ollama

Make sure no other Ollama instance is running. This should print nothing:

```bash
lsof -i :11434
```

If it prints something, quit the Ollama desktop app or run `brew services stop ollama`.

Start Ollama in its own terminal tab and leave it running:

```bash
./scripts/ollama-serve.sh
```

## 4. Pull models

In another terminal tab:

```bash
ollama pull qwen3.6:35b
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

The first is a large download. Then verify:

```bash
ollama show qwen3.6:35b                          # capabilities must include "tools"
ollama run qwen3.6:35b "Reply with one word: ready"
ollama ps                                         # PROCESSOR should say 100% GPU
```

## 5. Choose the secrets

Create both `.env` files from their templates:

```bash
cp .env.example .env                  # repo root: read by Docker Compose
cp backend/.env.example backend/.env  # backend: read by Deskpilot
```

Both are git-ignored. Generate a value for each secret with `openssl rand -hex 32`.
Several are **shared between the two files**, because they are how Deskpilot and a
vendor recognise each other, and a mismatch shows up later as a refused signature
rather than as an error here:

| Repo-root `.env` | `backend/.env` | What it is |
|---|---|---|
| `POSTGRES_PASSWORD` | `DESKPILOT_DATABASE__PASSWORD` | The database password |
| `SHIPTRACK_SECRET` | `DESKPILOT_SHIPTRACK__SECRET` | Deskpilot signs carrier requests with it |
| `SHIPTRACK_WEBHOOK_SECRET` | `DESKPILOT_SHIPTRACK__WEBHOOK_SECRET` | The carrier signs its callbacks with it |
| `PAYWISP_AGENT_CLIENT_SECRET` | *(not read by Deskpilot yet)* | The agent's credential at Paywisp |

Each pair must hold the same value, and **no two pairs should share one**. The
ShipTrack request and callback secrets in particular are separate on purpose: a leak
of one should not buy the other.

`DESKPILOT_AUTH__JWT_SECRET` in `backend/.env` is Deskpilot's own and pairs with
nothing. It still needs a real value.

Compose refuses to start at all if a required secret is missing, including
PostgreSQL, so fill in the repo-root file before the next step.

## 6. Start PostgreSQL and the vendors

From the repo root:

```bash
docker compose up -d
docker compose ps                     # every STATUS should show (healthy)
```

That starts PostgreSQL, ShipTrack (the fake carrier) and Paywisp (the fake payment
processor), which is two containers: its authorization server and its MCP server. The first start also creates the
`deskpilot_test` database used by integration tests.

## 7. Set up the backend

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run deskpilot db setup
uv run deskpilot db seed
uv run deskpilot policy index
```

`uv sync` installs the Python version pinned in `.python-version` (if missing) and every dependency exactly as recorded in `uv.lock`. `alembic upgrade head` creates the application tables, `deskpilot db setup` creates LangGraph's checkpoint tables, `deskpilot db seed` loads the Acme Gear sample data, and `deskpilot policy index` embeds the policy documents so the agent can search them.

Apart from the password, the defaults in `backend/.env.example` work as is. See the [configuration reference](../reference/configuration.md) for all options.

## 8. Verify

From `backend/`, with Ollama and PostgreSQL running:

```bash
uv run pytest                     # unit tests
uv run pytest -m integration      # real model and real database
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

All five should pass with no errors. The integration tests take longer the first time, while the model loads.

Then try the agent:

```bash
uv run deskpilot ticket new "Hi, where is my order ORD-1042?" \
    --as noah.kim@example.com --subject "Where is my order" --verbose

uv run deskpilot ticket reply TCK-0001 "Thanks. What about ORD-1031?" \
    --as noah.kim@example.com
```

The first should report that the order has shipped and show that `get_order` was
called. The second is a separate process picking the same conversation back up.

Then try a policy question, which uses a different tool:

```bash
uv run deskpilot ask "How long do I have to return a tent?" \
    --as noah.kim@example.com --verbose
```

See the [agent guide](agent.md) for more to try.

## 9. Set up the frontend

```bash
cd ../frontend
pnpm install
pnpm types
```

`pnpm types` generates TypeScript from the backend's OpenAPI document. It needs
`src/api/openapi.json`, which the backend writes:

```bash
cd ../backend && uv run deskpilot openapi --out ../frontend/src/api/openapi.json
```

See the [frontend guide](frontend.md).

## What should be running

| Process | Where | How |
|---|---|---|
| Ollama | Host, port 11434 | `./scripts/ollama-serve.sh` |
| PostgreSQL | Docker, port 5432 | `docker compose up -d` |
| ShipTrack | Docker, port 8100 | `docker compose up -d` |
| Paywisp authorization server | Docker, port 8200 | `docker compose up -d` |
| Paywisp MCP server | Docker, port 8210 | `docker compose up -d` |
| Deskpilot API | Host, port 8000 | `uv run deskpilot serve --reload` |
| Frontend | Host, port 5173 | `pnpm dev` (in `frontend/`) |

This table grows as later milestones add services.

## Troubleshooting

**`ollama ps` shows a CPU percentage.** Part of the model didn't fit in GPU memory. Close memory-heavy apps, check Docker's memory limit, or reduce `OLLAMA_NUM_PARALLEL`. See [the Ollama guide](ollama.md#troubleshooting).

**`address already in use` when starting Ollama.** Another Ollama instance is running. See step 3.

**`ollama pull` says the model doesn't exist.** Model tags change over time. Check [ollama.com/library](https://ollama.com/library) for the current tag and update `backend/.env`.

**Tests fail with a settings error.** Check your shell for leftover `DESKPILOT_*` variables with `env | grep DESKPILOT`. Unit tests clear these, but integration tests and other commands read them.

**Database errors.** See [troubleshooting in the database guide](database.md#troubleshooting).

**`docker compose up` says a variable is required.** A secret is missing from the
repo-root `.env`. Compose checks them all before starting anything, which is why
PostgreSQL does not start either. See step 5.

**The carrier refuses every request, or every callback is rejected.** One side of a
shared secret does not match the other. See the table in step 5.

**Integration tests say Ollama is not reachable.** Start `./scripts/ollama-serve.sh` and check `DESKPILOT_OLLAMA_BASE_URL` in `backend/.env`.

**An integration test fails once, then passes.** LLM output varies slightly even at temperature 0. Repeated failures of the same test are a real problem worth reporting.
