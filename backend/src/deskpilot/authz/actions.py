"""The verbs a policy can decide about.

Named after what is being attempted, not after who may do it. "refund.approve"
stays the same sentence whether a reviewer or a supervisor is asking.
"""

from enum import StrEnum


class Action(StrEnum):
    # Reading a customer's own world.
    TICKET_VIEW = "ticket.view"
    TICKET_CREATE = "ticket.create"
    TICKET_REPLY = "ticket.reply"
    ORDER_VIEW = "order.view"
    CUSTOMER_VIEW = "customer.view"
    POLICY_SEARCH = "policy.search"

    # Staff work. Nothing performs these yet; the human-in-the-loop milestone does.
    TICKET_VIEW_ANY = "ticket.view_any"
    REFUND_APPROVE = "refund.approve"
    PROPOSAL_EDIT = "proposal.edit"
    PROPOSAL_REJECT = "proposal.reject"

    # Administration.
    TRACE_VIEW = "trace.view"
    USER_MANAGE = "user.manage"
