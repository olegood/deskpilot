"""The decision engine: deny by default, first rule to speak wins.

A policy is a pure function of (principal, action, resource). It returns an
allow, a deny, or None meaning "not my business". The rules are tried in order and
the first one that answers decides. If none answers, the request is denied, and the
denial says so rather than pretending a rule rejected it.

Ordering is deliberate: the rules that deny come first, so no later allow can
override them. A separation-of-duties rule that can be outvoted is not a rule.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from deskpilot.authz.actions import Action
from deskpilot.authz.principal import Principal
from deskpilot.authz.resources import Resource

logger = logging.getLogger(__name__)

# What a caller is told when something is refused. The real reason is recorded, not
# returned: "you may not approve above 500.00" is useful to a colleague and useful
# to somebody probing for limits.
FORBIDDEN = "You are not allowed to do that."


@dataclass(frozen=True)
class Decision:
    """The outcome, and the rule that produced it."""

    allowed: bool
    # Written for a person reading an audit log, not for the person refused.
    reason: str
    # The rule that decided. "default" when nothing matched.
    policy: str = "default"

    def __bool__(self) -> bool:
        return self.allowed


class Forbidden(Exception):
    """Raised by require() when a decision denies."""

    def __init__(self, decision: Decision) -> None:
        super().__init__(FORBIDDEN)
        self.decision = decision


def allow(reason: str) -> Decision:
    return Decision(allowed=True, reason=reason)


def deny(reason: str) -> Decision:
    return Decision(allowed=False, reason=reason)


# A rule answers, or returns None to pass.
Rule = Callable[[Principal, Action, Resource], Decision | None]


def decide(
    principal: Principal,
    action: Action,
    resource: Resource,
    rules: Sequence[Rule] | None = None,
) -> Decision:
    """Evaluate one request. Never raises; always returns a Decision."""
    from deskpilot.authz.policies import RULES

    for rule in rules if rules is not None else RULES:
        decision = rule(principal, action, resource)
        if decision is not None:
            named = Decision(decision.allowed, decision.reason, rule.__name__)
            if not named.allowed:
                logger.info(
                    "denied %s for user %s by %s: %s",
                    action.value,
                    principal.user_id,
                    named.policy,
                    named.reason,
                )
            return named

    # Nothing matched. An action nobody wrote a rule for is not an oversight to
    # work around; it is an action that has not been thought about yet.
    logger.info("denied %s for user %s: no rule allows it", action.value, principal.user_id)
    return Decision(allowed=False, reason=f"no rule allows {action.value} on this resource")


def require(
    principal: Principal,
    action: Action,
    resource: Resource,
    rules: Sequence[Rule] | None = None,
) -> Decision:
    """Evaluate, and raise Forbidden if the answer is no."""
    decision = decide(principal, action, resource, rules)
    if not decision.allowed:
        raise Forbidden(decision)
    return decision
