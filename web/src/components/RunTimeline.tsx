import type { RunEvent, RunInfo } from "../types";
import AgentActivity from "./AgentActivity";
import { STAGES, fmtDuration, runStatusMeta, stageOutcome, stageStates, stageTimings, statusLine, type StageState } from "../run-utils";

function describe(event: RunEvent): string {
  const p = event.payload as Record<string, any>;
  switch (event.kind) {
    case "tool_call": return `→ ${p.tool}(${Object.keys(p.args ?? {}).join(", ")})`;
    case "tool_result": return `← ${p.tool}: ${(p.output?.status as string) ?? "ok"}`;
    case "agent_message": return p.text as string;
    case "thinking": return `${p.agent} thinking${p.done ? "" : "…"} (${String(p.text ?? "").length} chars)`;
    case "extraction_retry": return `extraction attempt ${p.attempt} failed: ${p.error}`;
    case "control_canonicalised": return `control ${p.term} → ${p.control_id} (${p.decision}, ${p.method})${p.needs_review ? ", needs review" : ""}`;
    case "stage_started": return `started ${p.label ?? p.agent ?? ""}`;
    case "stage_completed": return `done: ${p.label}`;
    case "stage_skipped": return `skipped: ${p.label} (${p.reason})`;
    case "run_queued": case "run_waiting": return p.message as string;
    case "run_failed": return `failed: ${p.error}`;
    case "rules_extracted": return `${(p.rules ?? []).length} rules extracted, awaiting review`;
    case "rules_reviewed": return `rules approved by ${p.by}`;
    case "run_rejected": return `rejected by ${p.by}${p.comment ? `: ${p.comment}` : ""}`;
    default: return event.kind.replaceAll("_", " ");
  }
}

const STATE_TEXT: Record<StageState, string> = {
  pending: "not started", active: "in progress", done: "done", failed: "failed", cancelled: "cancelled here", paused: "paused for review",
};

/** Stage strip with a one-line outcome each, a plain-language status line, and the raw event log behind a toggle. */
export default function RunTimeline({ run, events }: { run: RunInfo; events: RunEvent[] }) {
  const status = run.status;
  const states = stageStates(events, status);
  const { per, total } = stageTimings(events);
  const meta = runStatusMeta(status);
  const line = statusLine(run, events);
  const live = status === "RUNNING" || status === "CANCEL_REQUESTED";
  return (
    <div className="card">
      <h2>Agent pipeline <span className={`pill ${meta.tone}`} role="status">{meta.label}</span>
        {!live && total !== null && total > 0 && <span className="muted"> · total {fmtDuration(total)}</span>}</h2>
      {line && <div className={`banner ${status === "FAILED" ? "error" : status === "AWAITING_REVIEW" || status === "CANCEL_REQUESTED" ? "warn" : ""}`} role={status === "FAILED" ? "alert" : "status"}>{line}</div>}
      <ol className="stages" aria-label="Pipeline stages">
        {STAGES.map(([id, label]) => {
          const state = states.get(id) ?? "pending";
          const outcome = stageOutcome(id, events);
          return (
            <li key={id} className={state}>
              <span className="stage-name">{label}</span>
              <span className="sr-only">: {STATE_TEXT[state]}</span>
              {outcome && <span className="outcome">{outcome}</span>}
              {per.has(id) && <span className="elapsed">{fmtDuration(per.get(id)!)}{state === "active" ? " so far" : ""}</span>}
              {state === "failed" && <span className="outcome">Failed here</span>}
              {state === "cancelled" && <span className="outcome">Cancelled here</span>}
              {state === "paused" && <span className="outcome">Waiting for review</span>}
            </li>
          );
        })}
      </ol>
      <AgentActivity events={events} live={live} activeStage={run.current_stage ?? null} />
      <details className="event-log">
        <summary>Raw event log ({events.length})</summary>
        <ul className="log">
          {events.map((e) => (
            <li key={e.seq}><code>{e.stage}</code> <span className={e.kind}>{describe(e)}</span></li>
          ))}
        </ul>
      </details>
    </div>
  );
}
