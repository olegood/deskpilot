# Configuration reference

All Deskpilot backend settings, defined in `backend/src/deskpilot/config.py`.

> Last verified against: milestone 2, step 2.2.

## How settings are loaded

Each setting is read from, in order of priority:

1. Environment variables
2. `backend/.env`
3. Defaults in `config.py`

All variables start with `DESKPILOT_`. Nested settings use a double underscore: `DESKPILOT_AGENT__MODEL` sets the `model` field of the `agent` settings.

Overriding one nested field keeps the other fields of that role at their role-specific defaults. For example, setting only `DESKPILOT_GUARD__MODEL` leaves the guard's 256-token output limit in place.

Invalid configuration fails at startup, not on first use.

## General

| Variable | Default | Description |
|---|---|---|
| `DESKPILOT_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama server URL. Must be a valid HTTP URL. |
| `DESKPILOT_ANTHROPIC_API_KEY` | unset | Anthropic API key. Required if any role uses the `anthropic` provider. Never logged. |
| `DESKPILOT_MAX_AGENT_STEPS` | `6` | How many times the agent may call the model in one run. When the budget runs out, the run ends with an escalation message instead of looping. See [D-024](../decisions.md#d-024-the-agent-loop-is-bounded-by-a-step-budget). |

## Model roles

Four roles are configured independently: `AGENT`, `CLASSIFIER`, `GUARD`, and `JUDGE`. Each supports these fields, set as `DESKPILOT_<ROLE>__<FIELD>`:

| Field | Type | Description |
|---|---|---|
| `PROVIDER` | `ollama` or `anthropic` | Which provider serves this role. |
| `MODEL` | string | Model name, e.g. `qwen3.6:35b` or `claude-sonnet-5`. |
| `TEMPERATURE` | 0.0–1.0 | Sampling temperature. |
| `MAX_OUTPUT_TOKENS` | integer > 0 | Maximum tokens per response. |
| `TIMEOUT_S` | number > 0 | HTTP timeout for one model call, in seconds. |
| `NUM_CTX` | integer ≥ 2048 | Context window. Ollama only; see [silent truncation](../guides/ollama.md#things-that-bite). |
| `REASONING` | `true` or `false` | Thinking mode. Always explicit, see [D-015](../decisions.md#d-015-thinking-mode-is-always-explicit-and-off-by-default). Not yet supported for `anthropic`. |

### Defaults per role

| Field | Agent | Classifier | Guard | Judge |
|---|---|---|---|---|
| `PROVIDER` | `ollama` | `ollama` | `ollama` | `ollama` |
| `MODEL` | `qwen3.6:35b` | `qwen3.6:35b` | `qwen3.6:35b` | `gpt-oss:20b` |
| `TEMPERATURE` | 0.0 | 0.0 | 0.0 | 0.0 |
| `MAX_OUTPUT_TOKENS` | 2048 | 64 | 256 | 1024 |
| `TIMEOUT_S` | 120 | 30 | 30 | 300 |
| `NUM_CTX` | 32768 | 32768 | 32768 | 32768 |
| `REASONING` | `false` | `false` | `false` | `false` |

The classifier's 64-token limit is deliberate: it answers with one word inside a
JSON object, and a longer answer means it ignored the schema.

### How fields map to providers

| Field | Ollama (`ChatOllama`) | Anthropic (`ChatAnthropic`) |
|---|---|---|
| `MODEL` | `model` | `model` |
| `TEMPERATURE` | `temperature` | `temperature` |
| `MAX_OUTPUT_TOKENS` | `num_predict` | `max_tokens` |
| `TIMEOUT_S` | HTTP client timeout | `timeout` |
| `NUM_CTX` | `num_ctx` | ignored |
| `REASONING` | `reasoning` (sent as `think`) | rejected when `true` |

## Tools

| Variable | Default | Description |
|---|---|---|
| `DESKPILOT_TOOLS__MAX_ORDERS_LISTED` | `10` | How many orders `list_orders` returns. A cap, not a tool argument, so the model cannot raise it. When it truncates, the tool says so. |

## Embeddings

| Variable | Default | Description |
|---|---|---|
| `DESKPILOT_EMBEDDINGS__MODEL` | `nomic-embed-text` | Embedding model. Always served by Ollama, since Anthropic has no embeddings API. |
| `DESKPILOT_EMBEDDINGS__DIMENSIONS` | `768` | Vector width. Must match both the model's output and the database column; changing it needs a migration. |
| `DESKPILOT_EMBEDDINGS__DOCUMENT_PREFIX` | `search_document: ` | Task prefix for indexed passages. Required by `nomic-embed-text`; omitting it silently worsens retrieval. |
| `DESKPILOT_EMBEDDINGS__QUERY_PREFIX` | `search_query: ` | Task prefix for questions. |
| `DESKPILOT_EMBEDDINGS__NUM_CTX` | `8192` | Context window. Ollama's card says 2048, but the model handles 8192; longer passages are otherwise truncated silently. |
| `DESKPILOT_EMBEDDINGS__TIMEOUT_S` | `60` | HTTP timeout for one embedding call. |

Model, document prefix, and dimensions together form the embedding fingerprint
stored with each passage. Changing any of them marks the policy index stale. See the
[policy knowledge base guide](../guides/policy-search.md).

## Policy search

| Variable | Default | Description |
|---|---|---|
| `DESKPILOT_POLICY_SEARCH__DIRECTORY` | `policies` | Where the markdown source documents live, relative to `backend/`. |
| `DESKPILOT_POLICY_SEARCH__TOP_K` | `4` | Passages returned per search. |
| `DESKPILOT_POLICY_SEARCH__MAX_DISTANCE` | `0.6` | Cosine distance above which a passage is treated as irrelevant. 0 is identical, 2 is opposite. |
| `DESKPILOT_POLICY_SEARCH__MAX_CHUNK_CHARS` | `1200` | Sections longer than this are split further, at paragraph boundaries. |

## Database

| Variable | Default | Description |
|---|---|---|
| `DESKPILOT_DATABASE__PASSWORD` | **required** | PostgreSQL password. Must match `POSTGRES_PASSWORD` in the repo-root `.env`. Never logged. |
| `DESKPILOT_DATABASE__HOST` | `127.0.0.1` | PostgreSQL host. |
| `DESKPILOT_DATABASE__PORT` | `5432` | PostgreSQL port. |
| `DESKPILOT_DATABASE__NAME` | `deskpilot` | Database name. Integration tests use `<name>_test`. |
| `DESKPILOT_DATABASE__USER` | `deskpilot` | Database user. |
| `DESKPILOT_DATABASE__POOL_SIZE` | `5` | Connections kept open per process. |
| `DESKPILOT_DATABASE__ECHO_SQL` | `false` | Log every SQL statement. Useful for debugging queries. |

Deskpilot connects with the async psycopg 3 driver (`postgresql+psycopg`). See the [database guide](../guides/database.md).

## Repo-root `.env` (Docker Compose)

Separate from `backend/.env`, and read only by Docker Compose.

| Variable | Description |
|---|---|
| `POSTGRES_PASSWORD` | Password for the `deskpilot` database user. Required; Compose refuses to start without it. Only applied when the data volume is first created. |

## Switching a role to Anthropic

Set the API key, the provider, and the model together:

```bash
DESKPILOT_ANTHROPIC_API_KEY=sk-ant-...
DESKPILOT_AGENT__PROVIDER=anthropic
DESKPILOT_AGENT__MODEL=claude-sonnet-5
```

Startup fails with a clear error if the key is missing, if the provider is `anthropic` but the model isn't a Claude model, or if `REASONING` is `true`. That catches a half-finished switch before any LLM call.
