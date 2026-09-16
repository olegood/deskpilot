/**
 * Who is signed in, for the rest of the app.
 *
 * On first load the access token is gone - it only ever lived in memory - so the
 * app tries a silent refresh before deciding anybody is signed out. Skipping that
 * would sign the user out on every page reload.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { onSessionEnded, refreshSession, request, setAccessToken } from "../api/client";
import type { Identity, TokenResponse } from "../api/types";

interface Session {
  identity: Identity | null;
  // True until the silent refresh has finished, so the app can wait instead of
  // flashing the login page at somebody who is already signed in.
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(true);

  const loadIdentity = useCallback(async () => {
    try {
      setIdentity(await request<Identity>("/api/auth/me"));
    } catch {
      setIdentity(null);
    }
  }, []);

  useEffect(() => {
    // An AbortController rather than a boolean flag. A local assigned only in the
    // cleanup closure is narrowed to `false` inside this async function, so every
    // check of it reads as dead code. A signal is a property, not a narrowed local.
    const started = new AbortController();
    void (async () => {
      const token = await refreshSession();
      // Checked before each step rather than once at the top: the component can
      // unmount during either await, and an early return would make the second
      // check look redundant to the compiler.
      if (token !== null && !started.signal.aborted) await loadIdentity();
      if (!started.signal.aborted) setLoading(false);
    })();
    onSessionEnded(() => {
      setAccessToken(null);
      setIdentity(null);
    });
    return () => {
      started.abort();
    };
  }, [loadIdentity]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const body = await request<TokenResponse>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setAccessToken(body.access_token);
      await loadIdentity();
    },
    [loadIdentity],
  );

  const signOut = useCallback(async () => {
    try {
      await request<null>("/api/auth/logout", { method: "POST" });
    } finally {
      // Cleared whatever the server said. A sign-out that leaves the app looking
      // signed in because the request failed is the worst of both.
      setAccessToken(null);
      setIdentity(null);
    }
  }, []);

  const value = useMemo<Session>(
    () => ({ identity, loading, signIn, signOut }),
    [identity, loading, signIn, signOut],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (session === null) throw new Error("useSession must be used inside a SessionProvider");
  return session;
}
