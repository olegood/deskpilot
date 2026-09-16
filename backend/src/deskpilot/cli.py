"""Deskpilot command-line interface. Run `uv run deskpilot --help`."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

# aliased: deskpilot.evals.runner also exports a select().
from sqlalchemy import select as sql_select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.auth import session_store
from deskpilot.auth.session_store import SavedSession, SessionFileError
from deskpilot.auth.sessions import (
    authenticate_access_token,
    log_in,
    log_out,
    refresh,
)
from deskpilot.auth.tokens import TokenError
from deskpilot.auth.users import (
    AuthError,
    authenticate,
    count_users,
    create_user,
    get_user,
    set_password,
)
from deskpilot.authz import resources
from deskpilot.authz.actions import Action, AuditEvent
from deskpilot.authz.audit import guard, recent, record_event
from deskpilot.authz.engine import Decision, Forbidden, decide
from deskpilot.authz.principal import Principal
from deskpilot.authz.resources import Resource
from deskpilot.config import Settings, get_settings
from deskpilot.db.checkpointer import open_checkpointer, setup_checkpointer
from deskpilot.db.models import (
    AuditEntry,
    Customer,
    Region,
    Ticket,
    TicketStatus,
    User,
    UserRole,
)
from deskpilot.db.seed import SeedError, seed
from deskpilot.db.session import create_engine, create_session_factory
from deskpilot.evals.dataset import DatasetError, load_cases
from deskpilot.evals.runner import Report, run_suite, select, write_report
from deskpilot.graph.context import AgentContext
from deskpilot.graph.conversation import load_messages
from deskpilot.graph.runner import AgentRun, run_agent
from deskpilot.integrations.shiptrack import ShipTrackClient
from deskpilot.knowledge.index import DocumentState, PolicyIndexError, build_index, index_status
from deskpilot.knowledge.search import PolicyPassage, search_policy_index
from deskpilot.llm import build_embeddings
from deskpilot.tickets import TicketError, create_ticket, get_ticket, list_tickets, set_status

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True, help="Deskpilot: AI support agent for Acme Gear.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
ticket_app = typer.Typer(no_args_is_help=True, help="Support ticket commands.")
policy_app = typer.Typer(no_args_is_help=True, help="Policy knowledge base commands.")
app.add_typer(db_app, name="db")
eval_app = typer.Typer(no_args_is_help=True, help="Evaluate the agent against saved cases.")
auth_app = typer.Typer(no_args_is_help=True, help="Accounts and credentials.")
audit_app = typer.Typer(no_args_is_help=True, help="What happened, and whether it was allowed.")
app.add_typer(ticket_app, name="ticket")
app.add_typer(eval_app, name="eval")
app.add_typer(auth_app, name="auth")
app.add_typer(audit_app, name="audit")
app.add_typer(policy_app, name="policy")

# Options reused across commands.
CustomerOption = Annotated[
    str | None,
    typer.Option(
        "--as",
        help="Act as this customer instead of the logged-in one. Requires "
        "DESKPILOT_AUTH__ALLOW_IMPERSONATION.",
    ),
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
    carrier: ShipTrackClient | None
    embeddings: Embeddings

    def context_for(self, principal: Principal) -> AgentContext:
        return AgentContext(
            principal=principal,
            session_factory=self.sessions,
            embeddings=self.embeddings,
            policy_search=self.settings.policy_search,
            tools=self.settings.tools,
            carrier=self.carrier,
        )


@asynccontextmanager
async def runtime() -> AsyncIterator[Runtime]:
    settings = get_settings()
    engine = create_engine(settings.database)
    # None when no secret is configured, which is a perfectly reasonable way to
    # run: the carrier tool then says the carrier is unavailable rather than
    # failing to start.
    carrier = ShipTrackClient(settings.shiptrack) if settings.shiptrack.secret else None
    try:
        async with open_checkpointer(settings.database) as checkpointer:
            yield Runtime(
                settings=settings,
                sessions=create_session_factory(engine),
                checkpointer=checkpointer,
                carrier=carrier,
                embeddings=build_embeddings(settings),
            )
    finally:
        await engine.dispose()


# Failures a command can hit in normal use: a bad password, a missing ticket, a
# stale policy index. They are reported as one red line and a non-zero exit, not as
# a traceback. One tuple, so the two runners cannot drift apart.
EXPECTED_ERRORS = (
    AuthError,
    Forbidden,
    DatasetError,
    PolicyIndexError,
    SeedError,
    SessionFileError,
    TicketError,
    TokenError,
)


def run_sync[T](call: Callable[[], T]) -> T:
    """Run a synchronous command body with the same error handling as run()."""
    try:
        return call()
    except EXPECTED_ERRORS as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def run[T](coroutine: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run a command's async body, turning expected failures into clean exits."""
    try:
        return asyncio.run(coroutine())
    except EXPECTED_ERRORS as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def show_answer(result: AgentRun, verbose: bool) -> None:
    typer.echo(result.answer)
    if result.escalated:
        typer.secho("This ticket has been escalated to a human.", fg=typer.colors.YELLOW)
    if verbose:
        typer.secho(
            f"\n[{result.category.value if result.category else 'unclassified'} | "
            f"{result.steps} model call(s) this turn | "
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
    subject: Annotated[str, typer.Option("--subject", help="Short summary of the problem.")],
    customer: CustomerOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Open a ticket and let the agent respond to the first message."""

    async def body() -> tuple[Ticket, AgentRun]:
        async with runtime() as rt:
            acting_as = await current_principal(rt, customer)
            async with rt.sessions() as session:
                ticket = await create_ticket(session, acting_as.email, subject)
                result = await respond(rt, ticket, acting_as, message)
                record_outcome(ticket, result)
                await session.commit()
                return ticket, result

    ticket, result = run(body)
    typer.secho(f"Opened {ticket.reference}.\n", fg=typer.colors.GREEN)
    show_answer(result, verbose)


@ticket_app.command("reply")
def ticket_reply_command(
    reference: Annotated[str, typer.Argument(help="Ticket reference, e.g. TCK-0001.")],
    message: Annotated[str, typer.Argument(help="The customer's next message.")],
    customer: CustomerOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Add a message to an open ticket. The agent picks up where it left off."""

    async def body() -> AgentRun:
        async with runtime() as rt:
            acting_as = await current_principal(rt, customer)
            async with rt.sessions() as session:
                ticket = await get_ticket(session, reference, acting_as.email)
                if ticket.status is TicketStatus.RESOLVED:
                    raise TicketError(f"{ticket.reference} is resolved; open a new ticket instead")
                result = await respond(rt, ticket, acting_as, message)
            record_outcome(ticket, result)
            await session.commit()
            return result

    show_answer(run(body), verbose)


@ticket_app.command("list")
def ticket_list_command(customer: CustomerOption = None) -> None:
    """List your tickets, newest first."""

    async def body() -> list[Ticket]:
        async with runtime() as rt:
            # Scoped like every other ticket command. Before this went through the
            # resolver it listed every customer's tickets to anyone who ran it.
            acting_as = await current_principal(rt, customer)
            async with rt.sessions() as session:
                return await list_tickets(session, acting_as.email)

    tickets = run(body)
    if not tickets:
        typer.echo("No tickets yet.")
        return
    for ticket in tickets:
        category = ticket.category.value if ticket.category else "-"
        typer.echo(
            f"{ticket.reference}  {ticket.status.value:<18}  {category:<17}  "
            f"{ticket.customer.email:<28}  {ticket.subject}"
        )


@ticket_app.command("show")
def ticket_show_command(
    reference: Annotated[str, typer.Argument(help="Ticket reference, e.g. TCK-0001.")],
    customer: CustomerOption = None,
    tools: Annotated[
        bool, typer.Option("--tools", help="Also show tool calls and their results.")
    ] = False,
) -> None:
    """Print a ticket's conversation, read back from its checkpoint."""

    async def body() -> tuple[Ticket, list[AnyMessage]]:
        async with runtime() as rt:
            acting_as = await current_principal(rt, customer)
            async with rt.sessions() as session:
                ticket = await get_ticket(session, reference, acting_as.email)
                return ticket, await load_conversation(rt, ticket)

    ticket, history = run(body)
    category = ticket.category.value if ticket.category else "unclassified"
    typer.secho(
        f"{ticket.reference}  {ticket.status.value}  {category}  {ticket.subject}", bold=True
    )
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


async def signed_in_principal(rt: Runtime) -> Principal:
    """The principal of whoever is logged in, whatever their role.

    Separate from current_principal, which insists on a customer record because a
    ticket has to belong to somebody. Administration does not.
    """
    saved = await active_session(rt)
    async with rt.sessions() as session:
        user = await authenticate_access_token(session, saved.access_token, rt.settings.auth)
        return Principal.from_user(user)


async def current_principal(rt: Runtime, requested: str | None) -> Principal:
    """Decide who this command acts as, with the attributes a policy weighs.

    Normally that is whoever is logged in. `--as` overrides it, and is refused
    unless impersonation has been turned on: a flag that lets one person act as
    another is exactly what this milestone exists to remove, so it is opt-in and
    it is logged every time.
    """
    if requested is not None:
        if not rt.settings.auth.allow_impersonation:
            raise AuthError(
                f"--as is disabled. Log in with `deskpilot auth login {requested}`, or set "
                "DESKPILOT_AUTH__ALLOW_IMPERSONATION=true in backend/.env for local development."
            )
        logger.warning("impersonating %s because --as was given", requested)
        async with rt.sessions() as session:
            customer = await session.scalar(sql_select(Customer).where(Customer.email == requested))
            if customer is None:
                raise AuthError(f"no customer with the email {requested}")
            # Deliberately a bare customer principal: no regions, no approval
            # limit. The escape hatch cannot hand out staff attributes.
            return Principal.for_customer(customer)

    saved = await active_session(rt)
    async with rt.sessions() as session:
        user = await authenticate_access_token(session, saved.access_token, rt.settings.auth)
        if user.customer is None:
            raise AuthError(
                f"{user.email} is a {user.role.value} account with no customer record, "
                "so it has no orders or tickets of its own."
            )
        return Principal.from_user(user)


async def active_session(rt: Runtime) -> SavedSession:
    """The saved session, refreshed if its access token has run out."""
    saved = session_store.load(rt.settings.auth.session_file)
    if saved is None:
        raise AuthError("you are not logged in. Run: deskpilot auth login <email>")
    if not saved.access_has_expired():
        return saved

    # Rotation happens here rather than at login, so a long-running shell stays
    # usable without the user noticing anything.
    async with rt.sessions() as session:
        issued = await refresh(session, saved.refresh_token, rt.settings.auth)
        await session.commit()
    renewed = SavedSession.from_issued(issued)
    session_store.save(renewed, rt.settings.auth.session_file)
    return renewed


# ── auth ────────────────────────────────────────────────────────────────────


@auth_app.command("register")
def auth_register_command(
    email: Annotated[str, typer.Argument(help="Email address for the new account.")],
    name: Annotated[str, typer.Option("--name", help="The person's full name.")],
    role: Annotated[UserRole, typer.Option("--role", help="What kind of account.")] = (
        UserRole.CUSTOMER
    ),
) -> None:
    """Create an account. The password is asked for, never passed as an argument."""
    # hide_input keeps it off the screen; prompting keeps it out of shell history
    # and out of the process list, where an argument would be visible to anyone
    # running ps.
    password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)

    async def body() -> User:
        async with runtime() as rt, rt.sessions() as session:
            await check_may_create(rt, session, role)
            user = await create_user(
                session,
                email,
                password,
                name,
                role,
                rounds=rt.settings.auth.bcrypt_rounds,
            )
            await session.commit()
            await record_event(
                rt.sessions,
                AuditEvent.ACCOUNT_CREATED,
                f"created {user.email} as {user.role.value}",
                actor_user_id=user.id,
            )
            return user

    user = run(body)
    linked = " (linked to an existing customer)" if user.customer_id else ""
    typer.secho(f"Created {user.email} as {user.role.value}{linked}.", fg=typer.colors.GREEN)


