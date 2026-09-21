"""Running the authorization server. `uv run python -m paywisp.auth_server`."""

import logging

import uvicorn

from paywisp.auth_server.config import Settings


def configure_logging() -> None:
    """Show the service's own log lines, not only uvicorn's.

    uvicorn configures its own loggers and leaves the root logger alone, so without
    this everything below WARNING from the application is silently dropped, and
    warnings arrive with no level or source.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:     %(name)s: %(message)s",
    )


def main() -> None:
    configure_logging()
    settings = Settings()
    uvicorn.run(
        "paywisp.auth_server.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
