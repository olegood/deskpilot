"""Unit tests for the saved CLI session. No database, no network."""

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from deskpilot.auth.session_store import SavedSession, SessionFileError, clear, load, save
from deskpilot.auth.sessions import IssuedSession


def a_session(minutes_left: int = 15) -> SavedSession:
    now = datetime.now(UTC)
    return SavedSession(
        email="noah.kim@example.com",
        user_id=7,
        access_token="access-token-value",
        refresh_token="refresh-token-value",
        access_expires_at=(now + timedelta(minutes=minutes_left)).isoformat(),
        refresh_expires_at=(now + timedelta(days=14)).isoformat(),
    )


def test_a_saved_session_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "session.json"
    original = a_session()

    save(original, path)

    assert load(path) == original


def test_loading_nothing_returns_none(tmp_path: Path) -> None:
    assert load(tmp_path / "session.json") is None


def test_the_file_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    path = tmp_path / "session.json"

    save(a_session(), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_world_readable_session_is_refused(tmp_path: Path) -> None:
    """Refuse rather than quietly use a credential everybody on the box can read."""
    path = tmp_path / "session.json"
    save(a_session(), path)
    path.chmod(0o644)

    with pytest.raises(SessionFileError, match="readable by others"):
        load(path)


def test_a_corrupted_session_says_what_to_do(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("{not json at all")

    with pytest.raises(SessionFileError, match="log in again"):
        load(path)


def test_a_session_missing_a_field_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"email": "noah.kim@example.com"}))

    with pytest.raises(SessionFileError):
        load(path)


def test_saving_replaces_the_previous_session(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save(a_session(), path)

    newer = a_session(minutes_left=30)
    save(newer, path)

    assert load(path) == newer


def test_clearing_removes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save(a_session(), path)

    clear(path)

    assert not path.exists()


def test_clearing_nothing_is_not_an_error(tmp_path: Path) -> None:
    clear(tmp_path / "session.json")


def test_the_tokens_never_appear_in_a_repr() -> None:
    text = repr(a_session())

    assert "access-token-value" not in text
    assert "refresh-token-value" not in text
    assert "noah.kim@example.com" in text


def test_a_fresh_access_token_has_not_expired() -> None:
    assert not a_session(minutes_left=15).access_has_expired()


def test_a_spent_access_token_has_expired() -> None:
    assert a_session(minutes_left=-1).access_has_expired()


def test_a_token_about_to_expire_counts_as_expired() -> None:
    """The skew stops a token that is valid now from expiring mid-request."""
    now = datetime.now(UTC)
    nearly = SavedSession(
        email="noah.kim@example.com",
        user_id=7,
        access_token="a",
        refresh_token="r",
        access_expires_at=(now + timedelta(seconds=5)).isoformat(),
        refresh_expires_at=(now + timedelta(days=14)).isoformat(),
    )

    assert nearly.access_has_expired(skew_seconds=30)
    assert not nearly.access_has_expired(skew_seconds=0)


def test_it_is_built_from_what_a_login_returns() -> None:
    now = datetime.now(UTC)
    issued = IssuedSession(
        access_token="a",
        refresh_token="r",
        access_expires_at=now + timedelta(minutes=15),
        refresh_expires_at=now + timedelta(days=14),
        user_id=7,
        email="noah.kim@example.com",
    )

    saved = SavedSession.from_issued(issued)

    assert saved.user_id == 7
    assert saved.access_token == "a"
    assert not saved.access_has_expired()
