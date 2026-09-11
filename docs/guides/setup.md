# Setup guide

Sets up a development machine from zero. Follow the steps in order.

> Last verified against: milestone 1, step 1.3.

## Target machine

Developed on macOS with Apple Silicon (M4 Pro, 48 GB unified memory). The default agent model needs roughly 24 GB.
Machines with 32 GB or less should use the smaller model described in [the Ollama guide](ollama.md#smaller-machines).

## 1. Install tools

Install [Homebrew](https://brew.sh) if you don't have it, then:

```bash
brew install git uv ollama
```

Install **Docker Desktop** or **OrbStack** (lighter on macOS). It's needed from milestone 1 step 4, for PostgreSQL and
the fake vendors. In its settings, limit memory to about 6 GB, so the local model has room.

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

## 5. Set up the backend

```bash
cd backend
uv sync
cp .env.example .env
```

`uv sync` installs the Python version pinned in `.python-version` (if missing) and every dependency exactly as recorded
in `uv.lock`.

The defaults in `.env.example` work as is. See the [configuration reference](../reference/configuration.md) for all
options.

## 6. Verify

From `backend/`, with Ollama running:

```bash
uv run pytest                     # unit tests
uv run pytest -m integration      # real model: tool call, tool round trip, token usage
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

All five should pass with no errors. The integration tests take longer the first time, while the model loads.

## What should be running

| Process | Where            | How                         |
|---------|------------------|-----------------------------|
| Ollama  | Host, port 11434 | `./scripts/ollama-serve.sh` |

This table grows as later milestones add services.

## Troubleshooting

**`ollama ps` shows a CPU percentage.** Part of the model didn't fit in GPU memory. Close memory-heavy apps, check
Docker's memory limit, or reduce `OLLAMA_NUM_PARALLEL`. See [the Ollama guide](ollama.md#troubleshooting).

**`address already in use` when starting Ollama.** Another Ollama instance is running. See step 3.

**`ollama pull` says the model doesn't exist.** Model tags change over time.
Check [ollama.com/library](https://ollama.com/library) for the current tag and update `backend/.env`.

**Tests fail with a settings error.** Check your shell for leftover `DESKPILOT_*` variables with `env | grep DESKPILOT`.
Unit tests clear these, but integration tests and other commands read them.

**Integration tests say Ollama is not reachable.** Start `./scripts/ollama-serve.sh` and check
`DESKPILOT_OLLAMA_BASE_URL` in `backend/.env`.

**An integration test fails once, then passes.** LLM output varies slightly even at temperature 0. Repeated failures of
the same test are a real problem worth reporting.