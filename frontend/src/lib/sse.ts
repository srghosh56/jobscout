// Incremental parser for Server-Sent Events (text/event-stream).
// Network chunks can split an event anywhere (even inside a UTF-8 character, which the
// TextDecoder handles with {stream: true}), so we buffer text until a blank line ends an
// event. Spec: https://html.spec.whatwg.org/multipage/server-sent-events.html
export interface SSEEvent {
  event: string;
  data: string;
  id?: string;
}

export class SSEParser {
  private buffer = "";
  private pendingCR = false;

  /** Feed a decoded text chunk; returns the events completed by it. */
  feed(chunk: string): SSEEvent[] {
    // A "\r\n" line ending can be split across chunks: hold a trailing "\r" back.
    let text = (this.pendingCR ? "\r" : "") + chunk;
    this.pendingCR = text.endsWith("\r");
    if (this.pendingCR) text = text.slice(0, -1);
    this.buffer += text.replace(/\r\n?/g, "\n");
    const events: SSEEvent[] = [];
    let sep: number;
    while ((sep = this.buffer.indexOf("\n\n")) !== -1) {
      const block = this.buffer.slice(0, sep);
      this.buffer = this.buffer.slice(sep + 2);
      const ev = parseBlock(block);
      if (ev) events.push(ev);
    }
    return events;
  }
}

function parseBlock(block: string): SSEEvent | null {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // comment / keep-alive ping
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }
  if (data.length === 0) return null;
  return { event, data: data.join("\n"), id };
}