@auth_app.command("login")
def auth_login_command(
    email: Annotated[str, typer.Argument(help="Email address to log in as.")],
) -> None:
    """Log in and save the session, so other commands know who you are."""
    password = typer.prompt("Password", hide_input=True)

    async def body() -> Path:
        async with runtime() as rt:
            async with rt.sessions() as session:
                try:
                    issued = await log_in(session, email, password, rt.settings.auth)
                except AuthError as exc:
                    await session.commit()
                    await record_event(
                        rt.sessions,
                        exc.event or AuditEvent.LOGIN_FAILED,
                        f"login failed for {email}",
                        actor_user_id=exc.user_id,
                        allowed=False,
                    )
                    raise
                # Committed even on failure, just above: log_in records the failed
                # attempt on the user row, and rolling it back would mean the
                # lockout counter never advances.
                await session.commit()
            await record_event(
                rt.sessions,
                AuditEvent.LOGIN_SUCCEEDED,
                f"logged in as {issued.email}",
                actor_user_id=issued.user_id,
            )
            path = rt.settings.auth.session_file
            session_store.save(SavedSession.from_issued(issued), path)
            return path

    path = run(body)
    typer.secho(f"Logged in. Session saved to {path}.", fg=typer.colors.GREEN)


@auth_app.command("whoami")
def auth_whoami_command() -> None:
    """Show who the saved session belongs to, refreshing it if needed."""

    async def body() -> tuple[str, str, str | None]:
        async with runtime() as rt:
            saved = await active_session(rt)
            async with rt.sessions() as session:
                user = await authenticate_access_token(
                    session, saved.access_token, rt.settings.auth
                )
                return user.email, user.role.value, user.customer.email if user.customer else None

    email, role, customer = run(body)
    typer.secho(f"{email} ({role})", fg=typer.colors.GREEN)
    typer.secho(
        f"  customer record: {customer or 'none'}\n"
        f"  session file:    {get_settings().auth.session_file}",
        dim=True,
    )


