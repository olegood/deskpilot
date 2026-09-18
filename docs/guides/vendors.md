# Vendors guide

The fake companies Deskpilot integrates with.

> Last verified against: milestone 6 (complete).

| Vendor | What it is | Protocol | Authentication |
|---|---|---|---|
| **ShipTrack** | A shipping carrier | REST | HMAC-signed requests |
| **Paywisp** | A payment processor | MCP | OAuth 2.1 *(not built yet)* |

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
