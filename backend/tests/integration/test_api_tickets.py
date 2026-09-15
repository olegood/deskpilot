"""Ticket endpoints, driven through the real app.

Run with: uv run pytest -m integration (needs Ollama and `docker compose up -d`).
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.api.app import create_app
from deskpilot.auth.users import create_user
from deskpilot.authz.audit import recent
from deskpilot.config import DatabaseSettings, Settings
from deskpilot.db.models import UserRole

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
ANA = "ana.garcia@example.com"
REVIEWER = "lena@acmegear.example"
GOOD = "correct horse battery staple"
ROUNDS = 4


@pytest.fixture
async def accounts(seeded_sessions: async_sessionmaker[AsyncSession]) -> None:
    async with seeded_sessions() as session:
        await create_user(session, NOAH, GOOD, "Noah Kim", rounds=ROUNDS)
        await create_user(session, ANA, GOOD, "Ana Garcia", rounds=ROUNDS)
        await create_user(session, REVIEWER, GOOD, "Lena Fox", UserRole.REVIEWER, rounds=ROUNDS)
        await session.commit()


@pytest.fixture
async def client(
    test_database: DatabaseSettings, accounts: None, agent_settings: Settings
) -> AsyncIterator[AsyncClient]:
    settings = agent_settings.model_copy(
        update={
            "database": test_database,
            "auth": agent_settings.auth.model_copy(update={"bcrypt_rounds": ROUNDS}),
        }
    )
    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
        app.router.lifespan_context(app),
    ):
        yield client


async def sign_in(client: AsyncClient, email: str = NOAH) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"email": email, "password": GOOD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def open_ticket(client: AsyncClient, headers: dict[str, str], message: str = "Hello") -> str:
    response = await client.post(
        "/api/tickets", json={"subject": "A question", "message": message}, headers=headers
    )
    assert response.status_code == 201, response.text
    return str(response.json()["reference"])


# ── everything needs a session ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/tickets"),
        ("GET", "/api/tickets"),
        ("GET", "/api/tickets/TCK-0001"),
        ("POST", "/api/tickets/TCK-0001/replies"),
    ],
)
async def test_every_endpoint_refuses_without_a_token(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, json={"subject": "x", "message": "x"})

    assert response.status_code == 401


# ── creating and replying ───────────────────────────────────────────────────


async def test_opening_a_ticket_returns_the_agents_answer(client: AsyncClient) -> None:
    headers = await sign_in(client)

    response = await client.post(
        "/api/tickets",
        json={"subject": "Where is my order", "message": "Where is ORD-1042?"},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["reference"].startswith("TCK-")
    assert body["answer"]
    assert body["status"] == "awaiting_customer"


async def test_a_reply_continues_the_same_conversation(client: AsyncClient) -> None:
    headers = await sign_in(client)
    reference = await open_ticket(client, headers, "Where is ORD-1042?")

    response = await client.post(
        f"/api/tickets/{reference}/replies", json={"message": "And ORD-1031?"}, headers=headers
    )

    assert response.status_code == 200
    detail = (await client.get(f"/api/tickets/{reference}", headers=headers)).json()
    assert [m["speaker"] for m in detail["messages"]] == [
        "customer",
        "agent",
        "customer",
        "agent",
    ]


async def test_an_empty_message_is_rejected_before_the_agent_runs(client: AsyncClient) -> None:
    headers = await sign_in(client)

    response = await client.post(
        "/api/tickets", json={"subject": "x", "message": ""}, headers=headers
    )

    assert response.status_code == 422


# ── scoping ─────────────────────────────────────────────────────────────────


async def test_the_list_shows_only_your_own_tickets(client: AsyncClient) -> None:
    mine = await sign_in(client, NOAH)
    await open_ticket(client, mine)
    theirs = await sign_in(client, ANA)
    await open_ticket(client, theirs)

    response = await client.get("/api/tickets", headers=await sign_in(client, NOAH))

    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_another_customers_ticket_is_not_found(client: AsyncClient) -> None:
    """Not 403: saying "forbidden" would confirm the reference exists."""
    reference = await open_ticket(client, await sign_in(client, ANA))

    response = await client.get(f"/api/tickets/{reference}", headers=await sign_in(client, NOAH))

    assert response.status_code == 404


async def test_replying_to_another_customers_ticket_is_not_found(client: AsyncClient) -> None:
    reference = await open_ticket(client, await sign_in(client, ANA))

    response = await client.post(
        f"/api/tickets/{reference}/replies",
        json={"message": "let me in"},
        headers=await sign_in(client, NOAH),
    )

    assert response.status_code == 404


async def test_a_staff_account_has_no_tickets_of_its_own(client: AsyncClient) -> None:
    """A reviewer signs in fine and is told plainly, not shown an empty list."""
    headers = await sign_in(client, REVIEWER)

    listed = await client.get("/api/tickets", headers=headers)
    created = await client.post(
        "/api/tickets", json={"subject": "x", "message": "x"}, headers=headers
    )

    assert listed.status_code == created.status_code == 403
    assert listed.json()["detail"] == "You are not allowed to do that."


async def test_a_refusal_is_recorded_rather_than_only_returned(
    client: AsyncClient, seeded_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await client.get("/api/tickets", headers=await sign_in(client, REVIEWER))

    async with seeded_sessions() as session:
        denied = await recent(session, denied_only=True)

    # Which rule refused is the policy's business, and is asserted in the matrix.
    # What matters here is that the HTTP layer routed the question to the engine
    # and that the refusal left a trace.
    assert denied
    assert denied[0].event == "ticket.view"
    assert denied[0].rule


# ── reading ─────────────────────────────────────────────────────────────────


async def test_the_conversation_leaves_out_the_agents_working(client: AsyncClient) -> None:
    """Tool calls are not the conversation, and carry unsanitised tool output."""
    headers = await sign_in(client)
    reference = await open_ticket(client, headers, "Where is ORD-1042?")

    detail = (await client.get(f"/api/tickets/{reference}", headers=headers)).json()

    assert {m["speaker"] for m in detail["messages"]} <= {"customer", "agent"}


async def test_a_ticket_carries_its_category(client: AsyncClient) -> None:
    headers = await sign_in(client)
    reference = await open_ticket(client, headers, "Where is ORD-1042?")

    detail = (await client.get(f"/api/tickets/{reference}", headers=headers)).json()

    assert detail["category"]
    assert detail["subject"] == "A question"


async def test_an_unknown_reference_is_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/tickets/TCK-9999", headers=await sign_in(client))

    assert response.status_code == 404
