"""The auth endpoints, driven through the real app.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.api import security
from deskpilot.api.app import create_app
from deskpilot.auth.users import create_user
from deskpilot.config import DatabaseSettings, Settings
from deskpilot.db.models import UserRole

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
GOOD = "correct horse battery staple"
ROUNDS = 4


@pytest.fixture
async def accounts(seeded_sessions: async_sessionmaker[AsyncSession]) -> None:
    async with seeded_sessions() as session:
        await create_user(session, NOAH, GOOD, "Noah Kim", rounds=ROUNDS)
        await create_user(
            session, "lena@acmegear.example", GOOD, "Lena Fox", UserRole.REVIEWER, rounds=ROUNDS
        )
        await session.commit()


@pytest.fixture
async def client(test_database: DatabaseSettings, accounts: None) -> AsyncIterator[AsyncClient]:
    settings = Settings(_env_file=None, database=test_database)  # type: ignore[call-arg]
    settings = settings.model_copy(
        update={"auth": settings.auth.model_copy(update={"bcrypt_rounds": ROUNDS})}
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    # ASGITransport does not run lifespan, so the state it sets up is built here.
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as client,
        app.router.lifespan_context(app),
    ):
        yield client


async def login(client: AsyncClient, password: str = GOOD, email: str = NOAH) -> None:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


# ── login ───────────────────────────────────────────────────────────────────


async def test_a_good_password_returns_an_access_token(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"email": NOAH, "password": GOOD})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["email"] == NOAH
    assert body["role"] == "customer"


async def test_the_refresh_token_is_never_in_the_body(client: AsyncClient) -> None:
    """It belongs in an httpOnly cookie, where script cannot reach it."""
    response = await client.post("/api/auth/login", json={"email": NOAH, "password": GOOD})

    assert "refresh" not in response.text.lower()
    cookie = response.cookies.get(security.REFRESH_COOKIE)
    assert cookie
    assert cookie not in response.text


async def test_the_refresh_cookie_is_httponly_strict_and_scoped(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"email": NOAH, "password": GOOD})

    header = "".join(
        value for key, value in response.headers.multi_items() if key.lower() == "set-cookie"
    )
    assert "HttpOnly" in header
    assert "SameSite=strict" in header
    assert "Path=/api/auth" in header


async def test_the_csrf_cookie_is_readable_by_script(client: AsyncClient) -> None:
    """The SPA has to echo it back, so this one is deliberately not httpOnly."""
    response = await client.post("/api/auth/login", json={"email": NOAH, "password": GOOD})

    assert response.cookies.get(security.CSRF_COOKIE)


async def test_a_wrong_password_is_401_with_the_vague_message(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/login", json={"email": NOAH, "password": "not the password at all"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "That email and password do not match an account."


async def test_an_unknown_address_looks_identical(client: AsyncClient) -> None:
    wrong = await client.post(
        "/api/auth/login", json={"email": NOAH, "password": "not the password at all"}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": GOOD}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_a_malformed_address_is_rejected_before_anything_happens(
    client: AsyncClient,
) -> None:
    response = await client.post("/api/auth/login", json={"email": "noah", "password": GOOD})

    assert response.status_code == 422


# ── me ──────────────────────────────────────────────────────────────────────


async def test_me_needs_a_token(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.parametrize("header", ["", "token-without-a-scheme", "Basic abc", "Bearer", "Bearer "])
async def test_a_malformed_authorization_header_is_refused(
    client: AsyncClient, header: str
) -> None:
    response = await client.get("/api/auth/me", headers={"Authorization": header})

    assert response.status_code == 401


async def test_a_forged_token_is_refused(client: AsyncClient) -> None:
    response = await client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.token"})

    assert response.status_code == 401


async def test_me_reports_the_account_and_its_customer(client: AsyncClient) -> None:
    body = (await client.post("/api/auth/login", json={"email": NOAH, "password": GOOD})).json()

    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )

    assert response.status_code == 200
    assert response.json()["customer_email"] == NOAH


async def test_a_staff_account_has_no_customer(client: AsyncClient) -> None:
    body = (
        await client.post(
            "/api/auth/login", json={"email": "lena@acmegear.example", "password": GOOD}
        )
    ).json()

    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )

    assert response.json()["customer_email"] is None
    assert response.json()["role"] == "reviewer"


# ── refresh ─────────────────────────────────────────────────────────────────


async def csrf_headers(client: AsyncClient) -> dict[str, str]:
    return {security.CSRF_HEADER: client.cookies[security.CSRF_COOKIE]}


async def test_refreshing_returns_a_new_access_token(client: AsyncClient) -> None:
    await login(client)
    before = client.cookies[security.REFRESH_COOKIE]

    response = await client.post("/api/auth/refresh", headers=await csrf_headers(client))

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert client.cookies[security.REFRESH_COOKIE] != before


async def test_refreshing_without_the_csrf_header_is_refused(client: AsyncClient) -> None:
    """This is the one endpoint a browser could be tricked into calling."""
    await login(client)

    response = await client.post("/api/auth/refresh")

    assert response.status_code == 401


async def test_refreshing_with_the_wrong_csrf_header_is_refused(client: AsyncClient) -> None:
    await login(client)

    response = await client.post(
        "/api/auth/refresh", headers={security.CSRF_HEADER: "a value I made up"}
    )

    assert response.status_code == 401


async def test_refreshing_without_a_cookie_is_refused(client: AsyncClient) -> None:
    response = await client.post("/api/auth/refresh", headers={security.CSRF_HEADER: "anything"})

    assert response.status_code == 401


async def test_reusing_a_refresh_token_kills_the_family(client: AsyncClient) -> None:
    await login(client)
    stolen = client.cookies[security.REFRESH_COOKIE]
    headers = await csrf_headers(client)
    await client.post("/api/auth/refresh", headers=headers)

    # The thief presents the copy they kept.
    client.cookies.set(security.REFRESH_COOKIE, stolen)
    assert (await client.post("/api/auth/refresh", headers=headers)).status_code == 401

    # And the honest holder is signed out too, which is the point.
    assert (await client.post("/api/auth/refresh", headers=headers)).status_code == 401


# ── logout ──────────────────────────────────────────────────────────────────


async def test_logging_out_clears_the_cookie_and_revokes(client: AsyncClient) -> None:
    await login(client)
    headers = await csrf_headers(client)

    assert (await client.post("/api/auth/logout")).status_code == 204
    assert not client.cookies.get(security.REFRESH_COOKIE)
    assert (await client.post("/api/auth/refresh", headers=headers)).status_code == 401


async def test_logging_out_without_a_session_is_not_an_error(client: AsyncClient) -> None:
    assert (await client.post("/api/auth/logout")).status_code == 204


# ── rate limiting ───────────────────────────────────────────────────────────


async def test_an_address_is_limited_after_enough_failures(
    client: AsyncClient, seeded_sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The gap lockout could not close: one password against many accounts.

    Each attempt names a different address, so no single account's lockout ever
    trips. The source address is what catches it.
    """
    limit = client._transport.app.state.settings.api.login_attempts_per_ip  # type: ignore[union-attr]
    for index in range(limit):
        response = await client.post(
            "/api/auth/login", json={"email": f"person{index}@example.com", "password": GOOD}
        )
        assert response.status_code == 401

    blocked = await client.post("/api/auth/login", json={"email": NOAH, "password": GOOD})

    assert blocked.status_code == 401
    assert "Too many failed attempts" in blocked.json()["detail"]


async def test_a_success_clears_the_address(client: AsyncClient) -> None:
    for _ in range(3):
        await client.post("/api/auth/login", json={"email": NOAH, "password": "wrong password!"})

    await login(client)

    limiter = client._transport.app.state.login_limiter  # type: ignore[union-attr]
    assert not limiter.is_limited("testclient")


# ── headers ─────────────────────────────────────────────────────────────────


async def test_security_headers_are_on_every_response(client: AsyncClient) -> None:
    for response in (
        await client.get("/api/health"),
        await client.get("/api/auth/me"),
    ):
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-store"


async def test_the_interactive_docs_are_not_served(client: AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 404
    assert (await client.get("/redoc")).status_code == 404


async def test_health_says_nothing_about_dependencies(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
