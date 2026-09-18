"""Running the eval suite.

Every case gets its own thread id and no checkpointer, so cases cannot see each
other's conversations and the suite can be re-run without resetting anything. The
whole run is written to JSONL, so a later milestone can re-score old transcripts
with a judge model instead of paying for the agent again.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# aliased: this module already has a select() that narrows the case list.
from sqlalchemy import select as sql_select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.authz.principal import Principal
from deskpilot.config import ModelRole, Settings
from deskpilot.db.models import Customer
from deskpilot.evals.dataset import DATASETS_DIR, EvalCase
from deskpilot.evals.scoring import CaseResult, Summary, crashed, score, summarise
from deskpilot.graph.agent import build_agent_graph
from deskpilot.graph.context import AgentContext
from deskpilot.graph.runner import run_turn
from deskpilot.integrations.shiptrack import ShipTrackClient
from deskpilot.llm import build_chat_model
from deskpilot.tools import ALL_TOOLS

RUNS_DIR = DATASETS_DIR / "runs"


@dataclass(frozen=True)
class Report:
    """One run of the suite, with enough context to compare it against another."""

    results: list[CaseResult]
    summary: Summary
    agent_model: str
    classifier_model: str
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def select(cases: list[EvalCase], only: list[str], tags: list[str]) -> list[EvalCase]:
    """Narrow the suite. An empty filter means everything."""
    if only:
        return [case for case in cases if case.id in set(only)]
    if tags:
        wanted = set(tags)
        return [case for case in cases if wanted & set(case.tags)]
    return cases


async def run_case(
    case: EvalCase,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    carrier: ShipTrackClient | None = None,
) -> CaseResult:
    """Run one case in isolation and score it.

    The principal is built from the seeded customer directly. Eval cases name a
    customer, not a login, and giving them one would make the suite depend on
    accounts that the seed does not create.
    """
    async with sessions() as session:
        customer = await session.scalar(sql_select(Customer).where(Customer.email == case.customer))
    if customer is None:
        return crashed(case, ValueError(f"no seeded customer {case.customer}"), 0.0)
    principal = Principal.for_customer(customer)

    graph = build_agent_graph(
        model=build_chat_model(ModelRole.AGENT, settings),
        tools=ALL_TOOLS,
        max_steps=settings.max_agent_steps,
        # No checkpointer: a case is a single fresh conversation every time.
        classifier=build_chat_model(ModelRole.CLASSIFIER, settings),
    )
    context = AgentContext(
        principal=principal,
        session_factory=sessions,
        carrier=carrier,
        policy_search=settings.policy_search,
        tools=settings.tools,
    )
    started = time.perf_counter()
    try:
        run = await run_turn(graph, case.message, context, f"eval-{case.id}-{uuid.uuid4()}")
    except Exception as exc:  # one bad case must not stop the suite
        return crashed(case, exc, time.perf_counter() - started)
    return score(case, run, time.perf_counter() - started)


async def run_suite(
    cases: list[EvalCase],
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    concurrency: int = 2,
) -> Report:
    """Run every case, a few at a time, and summarise.

    Concurrency defaults to 2 to match OLLAMA_NUM_PARALLEL. Going wider does not
    make a local model faster; it just makes every case wait longer.
    """
    limit = asyncio.Semaphore(concurrency)

    async def guarded(case: EvalCase, carrier: ShipTrackClient | None) -> CaseResult:
        async with limit:
            return await run_case(case, sessions, settings, carrier)

    # One carrier for the suite, closed afterwards. Cases that never call it pay
    # nothing; cases that do would otherwise all report an outage.
    carrier = ShipTrackClient(settings.shiptrack) if settings.shiptrack.secret else None
    try:
        results = await asyncio.gather(*(guarded(case, carrier) for case in cases))
    finally:
        if carrier is not None:
            await carrier.aclose()
    ordered = sorted(results, key=lambda result: [case.id for case in cases].index(result.case_id))
    return Report(
        results=ordered,
        summary=summarise(ordered),
        agent_model=settings.agent.model,
        classifier_model=settings.classifier.model,
    )


def write_report(report: Report, directory: Path = RUNS_DIR) -> Path:
    """Save a run as JSONL: one header line, then one line per case."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.replace(":", "-").replace("+00:00", "Z")
    path = directory / f"{stamp}.jsonl"
    lines = [
        json.dumps(
            {
                "kind": "run",
                "started_at": report.started_at,
                "agent_model": report.agent_model,
                "classifier_model": report.classifier_model,
                **asdict(report.summary),
            }
        )
    ]
    lines.extend(json.dumps({"kind": "case", **as_json(result)}) for result in report.results)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def as_json(result: CaseResult) -> dict[str, object]:
    """A CaseResult as plain JSON, with enums turned into their values."""
    data = asdict(result)
    for key in ("expected_category", "actual_category"):
        value = data[key]
        data[key] = value.value if value is not None else None
    return data
