"""Every caller that can run the agent must pass the carrier.

Written after finding that none of them did. `respond` took `carrier` with a
default of None, so the API handed the agent no carrier at all and the tracking
tool reported an outage that was not happening. Everything type-checked, every
test passed, and the CLI worked - which is why the bug survived.

These tests check the wiring rather than the behaviour, because the behaviour is
"reports the carrier as unavailable", which is also what a genuine outage looks
like. That is precisely what made it invisible.
"""

import inspect

from deskpilot.api.routers import tickets
from deskpilot.evals import runner
from deskpilot.graph.context import AgentContext

RUNS_THE_AGENT = (
    tickets.open_ticket,
    tickets.reply_to_ticket,
    tickets.open_ticket_streaming,
    tickets.reply_streaming,
)


def test_every_agent_endpoint_asks_for_a_carrier() -> None:
    for endpoint in RUNS_THE_AGENT:
        assert "carrier" in inspect.signature(endpoint).parameters, endpoint.__name__


def test_respond_has_no_default_carrier() -> None:
    """A default is what hid this. Requiring it makes forgetting a type error."""
    carrier = inspect.signature(tickets.respond).parameters["carrier"]

    assert carrier.default is inspect.Parameter.empty


def test_turn_events_has_no_default_carrier() -> None:
    carrier = inspect.signature(tickets.turn_events).parameters["carrier"]

    assert carrier.default is inspect.Parameter.empty


def test_the_eval_runner_builds_one() -> None:
    """Without it the two carrier eval cases would fail for the wrong reason."""
    source = inspect.getsource(runner.run_suite)

    assert "ShipTrackClient" in source
    assert "aclose" in source


def test_a_context_without_a_carrier_is_still_valid() -> None:
    """None is a real configuration, not an accident: no secret, no carrier."""
    assert "carrier" in AgentContext.__dataclass_fields__
    assert AgentContext.__dataclass_fields__["carrier"].default is None
