import { describe, expect, it } from "vitest";

import { parseBlock, readEvents } from "./sse";

/** A Response whose body delivers exactly these chunks, in this order. */
function responseOf(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body);
}

async function collect(chunks: string[]) {
  const events = [];
  for await (const event of readEvents(responseOf(chunks))) events.push(event);
  return events;
}

describe("parseBlock", () => {
  it("reads an event name and a JSON payload", () => {
    expect(parseBlock('event: token\ndata: {"text":"hi"}')).toEqual({
      name: "token",
      data: { text: "hi" },
    });
  });

  it("treats an event with no data as an empty object", () => {
    expect(parseBlock("event: done")).toEqual({ name: "done", data: {} });
  });

  it("ignores comment lines, which is what a keepalive is", () => {
    expect(parseBlock(": keepalive\nevent: done\ndata: {}")).toEqual({ name: "done", data: {} });
  });

  it("ignores a block with no event name", () => {
    expect(parseBlock(": just a comment")).toBeNull();
  });

  it("survives a payload that is not JSON", () => {
    expect(parseBlock("event: token\ndata: not json")).toEqual({ name: "token", data: {} });
  });
});

describe("readEvents", () => {
  it("reads several events from one chunk", async () => {
    const events = await collect(['event: a\ndata: {}\n\nevent: b\ndata: {"x":1}\n\n']);

    expect(events.map((event) => event.name)).toEqual(["a", "b"]);
  });

  it("reassembles one event split across chunks", async () => {
    // The case that breaks a naive reader: a chunk boundary has nothing to do
    // with an event boundary.
    const events = await collect(["event: tok", 'en\ndata: {"text":', '"hello"}\n\n']);

    expect(events).toEqual([{ name: "token", data: { text: "hello" } }]);
  });

  it("keeps a partial event buffered until it is complete", async () => {
    const events = await collect(['event: token\ndata: {"text":"hi"}\n\nevent: half']);

    expect(events).toHaveLength(1);
  });

  it("does not split an event on a newline inside the data", async () => {
    // Why every payload is JSON: a raw newline would end the data field here.
    const events = await collect(['event: token\ndata: {"text":"one\\ntwo"}\n\n']);

    expect(events[0]?.data).toEqual({ text: "one\ntwo" });
  });

  it("yields nothing for a response with no body", async () => {
    const events = [];
    for await (const event of readEvents(new Response(null))) events.push(event);

    expect(events).toEqual([]);
  });
});
