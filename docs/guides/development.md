# Development guide

Daily workflow for working on Deskpilot.

> Last verified against: milestone 1, step 1.2.

## Current layout

```
deskpilot/
├── backend/
│   ├── pyproject.toml         # project metadata, tool configuration
│   ├── uv.lock                # exact dependency versions (committed)
│   ├── .python-version        # Python 3.13
│   ├── .env.example           # template for backend/.env (git-ignored)
│   ├── src/deskpilot/
│   │   └── config.py          # typed settings
│   └── tests/
│       └── unit/
├── scripts/
│   └── ollama-serve.sh
└── docs/
```

## Everyday commands

Run from `backend/`:

| Task                              | Command                                   |
|-----------------------------------|-------------------------------------------|
| Install or update the environment | `uv sync`                                 |
| Run unit tests                    | `uv run pytest`                           |
| Run one test file                 | `uv run pytest tests/unit/test_config.py` |
| Run tests matching a name         | `uv run pytest -k anthropic`              |
| Run integration tests             | `uv run pytest -m integration`            |
| Lint                              | `uv run ruff check .`                     |
| Auto-fix lint issues              | `uv run ruff check . --fix`               |
| Format                            | `uv run ruff format .`                    |
| Type check                        | `uv run mypy src`                         |

## Tests

- **Unit tests** (`tests/unit/`) are fast and need no external services. They run by default.
- **Integration tests** are marked `@pytest.mark.integration` and need real services such as Ollama or Postgres. They're
  skipped by default and run with `-m integration`.
- Async tests need no decorator: `asyncio_mode = "auto"` is set in `pyproject.toml`.
- Tests must not depend on your local `backend/.env` or shell. Settings tests clear `DESKPILOT_*` variables and pass
  `_env_file=None`.

## Dependencies

Always add dependencies through uv, never by editing `pyproject.toml` by hand:

```bash
uv add some-package          # runtime dependency
uv add --dev some-tool       # development-only dependency
uv remove some-package
```

Commit `pyproject.toml` and `uv.lock` together. After pulling changes, run `uv sync`.

## Code rules

- **Types everywhere.** mypy runs in strict mode on `src/`.
- **Async for I/O.** Network and database calls are async. Ruff's `ASYNC` rules catch blocking calls inside coroutines.
- **No secrets in code.** Secrets come from settings, typed as `SecretStr` so they never appear in logs or reprs.
- **Configuration through settings only.** No hardcoded URLs, model names, or credentials outside `config.py` defaults.

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