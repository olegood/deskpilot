# Vendors guide

The fake companies Deskpilot integrates with.

> Last verified against: milestone 7, step 7.1.

| Vendor | What it is | Protocol | Authentication |
|---|---|---|---|
| **ShipTrack** | A shipping carrier | REST | HMAC-signed requests |
| **Paywisp** | A payment processor | MCP *(from 7.2)* | OAuth 2.1 bearer tokens |

Each is a **separate service**: its own uv project, its own container, its own
settings, its own test suite. Deskpilot reaches them over HTTP and shares nothing
but a secret.

That is the point. A mock patched into the client tests the client against its
author's belief about the vendor. A service tests it against something that can
disagree — that can be slow, return a 500, hang, or refuse a signature the client
thought was fine ([D-117](../decisions.md#d-117-the-carrier-is-a-separate-service-not-a-mock)).

## ShipTrack

```bash
docker compose up -d shiptrack
curl -s localhost:8100/api/health

# or, for development
cd vendors/shiptrack && uv run python -m shiptrack
```

| Method | Path | What |
|---|---|---|
| `GET` | `/api/health` | Unsigned, so a container healthcheck needs no credential |
| `GET` | `/api/shipments/{tracking_number}` | Status, destination, and every scan |
| `POST` | `/api/shipments/{tracking_number}/advance` | Record a scan and fire a callback |
| `PUT` | `/api/_chaos` | Change how badly it behaves, at runtime |

Its parcels match the tracking numbers on Acme Gear's seeded orders. That is the
only thing the two systems share, and it is shared the way it would be in life: one
of them wrote it on a parcel and told the other.

Worth knowing about the data: `ST-100095` is scanned as delivered and disputed by
the customer, and `ST-100077` has not moved for eleven days — which is "lost" by
Acme Gear's own shipping policy. Those are the two cases that generate real tickets.

### How a request is signed

```
HMAC-SHA256(secret, "GET\n/api/shipments/ST-100042\n<timestamp>\n<nonce>\n<sha256 of body>")
```

Sent as four headers: `X-ShipTrack-Key`, `-Timestamp`, `-Nonce`, `-Signature`.

Every piece closes a hole, and a test asserts that changing each one changes the
signature ([D-118](../decisions.md#d-118-the-signature-covers-method-path-timestamp-nonce-and-a-body-digest)):

| Without | What becomes possible |
|---|---|
| method and path | A captured read replays as a write, or against another parcel |
| timestamp | A capture works for ever |
| nonce | A capture works repeatedly inside the window |
| body digest | The body is swapped under a valid signature |

The newlines matter too. Concatenated without a separator, a path ending in `1` with
nonce `23` produces the same string as a path ending in `12` with nonce `3`. That
has its own test.

### Replay protection

A timestamp more than five minutes from the server's clock is refused, in **both**
directions — a timestamp far in the future would widen the window rather than narrow
it. A nonce is remembered and refused the second time.

The nonce is recorded **only for a request that was otherwise valid**. Recording it
earlier would let anybody burn nonces they never used: a million unsigned requests
carrying guessed nonces, and the real client starts being refused
([D-119](../decisions.md#d-119-the-nonce-is-recorded-only-for-a-request-that-was-otherwise-valid)).

Every refusal reads the same and the real reason goes to the log. "Unknown key id"
versus "signature does not match" tells a prober which half they have right
([D-120](../decisions.md#d-120-every-refusal-reads-the-same-and-the-reason-goes-to-the-log)).

### Chaos

```bash
# by environment, in the repo-root .env
SHIPTRACK_ERROR_RATE=0.2
SHIPTRACK_HANG_RATE=0.1
SHIPTRACK_LATENCY_MS=400
```

Or at runtime, with a signed `PUT /api/_chaos`.

A supplier that always answers in five milliseconds teaches nothing about timeouts,
retries, or circuit breakers, and resilience that has never been exercised is a
claim rather than a property
([D-121](../decisions.md#d-121-the-chaos-knobs-exist-so-the-resilience-can-be-tested)).

**The hang is the important one.** A 500 is obvious. A connection that stays open
looks like a slow response until somebody's timeout decides otherwise — and a client
without one waits for ever. That is what step 6.2 has to survive.

Chaos is applied **after** authentication, so a failing carrier still refuses bad
signatures in exactly the same way. Otherwise the failure rate itself would leak
whether a key was right.

## The client

`backend/src/deskpilot/integrations/shiptrack/` is Deskpilot's side. Three layers,
and **they only work in this order**
([D-125](../decisions.md#d-125-the-three-layers-only-work-in-order)):

| Layer | What it does | Without it |
|---|---|---|
| Read timeout | Turns "never answers" into "failed" | Nothing ever fails, so nothing below ever fires |
| Retries | A blip does not reach the customer | A single 500 becomes an error |
| Circuit breaker | Stops asking a service that is down | Retries make an outage worse, not better |

The breaker is the one people leave out, and it is the one that stops a dead
supplier turning into a slow application: without it every request spends its whole
timeout budget three times over, and the retries arrive exactly as the supplier is
trying to recover. The jitter on the backoff is part of the same argument — without
it, every client that failed together retries together.

### What is retried, and what is not

Timeouts, connection errors and 5xx are retried. A 401 or a 404 is not: retrying is
asking the same question and expecting a different answer
([D-123](../decisions.md#d-123-retries-only-for-failures-that-might-not-happen-again)).

A wrong key also must not trip the breaker. That is our problem to fix, and letting
it open the circuit would stop every later request for a reason that has nothing to
do with the carrier's health. A test asserts a bad secret leaves the circuit closed.

**Each attempt signs afresh.** Reusing a timestamp and nonce would make the retry
fail as stale or as a replay — a failure with nothing to do with why the first
attempt failed.

**A retried call is one failure, not several.** Counting each attempt would trip a
five-failure circuit on the second bad request
([D-124](../decisions.md#d-124-a-retried-call-is-one-failure-not-several)).

### The signing code is written twice

`integrations/shiptrack/signing.py` re-implements the vendor's scheme rather than
importing it. That is deliberate: in life the scheme arrives as a document and you
write the code. Sharing a helper would also make the contract test meaningless,
because a shared implementation cannot disagree with itself
([D-122](../decisions.md#d-122-the-client-re-implements-the-signing-scheme)).

A test asserts nothing under `src/deskpilot` imports `shiptrack`, so the shortcut
cannot be taken by accident.

### The tool

`track_shipment(order_number)` — an **order** number, never a tracking number. It
looks the tracking number up from an order the customer owns, after asking the
policy engine. A tool that accepted a tracking number would report on any parcel in
the carrier's system to anybody who could guess one, and tracking numbers are
sequential ([D-126](../decisions.md#d-126-the-tool-takes-an-order-number-not-a-tracking-number)).

When the carrier is unreachable the tool returns a sentence, not an exception. The
agent's job at that moment is to say something true to a customer, and "we cannot
reach the carrier" is true ([D-127](../decisions.md#d-127-a-carrier-failure-is-a-sentence-not-an-exception)).

## Callbacks

When a parcel moves, ShipTrack posts to Deskpilot:

```
POST /api/webhooks/shiptrack
{"event": "shipment.updated", "tracking_number": "ST-100042", "status": "delivered", ...}
```

Signed with the same scheme, in the other direction, and with a **different
secret**. A leak of the key used to ask questions should not also let somebody
forge answers — and answers are the more dangerous half, because a forged callback
writes to our database while a forged request only reads from theirs
([D-129](../decisions.md#d-129-inbound-and-outbound-use-different-secrets)). A test
asserts the request secret does not work on the callback endpoint.

### A webhook is an unauthenticated POST until it is verified

This is the direction people forget. A team that signs its outbound requests
carefully will often accept a webhook because it arrived at a secret-looking URL —
which is a password sitting in every proxy log between the sender and here
([D-130](../decisions.md#d-130-a-webhook-is-an-unauthenticated-post-until-it-is-verified)).

The signature is checked over the **raw body, before it is parsed**. Handing
unverified bytes to a validator, a database write, or a model is the whole problem.

Tested: a tampered body signed as a different event, a replay, a stale timestamp, an
unknown key, a signature made for a different path, and no signature at all. The
tampered-body case is the interesting one — sign "in transit", deliver "delivered" —
and it is the body digest that catches it.

### What a callback is allowed to change

A small map of carrier statuses onto order statuses. Anything else leaves the order
alone.

The carrier knows where a parcel is. It does not know whether an order was
cancelled, refunded, or replaced, and a vendor that can set arbitrary states on our
records has more authority than the relationship warrants
([D-131](../decisions.md#d-131-the-carrier-is-authoritative-about-parcels-not-about-orders)).
Every callback is recorded in the audit log: an order changing state with nobody
asking is exactly the kind of thing somebody will later want to account for.

A callback for a parcel we have no order for returns 204 and does nothing. The
carrier has other customers, and a 4xx would make it retry something that will never
work ([D-132](../decisions.md#d-132-an-unknown-tracking-number-is-accepted-quietly)).

### Delivery is best effort

ShipTrack logs a failed callback and drops it. No retries, no blocking. A carrier
that retried into a customer that is down would turn one outage into two
([D-133](../decisions.md#d-133-delivery-is-best-effort-and-the-carrier-says-so)).

So the webhook is a **hint** that polling would be worth doing sooner.
`track_shipment` is where the truth comes from. A system that treats a webhook as
its only source of truth has made its supplier's availability its own.

### Trying it

```bash
# with both services running and DESKPILOT_SHIPTRACK__WEBHOOK_SECRET set
curl -s localhost:8000/api/health
uv run deskpilot ask "Where is ORD-1042?" --as noah.kim@example.com
# advance the parcel (signing by hand is fiddly; the test does it properly)
uv run deskpilot audit tail -n 3
```

## Paywisp

Two services, one project:

| Service | Port | What it does |
|---|---|---|
| `paywisp.auth_server` | 8200 | Issues access tokens |
| `paywisp.mcp_server` | 8210 | Holds the payments *(step 7.2)* |

They are separate **processes**, not just separate modules. The MCP server learns the
signing keys the way any resource server would — by fetching the authorization
server's JWKS over HTTP — and nothing is shared in memory. That is what keeps
"replace the authorization server with Keycloak", which is on the backlog, a
configuration change rather than a rewrite
([D-138](../decisions.md#d-138-paywisps-two-services-share-a-project-and-nothing-else)).

```bash
docker compose up -d paywisp-auth
curl -s localhost:8200/.well-known/oauth-authorization-server | jq

# or, for development
cd vendors/paywisp && uv run python -m paywisp.auth_server
```

### The authorization server

| Method | Path | What |
|---|---|---|
| `GET` | `/.well-known/oauth-authorization-server` | RFC 8414 metadata: where everything else is |
| `GET` | `/.well-known/jwks.json` | The public signing key |
| `POST` | `/oauth/token` | Client credentials only, for now |

Hand-built on `joserfc` rather than Authlib's server components, which only integrate
with Flask and Django. Authlib's own JOSE module is deprecated in favour of
`joserfc`, which is by the same author
([D-137](../decisions.md#d-137-the-authorization-server-is-hand-built-on-joserfc)).

Getting a token looks like this:

```bash
curl -s -u deskpilot-agent:$PAYWISP_AGENT_CLIENT_SECRET \
  -d grant_type=client_credentials \
  -d scope=payments:read \
  -d resource=http://127.0.0.1:8210/mcp \
  localhost:8200/oauth/token | jq
```

### What a token is, and why each part is there

An ES256-signed JWT in the RFC 9068 access token profile:

| Part | Why |
|---|---|
| **ES256**, asymmetric | The MCP server can verify a token without being able to mint one. A shared HMAC secret would hand every verifier the power to forge ([D-139](../decisions.md#d-139-tokens-are-signed-with-es256-so-a-verifier-cannot-mint)) |
| `typ: at+jwt` | Distinguishes an access token from any other JWT this issuer signs ([D-142](../decisions.md#d-142-an-access-token-says-that-it-is-one)) |
| `aud`, exactly one resource | A token for the MCP server does not work anywhere else |
| `scope` | Checked per operation by the resource server |
| `exp`, five minutes | A bearer token cannot be recalled |
| `kid`, the key's thumbprint | Derived, not chosen, so two keys cannot collide on a name |

### The agent can never be given write

Paywisp registers Deskpilot's agent as a client whose **ceiling** is
`payments:read`. Asking for `refunds:write` is refused with `invalid_scope` — and so
is asking for both at once, refused whole rather than trimmed
([D-140](../decisions.md#d-140-the-agents-client-can-never-be-granted-write)).

That is the difference between a promise and a property. Deskpilot's agent asking
only for read is a promise. The agent's client being unable to receive write is a
property of the authorization server, and a leaked agent secret cannot move money.

Every request must also **name its scope and its resource**. No default scope: a
default grows silently whenever a client's allowance does. No audience-less token:
one without an `aud` is accepted by every service that trusts the issuer
([D-141](../decisions.md#d-141-every-token-request-names-its-scope-and-its-resource)).

### Keys

Set `PAYWISP_SIGNING_KEY` to an EC P-256 PEM to keep tokens valid across restarts.
Without it, a key is generated at startup, logged as a warning, and every earlier
token stops verifying — which for a fake vendor is acceptable and occasionally
useful, since a restart revokes everything
([D-144](../decisions.md#d-144-a-generated-signing-key-is-allowed-and-a-restart-revokes-everything)).

### Contract tests

`vendors/paywisp/tests/contract/` describes what Deskpilot relies on from *any*
authorization server. Every endpoint is found through the metadata document, and
nothing there imports Paywisp, so the same tests can be pointed at another server:

```bash
PAYWISP_CONTRACT_ISSUER=https://keycloak.example/realms/paywisp \
PAYWISP_CONTRACT_CLIENT_ID=deskpilot-agent \
PAYWISP_CONTRACT_CLIENT_SECRET=... \
PAYWISP_CONTRACT_RESOURCE=http://127.0.0.1:8210/mcp \
uv run pytest tests/contract
```

That is how the Keycloak backlog item gets validated
([D-143](../decisions.md#d-143-the-authorization-server-is-tested-as-a-contract)). The
tests are written to be fair to a server that is correct, not to one that resembles
Paywisp; whether a particular one passes is what running them finds out. The
`typ: at+jwt` check is the likeliest to differ, since not every server follows
RFC 9068.

Paywisp's own policies — no default scope, one resource only — are tested separately
in `tests/test_auth_server.py`, because they are choices rather than requirements.

One interoperability detail worth remembering for the client side: RFC 6749 says a
client id and secret are **percent-encoded before** they are joined for Basic auth,
and Paywisp decodes them. httpx's `BasicAuth` does not encode. Hex secrets never
notice; a secret containing `%` would.

## Testing a vendor

Each vendor has its own suite, run by `scripts/check.sh` along with everything else:

```bash
cd vendors/shiptrack && uv run pytest
```

Deskpilot's side is tested against the real carrier, in process:
`tests/integration/test_carrier_client.py` runs both halves and asserts they agree.
ShipTrack is a **dev-only** dependency of the backend for exactly this, which is why
the "nothing imports it" test exists.

Two details that each cost an hour if you meet them cold.

**Starlette's 500 handler sends the response and then re-raises**, so the process
running the server logs the traceback. httpx's `ASGITransport` propagates that by
default, so a test asserting on a 500 gets the original exception instead. The
fixtures pass `raise_app_exceptions=False`, which is what a real client over a real
socket sees.

**The two sides are checked against each other.** One test runs both applications
and posts a real callback from one to the other. ShipTrack signs with its own code
and Deskpilot verifies with entirely separate code, so nothing else can catch them
drifting apart ([D-134](../decisions.md#d-134-the-two-sides-are-checked-against-each-other)).

**An in-process transport cannot test a timeout.** `ASGITransport` calls the
application directly, and an httpx timeout is a *network* timeout — there is no
socket to give up on, so a handler that sleeps simply sleeps and the client waits
with it. The hang test therefore runs against a real TCP listener that accepts and
never answers. The one property that most needed testing was the one the convenient
transport could not test
([D-128](../decisions.md#d-128-an-in-process-transport-cannot-test-a-timeout)).
