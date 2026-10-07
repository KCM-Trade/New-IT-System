/**
 * One AI analyst turn over `POST /api/v1/ai/turn` (SSE), plus the session
 * bookkeeping around it.
 *
 * Contract: docs/ai-agent/02-contracts.md §4.1 / §4.3 / §8.4. The stream is
 * consumed with `apiFetch` + `ReadableStream`, not `EventSource`: the question
 * travels in a POST body and EventSource can only GET. `apiFetch` still injects
 * the API key header and the session cookie, and returns the `Response` before
 * reading the body, so streaming works once its timeout and retry are switched
 * off (a retry would resend the question; a timeout would cut a long analysis).
 *
 * Memory model (slice 2): the SERVER remembers. Every turn is sent with the
 * current `session_id`; the main API feeds the agent the conversation so far
 * and stores the turn afterwards. The browser holds only the visible
 * transcript — live turns are built from the SSE frames, a reopened
 * conversation is rebuilt from `GET /ai/sessions/{id}` through the same
 * `AiMessage` shape (lib/ai-session.ts), so both render identically.
 * `newConversation()` just forgets the id; the server row stays in the list.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  appendRunText,
  applyCompareResult,
  applyCompareRunEvent,
  compareIsUntouched,
  isOpenCompare,
  newCompare,
  runOf,
  sumUsage,
  updateRun,
  type AiCompare,
  type CompareReason,
  type CompareRun,
} from "@/lib/ai-compare";
import { createDeltaBuffer, type DeltaBuffer } from "@/lib/delta-buffer";
import { sessionTranscript, type AiSessionDetail } from "@/lib/ai-session";
import { apiFetch } from "@/lib/fetch";
import { SseParser, parseFrameJson } from "@/lib/sse-parser";

export type AiModel = "gpt-5.6-terra" | "gpt-5.6-sol" | "gpt-6.1-sol" | "grok-4.7" | "DeepSeek-V4-Pro";

// Keep in sync with the backend's schemas.ai.AiModel (a backend test reads this line).
export const AI_MODELS: readonly AiModel[] = ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-6.1-sol", "grok-4.7", "DeepSeek-V4-Pro"];
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
  /**
   * The model's arguments as sent in `tool_use`. Kept for `run_sql`, whose
   * `input.sql` the badge popover must show verbatim (02 §10.3) — an
   * uncertified number with no visible query behind it is not reviewable.
   */
  input?: unknown;
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
  /** The model that gave this answer (a plain string: history may name a retired model). */
  model?: string;
  tools: ToolCall[];
  error?: TurnError;
  usage?: TurnUsage;
  /** Set when the user pressed stop before `done` arrived. */
  stopped?: boolean;
  /**
   * Set on the assistant message of a multi-model compare turn (02 §19–§23).
   * While `running` / `pending` the runs ARE the answer and render side by
   * side; once `selected` the message itself is the chosen answer and the
   * runs are the alternatives.
   */
  compare?: AiCompare;
}

export interface UseAiTurnResult {
  messages: AiMessage[];
  streaming: boolean;
  /** The last turn's error, also attached to that assistant message. */
  error: TurnError | null;
  /** The last completed turn's usage. */
  usage: TurnUsage | null;
  sessionId: string | null;
  /** True while a stored conversation is being loaded for redisplay. */
  loadingSession: boolean;
  /**
   * Ask a question. With `compareModels` (2–3 models) the turn fans out to all
   * of them and `model` is ignored by the server.
   */
  send: (message: string, model: AiModel, compareModels?: readonly AiModel[]) => Promise<void>;
  stop: () => void;
  /**
   * The open conversation's unresolved compare turn (still generating, or
   * waiting for a choice), or null. While it exists the server refuses any new
   * question with 409 — whatever the compare switch says.
   */
  pendingCompare: AiCompare | null;
  /** The model whose answer is being committed right now, if any. */
  selecting: string | null;
  /** The last failed `select`, cleared by the next attempt. */
  selectError: TurnError | null;
  /**
   * Continue the conversation with one model's answer, or (same model again)
   * attach / change the optional reason. Re-reads the session afterwards.
   */
  select: (compareId: string, model: string, reason?: CompareReason | null) => Promise<boolean>;
  /**
   * Replace the transcript with a stored conversation and continue it.
   * Resolves to the session detail, or `null` when it does not exist (404) or
   * the load was aborted — callers drop the id in that case.
   */
  resumeSession: (id: string, signal?: AbortSignal) => Promise<AiSessionDetail | null>;
  /** Forget the current session id and clear the transcript. No server call. */
  newConversation: () => void;
}

