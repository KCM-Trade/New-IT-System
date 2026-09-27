/**
 * Incremental Server-Sent Events parser for streams consumed through
 * `fetch()` + `ReadableStream` rather than `EventSource`.
 *
 * Why not EventSource: the AI turn endpoint is a POST (the question travels in
 * the body) and EventSource can only GET. Everything else about the wire format
 * is standard SSE, so this is a small state machine over the spec's rules:
 *
 *   - frames are separated by a blank line;
 *   - `event:` names the frame (default "message");
 *   - `data:` lines accumulate, joined with "\n";
 *   - lines starting with ":" are comments (the backend sends `: ping`);
 *   - `id:` / `retry:` and unknown fields are ignored.
 *
 * Network chunks do not respect frame or even line boundaries, so the parser
 * keeps whatever trails the last newline and re-reads it with the next chunk.
 * `flush()` dispatches a final frame the stream ended without terminating.
 */

export interface SseFrame {
  event: string;
  data: string;
}

const DEFAULT_EVENT = "message";

export class SseParser {
  private buffer = "";
  private event = DEFAULT_EVENT;
  private data: string[] = [];

  /** Feed one decoded chunk; returns every frame completed by it. */
  push(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];

    // Consume complete lines only. The remainder after the last "\n" may be a
    // partial line that the next chunk finishes.
    let newline = this.buffer.indexOf("\n");
    while (newline !== -1) {
      let line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      if (line.endsWith("\r")) line = line.slice(0, -1);

      const frame = this.consumeLine(line);
      if (frame) frames.push(frame);

      newline = this.buffer.indexOf("\n");
    }
    return frames;
  }

  /** Dispatch anything still pending once the stream has ended. */
  flush(): SseFrame[] {
    const frames: SseFrame[] = [];
    if (this.buffer.length > 0) {
      const frame = this.consumeLine(this.buffer.replace(/\r$/, ""));
      this.buffer = "";
      if (frame) frames.push(frame);
    }
    const last = this.dispatch();
    if (last) frames.push(last);
    return frames;
  }

  private consumeLine(line: string): SseFrame | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    // The spec strips exactly one leading space after the colon.
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") this.event = value || DEFAULT_EVENT;
    else if (field === "data") this.data.push(value);
    // id / retry / anything else: ignored on purpose.
    return null;
  }

  private dispatch(): SseFrame | null {
    if (this.data.length === 0) {
      // A frame with only an event name and no data is a no-op per spec.
      this.event = DEFAULT_EVENT;
      return null;
    }
    const frame: SseFrame = { event: this.event, data: this.data.join("\n") };
    this.event = DEFAULT_EVENT;
    this.data = [];
    return frame;
  }
}

/** Parse a frame's JSON payload, returning `null` instead of throwing. */
export function parseFrameJson<T = unknown>(frame: SseFrame): T | null {
  try {
    return JSON.parse(frame.data) as T;
  } catch {
    return null;
  }
}
