"""The decision matrix.

Every rule is a pure function of plain data, so the whole policy can be tested
exhaustively rather than through a handful of scenarios. That is the point of
building it this way: a policy you cannot enumerate is a policy you are guessing
about.
"""

import itertools

import pytest

from deskpilot.authz import resources
from deskpilot.authz.actions import Action
from deskpilot.authz.engine import Decision, Forbidden, decide, require
from deskpilot.authz.policies import RULES
from deskpilot.authz.principal import Principal
from deskpilot.db.models import Region, User, UserRole

NOAH_CUSTOMER_ID = 4
ANA_CUSTOMER_ID = 1


def customer(customer_id: int = NOAH_CUSTOMER_ID, **overrides: object) -> Principal:
    defaults: dict[str, object] = {
        "user_id": 10,
        "email": "noah.kim@example.com",
        "role": UserRole.CUSTOMER,
        "is_active": True,
        "customer_id": customer_id,
    }
    return Principal(**{**defaults, **overrides})  # type: ignore[arg-type]


def reviewer(
    regions: set[Region] | None = None,
    limit_cents: int = 50_000,
    role: UserRole = UserRole.REVIEWER,
    **overrides: object,
) -> Principal:
    defaults: dict[str, object] = {
        "user_id": 20,
        "email": "lena@acmegear.example",
        "role": role,
        "is_active": True,
        # `regions or {...}` would turn an explicit empty set back into EU, which
        # is exactly the case the "covers nothing" test needs.
        "regions": frozenset({Region.EU} if regions is None else regions),
        "approval_limit_cents": limit_cents,
    }
    return Principal(**{**defaults, **overrides})  # type: ignore[arg-type]


def admin() -> Principal:
    return Principal(user_id=30, email="root@acmegear.example", role=UserRole.ADMIN, is_active=True)


def order(owner: int = NOAH_CUSTOMER_ID, region: Region = Region.NA) -> resources.Order:
    return resources.Order(owner_customer_id=owner, region=region)


def ticket(owner: int = NOAH_CUSTOMER_ID, region: Region = Region.NA) -> resources.Ticket:
    return resources.Ticket(owner_customer_id=owner, region=region)


def proposal(
    owner: int = NOAH_CUSTOMER_ID,
    region: Region = Region.EU,
    amount_cents: int = 10_000,
    owner_user_id: int | None = None,
) -> resources.Proposal:
    return resources.Proposal(
        ticket_owner_customer_id=owner,
        region=region,
        amount_cents=amount_cents,
        owner_user_id=owner_user_id,
    )


# ── deny by default ─────────────────────────────────────────────────────────


def test_an_action_no_rule_covers_is_denied() -> None:
    """The default is no, and the reason says the default was reached."""
    outcome = decide(admin(), Action.REFUND_APPROVE, resources.PolicyDocuments())

    assert not outcome.allowed
    assert outcome.policy == "default"
    assert "no rule allows" in outcome.reason


def test_an_empty_rule_set_denies_everything() -> None:
    outcome = decide(admin(), Action.POLICY_SEARCH, resources.PolicyDocuments(), rules=())

    assert not outcome.allowed


@pytest.mark.parametrize("action", list(Action))
def test_a_disabled_account_is_refused_every_action(action: Action) -> None:
    """Checked before anything else, so no later rule can let one through."""
    for resource in (order(), ticket(), proposal(), resources.PolicyDocuments()):
        outcome = decide(
            admin().__class__(**{**vars(admin()), "is_active": False}), action, resource
        )
        assert not outcome.allowed
        assert outcome.reason == "the account is disabled"


# ── ownership ───────────────────────────────────────────────────────────────


def test_a_customer_reads_their_own_order() -> None:
    assert decide(customer(), Action.ORDER_VIEW, order(owner=NOAH_CUSTOMER_ID)).allowed


def test_a_customer_cannot_read_another_customers_order() -> None:
    assert not decide(customer(), Action.ORDER_VIEW, order(owner=ANA_CUSTOMER_ID)).allowed


def test_a_customer_cannot_read_another_customers_ticket() -> None:
    assert not decide(customer(), Action.TICKET_VIEW, ticket(owner=ANA_CUSTOMER_ID)).allowed


def test_ownership_is_what_decides_not_the_role() -> None:
    """A reviewer who is also a customer reads their own orders by the same rule."""
    both = reviewer(customer_id=NOAH_CUSTOMER_ID)

    outcome = decide(both, Action.ORDER_VIEW, order(owner=NOAH_CUSTOMER_ID, region=Region.APAC))

    assert outcome.allowed
    assert outcome.policy == "a_customer_reads_their_own_things"


def test_staff_without_a_customer_record_own_nothing() -> None:
    assert not decide(reviewer(), Action.TICKET_VIEW, ticket(region=Region.NA)).allowed


def test_everybody_signed_in_may_search_the_policies() -> None:
    for principal in (customer(), reviewer(), admin()):
        assert decide(principal, Action.POLICY_SEARCH, resources.PolicyDocuments()).allowed


