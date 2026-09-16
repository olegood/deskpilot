"""Server-sent events.

The format is deliberately plain text: `event:` and `data:` lines, a blank line
between messages. Two rules shape everything here.

First, **the status code is decided before the first byte**. Once a 200 and the
headers have gone out, an authorization failure cannot become a 403; it can only be
an event inside a stream the client already accepted. So everything that can refuse
runs before the generator starts.

Second, **the data must not contain a newline**. A raw newline ends the field, and a
customer's message or a model's answer is full of them, so every payload is JSON.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# No keepalive. A comment line every few seconds would stop an idle proxy closing
# the connection, and sending one needs a second task racing the real stream. The
# gaps here are a model thinking, measured in seconds rather than minutes, so the
# complexity is not earned yet. If a deployment sits behind a proxy that closes
# idle connections sooner than a turn takes, this is the thing to add.
SSE_HEADERS = {
    # Nginx buffers by default, which turns a stream into one late lump.
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def event(name: str, payload: dict[str, Any] | None = None) -> str:
    """Format one event. The payload is JSON, so newlines cannot break the framing."""
    return f"event: {name}\ndata: {json.dumps(payload or {}, separators=(',', ':'))}\n\n"


def stream(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers=SSE_HEADERS)


async def guarded(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """Turn a failure part-way through a stream into an event.

    By the time the generator runs, the client has a 200. An exception here would
    otherwise close the connection with no explanation, which a browser reports as
    a network error rather than as anything useful.
    """
    try:
        async for item in events:
            yield item
    except Exception:
        logger.exception("a stream failed part-way through")
        yield event("error", {"detail": "Something went wrong."})
