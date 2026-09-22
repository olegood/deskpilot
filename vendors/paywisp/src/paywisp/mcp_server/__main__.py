"""Running the MCP server. `uv run python -m paywisp.mcp_server`."""

import uvicorn

from paywisp.logs import configure_logging
from paywisp.mcp_server.config import Settings


def main() -> None:
    configure_logging()
    settings = Settings()
    uvicorn.run(
        "paywisp.mcp_server.server:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
