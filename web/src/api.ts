import { idToken } from "./auth";
import type {
  Config, Conversation, ConversationDetail, ExtractedRule, ImpactReport, ReportListItem, RunEvent, RunInfo, Sample, StartedRuns,
  ToolCallTrace,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

/** Server detail string, or the first FastAPI validation message (422 lists them). */
async function errorFrom(response: Response): Promise<Error> {
  const body = await response.json().catch(() => ({}));
  if (typeof body.detail === "string") return new Error(body.detail);
  const first = Array.isArray(body.detail) ? body.detail[0] : null;
  if (first?.msg) return new Error(`${(first.loc ?? []).slice(1).join(".")}: ${first.msg}`.replace(/^: /, ""));
  return new Error(`${response.status} ${response.statusText}`);
}

const runPath = (id: string) => `/api/runs/${encodeURIComponent(id)}`;
/** Statuses after which a run's event stream ends (AWAITING_REVIEW resumes as a new stream after approval). */
export const RUN_FINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "REJECTED", "AWAITING_REVIEW"]);

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await idToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) throw await errorFrom(response);
  return response.json() as Promise<T>;
}

const json = (body: unknown, method = "POST"): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const conversationPath = (id: string) => `/api/conversations/${encodeURIComponent(id)}`;

export const api = {
  config: () => call<Config>("/api/config"),
  samples: () => call<Sample[]>("/api/samples"),
  reports: () => call<ReportListItem[]>("/api/reports"),
  report: (id: string) => call<ImpactReport>(`/api/reports/${encodeURIComponent(id)}`),
  run: (id: string, after = 0) => call<{ run: RunInfo; events: RunEvent[] }>(`${runPath(id)}?after=${after}`),
  runs: (filters: { status?: string; mine?: boolean; sourceKind?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (filters.status) q.set("status", filters.status);
    if (filters.mine) q.set("mine", "1");
    if (filters.sourceKind) q.set("source_kind", filters.sourceKind);
    q.set("limit", String(filters.limit ?? 30));
    return call<RunInfo[]>(`/api/runs?${q}`);
  },
  /** One run per sample, file, pasted text or URL; all are validated before any starts. */
  startRuns: (input: { sampleIds?: string[]; files?: File[]; text?: string; url?: string; mode: string; asOf: string; reviewRules?: boolean }) => {
    const form = new FormData();
    input.sampleIds?.forEach((id) => form.append("sample_ids", id));
    input.files?.forEach((file) => form.append("files", file));
    if (input.text) form.set("text", input.text);
    if (input.url) form.set("url", input.url);
    form.set("mode", input.mode);
    if (input.asOf) form.set("as_of", input.asOf);
    if (input.reviewRules) form.set("review_rules", "true");
    return call<StartedRuns>("/api/runs", { method: "POST", body: form });
  },
  cancelRun: (id: string) => call<{ run_id: string; status: string }>(`${runPath(id)}/cancel`, { method: "POST" }),
  reviewRun: (id: string, decision: "APPROVE" | "REJECT", rules?: ExtractedRule[]) =>
    call<{ run_id: string; status: string }>(`${runPath(id)}/review`, json({ decision, rules })),
  rerun: (id: string, input: { asOf?: string; mode?: string; reviewRules?: boolean } = {}) =>
    call<StartedRuns>(`${runPath(id)}/rerun`, json({ as_of: input.asOf || null, mode: input.mode || null, review_rules: Boolean(input.reviewRules) })),
  decide: (actionId: string, decision: "APPROVED" | "REJECTED") =>
    call<{ status: string }>(`/api/actions/${actionId}/decision`, json({ decision })),
  outbox: (actionId: string) =>
    call<{ channel: string; recipient: string; body: string }[]>(`/api/actions/${actionId}/outbox`),
  reviewRule: (ruleId: string, decision: "APPROVED" | "REJECTED") =>
    call<{ status: string; edges_versioned: number }>("/api/rules/review", json({ rule_id: ruleId, decision })),
  graph: (asOf: string) => call<GraphSnapshot>(`/api/graph?as_of=${asOf}`),
  sanitize: (text: string) =>
    call<{ sanitized: string; entity_counts: Record<string, number>; engine: string }>("/api/privacy/sanitize", json({ text })),
  chat: (text: string, sessionId?: string) =>
    call<{ session_id: string; reply: string; tools_used: string[] }>("/api/chat", json({ text, session_id: sessionId })),
  conversations: () => call<Conversation[]>("/api/conversations"),
  conversation: (id: string) => call<ConversationDetail>(conversationPath(id)),
  renameConversation: (id: string, title: string) => call<Conversation>(conversationPath(id), json({ title }, "PATCH")),
  deleteConversation: (id: string) => call<{ deleted: boolean }>(conversationPath(id), { method: "DELETE" }),
};

// Assistant SSE stream ----------------------------------------------------------------
export interface StreamHandlers {
  onStart?: (data: { conversation_id: string; title: string; created: boolean; user_message_id: string }) => void;
  onThinking?: (delta: string) => void;
  onText?: (delta: string) => void;
  onToolCall?: (call: Pick<ToolCallTrace, "id" | "name" | "args">) => void;
  onToolResult?: (result: Required<Pick<ToolCallTrace, "id" | "name" | "output" | "status">> & { duration_ms: number | null }) => void;
  onError?: (message: string) => void;
  onDone?: (data: { conversation_id: string; message_id: string; status: string }) => void;
}

/** One SSE frame -> {event, data}; comment-only frames (heartbeats) return null. */
export function parseSseFrame(frame: string): { event: string; data: string } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return data.length ? { event, data: data.join("\n") } : null;
}

