"""The streaming endpoints.

Run with: uv run pytest -m integration (needs Ollama and `docker compose up -d`).
"""

import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.api.app import create_app
from deskpilot.auth.users import create_user
from deskpilot.config import DatabaseSettings, Settings
from deskpilot.db.models import UserRole

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
REVIEWER = "lena@acmegear.example"
GOOD = "correct horse battery staple"
ROUNDS = 4


@pytest.fixture
async def accounts(seeded_sessions: async_sessionmaker[AsyncSession]) -> None:
    async with seeded_sessions() as session:
        await create_user(session, NOAH, GOOD, "Noah Kim", rounds=ROUNDS)
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
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def parse(body: str) -> list[tuple[str, dict[str, object]]]:
    """Read an SSE body into (event name, payload) pairs."""
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if name is not None:
            events.append((name, json.loads(data or "{}")))
    return events


async def open_streaming(client: AsyncClient, headers: dict[str, str], message: str) -> str:
    async with client.stream(
        "POST",
        "/api/tickets/stream",
        json={"subject": "A question", "message": message},
        headers=headers,
    ) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        return "".join([chunk async for chunk in response.aiter_text()])


# ── the happy path ──────────────────────────────────────────────────────────


async def test_a_stream_opens_a_ticket_and_ends_with_an_answer(client: AsyncClient) -> None:
    headers = await sign_in(client)

    events = parse(await open_streaming(client, headers, "Where is ORD-1042?"))
    names = [name for name, _ in events]

    assert names[0] == "ticket"
    assert names[-1] == "done"
    assert "answer" in names
    reference = events[0][1]["reference"]
    assert isinstance(reference, str)
    assert reference.startswith("TCK-")


async def test_the_answer_event_carries_the_whole_answer(client: AsyncClient) -> None:
    headers = await sign_in(client)

    events = dict(parse(await open_streaming(client, headers, "Where is ORD-1042?")))

    assert events["answer"]["answer"]


async def test_progress_is_reported_before_the_answer(client: AsyncClient) -> None:
    """A tool call or a category, so the customer sees it working."""
    headers = await sign_in(client)

    names = [name for name, _ in parse(await open_streaming(client, headers, "Where is ORD-1042?"))]

    assert {"category", "tool"} & set(names)
    assert names.index("answer") > 0


async def test_the_ticket_is_saved_when_the_stream_finishes(client: AsyncClient) -> None:
    headers = await sign_in(client)
    events = dict(parse(await open_streaming(client, headers, "Where is ORD-1042?")))
    reference = events["ticket"]["reference"]

    detail = (await client.get(f"/api/tickets/{reference}", headers=headers)).json()

    assert detail["status"] == "awaiting_customer"
    assert detail["category"]
    assert [m["speaker"] for m in detail["messages"]] == ["customer", "agent"]


async def test_a_streamed_reply_continues_the_conversation(client: AsyncClient) -> None:
    headers = await sign_in(client)
    events = dict(parse(await open_streaming(client, headers, "Where is ORD-1042?")))
    reference = events["ticket"]["reference"]

    async with client.stream(
        "POST",
        f"/api/tickets/{reference}/replies/stream",
        json={"message": "And ORD-1031?"},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert [name for name, _ in parse(body)][-1] == "done"
    detail = (await client.get(f"/api/tickets/{reference}", headers=headers)).json()
    assert len(detail["messages"]) == 4


# ── refusals happen before the stream starts ────────────────────────────────


async def test_an_unauthenticated_stream_is_a_plain_401(client: AsyncClient) -> None:
    """Not a 200 carrying an error event: the status is decided before any bytes."""
    response = await client.post("/api/tickets/stream", json={"subject": "x", "message": "x"})

    assert response.status_code == 401
    assert not response.headers["content-type"].startswith("text/event-stream")


async def test_a_refused_stream_is_a_plain_403(client: AsyncClient) -> None:
    response = await client.post(
        "/api/tickets/stream",
        json={"subject": "x", "message": "x"},
        headers=await sign_in(client, REVIEWER),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You are not allowed to do that."


async def test_streaming_a_reply_to_another_ticket_is_a_plain_404(client: AsyncClient) -> None:
    response = await client.post(
        "/api/tickets/TCK-9999/replies/stream",
        json={"message": "let me in"},
        headers=await sign_in(client),
    )

    assert response.status_code == 404


async def test_a_malformed_body_is_rejected_before_the_stream(client: AsyncClient) -> None:
    response = await client.post(
        "/api/tickets/stream", json={"subject": "", "message": ""}, headers=await sign_in(client)
    )

    assert response.status_code == 422


# ── framing ─────────────────────────────────────────────────────────────────


async def test_newlines_in_the_answer_do_not_break_the_framing(client: AsyncClient) -> None:
    """A raw newline in a data field would end it early and split one event in two."""
    headers = await sign_in(client)

    body = await open_streaming(client, headers, "Tell me about returns and refunds")

    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                json.loads(line.removeprefix("data: "))


async def test_buffering_is_turned_off(client: AsyncClient) -> None:
    async with client.stream(
        "POST",
        "/api/tickets/stream",
        json={"subject": "x", "message": "hello"},
        headers=await sign_in(client),
    ) as response:
        assert response.headers["X-Accel-Buffering"] == "no"
        assert response.headers["Cache-Control"] == "no-store"
        await response.aread()
