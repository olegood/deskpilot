# ShipTrack runs as its own container, with its own dependencies and its own
# secret. Nothing from the Deskpilot image is available to it, which is the point.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY src ./src
RUN uv sync --locked --no-dev

EXPOSE 8100
CMD ["uv", "run", "python", "-m", "shiptrack"]