function dispatch(frame: string, h: StreamHandlers) {
  const parsed = parseSseFrame(frame);
  if (!parsed) return;
  const data = JSON.parse(parsed.data);
  switch (parsed.event) {
    case "start": return h.onStart?.(data);
    case "thinking": return h.onThinking?.(data.delta ?? "");
    case "text": return h.onText?.(data.delta ?? "");
    case "tool_call": return h.onToolCall?.(data);
    case "tool_result": return h.onToolResult?.(data);
    case "error": return h.onError?.(data.message ?? "The assistant failed.");
    case "done": return h.onDone?.(data);
  }
}

/**
 * POST /api/chat/stream straight to Cloud Run (VITE_API_BASE): Firebase Hosting rewrites buffer
 * responses and EventSource cannot send the ID token, so this reads the fetch body as SSE.
 */
export async function streamChat(
  body: { text: string; conversation_id?: string; context?: { report_id?: string } },
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const token = await idToken();
  const headers = new Headers({ "Content-Type": "application/json", Accept: "text/event-stream" });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}/api/chat/stream`, { method: "POST", headers, body: JSON.stringify(body), signal });
  if (!response.ok || !response.body) throw await errorFrom(response);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    frames.forEach((frame) => dispatch(frame, handlers));
  }
  if (buffer.trim()) dispatch(buffer, handlers);
}

export interface GraphSnapshot {
  as_of: string;
  nodes: { id: string; type: string; name: string }[];
  edges: { source: string; target: string; type: string; valid_from: string; valid_to: string | null }[];
}

/**
 * Streams a run's events over WebSocket and falls back to polling (e.g. behind Firebase Hosting, or when the socket
 * drops). `onDone` fires once, with the status that ended the stream (see RUN_FINAL). Returns a stop function.
 */
export function followRun(runId: string, onEvent: (event: RunEvent) => void, onDone: (status: string) => void): () => void {
  let stopped = false;
  let finished = false;
  let polling = false;
  let seq = 0;
  const finish = (status: string) => {
    if (finished || stopped) return;
    finished = true;
    onDone(status);
  };
  const deliver = (event: RunEvent) => {
    if (stopped || (event.seq && event.seq <= seq)) return;
    if (event.seq) seq = event.seq;
    onEvent(event);
  };
  const poll = async () => {
    if (polling) return;
    polling = true;
    while (!stopped && !finished) {
      try {
        const { run, events } = await api.run(runId, seq);
        events.forEach(deliver);
        if (RUN_FINAL.has(run.status)) return finish(run.status);
      } catch {
        /* transient; retry */
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  };
  const wsBase = API_BASE ? API_BASE.replace(/^http/, "ws") : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
  let socket: WebSocket | null = null;
  try {
    socket = new WebSocket(`${wsBase}/ws/runs/${encodeURIComponent(runId)}`);
    socket.onopen = async () => {
      const token = await idToken();
      if (token) socket?.send(JSON.stringify({ token }));
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.kind === "closed") finish(event.status);
      else deliver(event);
    };
    socket.onerror = () => void poll();
    socket.onclose = () => { if (!finished && !stopped) void poll(); };
  } catch {
    void poll();
  }
  return () => {
    stopped = true;
    if (socket) {
      socket.onclose = null;
      socket.close();
    }
  };
}
