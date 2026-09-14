# Database guide

Working with PostgreSQL, migrations, and seed data.

> Last verified against: milestone 2, step 2.2.

## Overview

| Item | Value |
|---|---|
| Server | PostgreSQL 18 with pgvector, in Docker Compose, bound to `127.0.0.1:5432` |
| Development database | `deskpilot` |
| Test database | `deskpilot_test`, used only by integration tests |
| User | `deskpilot` |
| Driver | psycopg 3 (async), via SQLAlchemy 2 |
| Migrations | Alembic, in `backend/migrations/` |
| Checkpoints | LangGraph's own tables, created by `deskpilot db setup` |
| Models | `backend/src/deskpilot/db/models.py` |

The password lives in two git-ignored files that must match: `POSTGRES_PASSWORD` in the repo-root `.env` (read by Compose) and `DESKPILOT_DATABASE__PASSWORD` in `backend/.env` (read by Deskpilot).

## Starting and stopping

From the repo root:

```bash
docker compose up -d          # start in the background
docker compose ps             # STATUS should show (healthy)
docker compose logs postgres  # server logs
docker compose stop           # stop, keep data
docker compose down -v        # stop and DELETE all data, including the test database
```

After `down -v`, the next `up -d` starts from an empty volume and recreates the test database automatically.

## Connecting with psql

```bash
docker compose exec postgres psql -U deskpilot -d deskpilot
```

Useful commands inside psql: `\dt` lists tables, `\d orders` describes a table, `\q` quits.

## Migrations

All commands run from `backend/`.

| Task | Command |
|---|---|
| Apply all migrations | `uv run alembic upgrade head` |
| Show the current revision | `uv run alembic current` |
| Show history | `uv run alembic history` |
| Roll back one migration | `uv run alembic downgrade -1` |
| Check models and migrations match | `uv run alembic check` |
| Preview SQL without running it | `uv run alembic upgrade head --sql` |

### Tables Alembic does not own

