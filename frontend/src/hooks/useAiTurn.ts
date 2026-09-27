/**
 * One AI analyst turn over `POST /api/v1/ai/turn` (SSE).
 *
 * Contract: docs/ai-agent/02-contracts.md §4.1 / §4.3. The stream is consumed
 * with `apiFetch` + `ReadableStream`, not `EventSource`: the question travels in
 * a POST body and EventSource can only GET. `apiFetch` still injects the API
 * key header and the session cookie, and returns the `Response` before reading
 * the body, so streaming works once its timeout and retry are switched off
 * (a retry would resend the question; a timeout would cut a long analysis).
 *
 * Slice 1 keeps no history: every turn is independent and the `session_id`
 * only groups audit rows. The hook therefore holds the visible transcript in
 * component state and nothing else — nothing is persisted, because what a risk
 * analyst is investigating is context, not a preference.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { createDeltaBuffer } from "@/lib/delta-buffer";
import { apiFetch } from "@/lib/fetch";
import { SseParser, parseFrameJson } from "@/lib/sse-parser";

export type AiModel = "gpt-5.6-terra" | "gpt-5.6-sol";

export const AI_MODELS: readonly AiModel[] = ["gpt-5.6-terra", "gpt-5.6-sol"];
export const DEFAULT_AI_MODEL: AiModel = "gpt-5.6-terra";

export interface ToolSource {
  service: string;
  function: string;
  as_of: string | null;
  certified: boolean;
}

export interface ToolCall {
  /** Stable key for React lists; the same tool can run twice in one turn. */
  key: string;
  name: string;
  /** `null` while the tool is still running. */
  ok: boolean | null;
  certified: boolean;
  source: ToolSource | null;
  errorCode?: string;
}

export interface TurnError {
  code: string;
  message: string;
  traceId?: string;
}

export interface TurnUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cost_usd: number | null;
}

export interface AiMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  model?: AiModel;
  tools: ToolCall[];
  error?: TurnError;
  usage?: TurnUsage;
  /** Set when the user pressed stop before `done` arrived. */
  stopped?: boolean;
}

export interface UseAiTurnResult {
  messages: AiMessage[];
  streaming: boolean;
  /** The last turn's error, also attached to that assistant message. */
  error: TurnError | null;
  /** The last completed turn's usage. */
  usage: TurnUsage | null;
  sessionId: string | null;
  send: (message: string, model: AiModel) => Promise<void>;
  stop: () => void;
  clear: () => void;
}

interface UseAiTurnOptions {
  /** Called after every terminal event (done / error) — e.g. refresh the quota. */
  onTurnEnd?: () => void;
}

// Wire payloads, per 02 §4.3.
interface InitEvent { session_id: string; model: string }
interface TextEvent { delta: string }
interface ToolUseEvent { name: string; input?: unknown }
interface ToolDoneEvent {
  name: string;
  ok: boolean;
  source: ToolSource | null;
  certified: boolean;
  error_code?: string;
}
interface ErrorEvent { code: string; message: string; trace_id?: string }