@auth_app.command("logout")
def auth_logout_command() -> None:
    """Revoke this session on the server and delete it from disk."""

    async def body() -> None:
        async with runtime() as rt:
            path = rt.settings.auth.session_file
            saved = session_store.load(path)
            if saved is not None:
                async with rt.sessions() as session:
                    await log_out(session, saved.refresh_token)
                    await session.commit()
                await record_event(
                    rt.sessions,
                    AuditEvent.LOGGED_OUT,
                    f"signed out {saved.email}",
                    actor_user_id=saved.user_id,
                )
            # Deleted whatever the server said. A logout that leaves the file
            # behind because revocation failed is the worst of both.
            session_store.clear(path)

    run(body)
    typer.secho("Logged out.", fg=typer.colors.GREEN)


@auth_app.command("grant")
def auth_grant_command(
    email: Annotated[str, typer.Argument(help="Which account to change.")],
    region: Annotated[
        list[Region] | None, typer.Option("--region", help="A region this person covers.")
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--approval-limit", help="Most they may approve alone, in cents."),
    ] = None,
) -> None:
    """Set the attributes a policy weighs: which regions, and how much.

    Attributes, not permissions. Nothing here grants an action directly; the rules
    in authz/policies.py decide what these values allow.
    """

    async def body() -> User:
        async with runtime() as rt, rt.sessions() as session:
            actor = await signed_in_principal(rt)
            user = await get_user(session, email)
            if user is None:
                raise AuthError(f"no account for {email}")
            await guard(rt.sessions, actor, Action.USER_MANAGE, resources.Account(user_id=user.id))
            if region is not None:
                user.regions = [item.value for item in region]
            if limit is not None:
                user.approval_limit_cents = limit
            await session.commit()
            await record_event(
                rt.sessions,
                AuditEvent.ATTRIBUTES_CHANGED,
                f"regions={user.regions} approval_limit_cents={user.approval_limit_cents}",
                actor_user_id=user.id,
            )
            return user

    user = run(body)
    typer.secho(
        f"{user.email}: regions {user.regions or '[]'}, approval limit {user.approval_limit_cents}",
        fg=typer.colors.GREEN,
    )


