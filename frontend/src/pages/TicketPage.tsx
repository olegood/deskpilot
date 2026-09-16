import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { readTicket, replyStreaming } from "../api/tickets";
import type { TicketDetail } from "../api/types";
import { Progress } from "../components/Progress";

export function TicketPage() {
  const { reference = "" } = useParams();
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [message, setMessage] = useState("");
  const [streamed, setStreamed] = useState("");
  const [progress, setProgress] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setTicket(await readTicket(reference));
  }, [reference]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function submit() {
    setBusy(true);
    setStreamed("");
    setProgress([]);
    setError(null);
    const asked = message;
    setMessage("");
    try {
      await replyStreaming(reference, asked, {
        onTool: (name) => { setProgress((seen) => [...seen, `Looking up ${name}`]); },
        // Appended as it arrives, which is what makes the answer appear a word at
        // a time instead of after twenty seconds of nothing.
        onToken: (text) => { setStreamed((soFar) => soFar + text); },
        onError: setError,
      });
      await reload();
    } finally {
      setBusy(false);
      setStreamed("");
    }
  }

  if (ticket === null) return <main className="card">Loading…</main>;

  return (
    <main>
      <p>
        <Link to="/tickets">← All tickets</Link>
      </p>
      <section className="card">
        <h2>
          {ticket.reference} <span className="subject">{ticket.subject}</span>
        </h2>
        <p>
          <span className={`status status-${ticket.status}`}>{ticket.status}</span>
          {ticket.category !== null && <span className="tag">{ticket.category}</span>}
        </p>

        <ol className="conversation">
          {ticket.messages.map((line, index) => (
            <li key={`${line.speaker}-${String(index)}`} className={line.speaker}>
              {/* Deliberately rendered as text. Nothing here is parsed as markup:
                  it is model output, and the security milestone is where
                  rendering untrusted text is dealt with properly. */}
              <strong>{line.speaker === "customer" ? "You" : "Acme Gear"}</strong>
              <p>{line.text}</p>
            </li>
          ))}
          {streamed !== "" && (
            <li className="agent pending">
              <strong>Acme Gear</strong>
              <p>{streamed}</p>
            </li>
          )}
        </ol>

        {busy && progress.length > 0 && <Progress steps={progress} />}
        {error !== null && <p role="alert">{error}</p>}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label htmlFor="reply">Reply</label>
          <textarea
            id="reply"
            required
            rows={3}
            maxLength={5000}
            value={message}
            onChange={(event) => { setMessage(event.target.value); }}
          />
          <button type="submit" disabled={busy}>
            {busy ? "Working…" : "Send"}
          </button>
        </form>
      </section>
    </main>
  );
}
