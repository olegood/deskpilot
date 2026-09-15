"""Turning the application's exceptions into HTTP responses.

One place, so a new route cannot accidentally leak a reason that the rest of the
system is careful about. Every handler returns the same shape: {"detail": "..."}.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from deskpilot.auth.tokens import TokenError
from deskpilot.auth.users import AuthError
from deskpilot.authz.engine import FORBIDDEN, Forbidden
from deskpilot.db.seed import SeedError
from deskpilot.tickets import TicketError

logger = logging.getLogger(__name__)


def problem(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def install(app: FastAPI) -> None:
    """Register every handler on an app."""

    @app.exception_handler(AuthError)
    async def _auth(request: Request, exc: AuthError) -> JSONResponse:
        # 401, and the message is already the deliberately vague one.
        return problem(status.HTTP_401_UNAUTHORIZED, str(exc))

    @app.exception_handler(TokenError)
    async def _token(request: Request, exc: TokenError) -> JSONResponse:
        return problem(status.HTTP_401_UNAUTHORIZED, str(exc))

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden) -> JSONResponse:
        # The real reason is already in the audit log. It does not go over the wire.
        return problem(status.HTTP_403_FORBIDDEN, FORBIDDEN)

    @app.exception_handler(TicketError)
    async def _ticket(request: Request, exc: TicketError) -> JSONResponse:
        return problem(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(SeedError)
    async def _seed(request: Request, exc: SeedError) -> JSONResponse:
        return problem(status.HTTP_400_BAD_REQUEST, str(exc))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Logged in full, reported as nothing. An exception message is written for
        # a developer and routinely contains hostnames and table names.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return problem(status.HTTP_500_INTERNAL_SERVER_ERROR, "Something went wrong.")