@auth_app.command("can")
def auth_can_command(
    email: Annotated[str, typer.Argument(help="Whose permissions to test.")],
    action: Annotated[Action, typer.Argument(help="The action to test.")],
    region: Annotated[Region, typer.Option("--region", help="Region of the resource.")] = Region.EU,
    owner: Annotated[
        int | None, typer.Option("--owner", help="Customer id that owns the resource.")
    ] = None,
    amount: Annotated[
        int, typer.Option("--amount", help="Amount in cents, for approval actions.")
    ] = 0,
) -> None:
    """Ask the policy engine a question directly, and see which rule answered.

    The fastest way to understand a refusal, and to check a rule change did what
    was intended before wiring it into anything.
    """

    async def body() -> tuple[Principal, Decision]:
        async with runtime() as rt, rt.sessions() as session:
            user = await get_user(session, email)
            if user is None:
                raise AuthError(f"no account for {email}")
            principal = Principal.from_user(user)
            return principal, decide(principal, action, resource_for(action, region, owner, amount))

    principal, outcome = run(body)
    colour = typer.colors.GREEN if outcome.allowed else typer.colors.RED
    typer.secho("allow" if outcome.allowed else "deny", fg=colour, bold=True)
    typer.secho(
        f"  rule:      {outcome.policy}\n"
        f"  reason:    {outcome.reason}\n"
        f"  principal: {principal.role.value}, regions "
        f"{sorted(r.value for r in principal.regions) or '[]'}, "
        f"limit {principal.approval_limit_cents}",
        dim=True,
    )


