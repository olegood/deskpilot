"""Deskpilot command-line interface. Run `uv run deskpilot --help`."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from deskpilot.config import get_settings
from deskpilot.db.seed import SeedError, SeedResult, seed
from deskpilot.db.session import create_engine, create_session_factory
from deskpilot.graph.context import AgentContext
from deskpilot.graph.runner import AgentRun, answer_question

app = typer.Typer(no_args_is_help=True, help="Deskpilot: AI support agent for Acme Gear.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")


@db_app.command("seed")
def seed_command(
    reset: Annotated[
        bool, typer.Option("--reset", help="Delete all shop data before seeding.")
    ] = False,
) -> None:
    """Load the deterministic Acme Gear seed data."""
    try:
        result = asyncio.run(_seed(reset=reset))
    except SeedError as exc:
        typer.secho(f"Seeding failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(
        f"Seeded {result.customers} customers, {result.products} products, {result.orders} orders.",
        fg=typer.colors.GREEN,
    )


async def _seed(*, reset: bool) -> SeedResult:
    engine = create_engine(get_settings().database)
    try:
        async with create_session_factory(engine)() as session:
            return await seed(session, reset=reset)
    finally:
        await engine.dispose()


@app.command("ask")
def ask_command(
    question: Annotated[str, typer.Argument(help="What the customer is asking.")],
    customer: Annotated[
        str,
        typer.Option(
            "--as",
            help="Email of the signed-in customer. The agent sees only this person's data.",
        ),
    ],
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Also show tool calls and token usage.")
    ] = False,
) -> None:
    """Ask the agent a question as one of the shop's customers."""
    run = asyncio.run(_ask(question, customer))
    typer.echo(run.answer)
    if verbose:
        typer.secho(
            f"\n[{run.steps} model call(s) | "
            f"tools: {', '.join(run.tool_calls) or 'none'} | "
            f"tokens: {run.input_tokens} in, {run.output_tokens} out]",
            fg=typer.colors.BRIGHT_BLACK,
        )


async def _ask(question: str, customer_email: str) -> AgentRun:
    settings = get_settings()
    engine = create_engine(settings.database)
    try:
        context = AgentContext(
            customer_email=customer_email,
            session_factory=create_session_factory(engine),
        )
        return await answer_question(question, context, settings)
    finally:
        await engine.dispose()
