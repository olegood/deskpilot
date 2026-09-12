# Development guide

Daily workflow for working on Deskpilot.

> Last verified against: milestone 1, step 1.4.

## Current layout

```
deskpilot/
├── docker-compose.yml         # local infrastructure (PostgreSQL)
├── .env.example               # template for the repo-root .env, read by Compose
├── infra/
│   └── postgres/init/         # SQL run once when the database volume is created
├── backend/
│   ├── pyproject.toml         # project metadata, tool configuration
│   ├── uv.lock                # exact dependency versions (committed)
│   ├── .python-version        # Python 3.13
│   ├── .env.example           # template for backend/.env (git-ignored)
│   ├── alembic.ini            # Alembic configuration (no database URL)
│   ├── migrations/            # Alembic environment and revisions
│   ├── src/deskpilot/
│   │   ├── config.py          # typed settings
│   │   ├── llm.py             # chat model factory, the only provider-aware module
│   │   ├── cli.py             # `deskpilot` command-line interface
│   │   └── db/                # models, sessions, seed data
│   └── tests/
│       ├── conftest.py        # shared fixtures, e.g. environment isolation
│       ├── unit/              # fast, no external services
│       └── integration/       # real services, skipped by default
├── scripts/
│   └── ollama-serve.sh
└── docs/
```

## Everyday commands

Run from `backend/`:

| Task                              | Command                                      |
|-----------------------------------|----------------------------------------------|
| Install or update the environment | `uv sync`                                    |
| Run unit tests                    | `uv run pytest`                              |
| Run one test file                 | `uv run pytest tests/unit/test_config.py`    |
| Run tests matching a name         | `uv run pytest -k anthropic`                 |
| Run integration tests             | `uv run pytest -m integration`               |
| Integration tests with timings    | `uv run pytest -m integration --durations=0` |
| Lint                              | `uv run ruff check .`                        |
| Auto-fix lint issues              | `uv run ruff check . --fix`                  |
| Format                            | `uv run ruff format .`                       |
| Type check                        | `uv run mypy src`                            |
| Apply migrations                  | `uv run alembic upgrade head`                |
| Reseed the development database   | `uv run deskpilot db seed --reset`           |
| Show all CLI commands             | `uv run deskpilot --help`                    |

Infrastructure commands run from the repo root: `docker compose up -d` to start, `docker compose ps` to check health.
See the [database guide](database.md) for more.

## Tests

- **Unit tests** (`tests/unit/`) are fast and need no external services. They run by default.
- **Integration tests** (`tests/integration/`) are marked `@pytest.mark.integration` and need real services. They're
  skipped by default and run with `-m integration`. They need Ollama running (`./scripts/ollama-serve.sh`) with the
  agent model from `backend/.env` pulled, and PostgreSQL running (`docker compose up -d`). They fail with a clear
  message if a service is missing. Run a subset with `-k`, for example `uv run pytest -m integration -k database`.
- **LLM tests are nondeterministic.** Even at temperature 0, a model can occasionally answer differently. A single
  integration failure is worth rerunning once; repeated failures are real findings. Graph logic is tested with scripted
  fake models in unit tests instead (from milestone 1, step 1.5).
- Async tests need no decorator: `asyncio_mode = "auto"` is set in `pyproject.toml`.
- Unit tests must not depend on your local `backend/.env` or shell. `tests/conftest.py` clears `DESKPILOT_*` variables
  for every test and sets a placeholder database password, and settings in unit tests are built with `_env_file=None`.
  Integration tests deliberately read `backend/.env`, so they test the model and database you actually configured.
- Database integration tests use the separate `deskpilot_test` database and never touch development data. See
  the [database guide](database.md#integration-tests-and-the-test-database).

## Dependencies

Always add dependencies through uv, never by editing `pyproject.toml` by hand:

```bash
uv add some-package          # runtime dependency
uv add --dev some-tool       # development-only dependency
uv remove some-package
```

Commit `pyproject.toml` and `uv.lock` together. After pulling changes, run `uv sync`.

If you import a package directly, declare it directly, even if another dependency already pulls it in.

## Code rules

- **Types everywhere.** mypy runs in strict mode on `src/`.
- **Async for I/O.** Network and database calls are async. Ruff's `ASYNC` rules catch blocking calls inside coroutines.
- **No secrets in code.** Secrets come from settings, typed as `SecretStr` so they never appear in logs or reprs.
- **Configuration through settings only.** No hardcoded URLs, model names, or credentials outside `config.py` defaults.
- **Providers only in `llm.py`.** Other modules get models from `build_chat_model(role)` and never import
  `langchain_ollama` or `langchain_anthropic`.
- **Explicit loading.** Relationships use `lazy="raise"`, so queries load what they need with `selectinload(...)`.
- **Schema changes need a migration.** See [changing the schema](database.md#changing-the-schema).

## Definition of done for a step

1. Unit tests pass, and integration tests pass where the step touches external services.
2. `ruff check`, `ruff format --check`, and `mypy src` report no issues.
3. Docs are updated: guides, configuration reference, roadmap checkbox, and a decision entry if a design choice was
   made.
4. `git status` shows no secrets or local files staged.

## Commits

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org):

| Type       | For                                     |
|------------|-----------------------------------------|
| `feat`     | New functionality                       |
| `fix`      | Bug fixes                               |
| `docs`     | Documentation only                      |
| `test`     | Tests only                              |
| `refactor` | Code changes that don't change behavior |
| `chore`    | Tooling, dependencies, configuration    |

Example: `feat(tools): add get_order tool with ownership check`.