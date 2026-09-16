"""What a request needs, and who is making it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.auth.sessions import authenticate_access_token
from deskpilot.auth.tokens import TokenError
from deskpilot.authz.principal import Principal
from deskpilot.config import Settings
from deskpilot.db.models import User
from deskpilot.graph.runner import AgentGraph
from deskpilot.integrations.shiptrack import ShipTrackClient


def settings_of(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def sessions_of(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.sessions  # type: ignore[no-any-return]


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, closed afterwards. The route commits."""
    async with sessions_of(request)() as session:
        yield session


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    """Pull the access token out of the Authorization header.

    The scheme is required and checked. Accepting a bare token would mean a
    copy-pasted header from somewhere else might work by accident.
    """
    if not authorization:
        raise TokenError("this request is not signed in")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise TokenError("this request is not signed in")
    return token


async def current_user(
    request: Request,
    token: Annotated[str, Depends(bearer_token)],
    session: Annotated[AsyncSession, Depends(db_session)],
) -> User:
    """Verify the token and load the account it names.

    The database is consulted even though the signature verified, because a
    signature cannot know the account was disabled a minute ago (D-064).
    """
    return await authenticate_access_token(session, token, settings_of(request).auth)


async def current_principal(user: Annotated[User, Depends(current_user)]) -> Principal:
    """The attributes a policy weighs, read fresh on every request."""
    return Principal.from_user(user)


def checkpointer_of(request: Request) -> BaseCheckpointSaver[Any]:
    return request.app.state.checkpointer  # type: ignore[no-any-return]


def carrier_of(request: Request) -> ShipTrackClient | None:
    return request.app.state.carrier  # type: ignore[no-any-return]


def agent_of(request: Request) -> AgentGraph:
    """The compiled graph, built once for the process."""
    return request.app.state.agent  # type: ignore[no-any-return]


CurrentUser = Annotated[User, Depends(current_user)]
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
DbSession = Annotated[AsyncSession, Depends(db_session)]
AppSettings = Annotated[Settings, Depends(settings_of)]
Checkpointer = Annotated["BaseCheckpointSaver[Any]", Depends(checkpointer_of)]
Agent = Annotated["AgentGraph", Depends(agent_of)]
Carrier = Annotated["ShipTrackClient | None", Depends(carrier_of)]

Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(sessions_of)]