def resource_for(action: Action, region: Region, owner: int | None, amount: int) -> Resource:
    """Build a plausible resource for whichever action is being asked about."""
    owner = owner if owner is not None else 0
    match action:
        case Action.POLICY_SEARCH:
            return resources.PolicyDocuments()
        case Action.ORDER_VIEW:
            return resources.Order(owner_customer_id=owner, region=region)
        case Action.CUSTOMER_VIEW:
            return resources.CustomerProfile(customer_id=owner, region=region)
        case Action.REFUND_APPROVE | Action.PROPOSAL_EDIT | Action.PROPOSAL_REJECT:
            return resources.Proposal(
                ticket_owner_customer_id=owner, region=region, amount_cents=amount
            )
        case Action.USER_MANAGE:
            return resources.Account(user_id=owner)
        case Action.TRACE_VIEW:
            return resources.Trace(owner_customer_id=owner)
        case _:
            return resources.Ticket(owner_customer_id=owner, region=region)


@auth_app.command("check")
def auth_check_command(
    email: Annotated[str, typer.Argument(help="Email address to check.")],
) -> None:
    """Verify a password without issuing anything. A smoke test for the hash path."""
    password = typer.prompt("Password", hide_input=True)

    async def body() -> User:
        async with runtime() as rt, rt.sessions() as session:
            try:
                user = await authenticate(session, email, password, rt.settings.auth)
            finally:
                # Committed even when it raised: authenticate records the failed
                # attempt on the row, and rolling that back means the lockout
                # counter never advances.
                await session.commit()
            return user

    user = run(body)
    typer.secho(f"OK: {user.email} ({user.role.value})", fg=typer.colors.GREEN)


@auth_app.command("passwd")
def auth_passwd_command(
    email: Annotated[str, typer.Argument(help="Whose password to change.")],
) -> None:
    """Change a password. Every existing session for that account stops working."""
    password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)

    async def body() -> None:
        async with runtime() as rt, rt.sessions() as session:
            user = await get_user(session, email)
            if user is None:
                raise AuthError(f"no account for {email}")
            await set_password(session, user, password, rounds=rt.settings.auth.bcrypt_rounds)
            await session.commit()
            await record_event(
                rt.sessions,
                AuditEvent.PASSWORD_CHANGED,
                f"password changed for {user.email}; all sessions revoked",
                actor_user_id=user.id,
            )

    run(body)
    typer.secho("Password changed. Existing sessions have been revoked.", fg=typer.colors.GREEN)


