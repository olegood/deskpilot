"""The MCP server and its tools.

Two layers of authorization, and they answer different questions:

- the transport requires a valid token with `payments:read` for any request at all,
  which the SDK enforces before a tool is even looked up
- each tool then checks the scope *it* needs. Listing a tool is not permission to
  call it, and a token that can read cannot refund.

A read-only token calling `issue_refund` is refused in-band, as a tool error, and
logged at WARNING. It is exactly what a hijacked agent would try, so it is worth
more than a line at INFO.
"""

from __future__ import annotations

import logging
from datetime import datetime

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, BaseModel, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from paywisp.mcp_server.config import Settings
from paywisp.mcp_server.payments import Payment, PaymentStore, RefundError, seeded_store
from paywisp.mcp_server.verifier import JWKSTokenVerifier
from paywisp.scopes import SCOPE_PAYMENTS_READ, SCOPE_REFUNDS_WRITE

logger = logging.getLogger(__name__)

NOT_ALLOWED = "This token does not allow that."


class RefundView(BaseModel):
    refund_id: str
    amount_cents: int
    reason: str
    created_at: datetime


class PaymentView(BaseModel):
    """What a caller sees of a payment. The card is its brand and last four only."""

    payment_id: str
    order_number: str
    status: str
    currency: str
    amount_cents: int
    refunded_cents: int
    refundable_cents: int
    card_brand: str
    card_last4: str
    created_at: datetime
    refunds: list[RefundView]


def view(payment: Payment) -> PaymentView:
    return PaymentView(
        payment_id=payment.payment_id,
        order_number=payment.order_number,
        status=payment.status.value,
        currency=payment.currency,
        amount_cents=payment.amount_cents,
        refunded_cents=payment.refunded_cents,
        refundable_cents=payment.refundable_cents,
        card_brand=payment.card_brand,
        card_last4=payment.card_last4,
        created_at=payment.created_at,
        refunds=[
            RefundView(
                refund_id=refund.refund_id,
                amount_cents=refund.amount_cents,
                reason=refund.reason,
                created_at=refund.created_at,
            )
            for refund in payment.refunds
        ],
    )


def require_scope(scope: str, tool: str) -> str:
    """Refuse unless the calling token carries `scope`. Returns the caller's client id."""
    token = get_access_token()
    if token is None:
        # The transport should have refused already. Belt and braces: a tool that
        # assumes someone upstream checked is a tool that one refactor from now
        # does not check at all.
        raise ToolError(NOT_ALLOWED)
    if scope not in token.scopes:
        logger.warning(
            "%s called %s without %s (it has: %s)",
            token.client_id,
            tool,
            scope,
            " ".join(token.scopes) or "nothing",
        )
        raise ToolError(NOT_ALLOWED)
    return token.client_id


def create_server(
    settings: Settings,
    verifier: JWKSTokenVerifier,
    store: PaymentStore | None = None,
) -> MCPServer:
    store = store or seeded_store()
    server = MCPServer(
        name="paywisp",
        title="Paywisp payments",
        instructions="Look up payments by the merchant's order number, and refund them.",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.issuer),
            resource_server_url=AnyHttpUrl(settings.resource),
            # Every caller needs at least read. Write is checked per tool.
            required_scopes=[SCOPE_PAYMENTS_READ],
            # The verifier checks the audience itself, and this makes the SDK check
            # it too. Two checks that agree cost nothing; one that was quietly
            # skipped would cost a great deal.
            validate_token_resource=True,
        ),
    )

    @server.tool(
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
    )
    def get_payment(order_number: str = Field(max_length=32)) -> PaymentView:
        """Look up the payment for one of the merchant's orders, by its order number.

        Returns the amount, currency, status, how much has been refunded and how
        much still can be, the card's brand and last four digits, and every refund.
        """
        require_scope(SCOPE_PAYMENTS_READ, "get_payment")
        payment = store.get(order_number)
        if payment is None:
            raise ToolError("There is no payment for that order.")
        return view(payment)

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
        )
    )
    def issue_refund(
        order_number: str = Field(max_length=32),
        amount_cents: int = Field(gt=0),
        reason: str = Field(min_length=1, max_length=200),
        idempotency_key: str = Field(min_length=16, max_length=128),
    ) -> RefundView:
        """Refund part or all of a captured payment. Moves money.

        The idempotency key makes a retry safe: the same key and the same request
        return the original refund instead of refunding again.
        """
        client_id = require_scope(SCOPE_REFUNDS_WRITE, "issue_refund")
        try:
            refund = store.refund(order_number, amount_cents, reason, idempotency_key)
        except RefundError as exc:
            raise ToolError(str(exc)) from exc
        logger.info(
            "%s refunded %d on %s (%s)", client_id, amount_cents, order_number, refund.refund_id
        )
        return RefundView(
            refund_id=refund.refund_id,
            amount_cents=refund.amount_cents,
            reason=refund.reason,
            created_at=refund.created_at,
        )

    return server


async def health(request: Request) -> JSONResponse:
    """Unauthenticated, so a container healthcheck needs no token."""
    return JSONResponse({"status": "ok"})


def create_app(
    settings: Settings | None = None,
    verifier: JWKSTokenVerifier | None = None,
    store: PaymentStore | None = None,
) -> Starlette:
    settings = settings or Settings()
    verifier = verifier or JWKSTokenVerifier(settings)
    server = create_server(settings, verifier, store)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        # Stateless and plain JSON. Deskpilot is a server calling a server: there
        # is no browser to stream to and nothing a session would remember that a
        # bearer token does not already carry. Less state is less to get wrong.
        stateless_http=True,
        json_response=True,
        # Always on, not only when bound to localhost as the SDK defaults to. In a
        # container this server binds 0.0.0.0, which is exactly when a rebinding
        # attack becomes possible.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[],
        ),
        host=settings.host,
    )
    # Added to the Starlette app directly rather than through the SDK's
    # `custom_route`, which is untyped. It sits outside the bearer middleware, which
    # only wraps the MCP endpoint.
    app.add_route("/health", health, methods=["GET"])
    return app
