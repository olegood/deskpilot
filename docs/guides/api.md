# API guide

The HTTP layer.

> Last verified against: milestone 5, step 5.1.

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
