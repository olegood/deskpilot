# Paywisp runs as two containers built from this one image: the authorization
# server and, from the next step, the MCP server. They share code the way two
# services in one company's repository would, and nothing with Deskpilot.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY src ./src
RUN uv sync --locked --no-dev

EXPOSE 8200 8210
CMD ["uv", "run", "python", "-m", "paywisp.auth_server"]
