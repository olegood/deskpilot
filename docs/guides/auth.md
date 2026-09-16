# Authentication guide

Accounts, passwords, and how a login is checked.

> Last verified against: milestone 5 (complete).

This covers **who somebody is**. What they are allowed to do is a separate
question, answered by the ABAC milestone.

## Users and customers are different things

| | `users` | `customers` |
|---|---|---|
| Who | Somebody who can log in | Somebody who bought something |
| Examples | A customer who registered, a reviewer, an admin | Anyone with an order |
| Linked by | `users.customer_id`, nullable | |

They overlap but are not the same population. A reviewer logs in and has never
bought anything; a customer who never registered still has orders and tickets.
Merging them would put staff rows in the customer table or hang orders off accounts
that do not exist ([D-053](../decisions.md#d-053-users-are-a-separate-table-from-customers)).

Registration links the two by email when a matching customer exists. From the ABAC
milestone onwards, that link is what lets a login see that person's orders.

Roles are `customer`, `reviewer`, `supervisor`, and `admin`. A role is **not** a
permission — it is one attribute among several that ABAC will weigh up. Nothing in
the code branches on it yet, and that is deliberate.

## Commands

```bash
uv run deskpilot auth register noah.kim@example.com --name "Noah Kim"
uv run deskpilot auth register lena@acmegear.example --name "Lena Fox" --role reviewer
uv run deskpilot auth check noah.kim@example.com
uv run deskpilot auth passwd noah.kim@example.com
```

Passwords are always prompted for, never passed as arguments. An argument lands in
shell history and is visible in the process list to anyone else on the machine
([D-058](../decisions.md#d-058-the-cli-never-takes-a-password-as-an-argument)).

```bash
uv run deskpilot auth login noah.kim@example.com
uv run deskpilot auth whoami
uv run deskpilot auth logout
```

`auth check` verifies a password and issues nothing; it is a smoke test for the
hashing path. The tokens themselves are never printed: a terminal scrollback is a
poor place for a credential.

## Passwords

Hashed with bcrypt at cost 12, using the `bcrypt` library directly. passlib is
unmaintained and breaks against modern bcrypt ([D-007](../decisions.md#d-007-bcrypt-and-pyjwt-directly)).

The policy rejects a password that is:

- shorter than 12 characters
- longer than **72 bytes**
- surrounded by whitespace
- on a small list of common passwords
- the same as the email address

Every problem is reported at once, rather than one per attempt.

### The 72-byte limit is the interesting one

bcrypt hashes at most 72 bytes and silently ignores the rest. Two different long
passwords can therefore produce the same hash, which quietly weakens a password the
user believed was strong. Deskpilot rejects instead of truncating
([D-054](../decisions.md#d-054-a-password-longer-than-72-bytes-is-rejected-not-truncated)).

The limit is **bytes, not characters**. Forty accented characters are eighty bytes
in UTF-8 and will be refused, which is surprising enough to have its own test.

### Cost

`DESKPILOT_AUTH__BCRYPT_ROUNDS` defaults to 12, which is about a quarter of a second
per hash by design. Tests pass 4 explicitly: a suite that registers dozens of
accounts would otherwise spend its time proving that bcrypt is slow on purpose
([D-056](../decisions.md#d-056-the-bcrypt-cost-is-configurable-and-tests-lower-it)).

The cost is recorded inside every hash, so raising the default later does not
invalidate existing passwords.

## Why every login failure looks identical

An unknown address, a wrong password, and a disabled account all produce the same
message:

> That email and password do not match an account.

They also take about the same time. The unknown-address path verifies against a
throwaway hash computed at import, purely to spend the time a real check would
([D-055](../decisions.md#d-055-every-login-failure-looks-and-costs-the-same)).

Both halves matter. A distinct "no such account" message enumerates registered
addresses in one request. A faster response for unknown addresses does the same
thing without any message at all, because bcrypt at cost 12 takes long enough to
measure over a network.

The logs record the real reason. The person typing at the form does not.

## Tokens

A login issues two things.

| | Access token | Refresh token |
|---|---|---|
| Form | Signed JWT | 256 random bits, opaque |
| Lifetime | 15 minutes | 14 days |
| Stored | Nowhere | As a SHA-256 digest |
| Proves | Who you are, right now | That you may have a new access token |

### The access token carries identity and nothing else

The claims are `sub`, `jti`, `ver`, `iat`, `exp`, `iss`, `aud`. No role, no email,
no approval limit.

A claim baked in at login is a snapshot of a permission that may since have been
taken away: a reviewer whose limit was lowered would keep the old one until their
token expired. Everything an authorization decision needs is read from the database
at the moment of the decision
([D-060](../decisions.md#d-060-an-access-token-carries-identity-and-nothing-else)).

This is the concrete form of **the token proves who you are; it does not decide what
you can do.**

### Verifying is not just checking the signature

`decode_access_token` pins the algorithm, checks the issuer and the audience, and
requires every claim to be present. Trusting the token's own `alg` header is how
`alg: none` gets accepted ([D-061](../decisions.md#d-061-the-algorithm-is-pinned-at-decode-and-issuer-and-audience-are-checked)).

`authenticate_access_token` then loads the account and checks `token_version` and
`is_active`. A signature proves the token is ours and unmodified; it cannot know the
account was disabled a minute ago. Without that lookup, "revoke all sessions" would
mean "revoke all sessions within fifteen minutes"
([D-064](../decisions.md#d-064-the-database-is-consulted-even-after-the-signature-verifies)).

### Refresh tokens rotate, and reuse is caught

Every refresh retires the old token and issues a new one in the same **family**.
Presenting an already-retired token means two copies are circulating, so every token
in the family is revoked ([D-063](../decisions.md#d-063-refresh-tokens-rotate-and-reuse-revokes-the-family)).

```
login          -> token A                  family f1
refresh(A)     -> token B, A retired       family f1
refresh(A)     -> reuse. f1 revoked entirely; B is dead too.
```

Which of the two holders is the thief is unknowable, so both lose the session. That
is a minor annoyance in exchange for turning a silent compromise into a visible one.

Each login starts its own family, so signing out a laptop does not sign out a phone.

Only the digest is stored, so a database dump cannot be used to mint sessions.
SHA-256 rather than bcrypt: the input is already random, so there is no dictionary
to run and no reason to be slow on a lookup path
([D-062](../decisions.md#d-062-refresh-tokens-are-opaque-and-stored-as-sha-256-digests)).

## The saved session

`auth login` writes `~/.deskpilot/session.json`, and every other command reads it to
work out who it is acting for.

That is a refresh token sitting in a file, which is worth being uncomfortable about.
The alternatives are worse for a local tool: a keychain drags in a platform-specific
dependency, and keeping it in memory means logging in for every command. Every
command-line tool that does not make you log in every time works this way. So it is
written carefully and the trade-off is stated rather than hidden
([D-066](../decisions.md#d-066-the-cli-session-lives-in-a-file-and-the-trade-off-is-documented)).

- `0600` inside a `0700` directory, created with the right mode from the start.
  Writing first and `chmod`-ing afterwards leaves a window where it is world-readable.
- Reading a file that others can read is **refused**, with the `chmod` to run.
- `__repr__` is overridden, so a traceback cannot carry the tokens.
- It lives under the home directory, not the repository, so a checkout cannot
  commit one.

The access token expires after fifteen minutes, and the CLI is used in bursts hours
apart, so it is refreshed at the point of use rather than at login. Thirty seconds
of skew stops a token that is valid at the check from expiring during the request it
was fetched for ([D-069](../decisions.md#d-069-the-access-token-is-refreshed-where-it-is-used-not-where-it-is-issued)).

## Who a command acts as

Every ticket command asks one resolver, `current_customer`:

1. `--as` was given, and impersonation is on → that customer, with a warning logged.
2. `--as` was given, and impersonation is off → refused, naming the command to log
   in and the variable to set.
3. Otherwise → the logged-in user's linked customer.
4. Logged in, but a `reviewer` or `admin` with no customer record → told plainly
   that the account has no orders or tickets of its own.

One resolver because writing this step found `ticket list` handling `--as` on its
own and never consulting the session, which meant it listed every customer's tickets
to anybody who ran it. It had been that way since tickets were added
([D-068](../decisions.md#d-068-every-ticket-command-goes-through-one-resolver)).

### `--as` is an escape hatch, not a feature

It works only when `DESKPILOT_AUTH__ALLOW_IMPERSONATION` is true, which it is not by
default. `.env.example` turns it on for local development, because the CLI is the
only interface until the web milestone and logging in as each of eight seeded
customers would make the project tedious to work on. It must never be true anywhere
real ([D-067](../decisions.md#d-067---as-survives-as-an-opt-in-escape-hatch)).

## Lockout

Five consecutive failed logins lock an account for a minute. Each further failure
doubles the lockout, capped at an hour. A success clears the counter, and expiry is
by timestamp, so nothing has to run to unlock an account
([D-071](../decisions.md#d-071-account-lockout-with-exponential-backoff-and-why-that-is-a-trade)).

Doubling matters more than the starting value. It makes sustained guessing cost
exponentially more, while one fat-fingered evening costs a minute.

**This is a trade, not a free win.** Anybody who knows an email address can lock its
owner out by failing on purpose. That is why the first lockout is short, and why the
real answer — rate limiting by source address — waits for the web milestone, where
there is a source address to limit by.

A locked account gives the same message as a wrong password, and takes the same
time. Saying "locked until 14:32" would confirm the account exists, and would let an
attacker watch their own lockout tick down
([D-072](../decisions.md#d-072-a-lockout-is-not-announced)). The log records what
actually happened.

One consequence worth knowing about: **a failed login has to be committed.** The
counter lives on the user row, and the only thing worth recording happens on the
failure path. Committing only on success would leave the whole mechanism in place
and permanently inert ([D-073](../decisions.md#d-073-a-failed-login-must-be-committed)).

## Revocation

`users.token_version` invalidates every token an account holds without storing a
list of them. An access token records the version it was issued under, and is
refused when the two differ.

It is bumped by `revoke_all_tokens`, and automatically by `set_password`. A password
change that leaves old sessions working is theatre: changing a password is usually a
response to it being compromised
([D-057](../decisions.md#d-057-a-password-change-revokes-every-session)).

Changing `DESKPILOT_AUTH__JWT_SECRET` invalidates every access token at once.
Refresh tokens survive, because they are rows rather than signed claims.

## Testing

| Suite | What it covers |
|---|---|
| `tests/unit/test_passwords.py` | Hashing, verification, every policy rule, the dummy hash |
| `tests/integration/test_users.py` | Registration, linking, normalisation, authentication, revocation |
| `tests/unit/test_tokens.py` | Claims, and the attacks: forged key, tampered payload, `alg: none`, wrong audience, wrong issuer, missing claims |
| `tests/integration/test_sessions.py` | Login, rotation, reuse detection, per-device families, logout, revocation |
| `tests/unit/test_session_store.py` | The saved file: permissions, refusal to read a widened one, expiry and skew |
| `tests/integration/test_cli_sessions.py` | The commands themselves, through typer's runner |
| `tests/unit/test_lockout.py` | The backoff arithmetic, thresholds, and expiry |

One test is marked `slow`: it compares how long an unknown-address login takes
against a real one. Timing on a shared machine is noisy, so it only asserts the same
order of magnitude. Skip it with `-m "not slow"`.

```bash
uv run pytest tests/unit/test_passwords.py
uv run pytest -m integration -k users
```