function newId(): string {
  // crypto.randomUUID is available in every browser this app supports; the
  // fallback only matters for non-secure contexts (plain http on a LAN IP).
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useAiTurn(options: UseAiTurnOptions = {}): UseAiTurnResult {
  const [messages, setMessages] = useState<AiMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<TurnError | null>(null);
  const [usage, setUsage] = useState<TurnUsage | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const controllerRef = useRef<AbortController | null>(null);
  const onTurnEndRef = useRef(options.onTurnEnd);
  onTurnEndRef.current = options.onTurnEnd;

  // Abort an in-flight stream when the page unmounts (React 18 StrictMode
  // mounts twice; the second mount starts nothing, so this is safe).
  useEffect(() => () => controllerRef.current?.abort(), []);

  const patchAssistant = useCallback(
    (id: string, patch: (m: AiMessage) => AiMessage) => {
      setMessages((prev) => prev.map((m) => (m.id === id ? patch(m) : m)));
    },
    [],
  );

  const stop = useCallback(() => {
    controllerRef.current?.abort();
  }, []);

  const clear = useCallback(() => {
    if (streaming) return;
    setMessages([]);
    setError(null);
    setUsage(null);
  }, [streaming]);

  const send = useCallback(
    async (message: string, model: AiModel) => {
      const question = message.trim();
      if (!question || controllerRef.current) return;

      const controller = new AbortController();
      controllerRef.current = controller;
      setStreaming(true);
      setError(null);

      const assistantId = newId();
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "user", text: question, tools: [] },
        { id: assistantId, role: "assistant", text: "", model, tools: [] },
      ]);

      // Declared outside `try` so the catch block can flush what was buffered
      // before an abort or a network error.
      const textBufferRef: { current: { flush: () => void } | null } = { current: null };

      const fail = (err: TurnError) => {
        setError(err);
        patchAssistant(assistantId, (m) => ({ ...m, error: err }));
      };

      try {
        const res = await apiFetch(
          "/api/v1/ai/turn",
          {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
            body: JSON.stringify({ session_id: sessionId, message: question, model }),
            signal: controller.signal,
          },
          // No timeout (a deep analysis can run for minutes) and no retry (a
          // retry would resend the question and be billed twice).
          { timeoutMs: 0, retries: 0 },
        );

        if (!res.ok || !res.body) {
          // 403 = module not granted (never 401 — see lib/fetch.ts). Anything
          // else is surfaced with its status so the trace can be found.
          let detail = "";
          try {
            const body = (await res.json()) as { detail?: unknown };
            if (typeof body.detail === "string") detail = body.detail;
          } catch {
            /* non-JSON body */
          }
          fail({
            code: res.status === 403 ? "forbidden" : `http_${res.status}`,
            message: detail || res.statusText,
            traceId: res.headers.get("X-Trace-ID") ?? undefined,
          });
          return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();
        let toolSeq = 0;

        // Text arrives one token per event. Appending each one to state
        // re-renders the whole transcript per token, so deltas are coalesced
        // and applied at most once per animation frame. Every other event
        // flushes first, so tool badges and errors never overtake the text
        // that preceded them on the wire.
        const textBuffer = createDeltaBuffer((chunk) => {
          patchAssistant(assistantId, (m) => ({ ...m, text: m.text + chunk }));
        });
        textBufferRef.current = textBuffer;

        const handle = (event: string, data: string) => {
          const frame = { event, data };
          if (event !== "text") textBuffer.flush();
          switch (event) {
            case "init": {
              const p = parseFrameJson<InitEvent>(frame);
              if (p?.session_id) setSessionId(p.session_id);
              break;
            }
            case "text": {
              const p = parseFrameJson<TextEvent>(frame);
              if (p?.delta) textBuffer.push(p.delta);
              break;
            }
            case "tool_use": {
              const p = parseFrameJson<ToolUseEvent>(frame);
              if (!p?.name) break;
              const key = `${p.name}#${toolSeq++}`;
              patchAssistant(assistantId, (m) => ({
                ...m,
                tools: [...m.tools, { key, name: p.name, ok: null, certified: false, source: null }],
              }));
              break;
            }
            case "tool_done": {
              const p = parseFrameJson<ToolDoneEvent>(frame);
              if (!p?.name) break;
              patchAssistant(assistantId, (m) => {
                // Resolve the oldest still-pending call of this name; the agent
                // finishes tools in the order it started them.
                const idx = m.tools.findIndex((t) => t.name === p.name && t.ok === null);
                const done: ToolCall = {
                  key: idx === -1 ? `${p.name}#${toolSeq++}` : m.tools[idx].key,
                  name: p.name,
                  ok: p.ok,
                  certified: Boolean(p.certified),
                  source: p.source ?? null,
                  errorCode: p.ok ? undefined : p.error_code ?? "error",
                };
                const tools = [...m.tools];
                if (idx === -1) tools.push(done);
                else tools[idx] = done;
                return { ...m, tools };
              });
              break;
            }
            case "usage": {
              const p = parseFrameJson<TurnUsage>(frame);
              if (!p) break;
              const u: TurnUsage = {
                input_tokens: p.input_tokens ?? 0,
                output_tokens: p.output_tokens ?? 0,
                cache_read_input_tokens: p.cache_read_input_tokens ?? 0,
                cost_usd: p.cost_usd ?? null,
              };
              setUsage(u);
              patchAssistant(assistantId, (m) => ({ ...m, usage: u }));
              break;
            }
            case "error": {
              const p = parseFrameJson<ErrorEvent>(frame);
              fail({
                code: p?.code ?? "internal",
                message: p?.message ?? "",
                traceId: p?.trace_id,
              });
              break;
            }
            case "done":
              // Terminal; the stream closes right after. Nothing to render.
              break;
            default:
              break;
          }
        };

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          for (const f of parser.push(decoder.decode(value, { stream: true }))) handle(f.event, f.data);
        }
        for (const f of parser.push(decoder.decode())) handle(f.event, f.data);
        for (const f of parser.flush()) handle(f.event, f.data);
        textBuffer.flush();
      } catch (err) {
        // Whatever was buffered before the stream broke is still the model's
        // answer; show it before the stop / error marker.
        textBufferRef.current?.flush();
        if (err instanceof DOMException && err.name === "AbortError") {
          patchAssistant(assistantId, (m) => ({ ...m, stopped: true }));
        } else {
          fail({ code: "network", message: err instanceof Error ? err.message : String(err) });
        }
      } finally {
        controllerRef.current = null;
        setStreaming(false);
        onTurnEndRef.current?.();
      }
    },
    [patchAssistant, sessionId],
  );

  return { messages, streaming, error, usage, sessionId, send, stop, clear };
}
