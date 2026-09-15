"""Headers, cookies, and the rate limit that account lockout could not provide.

Account lockout (D-071) stops somebody guessing one password. It cannot stop
somebody trying one password against a thousand accounts, because that never
trips any single account's counter. That needs a source address, which is
something only the HTTP layer has.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from deskpilot.config import ApiSettings, AuthSettings

logger = logging.getLogger(__name__)

# Name of the cookie the refresh token lives in.
REFRESH_COOKIE = "deskpilot_refresh"
# Readable by JavaScript on purpose: the SPA has to echo it back in a header.
CSRF_COOKIE = "deskpilot_csrf"
CSRF_HEADER = "X-CSRF-Token"

SECURITY_HEADERS = {
    # No framing, so the SPA cannot be embedded and clicked through.
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # The API returns JSON only, so nothing needs to load. The SPA gets its own,
    # looser policy from whatever serves it.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    # Turn off features this API has no use for.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeaders(BaseHTTPMiddleware):
    """Add the headers to every response, including error responses."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


def set_refresh_cookie(
    response: Response, token: str, api: ApiSettings, auth: AuthSettings
) -> None:
    """Put the refresh token where script cannot reach it.

    httponly so an XSS bug cannot read it; samesite=strict so another site cannot
    cause the browser to send it; path limited to the one endpoint that uses it, so
    it is not attached to every request.
    """
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=api.secure_cookies,
        samesite="strict",
        max_age=auth.refresh_token_days * 24 * 60 * 60,
        path="/api/auth",
    )


def clear_refresh_cookie(response: Response, api: ApiSettings) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth", secure=api.secure_cookies)


def set_csrf_cookie(response: Response, token: str, api: ApiSettings) -> None:
    """The other half of the double submit. Deliberately not httponly."""
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,
        secure=api.secure_cookies,
        samesite="strict",
        path="/",
    )


class LoginRateLimiter:
    """A fixed window of failed attempts per source address.

    In memory, so it is per process and forgets everything on restart. That is
    stated rather than hidden: the real answer is a shared store, and it arrives
    when there is more than one process to share it. Even so, this closes the gap
    lockout could not, because a spray across many accounts now has a counter.
    """

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, address: str, now: float) -> deque[float]:
        attempts = self._failures[address]
        while attempts and attempts[0] <= now - self.window:
            attempts.popleft()
        return attempts

    def is_limited(self, address: str) -> bool:
        return len(self._prune(address, time.monotonic())) >= self.limit

    def record_failure(self, address: str) -> None:
        now = time.monotonic()
        self._prune(address, now).append(now)
        if self.is_limited(address):
            logger.warning("rate limiting login attempts from %s", address)

    def forget(self, address: str) -> None:
        """A success clears the address, so one person's typo does not linger."""
        self._failures.pop(address, None)


def client_address(request: Request) -> str:
    """The address to count against.

    Behind a proxy this is the proxy unless the proxy is trusted to set
    X-Forwarded-For, which is a deployment decision rather than an application
    one. Taking the header unconditionally would let anybody pick their own
    identity and skip the limit entirely, so it is not taken.
    """
    return request.client.host if request.client else "unknown"