@app.command("serve")
def serve_command(
    reload: Annotated[bool, typer.Option("--reload", help="Restart on code changes.")] = False,
) -> None:
    """Run the HTTP API."""
    import uvicorn

    api = get_settings().api
    typer.secho(f"Serving on http://{api.host}:{api.port}", fg=typer.colors.GREEN)
    uvicorn.run(
        "deskpilot.api.app:create_app",
        factory=True,
        host=api.host,
        port=api.port,
        reload=reload,
    )


@app.command("openapi")
def openapi_command(
    out: Annotated[
        Path | None, typer.Option("--out", help="Write here instead of standard output.")
    ] = None,
) -> None:
    """Print the OpenAPI schema.

    The frontend generates its types from this, so the two cannot drift apart: a
    renamed field becomes a TypeScript error rather than an undefined at runtime.
    """
    from deskpilot.api.app import create_app

    schema = json.dumps(create_app().openapi(), indent=2)
    if out is None:
        typer.echo(schema)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(schema + "\n", encoding="utf-8")
    typer.secho(f"Wrote {out}", fg=typer.colors.GREEN)


# ── audit ───────────────────────────────────────────────────────────────────


@audit_app.command("tail")
def audit_tail_command(
    number: Annotated[int, typer.Option("-n", "--number", help="How many entries.")] = 20,
    email: Annotated[
        str | None, typer.Option("--as", help="Only entries for this account.")
    ] = None,
    denied: Annotated[bool, typer.Option("--denied", help="Only refusals.")] = False,
) -> None:
    """Show the most recent audit entries, newest first."""

    async def body() -> list[AuditEntry]:
        async with runtime() as rt:
            # Reading the record of what everyone did leaves a record of its own.
            await guard(
                rt.sessions,
                await signed_in_principal(rt),
                Action.AUDIT_VIEW,
                resources.AuditLog(),
            )
        async with runtime() as rt, rt.sessions() as session:
            actor_id = None
            if email is not None:
                user = await get_user(session, email)
                if user is None:
                    raise AuthError(f"no account for {email}")
                actor_id = user.id
            return await recent(session, number, actor_id, denied)

    entries = run(body)
    if not entries:
        typer.echo("Nothing recorded yet.")
        return
    for entry in reversed(entries):
        colour = typer.colors.GREEN if entry.allowed else typer.colors.RED
        typer.secho(
            f"{entry.at:%Y-%m-%d %H:%M:%S}  {'allow' if entry.allowed else 'deny ':<5}  "
            f"{entry.event:<28}  user={entry.actor_user_id or '-'}",
            fg=colour,
        )
        detail = f"        {entry.reason}"
        if entry.rule:
            detail += f"  [{entry.rule}]"
        typer.secho(detail, dim=True)


async def check_may_create(rt: Runtime, session: AsyncSession, role: UserRole) -> None:
    """Who may create which kind of account.

    Anybody may register themselves as a customer, as on any shop. Creating a
    member of staff is administration and needs an administrator.

    That leaves the bootstrap problem: the first administrator cannot be created by
    an administrator. An empty installation is therefore allowed to create one
    account of any role, and the event is recorded as such. It is the narrowest
    exception that still lets the system be set up, and it closes the moment the
    first account exists.
    """
    if role is UserRole.CUSTOMER:
        return
    if await count_users(session) == 0:
        logger.warning("creating the first account on an empty installation")
        await record_event(
            rt.sessions,
            AuditEvent.ACCOUNT_CREATED,
            f"bootstrap: first account created as {role.value} on an empty installation",
        )
        return
    await guard(
        rt.sessions, await signed_in_principal(rt), Action.USER_MANAGE, resources.Account(user_id=0)
    )


# ── eval ────────────────────────────────────────────────────────────────────


