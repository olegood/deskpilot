"""The rules.

Read this file top to bottom: that is the order they are applied in, and the order
is part of the policy. Denials come first so that nothing below can override them.

Every rule is a pure function with no I/O, which is what makes the whole set
testable as a matrix rather than as a handful of scenarios.
"""

from __future__ import annotations

from deskpilot.authz import resources
from deskpilot.authz.actions import Action
from deskpilot.authz.engine import Decision, Rule, allow, deny
from deskpilot.authz.principal import Principal
from deskpilot.authz.resources import Resource
from deskpilot.db.models import UserRole

# ── denials, applied first ──────────────────────────────────────────────────


def inactive_accounts_do_nothing(
    principal: Principal, action: Action, resource: Resource
) -> Decision | None:
    """A disabled account is refused everything, before any other rule runs."""
    if not principal.is_active:
        return deny("the account is disabled")
    return None


def nobody_approves_their_own_ticket(
    principal: Principal, action: Action, resource: Resource
) -> Decision | None:
    """Separation of duties.

    Placed among the denials so no later rule can allow it. A reviewer who is also
    a customer must not approve a refund on their own order, whatever their limit
    says and whichever region they cover.
    """
    if action not in {Action.REFUND_APPROVE, Action.PROPOSAL_EDIT, Action.PROPOSAL_REJECT}:
        return None
    if not isinstance(resource, resources.Proposal):
        return None
    own_ticket = (
        principal.customer_id is not None
        and principal.customer_id == resource.ticket_owner_customer_id
    ) or (resource.owner_user_id is not None and resource.owner_user_id == principal.user_id)
    if own_ticket:
        return deny("a person cannot decide on a proposal raised on their own ticket")
    return None


# ── customers, scoped by ownership ──────────────────────────────────────────


def a_customer_reads_their_own_things(
    principal: Principal, action: Action, resource: Resource
) -> Decision | None:
    """Ownership, not region, is what scopes a customer.

    Deliberately not "role is customer": a reviewer who is also a customer reads
    their own orders by exactly the same rule.
    """
    if action not in {
        Action.ORDER_VIEW,
        Action.TICKET_VIEW,
        Action.TICKET_CREATE,
        Action.TICKET_REPLY,
        Action.CUSTOMER_VIEW,
    }:
        return None
    if principal.customer_id is None:
        return None

    owner = None
    match resource:
        case resources.Order() | resources.Ticket():
            owner = resource.owner_customer_id
        case resources.CustomerProfile():
            owner = resource.customer_id
        case _:
            return None

    if owner == principal.customer_id:
        return allow("the customer owns this")
    return None


def anybody_signed_in_may_read_the_policies(
    principal: Principal, action: Action, resource: Resource
) -> Decision | None:
    """The published policies are what the shop tells the public."""
    if action is Action.POLICY_SEARCH and isinstance(resource, resources.PolicyDocuments):
        return allow("the policies are published")
    return None


# ── staff, scoped by region and limit ───────────────────────────────────────


def staff_read_tickets_in_their_regions(
    principal: Principal, action: Action, resource: Resource
) -> Decision | None:
    if action not in {Action.TICKET_VIEW, Action.TICKET_VIEW_ANY, Action.ORDER_VIEW}:
        return None
    if not principal.is_staff:
        return None
    match resource:
        case resources.Ticket() | resources.Order():
            if principal.covers(resource.region):
                return allow(f"staff covering {resource.region.value if resource.region else '?'}")
            named = resource.region.value if resource.region else "unknown region"
            return deny(f"this is a {named} ticket and they do not cover it")
        case _:
            return None


def a_reviewer_approves_within_their_limit(
    principal: Principal, action: Action, resource: Resource
) -> Decision | None:
    """The money rule.

    An amount above the limit is denied here rather than passed on, so the denial
    names the limit in the audit log. Routing it to a supervisor is the caller's
    job, not the policy's.
    """
    if action is not Action.REFUND_APPROVE or not isinstance(resource, resources.Proposal):
        return None
    if principal.role not in {UserRole.REVIEWER, UserRole.SUPERVISOR}:
        return None
    if not principal.covers(resource.region):
        return deny(f"they do not cover {resource.region.value}")
    if resource.amount_cents > principal.approval_limit_cents:
        return deny(
            f"{resource.amount_cents} is above their limit of {principal.approval_limit_cents}"
        )
    return allow("within their approval limit and region")


def staff_edit_and_reject_within_their_regions(
    principal: Principal, action: Action, resource: Resource
) -> Decision | None:
    """Rejecting costs nothing, so it is not limited by amount.

    Editing is. An edit that could raise an amount above the limit would be a way
    around the limit, so it is judged on the amount just like an approval.
    """
    if action not in {Action.PROPOSAL_EDIT, Action.PROPOSAL_REJECT}:
        return None
    if not isinstance(resource, resources.Proposal) or not principal.is_staff:
        return None
    if not principal.covers(resource.region):
        return deny(f"they do not cover {resource.region.value}")
    if action is Action.PROPOSAL_EDIT and resource.amount_cents > principal.approval_limit_cents:
        return deny("editing above their approval limit would be a way around it")
    return allow("staff covering this region")


# ── administration ──────────────────────────────────────────────────────────


def admins_manage_accounts_and_read_the_record(
    principal: Principal, action: Action, resource: Resource
) -> Decision | None:
    """Reading the audit log is itself audited, which is the point of it.

    Somebody who can read the record of what everyone did should leave a record of
    having read it.
    """
    if action not in {Action.USER_MANAGE, Action.TRACE_VIEW, Action.AUDIT_VIEW}:
        return None
    if principal.role is UserRole.ADMIN:
        return allow("administrator")
    return deny("only an administrator may do this")


# Order matters. Denials first, then ownership, then region and limit.
RULES: tuple[Rule, ...] = (
    inactive_accounts_do_nothing,
    nobody_approves_their_own_ticket,
    a_customer_reads_their_own_things,
    anybody_signed_in_may_read_the_policies,
    staff_read_tickets_in_their_regions,
    a_reviewer_approves_within_their_limit,
    staff_edit_and_reject_within_their_regions,
    admins_manage_accounts_and_read_the_record,
)
