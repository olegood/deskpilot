"""The principal: who is acting, and the attributes a decision weighs.

A principal is built from the database at the moment of the decision, never from a
token claim. A claim baked in at login is a snapshot of a permission that may since
have been taken away (see docs/decisions.md, D-060).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from deskpilot.db.models import Region, User, UserRole


@dataclass(frozen=True)
class Principal:
    """Everything a policy is allowed to look at about the actor.

    Frozen, so a policy cannot quietly adjust the attributes it is judging. Built
    by from_user, which is the only place attributes are read.
    """

    user_id: int
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

    @classmethod
    def from_user(cls, user: User) -> Principal:
        """Read a principal off a loaded user row."""
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
        )

    @property
    def is_staff(self) -> bool:
        return self.role in {UserRole.REVIEWER, UserRole.SUPERVISOR, UserRole.ADMIN}

    def covers(self, region: Region | None) -> bool:
        """True when this principal may act in a region."""
        return region is not None and region in self.regions
