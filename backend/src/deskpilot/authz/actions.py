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


# Actions worth recording even when they are allowed.
#
# Every denial is recorded, always. Allows are not: a customer reading their own
# order happens on every turn of every conversation, and a log that records it
# buries the entries somebody would actually want to find. These are the ones where
# "who did this, and when" is the question, not "was anything refused".
ALWAYS_AUDITED = frozenset(
    {
        Action.REFUND_APPROVE,
        Action.PROPOSAL_EDIT,
        Action.PROPOSAL_REJECT,
        Action.TICKET_VIEW_ANY,
        Action.TRACE_VIEW,
        Action.USER_MANAGE,
    }
)


class AuditEvent(StrEnum):
    """Things worth recording that are not authorization decisions.

    Three names carry "token" or "password", which the security linter reads as
    hardcoded credentials. They are event names, suppressed one line at a time
    rather than for the whole file.
    """

    LOGIN_SUCCEEDED = "login.succeeded"
    LOGIN_FAILED = "login.failed"
    LOGIN_REFUSED_LOCKED = "login.refused_locked"
    ACCOUNT_LOCKED = "account.locked"
    LOGGED_OUT = "session.logged_out"
    TOKEN_REFRESHED = "session.refreshed"  # noqa: S105 - an event name
    TOKEN_REUSE_DETECTED = "session.token_reuse_detected"  # noqa: S105 - an event name
    PASSWORD_CHANGED = "account.password_changed"  # noqa: S105 - an event name
    ACCOUNT_CREATED = "account.created"
    ATTRIBUTES_CHANGED = "account.attributes_changed"
