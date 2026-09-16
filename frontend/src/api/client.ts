/**
 * Talking to the API.
 *
 * Two things live here that are easy to get wrong elsewhere.
 *
 * The access token is held in a module variable, not in localStorage. An XSS bug
 * can read localStorage; it cannot read a closure it does not have a reference
 * to. That is a smaller win than it sounds - a script running on the page can do
 * plenty anyway - but it means a token does not survive being written down
 * somewhere a later bug can find it.
 *
 * And a 401 triggers exactly one refresh, however many requests hit it at once.
 * Five parallel requests failing together must not become five refreshes: four of
 * them would present a rotated token and trip the server's reuse detection, which
 * revokes the whole family and signs the user out.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let accessToken: string | null = null;
// The refresh in flight, if any. Everything that needs one awaits this.
let refreshing: Promise<string | null> | null = null;
let onSignedOut: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function onSessionEnded(handler: () => void): void {
  onSignedOut = handler;
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match?.[1] !== undefined ? decodeURIComponent(match[1]) : null;
}

/** Trade the refresh cookie for a new access token. At most one at a time. */
export function refreshSession(): Promise<string | null> {
  refreshing ??= (async () => {
    try {
      const csrf = readCookie("deskpilot_csrf");
      const response = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: csrf === null ? {} : { "X-CSRF-Token": csrf },
      });
      if (!response.ok) return null;
      const body = (await response.json()) as { access_token: string };
      accessToken = body.access_token;
      return accessToken;
    } catch {
      return null;
    } finally {
      // Cleared in a finally so a failed refresh does not wedge every later one.
      refreshing = null;
    }
  })();
  return refreshing;
}

async function send(path: string, init: RequestInit, retry: boolean): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken !== null) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetch(path, { ...init, headers });
  if (response.status !== 401 || !retry) return response;

  const renewed = await refreshSession();
  if (renewed === null) {
    accessToken = null;
    onSignedOut?.();
    return response;
  }
  return send(path, init, false);
}

async function detail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : "Something went wrong.";
  } catch {
    return "Something went wrong.";
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await send(path, init, true);
  if (!response.ok) throw new ApiError(response.status, await detail(response));
  // 204 has no body to parse. Callers that expect nothing ask for `null`.
  if (response.status === 204) return null as T;
  return (await response.json()) as T;
}

/** A streaming POST. The caller reads events off the response body. */
export async function stream(path: string, body: unknown): Promise<Response> {
  const response = await send(path, { method: "POST", body: JSON.stringify(body) }, true);
  if (!response.ok) throw new ApiError(response.status, await detail(response));
  return response;
}