# ── region ──────────────────────────────────────────────────────────────────


def test_a_reviewer_reads_a_ticket_in_a_region_they_cover() -> None:
    assert decide(reviewer({Region.EU}), Action.TICKET_VIEW, ticket(region=Region.EU)).allowed


def test_a_reviewer_cannot_read_a_ticket_outside_their_regions() -> None:
    outcome = decide(reviewer({Region.EU}), Action.TICKET_VIEW, ticket(region=Region.APAC))

    assert not outcome.allowed
    assert "apac" in outcome.reason


def test_covering_several_regions_works() -> None:
    wide = reviewer({Region.EU, Region.NA})

    assert decide(wide, Action.TICKET_VIEW, ticket(region=Region.EU)).allowed
    assert decide(wide, Action.TICKET_VIEW, ticket(region=Region.NA)).allowed
    assert not decide(wide, Action.TICKET_VIEW, ticket(region=Region.APAC)).allowed


def test_a_reviewer_covering_nothing_reads_nothing() -> None:
    assert not decide(reviewer(regions=set()), Action.TICKET_VIEW, ticket(region=Region.EU)).allowed


# ── approval limits ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(1, True), (49_999, True), (50_000, True), (50_001, False), (500_000, False)],
)
def test_the_limit_is_inclusive(amount: int, expected: bool) -> None:
    outcome = decide(
        reviewer(limit_cents=50_000), Action.REFUND_APPROVE, proposal(amount_cents=amount)
    )

    assert outcome.allowed is expected


def test_a_denial_over_the_limit_names_the_limit_for_the_audit_log() -> None:
    outcome = decide(
        reviewer(limit_cents=50_000), Action.REFUND_APPROVE, proposal(amount_cents=90_000)
    )

    assert "90000" in outcome.reason
    assert "50000" in outcome.reason


def test_a_supervisor_has_their_own_larger_limit() -> None:
    supervisor = reviewer(limit_cents=1_000_000, role=UserRole.SUPERVISOR)

    assert decide(supervisor, Action.REFUND_APPROVE, proposal(amount_cents=500_000)).allowed


def test_region_is_checked_before_the_amount() -> None:
    outcome = decide(
        reviewer({Region.EU}), Action.REFUND_APPROVE, proposal(region=Region.NA, amount_cents=1)
    )

    assert not outcome.allowed
    assert "cover" in outcome.reason


def test_a_customer_approves_nothing() -> None:
    assert not decide(customer(), Action.REFUND_APPROVE, proposal(owner=ANA_CUSTOMER_ID)).allowed


def test_an_admin_is_not_automatically_an_approver() -> None:
    """Administration is not the same job as approving money."""
    assert not decide(admin(), Action.REFUND_APPROVE, proposal()).allowed


# ── separation of duties ────────────────────────────────────────────────────


def test_nobody_approves_a_proposal_on_their_own_ticket() -> None:
    both = reviewer(customer_id=NOAH_CUSTOMER_ID, regions={Region.EU}, limit_cents=10_000_000)

    outcome = decide(both, Action.REFUND_APPROVE, proposal(owner=NOAH_CUSTOMER_ID))

    assert not outcome.allowed
    assert outcome.policy == "nobody_approves_their_own_ticket"


def test_separation_of_duties_beats_an_unlimited_limit_and_a_covered_region() -> None:
    """It is placed among the denials precisely so nothing can outvote it."""
    both = reviewer(
        customer_id=NOAH_CUSTOMER_ID,
        regions={Region.EU, Region.NA, Region.APAC},
        limit_cents=99_999_999,
        role=UserRole.SUPERVISOR,
    )

    for action in (Action.REFUND_APPROVE, Action.PROPOSAL_EDIT, Action.PROPOSAL_REJECT):
        assert not decide(both, action, proposal(owner=NOAH_CUSTOMER_ID)).allowed


def test_it_also_catches_a_match_on_the_user_id() -> None:
    """A staff account with no customer record can still own the ticket."""
    staff = reviewer(regions={Region.EU})

    outcome = decide(staff, Action.REFUND_APPROVE, proposal(owner=999, owner_user_id=staff.user_id))

    assert not outcome.allowed


def test_somebody_elses_ticket_is_fine() -> None:
    both = reviewer(customer_id=NOAH_CUSTOMER_ID, regions={Region.EU})

    assert decide(both, Action.REFUND_APPROVE, proposal(owner=ANA_CUSTOMER_ID)).allowed


# ── editing and rejecting ───────────────────────────────────────────────────


def test_rejecting_is_not_limited_by_amount() -> None:
    """Saying no costs nothing, so the money limit does not apply."""
    assert decide(
        reviewer(limit_cents=1), Action.PROPOSAL_REJECT, proposal(amount_cents=10_000_000)
    ).allowed


