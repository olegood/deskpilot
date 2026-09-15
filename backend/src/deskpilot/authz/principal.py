"""The principal: who is acting, and the attributes a decision weighs.

A principal is built from the database at the moment of the decision, never from a
token claim. A claim baked in at login is a snapshot of a permission that may since
have been taken away (see docs/decisions.md, D-060).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import inspect

from deskpilot.db.models import Customer, Region, User, UserRole


@dataclass(frozen=True)
class Principal:
    """Everything a policy is allowed to look at about the actor.

    Frozen, so a policy cannot quietly adjust the attributes it is judging. Built
    by from_user, which is the only place attributes are read.
    """

    # None when nobody is signed in: the impersonation escape hatch, and the eval
    # suite, act for a customer without a login. An audit entry then records no
    # actor, which is true and is also what the foreign key requires.
    user_id: int | None
    email: str
    role: UserRole
    is_active: bool
    # The customer this account can act as, when it is a customer account at all.
    # None for staff, who own no orders and no tickets.
    customer_id: int | None = None
    # Regions a member of staff may act in. Empty for customers, who are scoped by
    # ownership rather than by region.
    regions: frozenset[Region] = field(default_factory=frozenset)
    # The most this person may approve alone, in cents.
    approval_limit_cents: int = 0
    # The region of this account's own customer record, when it has one. Used to
    # describe their own orders and tickets to the policy; staff have None.
    home_region: Region | None = None

    @classmethod
    def from_user(cls, user: User) -> Principal:
        """Read a principal off a user row.

        The customer relationship has to be loaded, because a customer's home
        region is one of the attributes a policy weighs. Checked explicitly rather
        than left to lazy="raise": the failure would otherwise surface deep inside
        an unrelated call, and the fix is not obvious from the message.
        """
        if "customer" in inspect(user).unloaded:
            raise ValueError(
                "Principal.from_user needs the customer relationship loaded; "
                "query with .options(selectinload(User.customer))"
            )
        return cls(
            user_id=user.id,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            customer_id=user.customer_id,
            # Unknown region strings are dropped rather than raising: a region
            # removed from the enum should narrow what somebody can do, not break
            # every request they make.
            regions=frozenset(Region(value) for value in user.regions if value in set(Region)),
            approval_limit_cents=user.approval_limit_cents,
            home_region=user.customer.region if user.customer is not None else None,
        )

    @property
    def is_staff(self) -> bool:
        return self.role in {UserRole.REVIEWER, UserRole.SUPERVISOR, UserRole.ADMIN}

    @classmethod
    def for_customer(cls, customer: Customer) -> Principal:
        """A principal for a customer with no login of their own.

        Used by the impersonation escape hatch, and by anything that acts on a
        customer's behalf without them being signed in. Carries no staff
        attributes, so it can only ever reach what that customer owns.
        """
        return cls(
            user_id=None,
            email=customer.email,
            role=UserRole.CUSTOMER,
            is_active=True,
            customer_id=customer.id,
            home_region=customer.region,
        )

    def covers(self, region: Region | None) -> bool:
        """True when this principal may act in a region."""
        return region is not None and region in self.regions
