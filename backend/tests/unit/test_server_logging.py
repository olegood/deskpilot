"""The server prints its own INFO lines.

Several decisions say a refusal gives the caller a flat message and "the real
reason goes to the log". For the running server that was untrue until milestone 7:
uvicorn configures only its own loggers, so everything the application logged at
INFO was dropped (D-145).

Run in a subprocess on purpose. pytest installs its own handlers on the root
logger, and `logging.basicConfig` does nothing once a root handler exists, so an
in-process test would pass whether the function worked or not.
"""

import subprocess
import sys

SCRIPT = """
import logging
from deskpilot.cli import configure_server_logging
configure_server_logging()
logging.getLogger("deskpilot.authz.engine").info("denied refund.approve: over the limit")
logging.getLogger("deskpilot.api.errors").info("rejected a callback: signature does not match")
"""


def test_the_reasons_decisions_rely_on_reach_the_log() -> None:
    result = subprocess.run(  # noqa: S603 - a fixed script run with this interpreter
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=60, check=True
    )

    assert "INFO:" in result.stderr
    assert "deskpilot.authz.engine: denied refund.approve: over the limit" in result.stderr
    assert "rejected a callback: signature does not match" in result.stderr