def test_editing_above_the_limit_is_refused() -> None:
    """Otherwise editing an amount upwards would be a way around the limit."""
    outcome = decide(
        reviewer(limit_cents=50_000), Action.PROPOSAL_EDIT, proposal(amount_cents=90_000)
    )

    assert not outcome.allowed
    assert "way around" in outcome.reason


# ── administration ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("action", [Action.USER_MANAGE, Action.TRACE_VIEW])
def test_only_an_admin_administers(action: Action) -> None:
    assert decide(admin(), action, resources.Account(user_id=1)).allowed
    assert not decide(reviewer(), action, resources.Account(user_id=1)).allowed
    assert not decide(customer(), action, resources.Account(user_id=1)).allowed


# ── the engine itself ───────────────────────────────────────────────────────


def test_a_decision_names_the_rule_that_made_it() -> None:
    outcome = decide(customer(), Action.ORDER_VIEW, order())

    assert outcome.policy == "a_customer_reads_their_own_things"


def test_require_raises_on_a_denial_without_leaking_the_reason() -> None:
    with pytest.raises(Forbidden) as caught:
        require(customer(), Action.ORDER_VIEW, order(owner=ANA_CUSTOMER_ID))

    # The caller gets a flat refusal; the reason is on the exception, for the log.
    assert str(caught.value) == "You are not allowed to do that."
    assert caught.value.decision.reason


def test_require_returns_the_decision_when_allowed() -> None:
    assert require(customer(), Action.ORDER_VIEW, order()).allowed


def test_a_decision_is_truthy_when_allowed() -> None:
    assert Decision(allowed=True, reason="")
    assert not Decision(allowed=False, reason="")


def test_every_rule_is_reachable() -> None:
    """A rule nothing can trigger is either dead or a typo."""
    scenarios = [
        (customer(), Action.ORDER_VIEW, order()),
        (customer(), Action.POLICY_SEARCH, resources.PolicyDocuments()),
        (reviewer(), Action.TICKET_VIEW, ticket(region=Region.EU)),
        (reviewer(), Action.REFUND_APPROVE, proposal()),
        (reviewer(), Action.PROPOSAL_REJECT, proposal()),
        (admin(), Action.USER_MANAGE, resources.Account(user_id=1)),
        (customer(is_active=False), Action.ORDER_VIEW, order()),
        (
            reviewer(customer_id=NOAH_CUSTOMER_ID),
            Action.REFUND_APPROVE,
            proposal(owner=NOAH_CUSTOMER_ID),
        ),
    ]
    fired = {decide(*scenario).policy for scenario in scenarios}

    assert {rule.__name__ for rule in RULES} <= fired


def test_no_combination_lets_a_customer_reach_another_customer() -> None:
    """The sweep: every action, every resource, somebody else's data."""
    outsider = customer(customer_id=NOAH_CUSTOMER_ID)
    theirs = [order(owner=ANA_CUSTOMER_ID, region=region) for region in Region] + [
        ticket(owner=ANA_CUSTOMER_ID, region=region) for region in Region
    ]

    for action, resource in itertools.product(list(Action), theirs):
        assert not decide(outsider, action, resource).allowed, f"{action} on {resource}"


def test_a_principal_is_built_from_the_row_not_from_a_claim() -> None:
    user = User(
        id=20,
        email="lena@acmegear.example",
        password_hash="unused",
        full_name="Lena Fox",
        role=UserRole.REVIEWER,
        is_active=True,
        customer_id=None,
        regions=["eu", "na"],
        approval_limit_cents=50_000,
    )
    # from_user reads the customer relationship, so it has to be set even when
    # there is no customer. A staff account genuinely has none.
    user.customer = None

    principal = Principal.from_user(user)

    assert principal.regions == frozenset({Region.EU, Region.NA})
    assert principal.approval_limit_cents == 50_000
    assert principal.is_staff


def test_an_unknown_region_narrows_rather_than_breaks() -> None:
    """A region dropped from the enum should take access away, not raise."""
    user = User(
        id=20,
        email="lena@acmegear.example",
        password_hash="unused",
        full_name="Lena Fox",
        role=UserRole.REVIEWER,
        is_active=True,
        regions=["eu", "antarctica"],
        approval_limit_cents=0,
    )
    user.customer = None

    assert Principal.from_user(user).regions == frozenset({Region.EU})


def test_a_principal_without_a_login_has_no_user_id() -> None:
    """Impersonation and the eval suite act for a customer nobody signed in as."""
    from deskpilot.db.models import Customer, CustomerTier

    principal = Principal.for_customer(
        Customer(
            id=4,
            email="noah.kim@example.com",
            full_name="Noah Kim",
            region=Region.NA,
            tier=CustomerTier.STANDARD,
        )
    )

    assert principal.user_id is None
    assert principal.customer_id == 4
    assert principal.home_region is Region.NA
    # No staff attributes: the escape hatch cannot hand any out.
    assert principal.regions == frozenset()
    assert principal.approval_limit_cents == 0
    assert not principal.is_staff
