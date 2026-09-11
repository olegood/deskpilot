"""Fixtures shared by all tests."""

import os

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from DESKPILOT_* variables in the developer's shell."""
    for key in list(os.environ):
        if key.startswith("DESKPILOT_"):
            monkeypatch.delenv(key)
