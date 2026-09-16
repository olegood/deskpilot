"""The committed OpenAPI document must match the app.

The frontend generates its TypeScript from `frontend/src/api/openapi.json`, so a
stale copy means the types describe an API that no longer exists - and the whole
point of generating them was to make drift a compile error. This is the same idea
as `alembic check`: the checked-in artefact and the code it came from are compared
on every run.
"""

import json

from deskpilot.api.app import create_app
from deskpilot.config import BACKEND_DIR

SCHEMA = BACKEND_DIR.parent / "frontend" / "src" / "api" / "openapi.json"


def test_the_committed_schema_is_current() -> None:
    if not SCHEMA.exists():
        # The frontend is optional for a backend-only checkout.
        return

    committed = json.loads(SCHEMA.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(create_app().openapi()))

    assert committed == current, (
        "frontend/src/api/openapi.json is out of date. Regenerate it with:\n"
        "  uv run deskpilot openapi --out ../frontend/src/api/openapi.json\n"
        "and then, in frontend/, run: pnpm types"
    )


def test_the_schema_describes_the_endpoints_the_frontend_uses() -> None:
    paths = set(create_app().openapi()["paths"])

    assert {
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/tickets",
        "/api/tickets/stream",
        "/api/tickets/{reference}",
        "/api/tickets/{reference}/replies/stream",
    } <= paths
