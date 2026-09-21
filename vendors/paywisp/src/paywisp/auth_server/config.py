"""Authorization server settings. Nothing here is shared with Deskpilot."""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# What a token can let its holder do at Paywisp. Read and write are separate scopes,
# and separate clients are allowed them (see clients.py): the question is not only
# "what did this client ask for" but "what could it ever be given".
SCOPE_PAYMENTS_READ = "payments:read"
SCOPE_REFUNDS_WRITE = "refunds:write"
ALL_SCOPES = (SCOPE_PAYMENTS_READ, SCOPE_REFUNDS_WRITE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAYWISP_AUTH_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8200, gt=0, lt=65_536)

    # The issuer identifier. It appears in every token's `iss` claim and is the
    # base of the discovery URL, so it must be exactly what clients are told, with
    # no trailing slash: RFC 8414 compares it as a string.
    issuer: str = "http://127.0.0.1:8200"

    # The services a token may be issued for. A token names exactly one of these in
    # its `aud` claim, and a request for anything else is refused. The default is
    # Paywisp's own MCP server.
    resources: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:8210/mcp"])

    # An EC P-256 private key in PEM form. Without one, a key is generated at
    # startup: every token issued before a restart then stops verifying, which is
    # acceptable for a fake vendor and quietly useful, since a restart revokes
    # everything.
    signing_key: SecretStr | None = None

    # Deskpilot's service account. The only client that exists in this step; with
    # no secret configured it is not registered at all.
    agent_client_id: str = "deskpilot-agent"
    agent_client_secret: SecretStr | None = None

    # Short, because a bearer token cannot be recalled once it has been handed out.
    # The resource server re-checks nothing but the signature and the claims.
    access_token_seconds: int = Field(default=300, gt=0, le=3600)

    @field_validator("signing_key", "agent_client_secret", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        """Compose passes an unset variable through as an empty string.

        Taken literally, an empty signing key is a PEM that fails to parse at
        startup and an empty client secret is a client anybody can authenticate
        as. Neither is what an empty value in a .env file means.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value
