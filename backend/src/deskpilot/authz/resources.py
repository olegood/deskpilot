"""What an action is being attempted on.

Each resource carries only the attributes a policy needs. A policy never loads
anything: everything it weighs is already in the principal or the resource, which
is what makes the whole engine pure functions over plain data.
"""

from __future__ import annotations

from dataclasses import dataclass

from deskpilot.db.models import Region


@dataclass(frozen=True)
class Resource:
    """Base class, so a policy can match on type."""


@dataclass(frozen=True)
class Ticket(Resource):
    owner_customer_id: int
    region: Region


@dataclass(frozen=True)
class Order(Resource):
    owner_customer_id: int
    region: Region


@dataclass(frozen=True)
class CustomerProfile(Resource):
    customer_id: int
    region: Region


@dataclass(frozen=True)
class PolicyDocuments(Resource):
    """The published policies. Shared, not owned by anybody."""


@dataclass(frozen=True)
class Proposal(Resource):
    """An action the agent wants to take, waiting for a human to approve it."""

    ticket_owner_customer_id: int
    region: Region
    amount_cents: int
    # The account whose ticket this is, when the customer also has a login. Used to
    # stop somebody approving a proposal on their own ticket.
    owner_user_id: int | None = None


@dataclass(frozen=True)
class Trace(Resource):
    """A recorded agent run. May contain another customer's data."""

    owner_customer_id: int | None = None


@dataclass(frozen=True)
class Account(Resource):
    """A user account, as the subject of administration."""

    user_id: int
