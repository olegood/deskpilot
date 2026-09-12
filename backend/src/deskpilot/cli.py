"""Deskpilot command-line interface. Run `uv run deskpilot --help`."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from deskpilot.config import get_settings
from deskpilot.db.seed import SeedError, SeedResult, seed
from deskpilot.db.session import create_engine, create_session_factory

app = typer.Typer(no_args_is_help=True, help="Deskpilot: AI support agent for Acme Gear.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")


@db_app.command("seed")
def seed_command(
    reset: Annotated[
        bool, typer.Option("--reset", help="Delete all shop data before seeding")
    ] = False,
) -> None:
    """Load the deterministic Acme Gear seed data."""
    try:
        result = asyncio.run(_seed(reset=reset))
    except SeedError as exc:
        typer.secho(f"Seeding failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(
        f"Seeded {result.customers} customers, {result.products} products, {result.orders} orders."
    )


async def _seed(*, reset: bool) -> SeedResult:
    engine = create_engine(get_settings().database)
    try:
        async with create_session_factory(engine)() as session:
            return await seed(session, reset=reset)
    finally:
        await engine.dispose()
