"""The CLI session flow, driven through the real command-line app.

These go through typer's runner rather than calling functions directly, because
what is being tested is the wiring: that a command finds the saved session, that
--as is refused, that logout leaves nothing behind.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from deskpilot.auth.session_store import load
from deskpilot.auth.users import create_user
from deskpilot.config import DatabaseSettings, get_settings
from deskpilot.db.models import UserRole

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
REVIEWER = "lena@acmegear.example"
GOOD = "correct horse battery staple"
ROUNDS = 4

runner = CliRunner()


@pytest.fixture
def cli(
    seeded_sessions: async_sessionmaker[AsyncSession],
    test_database: DatabaseSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Point the CLI at the test database and a throwaway session file."""
    session_file = tmp_path / "session.json"
    # The CLI reads settings when a command runs, which is inside the test, where
    # the autouse clean_env fixture has already replaced the database password with
    # a placeholder. The real one has to be put back.
    assert test_database.password is not None
    monkeypatch.setenv("DESKPILOT_DATABASE__PASSWORD", test_database.password.get_secret_value())
    monkeypatch.setenv("DESKPILOT_DATABASE__NAME", test_database.name)
    monkeypatch.setenv("DESKPILOT_AUTH__SESSION_FILE", str(session_file))
    monkeypatch.setenv("DESKPILOT_AUTH__BCRYPT_ROUNDS", str(ROUNDS))
    monkeypatch.setenv("DESKPILOT_AUTH__ALLOW_IMPERSONATION", "false")
    # Settings are cached for the process, and the CLI reads them per command.
    get_settings.cache_clear()
    yield session_file
    get_settings.cache_clear()


@pytest.fixture
async def accounts(seeded_sessions: async_sessionmaker[AsyncSession]) -> None:
    async with seeded_sessions() as session:
        await create_user(session, NOAH, GOOD, "Noah Kim", rounds=ROUNDS)
        await create_user(session, REVIEWER, GOOD, "Lena Fox", UserRole.REVIEWER, rounds=ROUNDS)
        await session.commit()


def invoke(*args: str, stdin: str | None = None) -> str:
    from deskpilot.cli import app

    result = runner.invoke(app, list(args), input=stdin)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.output


def test_a_command_refuses_before_you_log_in(cli: Path, accounts: None) -> None:
    output = invoke("ticket", "list")

    assert "not logged in" in output
    assert not cli.exists()


def test_logging_in_saves_a_session(cli: Path, accounts: None) -> None:
    output = invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")

    assert "Logged in" in output
    saved = load(cli)
    assert saved is not None
    assert saved.email == NOAH


def test_a_wrong_password_saves_nothing(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin="not the password at all\n")

    assert not cli.exists()


def test_whoami_reports_the_session(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")

    output = invoke("auth", "whoami")

    assert NOAH in output
    assert "customer" in output


def test_commands_act_as_the_logged_in_customer(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")

    output = invoke("ticket", "list")

    # Noah has no tickets in a freshly seeded database; the point is that it ran.
    assert "not logged in" not in output


def test_impersonation_is_refused_when_it_is_turned_off(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")

    output = invoke("ticket", "list", "--as", "ana.garcia@example.com")

    assert "--as is disabled" in output


def test_impersonation_works_when_it_is_turned_on(
    cli: Path, accounts: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DESKPILOT_AUTH__ALLOW_IMPERSONATION", "true")
    get_settings.cache_clear()

    output = invoke("ticket", "list", "--as", "ana.garcia@example.com")

    # No login at all, and it ran: that is the escape hatch, working as intended.
    assert "not logged in" not in output
    assert "--as is disabled" not in output


def test_a_staff_account_has_no_tickets_of_its_own(cli: Path, accounts: None) -> None:
    """A reviewer logs in fine but is not a customer, and is told so plainly."""
    invoke("auth", "login", REVIEWER, stdin=f"{GOOD}\n")

    assert "reviewer" in invoke("auth", "whoami")
    assert "no customer record" in invoke("ticket", "list")


def test_logging_out_removes_the_session(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")

    output = invoke("auth", "logout")

    assert "Logged out" in output
    assert not cli.exists()
    assert "not logged in" in invoke("ticket", "list")


def test_logging_out_twice_is_not_an_error(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")
    invoke("auth", "logout")

    assert "Logged out" in invoke("auth", "logout")


# ── administration is authorized, and bootstrapping is the one exception ─────


def test_reading_the_audit_log_needs_an_administrator(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")

    assert "not allowed" in invoke("audit", "tail")


def test_changing_attributes_needs_an_administrator(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")

    output = invoke("auth", "grant", REVIEWER, "--approval-limit", "999999")

    assert "not allowed" in output


def test_creating_a_staff_account_needs_an_administrator(cli: Path, accounts: None) -> None:
    invoke("auth", "login", NOAH, stdin=f"{GOOD}\n")

    # The password is prompted for before the check runs, so it has to be supplied
    # even though the command will refuse.
    output = invoke(
        "auth",
        "register",
        "new@acmegear.example",
        "--name",
        "New",
        "--role",
        "admin",
        stdin=f"{GOOD}\n{GOOD}\n",
    )

    assert "not allowed" in output


def test_anybody_may_register_themselves_as_a_customer(cli: Path, accounts: None) -> None:
    """Self-registration is how a shop works; it is not administration."""
    output = invoke(
        "auth", "register", "newcomer@example.com", "--name", "New Comer", stdin=f"{GOOD}\n{GOOD}\n"
    )

    assert "Created newcomer@example.com" in output


def test_an_empty_installation_may_create_its_first_administrator(cli: Path) -> None:
    """The bootstrap exception: the first administrator cannot be made by one.

    Note there is no `accounts` fixture here, so the users table is empty.
    """
    output = invoke(
        "auth",
        "register",
        "root@acmegear.example",
        "--name",
        "Root",
        "--role",
        "admin",
        stdin=f"{GOOD}\n{GOOD}\n",
    )

    assert "Created root@acmegear.example as admin" in output


def test_the_exception_closes_as_soon_as_one_account_exists(cli: Path, accounts: None) -> None:
    """With accounts present, creating staff is administration and needs one."""
    output = invoke(
        "auth",
        "register",
        "second@acmegear.example",
        "--name",
        "Second",
        "--role",
        "admin",
        stdin=f"{GOOD}\n{GOOD}\n",
    )

    # No longer the empty installation, and nobody is signed in to authorize it.
    assert "not logged in" in output


def test_an_administrator_may_do_all_of_it(cli: Path) -> None:
    invoke(
        "auth",
        "register",
        "root@acmegear.example",
        "--name",
        "Root",
        "--role",
        "admin",
        stdin=f"{GOOD}\n{GOOD}\n",
    )
    invoke("auth", "login", "root@acmegear.example", stdin=f"{GOOD}\n")

    assert "not allowed" not in invoke("audit", "tail")
    assert "not allowed" not in invoke(
        "auth",
        "register",
        "lena2@acmegear.example",
        "--name",
        "Lena",
        "--role",
        "reviewer",
        stdin=f"{GOOD}\n{GOOD}\n",
    )
