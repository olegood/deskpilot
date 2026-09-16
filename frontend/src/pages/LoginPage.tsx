import { useState } from "react";

import { ApiError } from "../api/client";
import { useSession } from "../auth/session";

export function LoginPage() {
  const { signIn } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch (caught) {
      // Whatever the server said, which is deliberately the same for a wrong
      // password and an unknown address.
      setError(caught instanceof ApiError ? caught.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="card">
      <h1>Acme Gear support</h1>
      <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => { setEmail(event.target.value); }}
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => { setPassword(event.target.value); }}
        />
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      {error !== null && <p role="alert">{error}</p>}
    </main>
  );
}