interface UseAiTurnOptions {
  /** Called after every terminal event (done / error) — e.g. refresh the quota. */
  onTurnEnd?: () => void;
}

// Wire payloads, per 02 §4.3.
interface InitEvent {
  session_id: string;
  model: string;
  compare?: { compare_id?: string; models?: string[] };
}
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

/** Poll cadence for a compare turn the server is finishing in the background. */
const COMPARE_POLL_MS = 5_000;
/**
 * Stop polling after this long. The server treats a `running` turn older than
 * 720s as dead (02 §21) but only marks it so on the next claim; past that
 * point the next question is what unblocks the conversation, not more polls.
 */
const COMPARE_POLL_MAX_MS = 780_000;

async function readDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : "";
  } catch {
    return ""; // non-JSON body
  }
}

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
  const [loadingSession, setLoadingSession] = useState(false);
  const [selecting, setSelecting] = useState<string | null>(null);
  const [selectError, setSelectError] = useState<TurnError | null>(null);

  const controllerRef = useRef<AbortController | null>(null);
  // Mirror of `loadingSession` readable from inside `send` without making it a
  // dependency (a resume in flight must block a new question, or the resumed
  // transcript would replace the one the question was just appended to).
  const loadingRef = useRef(false);
  // Set inside `send` when the turn's outcome has to be read back from the
  // server once the stream is over; consumed in its `finally`.
  const compareRefreshRef = useRef<string | null>(null);
  const onTurnEndRef = useRef(options.onTurnEnd);
  onTurnEndRef.current = options.onTurnEnd;
  // Readable from async callbacks that must notice the conversation changed
  // underneath them (a poll or a select landing after "new conversation").
  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = sessionId;
  const selectingRef = useRef(false);

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

  const newConversation = useCallback(() => {
    if (streaming) return;
    setMessages([]);
    setError(null);
    setUsage(null);
    setSessionId(null);
    setSelectError(null);
  }, [streaming]);

  /**
   * Re-read the open conversation and rebuild the transcript from it. Used
   * after Stop, after a choice, and by the background poll. Never touches the
   * transcript while a turn is streaming or another conversation was opened.
   */
  const refreshSession = useCallback(async (id: string, signal?: AbortSignal): Promise<AiSessionDetail | null> => {
    try {
      const res = await apiFetch(`/api/v1/ai/sessions/${encodeURIComponent(id)}`, { signal });
      if (!res.ok) return null;
      const detail = (await res.json()) as AiSessionDetail;
      if (signal?.aborted || controllerRef.current || sessionIdRef.current !== id) return null;
      setMessages((prev) => sessionTranscript(detail, prev));
      return detail;
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return null;
      return null; // the transcript keeps its last value; the next poll retries
    }
  }, []);

  const select = useCallback(
    async (compareId: string, model: string, reason?: CompareReason | null): Promise<boolean> => {
      const id = sessionIdRef.current;
      if (!id || !compareId || controllerRef.current || selectingRef.current) return false;
      selectingRef.current = true;
      setSelecting(model);
      setSelectError(null);
      try {
        const res = await apiFetch(
          `/api/v1/ai/sessions/${encodeURIComponent(id)}/select`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ compare_id: compareId, model, reason: reason ?? null }),
          },
          // A choice is not idempotent across models; never replay it blindly.
          { retries: 0 },
        );
        if (!res.ok) {
          const detail = await readDetail(res);
          const code =
            res.status === 404
              ? "session_not_found"
              : res.status === 403
                ? "forbidden"
                : res.status === 409
                  ? detail === "compare stale"
                    ? "compare_stale"
                    : detail === "compare already selected"
                      ? "compare_selected"
                      : "session_busy"
                  : res.status === 422
                    ? "compare_unselectable"
                    : `http_${res.status}`;
          setSelectError({
            code,
            message: detail || res.statusText,
            traceId: res.headers.get("X-Trace-ID") ?? undefined,
          });
          // Stale / already chosen elsewhere / no longer selectable (422): the
          // server's view has moved on, so show it. The error line stays.
          if ((res.status === 409 && code !== "session_busy") || res.status === 422) await refreshSession(id);
          return false;
        }
        await refreshSession(id);
        setUsage(null);
        onTurnEndRef.current?.();
        return true;
      } catch (err) {
        setSelectError({ code: "network", message: err instanceof Error ? err.message : String(err) });
        return false;
      } finally {
        selectingRef.current = false;
        setSelecting(null);
      }
    },
    [refreshSession],
  );

  const resumeSession = useCallback(
    async (id: string, signal?: AbortSignal): Promise<AiSessionDetail | null> => {
      if (controllerRef.current || loadingRef.current) return null;
      loadingRef.current = true;
      setLoadingSession(true);
      try {
        const res = await apiFetch(`/api/v1/ai/sessions/${encodeURIComponent(id)}`, { signal });
        // A turn may have started while the fetch was in flight (the send
        // gate reads loadingRef, but a mount-time resume races the first
        // keystroke); never replace a live transcript.
        if (controllerRef.current) return null;
        if (res.status === 404) return null;
        if (!res.ok) {
          setError({ code: res.status === 403 ? "forbidden" : `http_${res.status}`, message: res.statusText });
          return null;
        }
        const detail = (await res.json()) as AiSessionDetail;
        if (signal?.aborted || controllerRef.current) return null;
        setMessages(sessionTranscript(detail));
        setError(null);
        setSelectError(null);
        setUsage(null);
        setSessionId(detail.session.session_id);
        return detail;
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return null;
        setError({ code: "network", message: err instanceof Error ? err.message : String(err) });
        return null;
      } finally {
        loadingRef.current = false;
        setLoadingSession(false);
      }
    },
    [],
  );

  const send = useCallback(
    async (message: string, model: AiModel, compareModels?: readonly AiModel[]) => {
      const question = message.trim();
      if (!question || controllerRef.current || loadingRef.current) return;
      const runModels = compareModels && compareModels.length > 0 ? [...compareModels] : null;

      const controller = new AbortController();
      controllerRef.current = controller;
      setStreaming(true);
      setError(null);
      setSelectError(null);

      const assistantId = newId();
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "user", text: question, tools: [] },
        runModels
          ? { id: assistantId, role: "assistant", text: "", tools: [], compare: newCompare(runModels, Date.now()) }
          : { id: assistantId, role: "assistant", text: "", model, tools: [] },
      ]);
      if (runModels) setUsage(null);

      // Declared outside `try` so the catch block can flush what was buffered
      // before an abort or a network error.
      const textBufferRef: { current: { flush: () => void } | null } = { current: null };

      // Compare turn: one delta buffer per run, so each column's text is
      // coalesced per animation frame on its own and a fast model does not
      // force a re-render of the others.
      const runBuffers = new Map<string, DeltaBuffer>();
      const runUsage = new Map<string, TurnUsage>();
      const patchCompare = (fn: (c: AiCompare) => AiCompare) => {
        patchAssistant(assistantId, (m) => {
          if (!m.compare) return m;
          const next = fn(m.compare);
          return next === m.compare ? m : { ...m, compare: next };
        });
      };
      const patchRun = (run: string, fn: (r: CompareRun) => CompareRun) => {
        patchCompare((c) => updateRun(c, run, fn));
      };
      const runBuffer = (run: string): DeltaBuffer => {
        let buf = runBuffers.get(run);
        if (!buf) {
          buf = createDeltaBuffer((chunk) => patchRun(run, (r) => appendRunText(r, chunk)));
          runBuffers.set(run, buf);
        }
        return buf;
      };
      const flushRuns = () => {
        for (const buf of runBuffers.values()) buf.flush();
      };
      let compareResolved = false;
      // The stream ended without the turn's verdict. With a session id the
      // server is asked for it (and polled while it is still finishing);
      // without one nothing was ever stored, so the turn is closed locally.
      const detachCompare = () => {
        const sid = sessionIdRef.current;
        if (sid) {
          patchCompare((c) => ({ ...c, detached: true }));
          compareRefreshRef.current = sid;
        } else {
          patchCompare((c) => applyCompareResult(c, { state: "void" }));
        }
      };

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
            // The single-model body is unchanged: `compare_models` is absent,
            // not null, so that request stays byte-identical (02 §19).
            body: runModels
              ? JSON.stringify({
                  session_id: sessionId,
                  message: question,
                  model: runModels[0],
                  compare_models: runModels,
                })
              : JSON.stringify({ session_id: sessionId, message: question, model }),
            signal: controller.signal,
          },
          // No timeout (a deep analysis can run for minutes) and no retry (a
          // retry would resend the question and be billed twice).
          { timeoutMs: 0, retries: 0 },
        );

        if (!res.ok || !res.body) {
          // 403 = module not granted (never 401 — see lib/fetch.ts). Anything
          // else is surfaced with its status so the trace can be found.
          const detail = await readDetail(res);
          // Refused before the stream started: no run ever existed.
          if (runModels) patchAssistant(assistantId, (m) => ({ ...m, compare: undefined }));
          if (res.status === 404) {
            // The conversation we were continuing is gone (deleted in another
            // tab, or never ours). Forget the id so the next question starts
            // a fresh one instead of failing the same way again.
            setSessionId(null);
          }
          fail({
            code:
              res.status === 403
                ? "forbidden"
                : res.status === 404
                  ? "session_not_found"
                  : res.status === 409
                    ? detail === "compare pending"
                      ? "compare_pending"
                      : "session_busy"
                    : `http_${res.status}`,
            message: detail || res.statusText,
            traceId: res.headers.get("X-Trace-ID") ?? undefined,
          });
          if (res.status === 409 && detail === "compare pending" && sessionId) {
            // The pending turn was created elsewhere (another tab); show it,
            // since choosing is the only way forward.
            compareRefreshRef.current = sessionId;
          }
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

        // Events of a compare turn that carry `run` belong to one column.
        const handleRun = (run: string, event: string, payload: unknown) => {
          if (event === "text") {
            const delta = (payload as TextEvent).delta;
            if (delta) runBuffer(run).push(delta);
            return;
          }
          // Flush first so a badge or an error never overtakes the text that
          // preceded it on the wire.
          runBuffers.get(run)?.flush();
          patchCompare((c) => applyCompareRunEvent(c, run, event, payload));
          if (event === "usage") {
            const u = payload as Partial<TurnUsage>;
            runUsage.set(run, {
              input_tokens: u.input_tokens ?? 0,
              output_tokens: u.output_tokens ?? 0,
              cache_read_input_tokens: u.cache_read_input_tokens ?? 0,
              cost_usd: u.cost_usd ?? null,
            });
            setUsage(sumUsage([...runUsage.values()]));
          }
        };

        const handle = (event: string, data: string) => {
          const frame = { event, data };
          if (runModels) {
            const payload = parseFrameJson<unknown>(frame);
            const run = runOf(payload);
            if (run !== null) {
              handleRun(run, event, payload);
              return;
            }
            // No `run`: the event is about the whole turn.
            flushRuns();
            if (event === "compare") {
              compareResolved = true;
              patchCompare((c) => applyCompareResult(c, payload));
              const state = (payload as { state?: string } | null)?.state;
              if (state !== "pending") {
                const err: TurnError = { code: "compare_failed", message: "" };
                setError(err);
                patchAssistant(assistantId, (m) => (m.error ? m : { ...m, error: err }));
              }
              return;
            }
            if (event === "error") {
              const p = payload as ErrorEvent | null;
              const err: TurnError = { code: p?.code ?? "internal", message: p?.message ?? "", traceId: p?.trace_id };
              compareResolved = true;
              setError(err);
              // Refused up front (quota / compare_busy): nothing ran, so the
              // message is a plain failed answer. Otherwise keep what the
              // runs produced and close the turn as void.
              patchAssistant(assistantId, (m) => {
                if (!m.compare) return { ...m, error: err };
                if (compareIsUntouched(m.compare)) return { ...m, error: err, compare: undefined };
                return { ...m, error: err, compare: applyCompareResult(m.compare, { state: "void" }) };
              });
              return;
            }
          }
          if (event !== "text") textBuffer.flush();
          switch (event) {
            case "init": {
              const p = parseFrameJson<InitEvent>(frame);
              if (p?.session_id) {
                setSessionId(p.session_id);
                sessionIdRef.current = p.session_id;
              }
              if (runModels && p?.compare?.compare_id) {
                const compareId = p.compare.compare_id;
                patchCompare((c) => ({ ...c, compareId }));
              }
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
                tools: [...m.tools, { key, name: p.name, ok: null, certified: false, source: null, input: p.input }],
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
                  input: idx === -1 ? undefined : m.tools[idx].input,
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
        flushRuns();
        if (runModels && !compareResolved) {
          // The stream closed without a verdict (proxy cut, worker restart).
          // The server may still be finishing; ask it instead of guessing.
          detachCompare();
        }
      } catch (err) {
        // Whatever was buffered before the stream broke is still the model's
        // answer; show it before the stop / error marker.
        textBufferRef.current?.flush();
        flushRuns();
        if (err instanceof DOMException && err.name === "AbortError") {
          patchAssistant(assistantId, (m) => ({ ...m, stopped: true }));
        } else {
          fail({ code: "network", message: err instanceof Error ? err.message : String(err) });
        }
        if (runModels && !compareResolved) {
          // Stop / disconnect: the server keeps draining the runs for up to
          // 120s and then stores whatever finished (02 §20). Its answer comes
          // from `pending_compare`, not from this stream.
          detachCompare();
        }
      } finally {
        controllerRef.current = null;
        setStreaming(false);
        onTurnEndRef.current?.();
        const refreshId = compareRefreshRef.current;
        compareRefreshRef.current = null;
        if (refreshId) void refreshSession(refreshId);
      }
    },
    [patchAssistant, refreshSession, sessionId],
  );

  // The open conversation's unresolved compare turn, if any. Always the last
  // assistant message: nothing can be asked after it until it is resolved.
  const pendingCompare = useMemo<AiCompare | null>(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const c = messages[i].compare;
      if (c) return isOpenCompare(c) ? c : null;
      if (messages[i].role === "assistant") return null;
    }
    return null;
  }, [messages]);

  // A compare turn the server is finishing without us (after Stop, or found
  // `running` on resume): poll the session until it is no longer running.
  // Hidden tabs skip the tick and catch up at once on return (CLAUDE.md).
  const pollingCompareId =
    !streaming && sessionId && pendingCompare?.state === "running" ? pendingCompare.compareId || "?" : null;
  const pollStartedAt = pendingCompare?.startedAt;
  useEffect(() => {
    if (!pollingCompareId || !sessionId) return;
    const id = sessionId;
    const deadline = (pollStartedAt ?? Date.now()) + COMPARE_POLL_MAX_MS;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    const giveUp = () => {
      if (timer) clearInterval(timer);
      timer = null;
      // The worker is presumed dead; release the conversation locally. The
      // server voids the stale turn on the next question.
      setMessages((prev) =>
        prev.map((m) =>
          m.compare && m.compare.state === "running"
            ? { ...m, error: { code: "incomplete", message: "" }, compare: applyCompareResult(m.compare, { state: "void" }) }
            : m,
        ),
      );
    };
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      const expired = Date.now() > deadline;
      controller?.abort();
      controller = new AbortController();
      const { signal } = controller;
      void refreshSession(id, signal).then((detail) => {
        if (signal.aborted) return;
        if (detail && detail.pending_compare?.state !== "running") {
          // The turn ended while we were away: quota and the list moved too.
          onTurnEndRef.current?.();
        } else if (expired) {
          giveUp();
        }
      });
    };
    timer = setInterval(tick, COMPARE_POLL_MS);
    const onVisibility = () => {
      if (document.visibilityState === "visible") tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      controller?.abort();
    };
  }, [pollingCompareId, pollStartedAt, sessionId, refreshSession]);

  return {
    messages,
    streaming,
    error,
    usage,
    sessionId,
    loadingSession,
    send,
    stop,
    pendingCompare,
    selecting,
    selectError,
    select,
    resumeSession,
    newConversation,
  };
}
