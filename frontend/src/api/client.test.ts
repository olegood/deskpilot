import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, refreshSession, request, setAccessToken } from "./client";

interface Call {
  path: string;
  headers: Headers;
}

let calls: Call[] = [];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Answers /refresh however the test says, and everything else per a queue. */
function server(options: { refresh: () => Response; replies: Response[] }) {
  const replies = [...options.replies];
  return vi.fn((path: string, init: RequestInit = {}) => {
    calls.push({ path, headers: new Headers(init.headers) });
    if (path === "/api/auth/refresh") return Promise.resolve(options.refresh());
    return Promise.resolve(replies.shift() ?? json(200, {}));
  });
}

beforeEach(() => {
  calls = [];
  setAccessToken(null);
  document.cookie = "deskpilot_csrf=csrf-value";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request", () => {
  it("sends the access token as a bearer header", async () => {
    vi.stubGlobal("fetch", server({ refresh: () => json(401, {}), replies: [json(200, { ok: 1 })] }));
    setAccessToken("token-abc");

    await request("/api/tickets");

    expect(calls[0]?.headers.get("Authorization")).toBe("Bearer token-abc");
  });

  it("sends no authorization header when signed out", async () => {
    vi.stubGlobal("fetch", server({ refresh: () => json(401, {}), replies: [json(200, {})] }));

    await request("/api/tickets");

    expect(calls[0]?.headers.get("Authorization")).toBeNull();
  });

  it("turns an error response into an ApiError carrying the detail", async () => {
    vi.stubGlobal(
      "fetch",
      server({ refresh: () => json(401, {}), replies: [json(403, { detail: "No." })] }),
    );

    await expect(request("/api/tickets")).rejects.toThrow(new ApiError(403, "No."));
  });

  it("does not invent a message when the body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      server({ refresh: () => json(401, {}), replies: [new Response("<html>", { status: 500 })] }),
    );

    await expect(request("/api/tickets")).rejects.toThrow("Something went wrong.");
  });
});

describe("refreshing after a 401", () => {
  it("refreshes once and retries the request", async () => {
    const fetchMock = server({
      refresh: () => json(200, { access_token: "renewed" }),
      replies: [json(401, {}), json(200, { ok: 1 })],
    });
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("expired");

    await request("/api/tickets");

    expect(calls.map((call) => call.path)).toEqual([
      "/api/tickets",
      "/api/auth/refresh",
      "/api/tickets",
    ]);
    expect(calls[2]?.headers.get("Authorization")).toBe("Bearer renewed");
  });

  it("does not retry twice", async () => {
    vi.stubGlobal(
      "fetch",
      server({
        refresh: () => json(200, { access_token: "renewed" }),
        replies: [json(401, {}), json(401, {})],
      }),
    );
    setAccessToken("expired");

    await expect(request("/api/tickets")).rejects.toThrow(ApiError);
    expect(calls.filter((call) => call.path === "/api/auth/refresh")).toHaveLength(1);
  });

  it("refreshes once for several requests that fail together", async () => {
    // The case this exists for. Five refreshes would mean four of them presenting
    // a rotated token, which trips the server's reuse detection and signs the
    // user out of everything.
    vi.stubGlobal(
      "fetch",
      server({
        refresh: () => json(200, { access_token: "renewed" }),
        replies: [json(401, {}), json(401, {}), json(401, {}), json(200, {}), json(200, {}), json(200, {})],
      }),
    );
    setAccessToken("expired");

    await Promise.all([request("/api/a"), request("/api/b"), request("/api/c")]);

    expect(calls.filter((call) => call.path === "/api/auth/refresh")).toHaveLength(1);
  });

  it("sends the CSRF header read from the cookie", async () => {
    vi.stubGlobal(
      "fetch",
      server({ refresh: () => json(200, { access_token: "renewed" }), replies: [json(401, {}), json(200, {})] }),
    );
    setAccessToken("expired");

    await request("/api/tickets");

    const refresh = calls.find((call) => call.path === "/api/auth/refresh");
    expect(refresh?.headers.get("X-CSRF-Token")).toBe("csrf-value");
  });

  it("gives up when the refresh itself fails", async () => {
    vi.stubGlobal("fetch", server({ refresh: () => json(401, {}), replies: [json(401, {})] }));
    setAccessToken("expired");

    await expect(request("/api/tickets")).rejects.toThrow(ApiError);
  });

  it("lets a later refresh happen after one fails", async () => {
    // The single-flight promise is cleared in a finally, so a failure does not
    // wedge every refresh after it.
    vi.stubGlobal("fetch", server({ refresh: () => json(401, {}), replies: [] }));
    expect(await refreshSession()).toBeNull();

    vi.stubGlobal(
      "fetch",
      server({ refresh: () => json(200, { access_token: "later" }), replies: [] }),
    );
    expect(await refreshSession()).toBe("later");
  });
});
