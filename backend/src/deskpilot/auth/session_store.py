"""Where the CLI keeps who you are logged in as.

A refresh token on disk is a credential sitting in a file, which is worth being
uncomfortable about. Every command-line tool that does not make you log in every
time works this way, and the alternatives are worse for a local tool: a keychain
means a platform-specific dependency, and holding it in memory means logging in
again for every command. The file is written with owner-only permissions and the
trade-off is documented rather than hidden.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from deskpilot.auth.sessions import IssuedSession

logger = logging.getLogger(__name__)

# rw------- for the file, rwx------ for the directory. Anything wider and every
# other account on the machine can read the refresh token.
FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
DIRECTORY_MODE = stat.S_IRWXU


class SessionFileError(Exception):
    """Raised when a saved session cannot be read or written."""


@dataclass(frozen=True)
class SavedSession:
    """What the CLI remembers between commands."""

    email: str
    user_id: int
    access_token: str
    refresh_token: str
    access_expires_at: str
    refresh_expires_at: str

    @classmethod
    def from_issued(cls, issued: IssuedSession) -> SavedSession:
        return cls(
            email=issued.email,
            user_id=issued.user_id,
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            access_expires_at=issued.access_expires_at.isoformat(),
            refresh_expires_at=issued.refresh_expires_at.isoformat(),
        )

    def access_has_expired(self, skew_seconds: int = 30) -> bool:
        """True when the access token is spent, or close enough that it will be.

        The skew stops a token that is valid now from expiring mid-request.
        """
        expires = datetime.fromisoformat(self.access_expires_at)
        return (expires - datetime.now(UTC)).total_seconds() <= skew_seconds

    def __repr__(self) -> str:
        # Spelled out, so a traceback or a debug log cannot carry the tokens.
        return f"SavedSession(email={self.email!r}, user_id={self.user_id!r})"


def save(session: SavedSession, path: Path) -> None:
    """Write the session with owner-only permissions."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, DIRECTORY_MODE)
        # Created with the right mode from the start. Writing first and chmod-ing
        # afterwards leaves a window where the file is world-readable.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(session), handle, indent=2)
        os.chmod(path, FILE_MODE)
    except OSError as exc:
        raise SessionFileError(f"could not write the session to {path}: {exc}") from exc


def load(path: Path) -> SavedSession | None:
    """Read the saved session, or None when there is not one."""
    if not path.exists():
        return None
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        # Somebody widened it. Refuse rather than quietly use a credential that
        # everybody on the machine can read.
        raise SessionFileError(
            f"{path} is readable by others. Run: chmod 600 {path}, then log in again."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SavedSession(**data)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise SessionFileError(
            f"the saved session at {path} could not be read ({exc}). Delete it and log in again."
        ) from exc


def clear(path: Path) -> None:
    """Remove the saved session. Silent when there is not one."""
    path.unlink(missing_ok=True)
