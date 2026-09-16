# Authorization guide

Deciding what somebody is allowed to do.

> Last verified against: milestone 5 (complete).

Who somebody is is a separate question, answered by the
[authentication guide](auth.md). This is about what happens next.

## The shape of a decision

```
Principal  +  Action  +  Resource   ->   Decision(allowed, reason, rule)
 who is         what           on what        yes or no, and why
 acting        they want       
```

Every rule is a pure function of those three things. No rule loads a row, reads a
setting, or calls anything. That is what makes the policy **enumerable**: the whole
rule set can be tested as a matrix rather than through a handful of scenarios, and a
policy you cannot enumerate is a policy you are guessing about
([D-075](../decisions.md#d-075-a-policy-is-a-pure-function-of-principal-action-and-resource)).

The cost is that the caller assembles the resource. Forgetting to put the region on
a `Ticket` changes the answer silently, which is the thing to be careful about.

## The principal

Built from the database at the moment of the decision, never from a token claim. A
claim baked in at login is a snapshot of a permission that may since have been taken
away ([D-060](../decisions.md#d-060-an-access-token-carries-identity-and-nothing-else)).

| Attribute | Who has it | What it decides |
|---|---|---|
| `customer_id` | Customer accounts | Which orders and tickets they own |
| `role` | Everyone | Which rules consider them at all |
| `regions` | Staff | Which tickets they may touch |
| `approval_limit_cents` | Reviewers, supervisors | How much they may approve alone |
| `is_active` | Everyone | A disabled account is refused everything |

Set the staff ones with:

```bash
uv run deskpilot auth grant lena@acmegear.example --region eu --approval-limit 50000
```

These are **attributes, not permissions**. Nothing here grants an action. The rules
decide what the values allow.

## Reading the policy

`backend/src/deskpilot/authz/policies.py` is meant to be read top to bottom, because
first match wins and **the order is part of the policy**.

| # | Rule | What it does |
|---|---|---|
| 1 | `inactive_accounts_do_nothing` | A disabled account is refused everything |
| 2 | `nobody_approves_their_own_ticket` | Separation of duties |
| 3 | `a_customer_reads_their_own_things` | Ownership |
| 4 | `anybody_signed_in_may_read_the_policies` | The published policies are public |
| 5 | `staff_read_tickets_in_their_regions` | Region scoping |
| 6 | `a_reviewer_approves_within_their_limit` | The money rule |
| 7 | `staff_edit_and_reject_within_their_regions` | Rejecting is free; editing is not |
| 8 | `admins_manage_accounts_and_read_the_record` | Administration and the audit log |

Denials come first deliberately. A rule stopping somebody approving a refund on
their own ticket is worthless if a later rule can allow it because their limit is
high enough. A test asserts that an unlimited supervisor covering every region still
cannot approve their own
([D-077](../decisions.md#d-077-denials-are-ordered-before-allows-and-separation-of-duties-is-a-denial)).

### Three rules worth understanding

**Ownership is about the pair, not the role.** Rule 3 matches
`principal.customer_id == resource.owner_customer_id` and never on `role ==
CUSTOMER`. A reviewer is also a person who buys tents; keying on role would either
cut staff off from their own orders or give them a second route to them
([D-078](../decisions.md#d-078-ownership-is-a-rule-about-the-resource-not-about-the-role)).

**Rejecting is not limited by amount; editing is.** Saying no to a refund costs
nothing. Editing one could raise the amount above the approver's limit, which would
be a way around the limit, so an edit is judged on the amount just like an approval.

**An admin is not automatically an approver.** Administration and approving money
are different jobs, and the matrix asserts it.

## Deny by default

When no rule answers, the request is denied and the recorded rule is `default`.

That distinction matters when reading an audit log: "a rule denied this" is somebody
doing something they should not, while "nothing covered this" is a gap in the policy
([D-076](../decisions.md#d-076-deny-by-default-and-the-denial-says-the-default-was-reached)).

The refusal a caller sees carries no reason at all — just "You are not allowed to do
that." The detail travels on the exception and into the log, because "you may not
approve above 500.00" is as useful to somebody mapping out the limits as it is to a
colleague ([D-079](../decisions.md#d-079-the-refusal-a-caller-sees-carries-no-reason)).

## Understanding a refusal

Deny-by-default makes "it says no" a common experience, and "it says no" is useless
on its own. So the engine can be asked directly:

```console
$ uv run deskpilot auth can lena@acmegear.example refund.approve --region eu --amount 90000
deny
  rule:      a_reviewer_approves_within_their_limit
  reason:    90000 is above their limit of 50000
  principal: reviewer, regions ['eu'], limit 50000
```

It is also the quickest way to check a rule change did what was intended, before
wiring it into anything
([D-081](../decisions.md#d-081-deskpilot-auth-can-exists-so-a-refusal-can-be-understood)).

## Adding a rule

1. Add the verb to `Action` if it is new. Name it after what is being attempted,
   not after who may do it.
2. Add a resource to `resources.py` if the rule needs attributes nothing carries yet.
3. Write the function in `policies.py` and put it in `RULES` **at the right
   position** — a denial belongs above the allows it must beat.
4. Extend the matrix in `tests/unit/test_authz.py`. At minimum: the allowed case,
   the boundary, and one case that must stay denied.
5. Check it with `deskpilot auth can` before wiring it up.

Two tests guard the set as a whole: one asserts every rule is reachable, because a
rule nothing can trigger is either dead or a typo; another sweeps every action
against every resource owned by somebody else and asserts a customer can never reach
any of it.

## The audit log

`decide` is pure and knows nothing about a database. `guard` wraps it: decide,
record, raise. The rest of the application calls `guard`, which keeps the policy
enumerable while every real call still leaves a trace
([D-087](../decisions.md#d-087-the-engine-stays-pure-a-separate-layer-remembers)).

```bash
uv run deskpilot audit tail -n 20
uv run deskpilot audit tail --denied
uv run deskpilot audit tail --as lena@acmegear.example
```

### What is recorded

**Every denial, always.** A refusal is the thing somebody will come looking for.

**Allows only when they are consequential** — approvals, edits, rejections, viewing
any ticket, viewing traces, managing accounts. A customer reading their own order
happens on every turn of every conversation, and recording it would bury the entries
somebody actually wants to find
([D-084](../decisions.md#d-084-every-denial-is-recorded-only-consequential-allows-are)).
The set is a policy decision, so a test asserts it. **A new action that moves money
has to be added to it.**

**Authentication events too**, in the same table with a `kind` column: logins,
lockouts, logouts, password changes, token reuse. The question people ask is "what
did this account do", and that answer spans both
([D-086](../decisions.md#d-086-decisions-and-authentication-events-share-one-log)).

### Two properties worth knowing

**An entry is written in its own transaction.** The record worth having most is the
one for an action that was refused — and a refused action rolls back. Sharing the
caller's transaction would roll the record back with it, leaving a log of only the
things that worked
([D-082](../decisions.md#d-082-an-audit-entry-is-written-in-its-own-transaction)).
An integration test rolls the caller's transaction back and asserts the entry
survives.

**Recording never raises.** A logging failure must not turn a working request into a
broken one. That is a real trade, stated rather than hidden: this is an
accountability record, not a ledger that has to balance
([D-083](../decisions.md#d-083-recording-never-raises)).

### The log gets the reason; the caller does not

```console
$ uv run deskpilot audit tail --denied -n 1
2026-09-15 09:58:02  deny   refund.approve   user=2
        90000 is above their limit of 50000  [a_reviewer_approves_within_their_limit]
```

The person refused saw only "You are not allowed to do that."

Entries store text rather than foreign keys, so they stay readable after the enum
changes or the row is deleted. Rewriting history is exactly what an audit log must
not do ([D-085](../decisions.md#d-085-the-audit-log-stores-text-not-foreign-keys)).

## How a tool asks

`AgentContext` carries a `Principal` rather than an email. An email is an
identifier; a principal is an identifier plus the attributes a policy weighs. Tools
that compared emails encoded one policy each, in several places, and those drift
([D-088](../decisions.md#d-088-the-agent-context-carries-a-principal-not-an-email)).

```python
order = await session.scalar(select(Order).where(Order.number == number))
if order is None:
    return NOT_FOUND
try:
    await guard(sessions, context.principal, Action.ORDER_VIEW,
                resources.Order(order.customer_id, order.customer.region))
except Forbidden:
    return NOT_FOUND
return describe(order)
```

**The row is loaded first and judged second.** Until this step the ownership check
was a `WHERE` clause, which meant a cross-customer attempt looked exactly like a
typo: same empty result, no trace of either. Now the policy is the single place
ownership is decided, and a refusal becomes evidence
([D-089](../decisions.md#d-089-a-row-is-loaded-first-and-judged-second)).

What the customer is told does not change — "not found" either way, so the tool is
still not an oracle for which order numbers are real. The difference is only in the
log:

```console
$ uv run deskpilot audit tail --denied -n 1
2026-09-15 10:21:49  deny   order.view   user=-
        no rule allows order.view on this resource  [default]
```

The cost is that another customer's row is briefly in memory. It never leaves the
function, and a test asserts a denial's recorded reason contains no order number.

**A list is authorized by scope.** `list_orders` asks one question — may this
principal read the orders of the customer they are — then filters by
`principal.customer_id`. A per-row decision on a list is either a query the policy
cannot express or a judgement per row that does not scale. It has a useful side
effect: staff, who own no customer record, are refused before any query runs rather
than shown somebody else's list
([D-090](../decisions.md#d-090-a-list-is-authorized-by-scope-not-row-by-row)).

## Administration

Three commands are administrator-only, and each refusal is recorded:

```bash
uv run deskpilot audit tail          # audit.view
uv run deskpilot auth grant ...      # user.manage
uv run deskpilot auth register --role reviewer ...   # user.manage
```

Registering yourself as a **customer** is open to anyone, as on any shop. Creating a
member of **staff** is administration.

Reading the audit log is itself audited. Somebody who can read the record of what
everyone did should leave a record of having read it
([D-093](../decisions.md#d-093-reading-the-audit-log-is-itself-audited)). It is a
separate action from `trace.view` even though the same people read both today: a
trace is debugging material, an audit entry is evidence.

### The bootstrap problem

The first administrator cannot be created by an administrator. An empty `users`
table is therefore allowed to create **one** account of any role, recorded as a
bootstrap event:

```bash
uv run deskpilot auth register root@acmegear.example --name "Root" --role admin
```

The exception closes the moment the first account exists, which a test asserts. The
alternatives are worse: a seeded default admin is a known account in a public
repository, a separate privileged command is the same exception wearing a hat, and
an environment variable that disables the check is a switch somebody leaves on
([D-094](../decisions.md#d-094-an-empty-installation-may-create-one-account-of-any-role)).

One wart worth knowing: `auth register` prompts for the password **before** checking
whether you may create the account, because that is where typer prompts. Nothing
leaks — the refusal is identical either way — but you are asked to type a password
for an account you will then be refused
([D-095](../decisions.md#d-095-a-password-is-prompted-for-before-authorization-is-checked)).

## Principals without a login

`Principal.user_id` is nullable. The impersonation hatch and the eval suite act for
a customer that nobody signed in as, and `Principal.for_customer` builds a bare
customer principal: no regions, no approval limit, no user id. The escape hatch
cannot hand out staff attributes
([D-091](../decisions.md#d-091-a-principal-may-have-no-user-id)).

An audit entry for such an action records no actor, which is true and is what the
nullable column expects.

`Principal.from_user` raises if the `customer` relationship is not loaded, naming
the `selectinload` to add. A home region is one of the attributes a policy weighs,
so it is genuinely required, and failing at the boundary beats failing deep inside
an unrelated call ([D-092](../decisions.md#d-092-building-a-principal-demands-a-loaded-relationship)).
