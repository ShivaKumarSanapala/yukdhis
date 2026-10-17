import { useEffect, useId, useRef, useState } from "react";
import type { RunEvent, RunInfo } from "../types";
import AgentActivity from "./AgentActivity";
import { STAGES, fmtDuration, isActive, runStatusMeta, stageLabel, stageStates, stageTimings, statusLine } from "../run-utils";

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

/** Pipeline card: a "thinking"-style header (pulsing dot while the agents work), then the progress rail, the stepper with
 *  per-stage activity and the raw event log. Open while the run is active, collapsed by itself once it finishes. */
export default function RunTimeline({ run, events }: { run: RunInfo; events: RunEvent[] }) {
  const status = run.status;
  const states = stageStates(events, status);
  const { total } = stageTimings(events);
  const meta = runStatusMeta(status);
  const line = statusLine(run, events);
  const live = status === "RUNNING" || status === "CANCEL_REQUESTED";
  const active = isActive(run) || status === "AWAITING_REVIEW";
  const done = STAGES.filter(([id]) => states.get(id) === "done").length;
  const [manual, setManual] = useState<boolean | null>(null);
  const was = useRef(active);
  useEffect(() => { if (was.current && !active) setManual(null); was.current = active; }, [active]);  // finished: fall back to collapsed
  const open = manual ?? active;
  const body = useId();
  const stage = stageLabel(run.current_stage);
  const dot = live ? "info live" : status === "SUCCEEDED" ? "ok" : status === "FAILED" ? "bad" : meta.tone === "warn" ? "warn" : "neutral";
  return (
    <div className="card timeline-card">
      <div className="card-head tl-head">
        <h2>
          <button className="tl-toggle" aria-expanded={open} aria-controls={body} onClick={() => setManual(!open)}>
            <span className="chevron" aria-hidden="true">{open ? "▾" : "▸"}</span>
            <span className={`dot ${dot}`} aria-hidden="true" />
            <span>Agent pipeline</span>
            {live && <span className="tl-working muted">{stage ? `${stage} working…` : "Working…"}</span>}
          </button>
          <span className={`pill ${meta.tone}`} role="status">{meta.label}</span>
        </h2>
        <span className="muted mono">
          {done}/{STAGES.length} stages{!live && total !== null && total > 0 && <> · total {fmtDuration(total)}</>}
        </span>
      </div>
      {line && <div className={`banner ${status === "FAILED" ? "error" : status === "AWAITING_REVIEW" || status === "CANCEL_REQUESTED" ? "warn" : ""}`} role={status === "FAILED" ? "alert" : "status"}>{line}</div>}
      <div className="rail" aria-hidden="true">
        {STAGES.map(([id]) => <span key={id} className={states.get(id) ?? "pending"} />)}
      </div>
      {open && (
        <div id={body}>
          <AgentActivity events={events} live={live} activeStage={run.current_stage ?? null} status={status} />
          <details className="event-log">
            <summary>Raw event log ({events.length})</summary>
            <ul className="log">
              {events.map((e) => (
                <li key={e.seq}><code>{e.stage}</code> <span className={e.kind}>{describe(e)}</span></li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}
