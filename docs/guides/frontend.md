# Frontend guide

The React app customers use.

> Last verified against: milestone 5, step 5.4.

## Stack

Vite, React 19, TypeScript in strict mode with `noUncheckedIndexedAccess` and
`exactOptionalPropertyTypes`, React Router, ESLint with type-aware rules, and
Vitest. Managed with pnpm; uv covers only the Python side.

## Running it

```bash
cd frontend
pnpm install
pnpm types      # generate TypeScript from the backend's OpenAPI document
pnpm dev
```

The backend must be running (`uv run deskpilot serve`). Vite proxies `/api` to it,
which matters for more than convenience: the browser then sees **one origin**, so
the refresh cookie is first-party and `SameSite=strict` behaves exactly as it will
in production. Pointing the app at `http://127.0.0.1:8000` directly would make every
cookie cross-site and hide problems until deployment.

| Command | What |
|---|---|
| `pnpm dev` | Dev server on 5173 |
| `pnpm types` | Regenerate `src/api/schema.d.ts` from `src/api/openapi.json` |
| `pnpm lint` | ESLint, type-aware |
| `pnpm build` | Type-check and bundle |
| `pnpm test` | Vitest |
| `pnpm check` | All of the above |

`./scripts/check.sh` from the repo root runs the backend and frontend checks
together, and skips the frontend when `node_modules` is missing.

## Types come from the backend

```
backend  ──  deskpilot openapi --out frontend/src/api/openapi.json
                 ↓  pnpm types
frontend ──  src/api/schema.d.ts  ──  src/api/types.ts  ──  the app
```

`schema.d.ts` is generated and git-ignored. A renamed field on the server becomes a
TypeScript error here rather than an `undefined` in a browser three weeks later
([D-110](../decisions.md#d-110-the-frontends-types-are-generated-from-the-backends-schema)).

`check.sh` regenerates before type-checking, because a stale generated file hides
exactly the drift it exists to catch.

## Where the access token lives

In a module variable in `api/client.ts`. Not in `localStorage`.

An XSS bug can read `localStorage`; it cannot read a variable it has no reference
to. That is a smaller win than it is usually presented as — a script on the page can
call the API directly — but the token is not written down somewhere a later bug can
find it ([D-111](../decisions.md#d-111-the-access-token-lives-in-a-module-variable-not-in-localstorage)).

The cost: a page reload loses it. So the app calls `/refresh` on load and shows a
loading state until that finishes. Skipping it would sign the user out every reload.

### One refresh, however many 401s

This is the bug the refresh-token design creates for the client, and it is worth
understanding.

Five requests fail with 401 at the same moment. Each wants a refresh. Four of those
five present a refresh token the first one has already rotated — which is exactly
what the server treats as theft, so it revokes the family and signs the user out of
everything ([D-063](../decisions.md#d-063-refresh-tokens-rotate-and-reuse-revokes-the-family)).

So `refreshSession` keeps the in-flight promise in a module variable and everyone
awaits the same one. A test fires three parallel requests and asserts exactly one
refresh happened ([D-112](../decisions.md#d-112-a-401-triggers-exactly-one-refresh)).

Clearing that promise in a `finally` matters as much: a failed refresh that left it
in place would wedge every later one. That has its own test too.

## Reading the stream

`api/sse.ts` reads the fetch body stream by hand. `EventSource` cannot set an
`Authorization` header and cannot POST, and this API needs both, so a library would
be wrapping the same loop ([D-113](../decisions.md#d-113-the-sse-reader-is-hand-written)).

The part worth getting right: **a network chunk has no relationship to an event
boundary.** One event can arrive in three chunks and three events in one. The reader
buffers and emits only on a blank line. Tests cover both directions.

An unknown event name is ignored rather than treated as an error, so the server can
add one without breaking a deployed frontend.

## Rendering

Every message is a text node. Nothing is parsed as markdown or HTML, and
`dangerouslySetInnerHTML` appears nowhere.

It is model output derived from a ticket somebody else wrote. Rendering it as markup
is how an image tag pointing at another host turns a conversation into an
exfiltration channel. Making it pretty is worth doing after the security milestone
has dealt with sanitising untrusted text
([D-114](../decisions.md#d-114-model-output-is-rendered-as-text)).

## Layout

```
frontend/
  src/
    api/       client.ts, sse.ts, tickets.ts, types.ts, openapi.json
    auth/      session.tsx — who is signed in
    pages/     LoginPage, TicketsPage, TicketPage
    components/Progress.tsx
    App.tsx, main.tsx, styles.css
```

Staff can sign in and have no tickets of their own, so `App` says so plainly rather
than showing an empty list that looks like an answer. The reviewer console arrives
with human-in-the-loop approvals.

## What is tested, and what is not

Vitest covers `api/sse.ts` and `api/client.ts` — the two modules with logic that can
be wrong in a way nobody notices. The pages are not tested: they are wiring, and
component tests for wiring cost more to maintain than they catch. The end-to-end
suite in milestone 14 is what covers a real click-through.
