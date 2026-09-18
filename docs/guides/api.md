# API guide

The HTTP layer.

> Last verified against: milestone 6 (complete).

Everything here is a thin shell over services that already exist and are already
tested. A route reads the request, calls a service, and shapes the response; the
decisions were made in `deskpilot.auth` and `deskpilot.authz`.

## Running it

```bash
uv run deskpilot serve --reload
curl http://127.0.0.1:8000/api/health
```

The interactive docs are deliberately not served. The OpenAPI schema is still
generated, which is what the frontend's typed client needs; a browsable explorer of
every endpoint with a form for each is a target rather than a feature
([D-100](../decisions.md#d-100-the-interactive-docs-are-not-served)).

## Endpoints so far

| Method | Path | Needs | Returns |
|---|---|---|---|
| `GET` | `/api/health` | nothing | `{"status": "ok"}` |
| `POST` | `/api/auth/login` | email and password | access token, sets two cookies |
| `POST` | `/api/auth/refresh` | refresh cookie + CSRF header | a new access token, rotates the cookie |
| `POST` | `/api/auth/logout` | refresh cookie | 204 |
| `GET` | `/api/auth/me` | `Authorization: Bearer` | who you are |
| `POST` | `/api/tickets` | bearer token | opens a ticket, returns the agent's first answer |
| `GET` | `/api/tickets` | bearer token | your tickets, newest first |
| `GET` | `/api/tickets/{reference}` | bearer token | one ticket and its conversation |
| `POST` | `/api/tickets/{reference}/replies` | bearer token | adds a message, returns the answer |
| `POST` | `/api/tickets/stream` | bearer token | opens a ticket, streams the answer |
| `POST` | `/api/tickets/{reference}/replies/stream` | bearer token | replies, streams the answer |

Health says nothing about the database on purpose. An endpoint that reports which
dependency is down tells an attacker which dependency to attack.

## Where the two tokens live

```
access token   ->  response body  ->  SPA memory        15 minutes
refresh token  ->  httpOnly cookie ->  script cannot see  14 days
```

They have different lifetimes and different threats. An XSS bug can read anything
the page can reach, so the thing it can reach expires in fifteen minutes; the thing
that lasts a fortnight goes where script cannot look. Putting both in `localStorage`
is the usual shortcut and hands an attacker the fortnight
([D-096](../decisions.md#d-096-the-access-token-goes-in-the-body-the-refresh-token-in-an-httponly-cookie)).

The refresh cookie is `HttpOnly`, `SameSite=strict`, and scoped to `/api/auth`, so
it is not attached to every request. `Secure` is off for local http and controlled
by `DESKPILOT_API__SECURE_COOKIES`.

The cost: the SPA loses its access token when the page reloads, so it calls
`/refresh` on load. Reuse detection still applies, so a stolen refresh token is
caught the moment either holder uses it twice
([D-063](../decisions.md#d-063-refresh-tokens-rotate-and-reuse-revokes-the-family)).

## CSRF, on exactly one endpoint

`/api/auth/refresh` is the only endpoint authenticated by a cookie, so it is the
only one a browser could be tricked into calling from another site. It requires an
`X-CSRF-Token` header matching a readable `deskpilot_csrf` cookie.

Everything else is authenticated by an `Authorization` header, which a browser will
never attach on its own. Adding a CSRF check there would be ritual
([D-097](../decisions.md#d-097-the-refresh-endpoint-checks-a-double-submit-token)).

Logout has no check either: being signed out against your will is an annoyance, not
a compromise.

Logout revokes the refresh family and clears the cookie, but an **access token
already in the client's hands stays valid until it expires** — at most fifteen
minutes. Killing it immediately would mean bumping `token_version`, which signs the
person out of every other device too. That is what "sign out everywhere" is for. The
window is bounded, and it is short for exactly this reason
([D-116](../decisions.md#d-116-logging-out-does-not-invalidate-the-access-token)).

## Rate limiting

Failed logins are counted per **source address**, in a fixed window.

This is the counter account lockout could not provide. Lockout stops somebody
guessing one password; it cannot stop one password tried against a thousand
accounts, because that never trips any single account's counter. The HTTP layer is
the first place with an address to count against
([D-098](../decisions.md#d-098-rate-limiting-by-source-address-in-memory-and-honest-about-it)).

Two limitations, stated rather than hidden:

- **It is in process memory.** Per process, forgotten on restart. A shared store is
  the real answer, and belongs where there is more than one process to share it.
- **The address is `request.client.host`, not `X-Forwarded-For`.** Taking that
  header unconditionally would let anybody choose their own identity and skip the
  limit. Trusting a proxy is a deployment decision, made where the proxy is.

A successful login clears the address, so one person's bad evening does not linger.

## Tickets

Every endpoint asks the same `guard` the tools ask, on the same rules. One
definition of who may read a ticket, rather than one per entry point.

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"noah.kim@example.com","password":"..."}' | jq -r .access_token)

curl -s -X POST localhost:8000/api/tickets -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"subject":"Where is my order","message":"Where is ORD-1042?"}' | jq
```

The agent runs **synchronously**, so opening a ticket takes as long as the model
does. Step 5.3 streams the turn as it happens, using the same call underneath.

Three things worth knowing:

**Another customer's ticket is 404, not 403.** A 403 would confirm the reference
exists, turning the endpoint into an oracle for valid references. The audit log
still records which of the two really happened
([D-103](../decisions.md#d-103-another-customers-ticket-is-404-not-403)).

**No dependency refuses on its own.** An endpoint builds a resource whose owner is
an id nothing owns and lets `guard` refuse. The first version raised from a
dependency: right status, no audit entry — a refusal with no trace, in a project
whose whole argument is that refusals are recorded
([D-102](../decisions.md#d-102-a-dependency-must-not-refuse-on-its-own)).

**The conversation leaves out the agent's working.** Tool calls and their results
are not returned. They are the agent's working rather than the conversation, and a
tool result is raw text from a database row heading for a browser that nothing has
sanitised. Rendering untrusted text is dealt with properly in the security
milestone; until then it does not leave the server
([D-104](../decisions.md#d-104-the-conversation-the-api-returns-leaves-out-the-agents-working)).

The checkpointer and the compiled graph are built once in the app's lifespan and
shared. `run_turn` was split out from `run_agent` back in milestone 2 for exactly
this caller ([D-101](../decisions.md#d-101-the-graph-is-built-once-for-the-process)).

## Streaming

The non-streaming endpoints make the caller wait for the whole model run. The
streaming ones report progress as it happens:

```
event: ticket    data: {"reference":"TCK-0002"}
event: category  data: {"category":"order_status"}
event: tool      data: {"name":"get_order"}
event: token     data: {"text":"Your order "}
event: token     data: {"text":"shipped on "}
event: answer    data: {"answer":"Your order shipped on ...","escalated":false,...}
event: done      data: {"reference":"TCK-0002","status":"awaiting_customer"}
```

`token` events make the answer appear a word at a time. `category` and `tool` are
what let a customer see it working rather than staring at a spinner. The `answer`
event carries the whole thing, so a client that ignored the tokens still gets it —
and it is read back from the checkpoint rather than from the accumulated tokens,
because the saved state is what the conversation actually contains
([D-108](../decisions.md#d-108-two-stream-modes-and-the-saved-state-wins)).

These are `POST` rather than `GET`, because there is a message to send and because
the browser's `EventSource` cannot set an `Authorization` header. The frontend uses
fetch-based streaming instead.

### Three things that shape the implementation

**The status code is decided before the first byte.** Authentication,
authorization, validation and loading the ticket all happen before the streaming
response is returned; only the agent run is inside the generator. Once a 200 has
gone out, a refusal can only be an event inside a stream the client already
accepted, which every HTTP client treats as success. A refused stream is a plain
403, and there are tests for 401, 403, 404 and 422
([D-105](../decisions.md#d-105-the-status-code-is-decided-before-the-first-byte)).

**The generator opens its own database session.** FastAPI closes a dependency's
session when the handler returns, and a streaming handler returns immediately — the
generator runs afterwards
([D-106](../decisions.md#d-106-a-streaming-handler-cannot-use-the-requests-database-session)).

**Every payload is JSON.** A raw newline ends a `data:` field, and model output is
full of newlines; sending text directly would split one event in two at the first
line break. A test asserts every `data:` line parses
([D-107](../decisions.md#d-107-every-sse-payload-is-json)).

`X-Accel-Buffering: no` is set, because Nginx buffers by default and turns a stream
into one late lump. There is no keepalive: sending one needs a second task racing
the real stream, and the gaps here are a model thinking rather than minutes of
silence. If a proxy closes idle connections sooner than a turn takes, that is the
thing to add ([D-109](../decisions.md#d-109-no-keepalive-for-now)).

### If the client disconnects

The run continues and the checkpoint is written, so the customer's next request
finds the answer waiting. That falls out of checkpointing rather than being designed
for, but it is the behaviour you want.

## Errors

Every error is `{"detail": "..."}`, and the mapping is registered once on the app
so a new route inherits it ([D-099](../decisions.md#d-099-one-place-turns-exceptions-into-responses)).

| Exception | Status | Body |
|---|---|---|
| `AuthError` | 401 | its own deliberately vague message |
| `TokenError` | 401 | "this session is not valid", and similar |
| `Forbidden` | 403 | "You are not allowed to do that." |
| `TicketError` | 404 | the message |
| anything else | 500 | "Something went wrong." |

The 500 handler matters most. An exception message is written for a developer and
routinely contains hostnames and table names, so it is logged in full and reported
as nothing.

`Forbidden` carries the real reason, and it goes to the audit log rather than over
the wire — the same rule as everywhere else
([D-079](../decisions.md#d-079-the-refusal-a-caller-sees-carries-no-reason)).

## The schema is a checked contract

`frontend/src/api/openapi.json` is committed, and a backend unit test compares it
against what the app produces. A stale copy would mean the frontend's generated
types describe an API that no longer exists, which defeats the reason for
generating them ([D-115](../decisions.md#d-115-the-committed-openapi-document-is-checked-against-the-app)).

Changing a response model therefore fails the test suite until you run:

```bash
uv run deskpilot openapi --out ../frontend/src/api/openapi.json
cd ../frontend && pnpm types
```

Which is the right moment to notice.

## Headers and CORS

Every response, including error responses, carries `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`Cache-Control: no-store`, a `Permissions-Policy` turning off features the API does
not use, and a `Content-Security-Policy` of `default-src 'none'; frame-ancestors
'none'` — the API returns JSON, so nothing needs to load.

CORS allows exactly one origin, from `DESKPILOT_API__FRONTEND_ORIGIN`. A wildcard
with credentials is refused by browsers anyway, and a list of origins is a list to
get wrong.

## Testing

`tests/integration/test_api_auth.py` drives the real app through httpx's
`ASGITransport` — no server, no port, but the real middleware, the real dependency
graph, and the real database.

One wrinkle worth knowing: `ASGITransport` does not run the lifespan, so the
fixture enters `app.router.lifespan_context` itself. Without it, `app.state` is
empty and every request fails on a missing engine.

```bash
uv run pytest -m integration -k api_auth
```
