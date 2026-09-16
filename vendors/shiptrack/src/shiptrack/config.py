"""ShipTrack settings. Nothing here is shared with Deskpilot."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChaosSettings(BaseModel):
    """Ways to make the carrier behave like a real one.

    A supplier that always answers in five milliseconds teaches you nothing about
    timeouts, retries, or circuit breakers. These knobs exist so the client has
    something to be resilient against, and so its resilience can be tested rather
    than asserted.
    """

    model_config = ConfigDict(frozen=True)

    # Added to every response.
    latency_ms: int = Field(default=0, ge=0)
    # Fraction of requests answered with a 500.
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # Fraction of requests that never answer at all. The nastier failure: a
    # connection that stays open forever looks like a slow one until the timeout.
    hang_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # How long a hang lasts before the server gives up too.
    hang_seconds: float = Field(default=120.0, gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHIPTRACK_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8100, gt=0, lt=65_536)

    # The credential Deskpilot signs with. A key id so a key can be rotated without
    # a flag day: the server can accept the old one and the new one at once.
    key_id: str = "deskpilot"
    secret: SecretStr | None = None

    # How far a request's timestamp may be from ours. Five minutes is the usual
    # allowance for clock drift; wider means a captured request stays replayable
    # for longer.
    max_skew_seconds: int = Field(default=300, gt=0)
    # How many nonces to remember. Replay protection only works for as long as the
    # nonce is remembered, so this has to outlast the skew window.
    nonce_capacity: int = Field(default=10_000, gt=0)

    chaos: ChaosSettings = ChaosSettings()
