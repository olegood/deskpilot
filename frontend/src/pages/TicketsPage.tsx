import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import { listTickets, openTicketStreaming } from "../api/tickets";
import type { TicketSummary } from "../api/types";
import { Progress } from "../components/Progress";

export function TicketsPage() {
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [progress, setProgress] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const reload = useCallback(async () => {
    setTickets(await listTickets());
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function submit() {
    setBusy(true);
    setProgress([]);
    try {
      const reference = await openTicketStreaming(subject, message, {
        onCategory: (value) => { setProgress((seen) => [...seen, `Classified as ${value}`]); },
        onTool: (name) => { setProgress((seen) => [...seen, `Looking up ${name}`]); },
      });
      // Straight to the ticket: the conversation is already saved, so the detail
      // page reads it back rather than being handed it.
      if (reference !== null) void navigate(`/tickets/${reference}`);
    } finally {
      setBusy(false);
      void reload();
    }
  }

  return (
    <main>
      <section className="card">
        <h2>Ask us something</h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label htmlFor="subject">Subject</label>
          <input
            id="subject"
            required
            maxLength={200}
            value={subject}
            onChange={(event) => { setSubject(event.target.value); }}
          />
          <label htmlFor="message">Message</label>
          <textarea
            id="message"
            required
            rows={4}
            maxLength={5000}
            value={message}
            onChange={(event) => { setMessage(event.target.value); }}
          />
          <button type="submit" disabled={busy}>
            {busy ? "Working…" : "Send"}
          </button>
        </form>
        {busy && <Progress steps={progress} />}
      </section>

      <section className="card">
        <h2>Your tickets</h2>
        {tickets.length === 0 ? (
          <p>Nothing yet.</p>
        ) : (
          <ul className="tickets">
            {tickets.map((ticket) => (
              <li key={ticket.reference}>
                <Link to={`/tickets/${ticket.reference}`}>{ticket.reference}</Link>
                <span className="subject">{ticket.subject}</span>
                <span className={`status status-${ticket.status}`}>{ticket.status}</span>
                {ticket.category !== null && <span className="tag">{ticket.category}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
