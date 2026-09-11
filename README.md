# Deskpilot

A production-style AI support agent for **Acme Gear**, a fictional online shop.

Customers submit support tickets. The agent investigates them with tools (orders,
shipping, refund policy, payments), proposes actions, and pauses for human approval
before anything that costs money or contacts the customer.

The project exists to demonstrate, end to end, what a real agent system needs:
multiple tools, persistent state, error handling, timeouts, human-in-the-loop,
ABAC and JWT authentication, REST and OAuth-protected MCP integrations,
prompt-injection defenses, tracing, token accounting, tests, and evals.

**Stack:** Python 3.13, uv, LangGraph, LangChain, FastAPI, PostgreSQL,
Ollama (local models) with a switch to Anthropic, React and TypeScript.

> **Status:** Milestone 1 (Foundation) in progress. See the [roadmap](docs/roadmap.md).

## Documentation

- [Set up your environment](docs/guides/setup.md)
- [Architecture overview](docs/architecture/overview.md)
- [All documentation](docs/README.md)

## License

[MIT](LICENSE)