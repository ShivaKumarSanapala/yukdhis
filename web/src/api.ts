import { idToken } from "./auth";
import type { Config, ImpactReport, ReportListItem, RunEvent, RunInfo, Sample } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await idToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  config: () => call<Config>("/api/config"),
  samples: () => call<Sample[]>("/api/samples"),
  reports: () => call<ReportListItem[]>("/api/reports"),
  report: (id: string) => call<ImpactReport>(`/api/reports/${encodeURIComponent(id)}`),
  run: (id: string, after = 0) => call<{ run: RunInfo; events: RunEvent[] }>(`/api/runs/${id}?after=${after}`),
  startRun: (input: { sampleId?: string; file?: File; mode: string; asOf: string }) => {
    const form = new FormData();
    if (input.sampleId) form.set("sample_id", input.sampleId);
    if (input.file) form.set("file", input.file);
    form.set("mode", input.mode);
    if (input.asOf) form.set("as_of", input.asOf);
    return call<{ run_id: string; mode: string }>("/api/runs", { method: "POST", body: form });
  },
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
};

export interface GraphSnapshot {
  as_of: string;
  nodes: { id: string; type: string; name: string }[];
  edges: { source: string; target: string; type: string; valid_from: string; valid_to: string | null }[];
}

/** Streams run events over WebSocket; falls back to polling (e.g. behind Firebase Hosting). */
export function followRun(runId: string, onEvent: (event: RunEvent) => void, onDone: (status: string) => void): () => void {
  let stopped = false;
  let seq = 0;
  const deliver = (event: RunEvent) => {
    if (event.seq && event.seq <= seq) return;
    if (event.seq) seq = event.seq;
    onEvent(event);
  };
  const poll = async () => {
    while (!stopped) {
      try {
        const { run, events } = await api.run(runId, seq);
        events.forEach(deliver);
        if (run.status === "SUCCEEDED" || run.status === "FAILED") return onDone(run.status);
      } catch {
        /* transient; retry */
      }
      await new Promise((r) => setTimeout(r, 1200));
    }
  };
  const wsBase = API_BASE ? API_BASE.replace(/^http/, "ws") : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
  let socket: WebSocket | null = null;
  try {
    socket = new WebSocket(`${wsBase}/ws/runs/${runId}`);
    socket.onopen = async () => {
      const token = await idToken();
      if (token) socket?.send(JSON.stringify({ token }));
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.kind === "closed") {
        stopped = true;
        onDone(event.status);
      } else deliver(event);
    };
    socket.onerror = () => {
      socket = null;
      void poll();
    };
  } catch {
    void poll();
  }
  return () => {
    stopped = true;
    socket?.close();
  };
}
