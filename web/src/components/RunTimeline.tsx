import type { RunEvent } from "../types";

const STAGES: [string, string][] = [
  ["reader", "Reader · Document AI + Gemini"],
  ["mapper", "Mapper · BIAN entity resolution"],
  ["graphrag", "GraphRAG · Spanner Graph traversal"],
  ["precedent", "Precedent · Memory Bank"],
  ["data_analyst", "Data Analyst · guarded SQL"],
  ["evaluator", "Evaluator · deterministic checks"],
  ["orchestrator", "Orchestrator · report & actions"],
];

function describe(event: RunEvent): string {
  const p = event.payload as Record<string, any>;
  switch (event.kind) {
    case "tool_call": return `→ ${p.tool}(${Object.keys(p.args ?? {}).join(", ")})`;
    case "tool_result": return `← ${p.tool}: ${(p.output?.status as string) ?? "ok"}`;
    case "agent_message": return p.text as string;
    case "stage_started": return `started ${p.label ?? p.agent ?? ""}`;
    case "stage_completed": return `done: ${p.label}`;
    default: return event.kind;
  }
}

const fmt = (ms: number) => (ms < 10_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms / 1000)}s`);

/** Elapsed ms per stage: first event of a stage until the first event of the next stage (last stage: until the final event). */
function timings(events: RunEvent[]) {
  const stamped = events.map((e) => ({ stage: e.stage, t: Date.parse(e.ts) })).filter((e) => !Number.isNaN(e.t));
  const starts: { stage: string; t: number }[] = [];
  for (const e of stamped) if (!starts.some((s) => s.stage === e.stage)) starts.push(e);
  const end = stamped.length ? Math.max(...stamped.map((e) => e.t)) : 0;
  const per = new Map<string, number>();
  starts.forEach((s, i) => per.set(s.stage, Math.max(0, (starts[i + 1]?.t ?? end) - s.t)));
  const total = stamped.length ? end - Math.min(...stamped.map((e) => e.t)) : null;
  return { per, total };
}

export default function RunTimeline({ events, status }: { events: RunEvent[]; status: string }) {
  const seen = new Set(events.map((e) => e.stage));
  const { per, total } = timings(events);
  const finished = status === "SUCCEEDED" || status === "FAILED";
  const latest = [...events].reverse().find((e) => STAGES.some(([s]) => s === e.stage))?.stage;
  return (
    <div className="card">
      <h2>Agent pipeline <span className={`pill ${status === "SUCCEEDED" ? "ok" : status === "FAILED" ? "bad" : ""}`} role="status">{status}</span>
        {finished && total !== null && <span className="muted"> · total {fmt(total)}</span>}</h2>
      <ol className="stages" aria-label="Pipeline stages">
        {STAGES.map(([id, label]) => (
          <li key={id} className={seen.has(id) ? (id === latest && status === "RUNNING" ? "active" : "done") : ""}>
            {label}
            {per.has(id) && <span className="elapsed">{fmt(per.get(id)!)}{id === latest && status === "RUNNING" ? " so far" : ""}</span>}
          </li>
        ))}
      </ol>
      <details className="event-log">
        <summary>Event log ({events.length})</summary>
        <ul className="log">
          {events.map((e) => (
            <li key={e.seq}><code>{e.stage}</code> <span className={e.kind}>{describe(e)}</span></li>
          ))}
        </ul>
      </details>
    </div>
  );
}
