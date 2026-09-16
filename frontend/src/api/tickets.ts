import { request, stream } from "./client";
import { readEvents } from "./sse";
import type { TicketDetail, TicketSummary } from "./types";

export function listTickets(): Promise<TicketSummary[]> {
  return request<TicketSummary[]>("/api/tickets");
}

export function readTicket(reference: string): Promise<TicketDetail> {
  return request<TicketDetail>(`/api/tickets/${encodeURIComponent(reference)}`);
}

/** What the caller is told while a turn runs. */
export interface TurnHandlers {
  onTicket?: (reference: string) => void;
  onCategory?: (category: string) => void;
  onTool?: (name: string) => void;
  onToken?: (text: string) => void;
  onAnswer?: (answer: string) => void;
  onDone?: () => void;
  onError?: (detail: string) => void;
}

async function consume(response: Response, handlers: TurnHandlers): Promise<void> {
  for await (const event of readEvents(response)) {
    const data = event.data as Record<string, unknown>;
    switch (event.name) {
      case "ticket":
        if (typeof data.reference === "string") handlers.onTicket?.(data.reference);
        break;
      case "category":
        if (typeof data.category === "string") handlers.onCategory?.(data.category);
        break;
      case "tool":
        if (typeof data.name === "string") handlers.onTool?.(data.name);
        break;
      case "token":
        if (typeof data.text === "string") handlers.onToken?.(data.text);
        break;
      case "answer":
        if (typeof data.answer === "string") handlers.onAnswer?.(data.answer);
        break;
      case "done":
        handlers.onDone?.();
        break;
      case "error":
        handlers.onError?.(typeof data.detail === "string" ? data.detail : "Something went wrong.");
        break;
      default:
        // An event this client does not know about. Ignored on purpose, so the
        // server can add one without breaking a deployed frontend.
        break;
    }
  }
}

/**
 * Returns the new ticket's reference.
 *
 * It arrives as the first event rather than in a response body, so it is returned
 * rather than left for the caller to capture in a callback: a variable assigned
 * inside a closure is something TypeScript cannot narrow, and the caller ends up
 * comparing a `never` against null.
 */
export async function openTicketStreaming(
  subject: string,
  message: string,
  handlers: TurnHandlers,
): Promise<string | null> {
  let reference: string | null = null;
  const response = await stream("/api/tickets/stream", { subject, message });
  await consume(response, {
    ...handlers,
    onTicket: (value) => {
      reference = value;
      handlers.onTicket?.(value);
    },
  });
  return reference;
}

export async function replyStreaming(
  reference: string,
  message: string,
  handlers: TurnHandlers,
): Promise<void> {
  const path = `/api/tickets/${encodeURIComponent(reference)}/replies/stream`;
  await consume(await stream(path, { message }), handlers);
}
