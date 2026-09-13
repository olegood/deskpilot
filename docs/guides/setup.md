# Setup guide

Sets up a development machine from zero. Follow the steps in order.

> Last verified against: milestone 1 (complete).

## Target machine

Developed on macOS with Apple Silicon (M4 Pro, 48 GB unified memory). The default agent model needs roughly 24 GB.
Machines with 32 GB or less should use the smaller model described in [the Ollama guide](ollama.md#smaller-machines).

## 1. Install tools

Install [Homebrew](https://brew.sh) if you don't have it, then:

```bash
brew install git uv ollama
```

Install **Docker Desktop** or **OrbStack** (lighter on macOS). It runs PostgreSQL now and the fake vendors later. In its
settings, limit memory to about 6 GB, so the local model has room.

Node.js and pnpm are needed from milestone 5 and will be added to this guide then.

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

## 5. Choose a database password

Generate a local password:

```bash
openssl rand -hex 16
```

Create both `.env` files from their templates, and put the same generated value in each:

```bash
cp .env.example .env                  # repo root: set POSTGRES_PASSWORD
cp backend/.env.example backend/.env  # backend: set DESKPILOT_DATABASE__PASSWORD
```

Both files are git-ignored.

## 6. Start PostgreSQL

From the repo root:

```bash
docker compose up -d
docker compose ps                     # STATUS should show (healthy)
```

The first start also creates the `deskpilot_test` database used by integration tests.

## 7. Set up the backend

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run deskpilot db seed
```

`uv sync` installs the Python version pinned in `.python-version` (if missing) and every dependency exactly as recorded
in `uv.lock`. `alembic upgrade head` creates the tables, and `deskpilot db seed` loads the Acme Gear sample data.

Apart from the password, the defaults in `backend/.env.example` work as is. See
the [configuration reference](../reference/configuration.md) for all options.

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
uv run deskpilot ask "Hi, where is my order ORD-1042?" --as noah.kim@example.com --verbose
```

It should report that the order has shipped, and the verbose line should show that
`get_order` was called. See the [agent guide](agent.md) for more to try.

## What should be running

| Process    | Where             | How                         |
|------------|-------------------|-----------------------------|
| Ollama     | Host, port 11434  | `./scripts/ollama-serve.sh` |
| PostgreSQL | Docker, port 5432 | `docker compose up -d`      |

This table grows as later milestones add services.

## Troubleshooting

**`ollama ps` shows a CPU percentage.** Part of the model didn't fit in GPU memory. Close memory-heavy apps, check
Docker's memory limit, or reduce `OLLAMA_NUM_PARALLEL`. See [the Ollama guide](ollama.md#troubleshooting).

**`address already in use` when starting Ollama.** Another Ollama instance is running. See step 3.

**`ollama pull` says the model doesn't exist.** Model tags change over time.
Check [ollama.com/library](https://ollama.com/library) for the current tag and update `backend/.env`.

**Tests fail with a settings error.** Check your shell for leftover `DESKPILOT_*` variables with `env | grep DESKPILOT`.
Unit tests clear these, but integration tests and other commands read them.

**Database errors.** See [troubleshooting in the database guide](database.md#troubleshooting).

**Integration tests say Ollama is not reachable.** Start `./scripts/ollama-serve.sh` and check
`DESKPILOT_OLLAMA_BASE_URL` in `backend/.env`.

**An integration test fails once, then passes.** LLM output varies slightly even at temperature 0. Repeated failures of
the same test are a real problem worth reporting.
