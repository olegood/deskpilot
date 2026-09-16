"""Logging in, refreshing, and logging out over HTTP.

The split matters: the access token goes back in the response body for the SPA to
hold in memory, and the refresh token goes into an httpOnly cookie the SPA cannot
read. An XSS bug can then steal at most fifteen minutes.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Cookie, Header, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from deskpilot.api import security
from deskpilot.api.deps import AppSettings, CurrentUser, DbSession, Sessions
from deskpilot.auth.sessions import IssuedSession, log_in, log_out, refresh
from deskpilot.auth.tokens import TokenError
from deskpilot.auth.users import AuthError
from deskpilot.authz.actions import AuditEvent
from deskpilot.authz.audit import record_event

router = APIRouter(prefix="/api/auth", tags=["auth"])

TOO_MANY = "Too many failed attempts from this address. Try again in a few minutes."


class Credentials(BaseModel):
    email: EmailStr
    # Not constrained here beyond a sane ceiling: the policy lives in one place and
    # rejecting early would mean two definitions of a valid password.
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """What the SPA keeps in memory. The refresh token is not in here."""

    access_token: str
    expires_at: str
    email: str
    role: str


class Identity(BaseModel):
    email: str
    role: str
    full_name: str
    customer_email: str | None


def issued(session: IssuedSession, role: str) -> TokenResponse:
    return TokenResponse(
        access_token=session.access_token,
        expires_at=session.access_expires_at.isoformat(),
        email=session.email,
        role=role,
    )


def hand_over(
    response: Response, session: IssuedSession, settings: AppSettings, role: str
) -> TokenResponse:
    """Set the cookies and return the body."""
    security.set_refresh_cookie(response, session.refresh_token, settings.api, settings.auth)
    security.set_csrf_cookie(response, secrets.token_urlsafe(32), settings.api)
    return issued(session, role)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    credentials: Credentials,
    db: DbSession,
    sessions: Sessions,
    settings: AppSettings,
) -> TokenResponse:
    """Exchange a password for a session."""
    limiter: security.LoginRateLimiter = request.app.state.login_limiter
    address = security.client_address(request)
    if limiter.is_limited(address):
        # Deliberately a different message from a wrong password: this one is about
        # the address, not the account, so it confirms nothing about either.
        await record_event(
            sessions, AuditEvent.LOGIN_FAILED, f"rate limited {address}", allowed=False
        )
        raise AuthError(TOO_MANY)

    try:
        session = await log_in(db, credentials.email, credentials.password, settings.auth)
    except AuthError as exc:
        # Committed even though it failed: log_in recorded the attempt on the user
        # row, and rolling that back would mean the lockout never advances (D-073).
        await db.commit()
        limiter.record_failure(address)
        await record_event(
            sessions,
            exc.event or AuditEvent.LOGIN_FAILED,
            f"login failed for {credentials.email} from {address}",
            actor_user_id=exc.user_id,
            allowed=False,
        )
        raise
    await db.commit()
    limiter.forget(address)
    await record_event(
        sessions,
        AuditEvent.LOGIN_SUCCEEDED,
        f"logged in as {session.email} from {address}",
        actor_user_id=session.user_id,
    )
    return hand_over(response, session, settings, await role_of(db, session.user_id))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    request: Request,
    response: Response,
    db: DbSession,
    sessions: Sessions,
    settings: AppSettings,
    deskpilot_refresh: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None),
) -> TokenResponse:
    """Trade a refresh token for a new pair.

    This is the one endpoint authenticated by a cookie, so it is the one endpoint
    a browser could be tricked into calling. SameSite=strict already stops that;
    the double-submit check is a second lock on the same door, and costs one header.
    """
    expected = request.cookies.get(security.CSRF_COOKIE)
    if not expected or not x_csrf_token or not secrets.compare_digest(x_csrf_token, expected):
        raise TokenError("this request could not be verified")
    if not deskpilot_refresh:
        raise TokenError("this session is not valid")

    session = await refresh(db, deskpilot_refresh, settings.auth)
    await db.commit()
    await record_event(
        sessions,
        AuditEvent.TOKEN_REFRESHED,
        f"refreshed the session for {session.email}",
        actor_user_id=session.user_id,
    )
    return hand_over(response, session, settings, await role_of(db, session.user_id))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: DbSession,
    sessions: Sessions,
    settings: AppSettings,
    deskpilot_refresh: str | None = Cookie(default=None),
) -> None:
    """Revoke the family and clear the cookie.

    No CSRF check and no error when the token is unknown: being logged out against
    your will is an annoyance, not a compromise, and a logout that reports whether
    a token existed is an oracle (D-065).

    The access token the client already holds stays valid until it expires, which
    is at most fifteen minutes. Killing it immediately would mean bumping
    token_version, and that signs the person out of every other device too -
    which is what "sign out everywhere" is for, not what "sign out" means. Short
    access tokens are the answer, and they are already short.
    """
    if deskpilot_refresh:
        await log_out(db, deskpilot_refresh)
        await db.commit()
        await record_event(sessions, AuditEvent.LOGGED_OUT, "signed out")
    security.clear_refresh_cookie(response, settings.api)


@router.get("/me", response_model=Identity)
async def me(user: CurrentUser) -> Identity:
    """Who the access token says you are."""
    return Identity(
        email=user.email,
        role=user.role.value,
        full_name=user.full_name,
        customer_email=user.customer.email if user.customer else None,
    )


async def role_of(db: DbSession, user_id: int) -> str:
    """The role, for the SPA to decide what to show. Never used to authorize."""
    from deskpilot.db.models import User

    user = await db.get(User, user_id)
    return user.role.value if user else "customer"
