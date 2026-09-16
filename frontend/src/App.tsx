import { Navigate, Route, Routes } from "react-router";

import { useSession } from "./auth/session";
import { LoginPage } from "./pages/LoginPage";
import { TicketPage } from "./pages/TicketPage";
import { TicketsPage } from "./pages/TicketsPage";

export function App() {
  const { identity, loading, signOut } = useSession();

  // Waiting for the silent refresh. Without this the login page flashes on every
  // reload for somebody who is already signed in.
  if (loading) return <main className="card">Loading…</main>;
  if (identity === null) return <LoginPage />;

  return (
    <>
      <header>
        <strong>Acme Gear support</strong>
        <span>
          {identity.full_name}
          {identity.customer_email === null && <em> (staff)</em>}
        </span>
        <button type="button" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>
      {identity.customer_email === null ? (
        // Staff can sign in and have no tickets of their own. Saying so beats an
        // empty list, which looks like an answer. The reviewer console is a later
        // milestone.
        <main className="card">
          <h2>Nothing here yet</h2>
          <p>
            This is a {identity.role} account, so it has no tickets of its own. The
            reviewer console arrives with human-in-the-loop approvals.
          </p>
        </main>
      ) : (
        <Routes>
          <Route path="/tickets" element={<TicketsPage />} />
          <Route path="/tickets/:reference" element={<TicketPage />} />
          <Route path="*" element={<Navigate to="/tickets" replace />} />
        </Routes>
      )}
    </>
  );
}
