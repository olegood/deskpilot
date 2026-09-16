/** What the agent is doing, while it is doing it. */
export function Progress({ steps }: { steps: string[] }) {
  return (
    <ul className="progress" aria-live="polite">
      {steps.map((step, index) => (
        <li key={`${step}-${String(index)}`}>{step}</li>
      ))}
      <li className="working">Thinking…</li>
    </ul>
  );
}
