"""MCP server settings. Nothing here is shared with Deskpilot, or with the auth server."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAYWISP_MCP_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8210, gt=0, lt=65_536)

    # This server's own canonical URL. It is the audience every token must name,
    # and it must be exactly the string the authorization server lists among its
    # resources: the comparison is a string comparison, trailing slash and all.
    resource: str = "http://127.0.0.1:8210/mcp"

    # Who is trusted to issue tokens. Compared exactly against each token's `iss`.
    issuer: str = "http://127.0.0.1:8200"

    # Where to fetch the signing keys. Unset means "discover it from the issuer's
    # metadata", which is right when the issuer's URL is reachable from here. In a
    # container it usually is not: the issuer is a name clients were told, the key
    # set is an address this server can reach, and the two can differ.
    jwks_url: str | None = None

    # Seconds of clock drift tolerated on `exp`, `nbf` and `iat`.
    leeway_seconds: int = Field(default=30, ge=0, le=300)

    # The least time between two fetches of the key set. A token naming an unknown
    # key triggers a refetch, which is how a rotated key gets picked up; without a
    # floor, anybody could make this server hammer the authorization server by
    # sending tokens with made-up key ids.
    jwks_min_refresh_seconds: float = Field(default=60.0, ge=0)

    # Host headers this server answers to. DNS rebinding protection is only on by
    # default in the SDK when bound to localhost, and a container is not.
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1:*", "localhost:*"])

    @field_validator("jwks_url", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        # Compose passes an unset variable as an empty string (D-144).
        if isinstance(value, str) and not value.strip():
            return None
        return value
