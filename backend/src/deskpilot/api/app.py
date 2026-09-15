"""Building the FastAPI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deskpilot.api import errors, security
from deskpilot.api.routers import auth
from deskpilot.config import Settings, get_settings
from deskpilot.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Takes settings so a test can supply its own."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One engine for the process, rather than one per request. The CLI opens
        # and closes one per command because a command is the whole process; a
        # server is not.
        engine = create_engine(settings.database)
        app.state.settings = settings
        app.state.sessions = create_session_factory(engine)
        app.state.login_limiter = security.LoginRateLimiter(
            settings.api.login_attempts_per_ip, settings.api.login_window_seconds
        )
        logger.info("deskpilot api ready")
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Deskpilot",
        summary="Support agent for Acme Gear.",
        lifespan=lifespan,
        # No interactive docs by default: the schema is generated for the frontend
        # client, and a public explorer is a target rather than a feature.
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(security.SecurityHeaders)
    app.add_middleware(
        CORSMiddleware,
        # Exactly one origin. allow_credentials with a wildcard is refused by
        # browsers anyway, and a list of origins is a list to get wrong.
        allow_origins=[settings.api.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", security.CSRF_HEADER],
    )
    errors.install(app)
    app.include_router(auth.router)

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness only. Says nothing about the database on purpose: a health
        endpoint that reports which dependency is down tells an attacker too."""
        return {"status": "ok"}

    return app
