"""What an admitted caller may do. Listing a tool is not permission to call it."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest

from tests.mcp_harness import Stack, stack_in_own_task

READ_WRITE = "payments:read refunds:write"


@pytest.fixture
async def stack() -> AsyncIterator[Stack]:
    async with stack_in_own_task() as running:
        yield running


def key() -> str:
    return uuid.uuid4().hex


def text(result: object) -> str:
    content = getattr(result, "content", [])
    return " ".join(getattr(item, "text", "") for item in content)


# ── reading ─────────────────────────────────────────────────────────────────


async def test_the_agent_can_read_a_payment(stack: Stack) -> None:
    result = await stack.call(await stack.agent_token(), "get_payment", order_number="ORD-1042")

    assert not result.is_error
    payment = result.structured_content
    assert payment is not None
    assert payment["amount_cents"] == 17700
    assert payment["currency"] == "USD"
    assert payment["refundable_cents"] == 17700


async def test_the_card_is_only_ever_its_last_four_digits(stack: Stack) -> None:
    result = await stack.call(await stack.agent_token(), "get_payment", order_number="ORD-1042")

    assert result.structured_content is not None
    assert set(result.structured_content) >= {"card_brand", "card_last4"}
    assert len(result.structured_content["card_last4"]) == 4
    assert not {"card_number", "pan", "cvc", "expiry"} & set(result.structured_content)


async def test_an_unknown_order_is_an_error_the_model_can_read(stack: Stack) -> None:
    result = await stack.call(await stack.agent_token(), "get_payment", order_number="ORD-9999")

    assert result.is_error
    assert "no payment for that order" in text(result)


async def test_a_partial_refund_shows_up(stack: Stack) -> None:
    result = await stack.call(await stack.agent_token(), "get_payment", order_number="ORD-1017")

    assert result.structured_content is not None
    assert result.structured_content["refunded_cents"] == 5000
    assert result.structured_content["refundable_cents"] == 13900


# ── the line the agent cannot cross ─────────────────────────────────────────


async def test_the_agent_sees_the_refund_tool_listed(stack: Stack) -> None:
    """Stated on purpose: listing is not permission. Deskpilot's allowlist is what
    keeps the tool away from the model; the scope check below is what makes it safe."""
    async with stack.session(await stack.agent_token()) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    assert set(tools) == {"get_payment", "issue_refund"}
    refund_hints = tools["issue_refund"].annotations
    read_hints = tools["get_payment"].annotations
    assert refund_hints is not None
    assert refund_hints.destructive_hint
    assert read_hints is not None
    assert read_hints.read_only_hint


async def test_the_agents_token_cannot_refund(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    """The property D-009 promised: a hijacked agent cannot move money."""
    with caplog.at_level(logging.WARNING):
        result = await stack.call(
            await stack.agent_token(),
            "issue_refund",
            order_number="ORD-1042",
            amount_cents=17700,
            reason="Give it all back",
            idempotency_key=key(),
        )

    assert result.is_error
    assert "does not allow that" in text(result)
    payment = stack.store.get("ORD-1042")
    assert payment is not None
    assert payment.refunded_cents == 0
    # Worth a warning, not a line at INFO: it is what an attack looks like.
    assert "deskpilot-agent called issue_refund without refunds:write" in caplog.text


# ── refunding, with a token that may ────────────────────────────────────────


async def test_a_token_with_write_scope_can_refund(stack: Stack) -> None:
    result = await stack.call(
        stack.mint(scope=READ_WRITE),
        "issue_refund",
        order_number="ORD-1042",
        amount_cents=2500,
        reason="Damaged bottle",
        idempotency_key=key(),
    )

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["amount_cents"] == 2500
    payment = stack.store.get("ORD-1042")
    assert payment is not None
    assert payment.refundable_cents == 17700 - 2500


async def test_a_retry_with_the_same_key_refunds_once(stack: Stack) -> None:
    """The client timed out and asked again. It must not pay twice."""
    token = stack.mint(scope=READ_WRITE)
    arguments = {
        "order_number": "ORD-1042",
        "amount_cents": 2500,
        "reason": "Damaged bottle",
        "idempotency_key": key(),
    }

    first = await stack.call(token, "issue_refund", **arguments)
    second = await stack.call(token, "issue_refund", **arguments)

    assert first.structured_content == second.structured_content
    payment = stack.store.get("ORD-1042")
    assert payment is not None
    assert len(payment.refunds) == 1


async def test_a_reused_key_for_a_different_refund_is_refused(stack: Stack) -> None:
    token = stack.mint(scope=READ_WRITE)
    shared = key()
    await stack.call(
        token,
        "issue_refund",
        order_number="ORD-1042",
        amount_cents=2500,
        reason="Damaged bottle",
        idempotency_key=shared,
    )

    result = await stack.call(
        token,
        "issue_refund",
        order_number="ORD-1042",
        amount_cents=9000,
        reason="Damaged bottle",
        idempotency_key=shared,
    )

    assert result.is_error
    assert "different refund" in text(result)


async def test_more_than_is_refundable_is_refused(stack: Stack) -> None:
    result = await stack.call(
        stack.mint(scope=READ_WRITE),
        "issue_refund",
        order_number="ORD-1017",
        amount_cents=18900,
        reason="All of it",
        idempotency_key=key(),
    )

    assert result.is_error
    assert "At most 13900" in text(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [("amount_cents", 0), ("amount_cents", -100), ("idempotency_key", "short"), ("reason", "")],
)
async def test_malformed_arguments_are_refused_by_the_schema(
    stack: Stack, field: str, value: object
) -> None:
    arguments: dict[str, object] = {
        "order_number": "ORD-1042",
        "amount_cents": 100,
        "reason": "A reason",
        "idempotency_key": key(),
    }
    arguments[field] = value

    result = await stack.call(stack.mint(scope=READ_WRITE), "issue_refund", **arguments)

    assert result.is_error
    payment = stack.store.get("ORD-1042")
    assert payment is not None
    assert payment.refunds == ()
