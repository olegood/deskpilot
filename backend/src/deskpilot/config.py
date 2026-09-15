"""Deskpilot settings.

Every value comes from an environment variable with the DESKPILOT_ prefix, or from
backend/.env. Nested fields use a double underscore, for example
DESKPILOT_AGENT__MODEL=qwen3:8b overrides only the agent's model name.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# backend/src/deskpilot/config.py -> parents[2] is backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Provider(StrEnum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"


class ModelRole(StrEnum):
    """Each role has its own model settings and can switch provider independently."""

    AGENT = "agent"
    CLASSIFIER = "classifier"
    GUARD = "guard"
    JUDGE = "judge"


class ModelSettings(BaseModel):
    """How to build one chat model."""

    model_config = ConfigDict(frozen=True)

    provider: Provider = Provider.OLLAMA
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    max_output_tokens: int = Field(default=2048, gt=0)
    timeout_s: float = Field(default=120.0, gt=0)
    # Ollama only. Must be set explicitly: when a prompt exceeds the window, Ollama
    # silently drops the oldest tokens, which removes the system prompt first.
    num_ctx: int = Field(default=32_768, ge=2_048)
    # Thinking mode, always explicit. Leaving it unset lets thinking models put raw
    # <think> blocks into the response content, which could reach customers.
    reasoning: bool = False

    @model_validator(mode="after")
    def _model_matches_provider(self) -> Self:
        if self.provider is Provider.ANTHROPIC and not self.model.startswith("claude-"):
            raise ValueError(
                f"provider is anthropic but model {self.model!r} is not a Claude model; "
                "set the MODEL variable for this role too"
            )
        return self

    @model_validator(mode="after")
    def _reasoning_supported(self) -> Self:
        if self.provider is Provider.ANTHROPIC and self.reasoning:
            raise ValueError(
                "reasoning is not supported for anthropic yet; it arrives with the "
                "Anthropic switch milestone"
            )
        return self


class EmbeddingSettings(BaseModel):
    """Embeddings stay on Ollama after the LLM switch: Anthropic has no embeddings API."""

    model_config = ConfigDict(frozen=True)

    provider: Literal["ollama"] = "ollama"
    model: str = Field(default="nomic-embed-text", min_length=1)
    # Must match the model's output size and the vector column in the database.
    # Changing either one without the other fails loudly at indexing time.
    dimensions: int = Field(default=768, gt=0)
    timeout_s: float = Field(default=60.0, gt=0)
    # nomic-embed-text is trained with task prefixes and expects them. Omitting them
    # produces different vectors and worse retrieval, with no error raised. Other
    # models want no prefix, or a different one, so both sides are configurable.
    document_prefix: str = "search_document: "
    query_prefix: str = "search_query: "
    # Ollama's model card advertises 2048, but nomic-embed-text handles 8192. Long
    # passages are otherwise truncated without warning.
    num_ctx: int = Field(default=8192, ge=512)

    @property
    def fingerprint(self) -> str:
        """Identifies everything that changes the vectors for the same text.

        Stored alongside each embedding, so switching model, prefix, or dimensions
        marks the index stale instead of silently mixing incomparable vectors.
        """
        return f"{self.model}|{self.document_prefix}|{self.dimensions}"


class AuthSettings(BaseModel):
    """How accounts and credentials are handled."""

    model_config = ConfigDict(frozen=True)

    # bcrypt work factor. Every increment doubles the time to hash and to verify.
    # 12 is the current sensible default; raise it as hardware gets faster. Tests
    # override it downwards, because 12 rounds times a hundred logins is a slow
    # test suite.
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    # Signing key for access tokens. No default: a shared default secret in a public
    # repository is a way to hand out valid tokens. Settings refuses to load without
    # it, the same as the database password.
    jwt_secret: SecretStr | None = None
    # Pinned, and pinned again when decoding. Accepting whatever the token's own
    # header claims is how "alg: none" and algorithm-confusion attacks work.
    jwt_algorithm: Literal["HS256"] = "HS256"
    # Who issued the token and who it is for. Checked on decode, so a token minted
    # for another service cannot be replayed here.
    jwt_issuer: str = "deskpilot"
    jwt_audience: str = "deskpilot-api"
    # Short, because an access token cannot be revoked individually before it
    # expires. Revocation works by token_version, checked when the token is used.
    access_token_minutes: int = Field(default=15, gt=0)
    # Long, because this is what saves the user from logging in every quarter hour.
    # Safe to be long only because it rotates on every use and reuse is detected.
    refresh_token_days: int = Field(default=14, gt=0)

    # Failed logins allowed before an account is locked. Low enough to stop a
    # password being guessed, high enough to survive a person mistyping.
    max_failed_logins: int = Field(default=5, gt=0)
    # First lockout, doubling with each further failure up to the cap. Short at
    # first, because the common cause is a typo, not an attack.
    lockout_seconds: int = Field(default=60, gt=0)
    max_lockout_seconds: int = Field(default=3600, gt=0)

    # Where the CLI keeps its session. Under the user's home rather than the repo,
    # so a checkout cannot accidentally commit one.
    session_file: Path = Path.home() / ".deskpilot" / "session.json"
    # Lets --as name any customer without logging in. Off by default, because a
    # switch that lets one person act as another is exactly the thing this
    # milestone exists to remove. Turned on in development because the CLI is the
    # only interface until the web milestone, and logging in as eight seeded
    # customers to try something is not a good use of anybody's time.
    allow_impersonation: bool = False


class ToolSettings(BaseModel):
    """Limits that protect the context window from a tool's own output.

    A tool that returns everything it finds will eventually return more than the
    model can read, and Ollama truncates silently when that happens. These caps are
    set here rather than taken as tool arguments, so the model cannot raise them.
    """

    model_config = ConfigDict(frozen=True)

    # Orders returned by list_orders. Enough to cover "my recent orders" without
    # crowding out the rest of the conversation.
    max_orders_listed: int = Field(default=10, gt=0)


class PolicySearchSettings(BaseModel):
    """How the agent searches the policy knowledge base."""

    model_config = ConfigDict(frozen=True)

    # Where the markdown source documents live, relative to backend/.
    directory: Path = Path("policies")
    # Passages returned per search. Few enough that the model reads them all.
    top_k: int = Field(default=4, gt=0)
    # Cosine distance above which a passage is treated as irrelevant. 0 is identical
    # and 2 is opposite; a wrong-topic passage usually lands above 0.6.
    max_distance: float = Field(default=0.6, ge=0.0, le=2.0)
    # Chunks are split at markdown headings, then further if a section is long.
    max_chunk_chars: int = Field(default=1200, gt=0)

    @property
    def path(self) -> Path:
        return BACKEND_DIR / self.directory


class DatabaseSettings(BaseModel):
    """PostgreSQL connection. The password has no default; Settings requires it."""

    model_config = ConfigDict(frozen=True)

    host: str = "127.0.0.1"
    port: int = Field(default=5432, gt=0, lt=65_536)
    name: str = Field(default="deskpilot", min_length=1)
    user: str = Field(default="deskpilot", min_length=1)
    password: SecretStr | None = None
    pool_size: int = Field(default=5, gt=0)
    echo_sql: bool = False

    @property
    def url(self) -> URL:
        """SQLAlchemy URL for the async psycopg driver. Its repr hides the password."""
        if self.password is None:
            raise ValueError("DESKPILOT_DATABASE__PASSWORD is not set")
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DESKPILOT_",
        env_nested_delimiter="__",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Without this, DESKPILOT_GUARD__MODEL=x would rebuild the guard settings from
        # ModelSettings defaults and silently drop the guard-specific values below.
        nested_model_default_partial_update=True,
    )

    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    anthropic_api_key: SecretStr | None = None

    # reasoning=True: with thinking off, qwen3.6 announces a tool call and then ends
    # its turn without making it, so the customer gets a promise and no answer. See
    # docs/decisions.md, D-070.
    agent: ModelSettings = ModelSettings(model="qwen3.6:35b", reasoning=True)
    classifier: ModelSettings = ModelSettings(
        model="qwen3.6:35b",
        # One short structured answer. A long one means the model ignored the schema.
        max_output_tokens=64,
        timeout_s=30.0,
    )
    guard: ModelSettings = ModelSettings(
        model="qwen3.6:35b",
        max_output_tokens=256,
        timeout_s=30.0,
    )
    judge: ModelSettings = ModelSettings(
        model="gpt-oss:20b",
        max_output_tokens=1024,
        timeout_s=300.0,
    )
    embeddings: EmbeddingSettings = EmbeddingSettings()
    auth: AuthSettings = AuthSettings()
    tools: ToolSettings = ToolSettings()
    policy_search: PolicySearchSettings = PolicySearchSettings()
    database: DatabaseSettings = DatabaseSettings()

    # How many times the agent may call the model in one run before it gives up.
    # Bounds cost and stops a model that keeps calling tools in a loop.
    max_agent_steps: int = Field(default=6, gt=0)

    def model_for(self, role: ModelRole) -> ModelSettings:
        match role:
            case ModelRole.AGENT:
                return self.agent
            case ModelRole.CLASSIFIER:
                return self.classifier
            case ModelRole.GUARD:
                return self.guard
            case ModelRole.JUDGE:
                return self.judge

    @model_validator(mode="after")
    def _anthropic_needs_api_key(self) -> Self:
        roles = [r.value for r in ModelRole if self.model_for(r).provider is Provider.ANTHROPIC]
        if roles and self.anthropic_api_key is None:
            raise ValueError(
                "DESKPILOT_ANTHROPIC_API_KEY is required because these roles use "
                f"anthropic: {', '.join(roles)}"
            )
        return self

    @model_validator(mode="after")
    def _auth_needs_a_signing_key(self) -> Self:
        if self.auth.jwt_secret is None:
            raise ValueError("DESKPILOT_AUTH__JWT_SECRET is required")
        return self

    @model_validator(mode="after")
    def _database_needs_password(self) -> Self:
        if self.database.password is None:
            raise ValueError("DESKPILOT_DATABASE__PASSWORD is required")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