@eval_app.command("list")
def eval_list_command() -> None:
    """Show the cases in the suite."""
    cases = run_sync(load_cases)
    for case in cases:
        tags = ",".join(case.tags) or "-"
        category = case.category.value if case.category else "any"
        typer.echo(f"{case.id:<26}  {category:<17}  {tags:<12}  {case.customer}")
    typer.secho(f"\n{len(cases)} case(s)", dim=True)


@eval_app.command("run")
def eval_run_command(
    only: Annotated[
        list[str] | None, typer.Option("--only", help="Run just these case ids.")
    ] = None,
    tag: Annotated[
        list[str] | None, typer.Option("--tag", help="Run only cases with these tags.")
    ] = None,
    concurrency: Annotated[int, typer.Option("--concurrency", help="Cases in flight at once.")] = 2,
    save: Annotated[
        bool, typer.Option("--save/--no-save", help="Write the run to evals/runs/.")
    ] = True,
) -> None:
    """Run the eval suite against the configured models and print a report."""

    async def body() -> tuple[Report, Path | None]:
        cases = select(load_cases(), only or [], tag or [])
        if not cases:
            raise DatasetError("no cases matched that filter")
        async with runtime() as rt:
            report = await run_suite(cases, rt.sessions, rt.settings, concurrency)
        return report, (write_report(report) if save else None)

    report, path = run(body)
    show_report(report, path)


def show_report(report: Report, path: Path | None) -> None:
    for result in report.results:
        if result.passed:
            typer.secho(f"pass  {result.case_id}", fg=typer.colors.GREEN)
            continue
        # Red for something the agent must never do, yellow for a weaker answer.
        colour = typer.colors.RED if result.critical_failures else typer.colors.YELLOW
        typer.secho(f"FAIL  {result.case_id}", fg=colour)
        for failure in result.failures:
            typer.secho(f"        {failure}", fg=colour)
        typer.secho(f"        tools: {result.tools_called or 'none'}", dim=True)
        typer.secho(f"        answer: {result.answer[:160]}", dim=True)

    summary = report.summary
    typer.echo()
    typer.secho(
        f"{summary.passed}/{summary.total} passed  |  "
        f"{summary.critical} critical  |  "
        f"category {summary.category_correct}/{summary.total}  |  "
        f"{summary.total_tokens} tokens  |  {summary.seconds:.1f}s  |  "
        f"agent {report.agent_model}",
        bold=True,
    )
    if path is not None:
        typer.secho(f"Saved to {path}", dim=True)
    # A non-zero exit makes the suite scriptable without pretending it is a test
    # suite: a model can fail a case today and pass it tomorrow.
    if summary.passed < summary.total:
        raise typer.Exit(code=1)


# ── ask (one-shot, no ticket) ───────────────────────────────────────────────


@app.command("ask")
def ask_command(
    question: Annotated[str, typer.Argument(help="What the customer is asking.")],
    customer: CustomerOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Ask a one-off question. Nothing is saved; use `ticket new` for a conversation."""

    async def body() -> AgentRun:
        async with runtime() as rt:
            context = rt.context_for(await current_principal(rt, customer))
            # No checkpointer, and a throwaway thread id: this run leaves no trace.
            return await run_agent(question, context, str(uuid.uuid4()), settings=rt.settings)

    show_answer(run(body), verbose)


# ── helpers ─────────────────────────────────────────────────────────────────


def record_outcome(ticket: Ticket, result: AgentRun) -> None:
    """Project what the run decided back onto the queryable ticket row.

    The graph's state is the working copy; these columns exist so tickets can be
    listed and filtered without reading every checkpoint.
    """
    set_status(
        ticket, TicketStatus.ESCALATED if result.escalated else TicketStatus.AWAITING_CUSTOMER
    )
    if result.category is not None:
        ticket.category = result.category


async def respond(rt: Runtime, ticket: Ticket, principal: Principal, message: str) -> AgentRun:
    context = rt.context_for(principal)
    return await run_agent(message, context, ticket.thread_id, rt.checkpointer, rt.settings)


async def load_conversation(rt: Runtime, ticket: Ticket) -> list[AnyMessage]:
    """Read a ticket's messages straight out of its latest checkpoint."""
    return await load_messages(rt.checkpointer, ticket.thread_id)
