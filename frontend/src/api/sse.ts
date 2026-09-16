/**
 * Reading server-sent events from a fetch response.
 *
 * The browser's own EventSource cannot set an Authorization header and cannot
 * POST, and this API needs both. Reading the body stream by hand is about thirty
 * lines and avoids a dependency.
 *
 * The parsing detail that matters: a chunk from the network has no relationship
 * to an event boundary. One event can arrive in three chunks, and three events
 * can arrive in one. So the reader keeps a buffer and only emits on a blank line.
 */

export interface ServerEvent {
  name: string;
  data: unknown;
}

export function parseBlock(block: string): ServerEvent | null {
  let name: string | null = null;
  let data = "";
  for (const line of block.split("\n")) {
    // A line starting with a colon is a comment, such as a keepalive.
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) name = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) data += line.slice("data:".length).trim();
  }
  if (name === null) return null;
  try {
    return { name, data: data === "" ? {} : JSON.parse(data) };
  } catch {
    return { name, data: {} };
  }
}

export async function* readEvents(response: Response): AsyncGenerator<ServerEvent> {
  if (response.body === null) return;
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const event = parseBlock(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (event !== null) yield event;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    // Releasing matters when the caller stops early: without it the connection
    // stays open until the server gives up.
    reader.releaseLock();
  }
}
