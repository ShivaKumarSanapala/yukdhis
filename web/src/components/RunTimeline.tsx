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

export default function RunTimeline({ events, status }: { events: RunEvent[]; status: string }) {
  const seen = new Set(events.map((e) => e.stage));
  const latest = [...events].reverse().find((e) => STAGES.some(([s]) => s === e.stage))?.stage;
  return (
    <div className="card">
      <h2>Agent pipeline <span className={`pill ${status === "SUCCEEDED" ? "ok" : status === "FAILED" ? "bad" : ""}`}>{status}</span></h2>
      <ol className="stages">
        {STAGES.map(([id, label]) => (
          <li key={id} className={seen.has(id) ? (id === latest && status === "RUNNING" ? "active" : "done") : ""}>{label}</li>
        ))}
      </ol>
      <details>
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
