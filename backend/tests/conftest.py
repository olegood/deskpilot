"""Fixtures shared by all tests."""

import os

import pytest

TEST_DATABASE_PASSWORD = "test-password"
TEST_JWT_SECRET = "test-signing-key-not-used-anywhere-real"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from DESKPILOT_* variables in the developer's shell.

    Sets only the one setting without a default, so Settings() can be built.
    """
    for key in list(os.environ):
        if key.startswith("DESKPILOT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DESKPILOT_DATABASE__PASSWORD", TEST_DATABASE_PASSWORD)
    monkeypatch.setenv("DESKPILOT_AUTH__JWT_SECRET", TEST_JWT_SECRET)
