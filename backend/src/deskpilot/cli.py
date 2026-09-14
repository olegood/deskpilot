"""Deskpilot command-line interface. Run `uv run deskpilot --help`."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

import typer
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.config import Settings, get_settings
from deskpilot.db.checkpointer import open_checkpointer, setup_checkpointer
from deskpilot.db.models import Ticket, TicketStatus
from deskpilot.db.seed import SeedError, seed
from deskpilot.db.session import create_engine, create_session_factory
from deskpilot.graph.context import AgentContext
from deskpilot.graph.runner import AgentRun, run_agent
from deskpilot.knowledge.index import DocumentState, PolicyIndexError, build_index, index_status
from deskpilot.knowledge.search import PolicyPassage, search_policy_index
from deskpilot.llm import build_embeddings
from deskpilot.tickets import TicketError, create_ticket, get_ticket, list_tickets, set_status

app = typer.Typer(no_args_is_help=True, help="Deskpilot: AI support agent for Acme Gear.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
ticket_app = typer.Typer(no_args_is_help=True, help="Support ticket commands.")
policy_app = typer.Typer(no_args_is_help=True, help="Policy knowledge base commands.")
app.add_typer(db_app, name="db")
app.add_typer(ticket_app, name="ticket")
app.add_typer(policy_app, name="policy")

# Options reused across commands.
CustomerOption = Annotated[
    str,
    typer.Option("--as", help="Email of the signed-in customer. The agent sees only their data."),
]
VerboseOption = Annotated[
    bool, typer.Option("--verbose", "-v", help="Also show tool calls and token usage.")
]


@dataclass
class Runtime:
    """Everything a command needs, opened once and closed on the way out."""

    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    checkpointer: BaseCheckpointSaver[Any]
    embeddings: Embeddings

    def context_for(self, customer_email: str) -> AgentContext:
        return AgentContext(
            customer_email=customer_email,
            session_factory=self.sessions,
            embeddings=self.embeddings,
            policy_search=self.settings.policy_search,
            tools=self.settings.tools,
        )


@asynccontextmanager
async def runtime() -> AsyncIterator[Runtime]:
    settings = get_settings()
    engine = create_engine(settings.database)
    try:
        async with open_checkpointer(settings.database) as checkpointer:
            yield Runtime(
                settings=settings,
                sessions=create_session_factory(engine),
                checkpointer=checkpointer,
                embeddings=build_embeddings(settings),
            )
    finally:
        await engine.dispose()


def run[T](coroutine: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run a command's async body, turning expected failures into clean exits."""
    try:
        return asyncio.run(coroutine())
    except (TicketError, SeedError, PolicyIndexError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def show_answer(result: AgentRun, verbose: bool) -> None:
    typer.echo(result.answer)
    if result.escalated:
        typer.secho("This ticket has been escalated to a human.", fg=typer.colors.YELLOW)
    if verbose:
        typer.secho(
            f"\n[{result.steps} model call(s) this turn | "
            f"tools: {', '.join(result.tool_calls) or 'none'} | "
            f"ticket tokens: {result.input_tokens} in, {result.output_tokens} out]",
            fg=typer.colors.BRIGHT_BLACK,
        )


# ── db ──────────────────────────────────────────────────────────────────────


@db_app.command("setup")
def db_setup_command() -> None:
    """Create LangGraph's checkpoint tables. Safe to run repeatedly."""

    async def body() -> None:
        await setup_checkpointer(get_settings().database)

    run(body)
    typer.secho("Checkpoint tables are ready.", fg=typer.colors.GREEN)


@db_app.command("seed")
def seed_command(
    reset: Annotated[
        bool, typer.Option("--reset", help="Delete all shop data before seeding.")
    ] = False,
) -> None:
    """Load the deterministic Acme Gear seed data."""

    async def body() -> str:
        async with runtime() as rt, rt.sessions() as session:
            result = await seed(session, reset=reset)
        return (
            f"Seeded {result.customers} customers, {result.products} products, "
            f"{result.orders} orders."
        )

    typer.secho(run(body), fg=typer.colors.GREEN)


# ── ticket ──────────────────────────────────────────────────────────────────


@ticket_app.command("new")
def ticket_new_command(
    message: Annotated[str, typer.Argument(help="The customer's first message.")],
    customer: CustomerOption,
    subject: Annotated[str, typer.Option("--subject", help="Short summary of the problem.")],
    verbose: VerboseOption = False,
) -> None:
    """Open a ticket and let the agent respond to the first message."""

    async def body() -> tuple[Ticket, AgentRun]:
        async with runtime() as rt, rt.sessions() as session:
            ticket = await create_ticket(session, customer, subject)
            result = await respond(rt, ticket, customer, message)
            set_status(ticket, status_after(result))
            await session.commit()
            return ticket, result

    ticket, result = run(body)
    typer.secho(f"Opened {ticket.reference}.\n", fg=typer.colors.GREEN)
    show_answer(result, verbose)


@ticket_app.command("reply")
def ticket_reply_command(
    reference: Annotated[str, typer.Argument(help="Ticket reference, e.g. TCK-0001.")],
    message: Annotated[str, typer.Argument(help="The customer's next message.")],
    customer: CustomerOption,
    verbose: VerboseOption = False,
) -> None:
    """Add a message to an open ticket. The agent picks up where it left off."""

    async def body() -> AgentRun:
        async with runtime() as rt, rt.sessions() as session:
            ticket = await get_ticket(session, reference, customer)
            if ticket.status is TicketStatus.RESOLVED:
                raise TicketError(f"{ticket.reference} is resolved; open a new ticket instead")
            result = await respond(rt, ticket, customer, message)
            set_status(ticket, status_after(result))
            await session.commit()
            return result

    show_answer(run(body), verbose)


@ticket_app.command("list")
def ticket_list_command(
    customer: Annotated[
        str | None, typer.Option("--as", help="Only this customer's tickets.")
    ] = None,
) -> None:
    """List recent tickets, newest first."""

    async def body() -> list[Ticket]:
        async with runtime() as rt, rt.sessions() as session:
            return await list_tickets(session, customer)

    tickets = run(body)
    if not tickets:
        typer.echo("No tickets yet.")
        return
    for ticket in tickets:
        typer.echo(
            f"{ticket.reference}  {ticket.status.value:<18}  "
            f"{ticket.customer.email:<28}  {ticket.subject}"
        )


@ticket_app.command("show")
def ticket_show_command(
    reference: Annotated[str, typer.Argument(help="Ticket reference, e.g. TCK-0001.")],
    customer: CustomerOption,
    tools: Annotated[
        bool, typer.Option("--tools", help="Also show tool calls and their results.")
    ] = False,
) -> None:
    """Print a ticket's conversation, read back from its checkpoint."""

    async def body() -> tuple[Ticket, list[AnyMessage]]:
        async with runtime() as rt, rt.sessions() as session:
            ticket = await get_ticket(session, reference, customer)
            return ticket, await load_conversation(rt, ticket)

    ticket, history = run(body)
    typer.secho(f"{ticket.reference}  {ticket.status.value}  {ticket.subject}", bold=True)
    for message in history:
        if isinstance(message, HumanMessage):
            typer.secho(f"\ncustomer: {message.text}", fg=typer.colors.CYAN)
        elif isinstance(message, AIMessage) and message.text.strip():
            typer.echo(f"\nagent: {message.text.strip()}")
        elif tools and isinstance(message, AIMessage) and message.tool_calls:
            for call in message.tool_calls:
                typer.secho(f"  -> {call['name']}({call['args']})", fg=typer.colors.BRIGHT_BLACK)
        elif tools and isinstance(message, ToolMessage):
            first_line = str(message.content).splitlines()[0] if message.content else ""
            typer.secho(f"  <- {first_line}", fg=typer.colors.BRIGHT_BLACK)


# ── policy ──────────────────────────────────────────────────────────────────


@policy_app.command("index")
def policy_index_command(
    force: Annotated[
        bool, typer.Option("--force", help="Re-embed every document, even unchanged ones.")
    ] = False,
) -> None:
    """Build the policy search index from the markdown in backend/policies."""

    async def body() -> str:
        async with runtime() as rt, rt.sessions() as session:
            result = await build_index(session, rt.embeddings, rt.settings, force=force)
            await session.commit()
        return (
            f"Indexed {result.documents_indexed} document(s) into {result.chunks_written} "
            f"passage(s); skipped {result.documents_skipped} unchanged, "
            f"removed {result.documents_removed} orphaned."
        )

    typer.secho(run(body), fg=typer.colors.GREEN)


@policy_app.command("status")
def policy_status_command() -> None:
    """Show which policy documents are indexed and which need rebuilding."""

    async def body() -> list[tuple[str, str, int]]:
        async with runtime() as rt, rt.sessions() as session:
            return [
                (s.document, s.state.value, s.chunks)
                for s in await index_status(session, rt.settings)
            ]

    statuses = run(body)
    colours = {
        DocumentState.CURRENT.value: typer.colors.GREEN,
        DocumentState.MISSING.value: typer.colors.YELLOW,
        DocumentState.STALE.value: typer.colors.YELLOW,
        DocumentState.ORPHANED.value: typer.colors.RED,
    }
    for document, state, chunks in statuses:
        typer.secho(f"{document:<32} {state:<10} {chunks} passage(s)", fg=colours[state])
    if any(state != DocumentState.CURRENT.value for _, state, _ in statuses):
        typer.secho("\nRun `deskpilot policy index` to bring the index up to date.", bold=True)


@policy_app.command("search")
def policy_search_command(
    question: Annotated[str, typer.Argument(help="What to look up in the policies.")],
) -> None:
    """Search the policy index directly, showing distances.

    The same search the agent's tool runs, without the model in the way. Use it to
    sanity-check retrieval and to choose DESKPILOT_POLICY_SEARCH__MAX_DISTANCE.
    """

    async def body() -> list[PolicyPassage]:
        async with runtime() as rt, rt.sessions() as session:
            return await search_policy_index(
                session, rt.embeddings, question, rt.settings.policy_search
            )

    passages = run(body)
    if not passages:
        typer.secho("Nothing matched closely enough.", fg=typer.colors.YELLOW)
        return
    for passage in passages:
        typer.secho(
            f"{passage.distance:.3f}  {passage.document} - {passage.heading}",
            fg=typer.colors.GREEN,
        )
        typer.secho(f"        {passage.content.splitlines()[-1][:100]}", dim=True)


# ── ask (one-shot, no ticket) ───────────────────────────────────────────────


@app.command("ask")
def ask_command(
    question: Annotated[str, typer.Argument(help="What the customer is asking.")],
    customer: CustomerOption,
    verbose: VerboseOption = False,
) -> None:
    """Ask a one-off question. Nothing is saved; use `ticket new` for a conversation."""

    async def body() -> AgentRun:
        async with runtime() as rt:
            context = rt.context_for(customer)
            # No checkpointer, and a throwaway thread id: this run leaves no trace.
            return await run_agent(question, context, str(uuid.uuid4()), settings=rt.settings)

    show_answer(run(body), verbose)


# ── helpers ─────────────────────────────────────────────────────────────────


def status_after(result: AgentRun) -> TicketStatus:
    return TicketStatus.ESCALATED if result.escalated else TicketStatus.AWAITING_CUSTOMER


async def respond(rt: Runtime, ticket: Ticket, customer_email: str, message: str) -> AgentRun:
    context = rt.context_for(customer_email)
    return await run_agent(message, context, ticket.thread_id, rt.checkpointer, rt.settings)


async def load_conversation(rt: Runtime, ticket: Ticket) -> list[AnyMessage]:
    """Read a ticket's messages straight out of its latest checkpoint.

    No model and no graph: this only reads state, so it costs nothing and works even
    when Ollama is not running.
    """
    saved = await rt.checkpointer.aget_tuple({"configurable": {"thread_id": ticket.thread_id}})
    if saved is None:
        return []
    messages = saved.checkpoint["channel_values"].get("messages", [])
    return list(messages)
