# Authentication guide

Accounts, passwords, and how a login is checked.

> Last verified against: milestone 3, step 3.1.

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

`auth check` verifies a password and issues nothing. It exists as a smoke test for
the hashing path; real sessions arrive in step 3.3.

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

## Revocation

`users.token_version` is an integer bumped to invalidate every token an account
holds, without storing a list of them. Step 3.2 checks it on every request.

It is bumped by `revoke_all_tokens`, and automatically by `set_password`. A password
change that leaves old sessions working is theatre: changing a password is usually a
response to it being compromised
([D-057](../decisions.md#d-057-a-password-change-revokes-every-session)).

## Testing

| Suite | What it covers |
|---|---|
| `tests/unit/test_passwords.py` | Hashing, verification, every policy rule, the dummy hash |
| `tests/integration/test_users.py` | Registration, linking, normalisation, authentication, revocation |

One test is marked `slow`: it compares how long an unknown-address login takes
against a real one. Timing on a shared machine is noisy, so it only asserts the same
order of magnitude. Skip it with `-m "not slow"`.

```bash
uv run pytest tests/unit/test_passwords.py
uv run pytest -m integration -k users
```