LangGraph stores every ticket's conversation in `checkpoints`, `checkpoint_blobs`,
`checkpoint_writes`, and `checkpoint_migrations`. It creates and migrates those
itself, so `migrations/env.py` excludes them from autogenerate. Without that filter
Alembic sees four unknown tables and writes `DROP TABLE` for each, which would
delete every conversation ([D-029](../decisions.md#d-029-alembic-is-told-to-ignore-langgraphs-tables)).

Create them with:

```bash
uv run deskpilot db setup
```

It is idempotent, so run it after pulling changes or upgrading LangGraph.

### Changing the schema

1. Change the models in `db/models.py`.
2. Generate a migration with the next sequential ID:
   ```bash
   uv run alembic revision --autogenerate --rev-id 0002 -m "short description"
   ```
3. **Read the generated file.** Autogenerate misses some changes (renames look like drop plus add, for example) and can't know about data migrations.
4. Apply it: `uv run alembic upgrade head`.
5. Run the integration tests. `test_migrations_match_models` fails if models and migrations drift apart.

Generated migrations are formatted and linted automatically by ruff, through post-write hooks in `alembic.ini`.

The database URL is never stored in `alembic.ini`. `migrations/env.py` reads it from Deskpilot settings.

## Schema conventions

- **Money is integer cents.** Floats can't represent amounts like 0.10 exactly.
- **Timestamps are timezone-aware** (`timestamptz`).
- **Enums are strings with a CHECK constraint**, not native PostgreSQL enums, which are awkward to change in migrations.
- **Constraint names are deterministic**, from a naming convention on the metadata, so migrations can refer to them.
- **Relationships use `lazy="raise"`.** In async code an implicit lazy load fails at runtime, so every query loads related rows explicitly, for example `selectinload(Order.items)`. Forgetting fails immediately and clearly instead of intermittently.
- **Orders have an internal `id` and a public `number`** (such as `ORD-1042`). Customers and the agent only ever see the number.
- **`orders.notes` is untrusted text** typed by customers at checkout. It will be treated as a prompt-injection vector.

See [D-017](../decisions.md#d-017-schema-conventions) for the reasons.

## Users

`users` is separate from `customers`: one is people who log in, the other is people
who bought something ([D-053](../decisions.md#d-053-users-are-a-separate-table-from-customers)).
`password_hash` is bcrypt output and is never logged; `User.__repr__` is overridden
so a stray traceback cannot carry it. See the [authentication guide](auth.md).

## Tickets

A ticket row is deliberately thin, because the conversation itself lives in the
checkpoint tables under `thread_id` and is not duplicated here
([D-027](../decisions.md#d-027-a-ticket-is-a-langgraph-thread-the-conversation-is-not-duplicated)).

| Column | Purpose |
|---|---|
| `reference` | What the customer sees and types, e.g. `TCK-0007` |
| `thread_id` | The key this ticket's checkpoints are stored under |
| `status` | `open`, `awaiting_customer`, `escalated`, or `resolved` |
| `category` | What the ticket is about, set by the classifier on the first turn. Nullable |
| `subject` | Written by the customer. Untrusted text, not yet shown to the model |

The reference and the thread id are deliberately different values
([D-028](../decisions.md#d-028-the-thread-id-is-separate-from-the-public-reference)).

`status` and `category` are projections of what the agent run decided: the graph's
state is the working copy, and these columns exist so tickets can be listed and
filtered without reading every checkpoint.

Ticket functions in `deskpilot/tickets.py` never commit; the caller owns the
transaction ([D-031](../decisions.md#d-031-services-do-not-manage-transactions)).

## Seed data

The seed is deterministic: the same customers, products, and orders every time, so tests, demos, and evals can refer to them by name.

```bash
uv run deskpilot db seed            # refuses if the database already has shop data
uv run deskpilot db seed --reset    # deletes all shop data, then seeds
```

### Customers

| Email | Name | Region | Tier |
|---|---|---|---|
| ana.garcia@example.com | Ana García | eu | gold |
| liam.oconnor@example.com | Liam O'Connor | eu | standard |
| maya.patel@example.com | Maya Patel | na | gold |
| noah.kim@example.com | Noah Kim | na | standard |
| sofia.rossi@example.com | Sofia Rossi | eu | standard |
| kenji.tanaka@example.com | Kenji Tanaka | apac | gold |
| chloe.martin@example.com | Chloe Martin | na | standard |
| aisha.rahman@example.com | Aisha Rahman | apac | standard |

### Orders

| Order | Customer | Status | Placed | Items | Tracking |
|---|---|---|---|---|---|
| ORD-1001 | Ana García | delivered | 2026-08-02 | tent, 2 headlamps | ST-100001 |
| ORD-1002 | Ana García | paid | 2026-09-08 | backpack | |
| ORD-1017 | Liam O'Connor | delivered | 2026-07-20 | rain jacket | ST-100017 |
| ORD-1023 | Maya Patel | shipped | 2026-09-03 | sleeping bag, stove | ST-100023 |
| ORD-1031 | Noah Kim | cancelled | 2026-08-25 | trekking poles | |
| ORD-1042 | Noah Kim | shipped | 2026-09-05 | backpack, 2 bottles | ST-100042 |
| ORD-1050 | Sofia Rossi | pending | 2026-09-10 | 2 bottles | |
| ORD-1063 | Kenji Tanaka | delivered | 2026-08-15 | tent | ST-100063 |
| ORD-1077 | Chloe Martin | shipped | 2026-08-20 | headlamp | ST-100077 |
| ORD-1088 | Aisha Rahman | paid | 2026-09-09 | rain jacket | |
| ORD-1095 | Maya Patel | delivered | 2026-08-28 | 2 trekking poles | ST-100095 |
| ORD-1101 | Liam O'Connor | shipped | 2026-09-07 | stove | ST-100101 |

`ORD-1042` is the reference order used in examples and smoke tests. `ORD-1077` has been in transit unusually long, and `ORD-1017` is an older delivery; both are useful for refund-policy scenarios later.

Currency follows the customer's region: EUR for eu, USD for na and apac. Product prices are the same number in every currency, a simplification for a fictional shop.

## Integration tests and the test database

Integration tests connect to `deskpilot_test`, downgrade it to an empty schema, and migrate it back to the latest revision once per test run. That proves every migration works in both directions. Tests that need data reseed with `reset=True`.

## Troubleshooting

**`POSTGRES_PASSWORD` is not set.** Create the repo-root `.env` from `.env.example`.

**`password authentication failed for user "deskpilot"`.** The two password files don't match, or the volume was created with an older password. PostgreSQL only reads `POSTGRES_PASSWORD` when initializing an empty volume. Either set the old password back, or reset with `docker compose down -v` (deletes all data).

**`database "deskpilot_test" does not exist`.** The volume was created before the init script existed. Create it manually:
```bash
docker compose exec postgres psql -U deskpilot -d deskpilot -c "CREATE DATABASE deskpilot_test OWNER deskpilot"
```

**Port 5432 is already in use.** Another PostgreSQL is running on your Mac, for example from Homebrew. Stop it (`brew services stop postgresql@17` or similar), or map Compose to another port and set `DESKPILOT_DATABASE__PORT` to match.

**`InvalidRequestError` mentioning `lazy='raise'`.** Code accessed a relationship that wasn't loaded. Add `selectinload(...)` to the query.

**`InvalidRequestError: A transaction is already begun on this Session`.** Something called `session.begin()` when a query had already opened a transaction implicitly. Services should not manage transactions; let the caller commit.

**`relation "checkpoints" does not exist`.** Run `uv run deskpilot db setup`.

**A generated migration wants to drop the checkpoint tables.** The `include_name` filter in `migrations/env.py` is missing or was bypassed. Never apply such a migration.

**`type "vector" does not exist`.** The database is running a plain `postgres` image rather than `pgvector/pgvector`. Check `docker-compose.yml`, then `docker compose up -d --force-recreate postgres`. Existing data survives, since only the image changes.

**`permission denied to create extension "vector"`.** `CREATE EXTENSION` needs a superuser. The `POSTGRES_USER` of the pgvector image is one, so this usually means the migration is running as a different user.
