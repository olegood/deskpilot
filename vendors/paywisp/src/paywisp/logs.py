"""Logging for both services."""

import logging


def configure_logging() -> None:
    """Show the service's own log lines, not only uvicorn's.

    uvicorn configures its own loggers and leaves the root logger alone, so without
    this everything below WARNING from the application is silently dropped, and
    warnings arrive with no level or source (Deskpilot's D-145).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:     %(name)s: %(message)s",
    )
