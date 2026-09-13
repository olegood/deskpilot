# Deskpilot documentation

## Contents

| Document                                              | What it covers                                                       |
|-------------------------------------------------------|----------------------------------------------------------------------|
| [Roadmap](roadmap.md)                                 | Milestones, current progress, and the post-production backlog        |
| [Decisions](decisions.md)                             | Every significant design decision, with its reasons and consequences |
| [Architecture overview](architecture/overview.md)     | Components, ticket flow, authentication patterns, security layers    |
| [Setup guide](guides/setup.md)                        | Setting up a development machine from zero                           |
| [Ollama guide](guides/ollama.md)                      | Running local models: settings, memory, troubleshooting              |
| [Development guide](guides/development.md)            | Daily workflow: tests, linting, dependencies, commits                |
| [Database guide](guides/database.md)                  | PostgreSQL, migrations, schema conventions, seed data                |
| [Agent guide](guides/agent.md)                        | The graph, tools, where identity lives, and how to run the agent     |
| [Configuration reference](reference/configuration.md) | Every setting and environment variable                               |

## Where to start

- **New to the project:** read the [architecture overview](architecture/overview.md), then follow
  the [setup guide](guides/setup.md).
- **Setting up a machine:** follow the [setup guide](guides/setup.md) top to bottom.
- **Changing a setting:** see the [configuration reference](reference/configuration.md).
- **Changing the database schema:** see the [database guide](guides/database.md#changing-the-schema).
- **Adding a tool or changing the loop:** see the [agent guide](guides/agent.md).
- **Wondering why something is built a certain way:** check the [decisions](decisions.md).

## Conventions

- **Docs change with code.** A change that affects setup, configuration, or behavior updates the relevant doc in the
  same commit.
- **Guides are followed top to bottom.** Each guide works on a fresh machine without skipping around, and says which
  milestone it was last verified against.
- **Decisions are recorded, not silently changed.** Reversing a decision adds a new entry
  to [decisions.md](decisions.md) that references the old one.
- **Docs describe what exists.** Planned features belong in the [roadmap](roadmap.md) or are clearly marked as planned.
- **No secrets.** Never put real keys, tokens, or passwords in docs, even expired ones. Use placeholders like
  `sk-ant-...`.
