import type { RunEvent, ToolCallTrace } from "../types";
import { useId, useState } from "react";
import { STAGES, fmtDuration, stageOutcome, stageStates, stageTimings, type StageState } from "../run-utils";
import { Thinking, ToolCall } from "./ChatMessageView";
import Markdown from "./Markdown";

type Item =
  | { kind: "thinking"; key: string; agent: string; text: string; done: boolean }
  | { kind: "tool"; key: string; call: ToolCallTrace }
  | { kind: "message"; key: string; agent: string; text: string }
  | { kind: "note"; key: string; text: string; tone?: "warn" | "bad" };

const AGENT_NAMES: Record<string, string> = {
  reader_agent: "Reader", mapper_agent: "Mapper", graphrag_agent: "GraphRAG", precedent_agent: "Precedent",
  data_analyst_agent: "Data Analyst", evaluator_agent: "Evaluator", orchestrator_agent: "Orchestrator",
};
const agentName = (agent: unknown) => AGENT_NAMES[String(agent)] ?? String(agent ?? "Agent");

/** Folds run events into per-stage activity: thinking streams (latest text wins), tool calls with their results. */
export function activityByStage(events: RunEvent[], live: boolean): Map<string, Item[]> {
  const stages = new Map<string, Item[]>();
  const thinking = new Map<string, Extract<Item, { kind: "thinking" }>>();
  const open = new Map<string, Extract<Item, { kind: "tool" }>>(); // by call id, else by tool name
  const push = (stage: string, item: Item) => {
    if (!stages.has(stage)) stages.set(stage, []);
    stages.get(stage)!.push(item);
  };
  for (const e of events) {
    const p = e.payload as Record<string, any>;
    switch (e.kind) {
      case "thinking": {
        const key = `${e.stage}:${p.stream ?? p.agent}`;
        const known = thinking.get(key);
        if (known) { known.text = String(p.text ?? known.text); known.done = Boolean(p.done); break; }
        const item = { kind: "thinking" as const, key, agent: agentName(p.agent), text: String(p.text ?? ""), done: Boolean(p.done) };
        thinking.set(key, item);
        push(e.stage, item);
        break;
      }
      case "tool_call": {
        const call: ToolCallTrace = { id: String(p.id ?? e.seq), name: String(p.tool), args: p.args, status: live ? "running" : "ok" };
        const item = { kind: "tool" as const, key: `tool-${e.seq}`, call };
        open.set(String(p.id ?? p.tool), item);
        push(e.stage, item);
        break;
      }
      case "tool_result": {
        const item = open.get(String(p.id ?? p.tool)) ?? open.get(String(p.tool));
        const status = p.status ?? (p.output?.status === "error" ? "error" : "ok");
        if (item) {
          item.call = { ...item.call, output: p.output, status, duration_ms: p.duration_ms ?? null };
          open.delete(String(p.id ?? p.tool));
        } else {
          push(e.stage, { kind: "tool", key: `tool-${e.seq}`, call: { id: String(e.seq), name: String(p.tool), args: undefined, output: p.output, status } });
        }
        break;
      }
      case "agent_message":
        push(e.stage, { kind: "message", key: `msg-${e.seq}`, agent: agentName(p.agent), text: String(p.text ?? "") });
        break;
      case "extraction_retry":
        push(e.stage, { kind: "note", key: `note-${e.seq}`, tone: "warn",
          text: `Extraction attempt ${p.attempt} of ${p.of} failed: ${p.error}${p.attempt < p.of ? " — retrying" : ""}` });
        break;
      case "control_canonicalised":
        push(e.stage, { kind: "note", key: `note-${e.seq}`, tone: p.needs_review ? "warn" : undefined, text: canonicalisedText(p) });
        break;
      case "document_classified":
        push(e.stage, { kind: "note", key: `note-${e.seq}`, text: `Classified as ${String(p.doc_type).replaceAll("_", " ")} (${Math.round(Number(p.confidence ?? 0) * 100)}%)${p.reason ? `: ${p.reason}` : ""}` });
        break;
      case "run_failed":
        push(e.stage, { kind: "note", key: `note-${e.seq}`, tone: "bad", text: `Failed: ${p.error}` });
        break;
      default:
        break;
    }
  }
  if (!live) open.forEach((item) => { item.call = { ...item.call, status: item.call.output === undefined ? "cancelled" : item.call.status }; });
  return stages;
}

/** "maintain_ferr → control:maintain-ferr (MATCH via gemini, 93%)", plus a review flag. */
function canonicalisedText(p: Record<string, any>): string {
  const pct = typeof p.confidence === "number" && p.confidence > 0 ? `, ${Math.round(p.confidence * 100)}%` : "";
  const review = p.needs_review ? " — needs review" : "";
  return `Control “${String(p.term ?? "")}” → ${String(p.control_id ?? "")} (${String(p.decision ?? "")} via ${String(p.method ?? "")}${pct})${review}`;
}

const LABELS = new Map([...STAGES, ["pipeline", "Pipeline"] as [string, string]]);

const STATE_TEXT: Record<StageState, string> = {
  pending: "not started", active: "in progress", done: "done", failed: "failed", cancelled: "cancelled here", paused: "paused for review",
};
const DOT: Record<StageState, string> = { pending: "pending", active: "info live", done: "ok", failed: "bad", cancelled: "warn", paused: "warn" };

function Step({ id, name, sub, state, outcome, time, items, live, open, onToggle }: {
  id: string; name: string; sub: string; state: StageState; outcome: string; time: string; items: Item[]; live: boolean;
  open: boolean; onToggle: () => void;
}) {
  const panel = useId();
  const expandable = items.length > 0;
  const tools = items.filter((i) => i.kind === "tool").length;
  const note = state === "failed" ? "Failed here" : state === "cancelled" ? "Cancelled here" : state === "paused" ? "Waiting for review" : "";
  const head = (
    <>
      <span className={`dot ${DOT[state]}`} aria-hidden="true" />
      <span className="step-name">{name}<span className="sr-only">: {STATE_TEXT[state]}</span></span>
      <span className="step-sub mono">{sub}</span>
      <span className="step-outcome">
        {outcome}{outcome && note ? " · " : ""}{note && <b>{note}</b>}
        {expandable && tools > 0 && <span className="step-tools mono">{outcome || note ? " · " : ""}{tools} tool call{tools === 1 ? "" : "s"}</span>}
      </span>
      <span className="step-time mono">{time}</span>
      <span className="chevron step-chev" aria-hidden="true">{expandable ? (open ? "▾" : "▸") : ""}</span>
    </>
  );
  return (
    <li className={`step ${state}`} data-stage={id}>
      {expandable
        ? <button className="step-head" aria-expanded={open} aria-controls={panel} onClick={onToggle}>{head}</button>
        : <div className="step-head static">{head}</div>}
      {expandable && open && (
        <div id={panel} className="step-body" aria-live={state === "active" ? "polite" : undefined}>
          {items.map((item) => {
            if (item.kind === "thinking") return <Thinking key={item.key} text={item.text} live={live && !item.done} label={`${item.agent} thinking`} />;
            if (item.kind === "tool") return <ul key={item.key} className="tool-calls"><ToolCall call={item.call} /></ul>;
            if (item.kind === "message") return <div key={item.key} className="activity-message"><span className="agent-tag mono">{item.agent}</span><Markdown text={item.text} /></div>;
            return <p key={item.key} className={`activity-note ${item.tone ?? ""}`}>{item.text}</p>;
          })}
        </div>
      )}
    </li>
  );
}

/** CI/CD-style stepper: one row per pipeline stage with status dot, one-line outcome and duration; rows expand to the
 *  agent's thinking, tool calls (input/output) and messages. The active stage is open by default. */
export default function AgentActivity({ events, live, activeStage, status }: { events: RunEvent[]; live: boolean; activeStage?: string | null; status: string }) {
  const [manual, setManual] = useState<Record<string, boolean>>({});
  const activity = activityByStage(events, live);
  const states = stageStates(events, status);
  const { per } = stageTimings(events);
  const extra = [...activity.keys()].filter((s) => !STAGES.some(([id]) => id === s));
  const rows = [...STAGES.map(([id, label]) => ({ id, label })), ...extra.map((id) => ({ id, label: LABELS.get(id) ?? id }))];
  if (!events.length && live) return <p className="muted activity-wait" role="status">Waiting for the first agent to start…</p>;
  const withItems = rows.filter(({ id }) => (activity.get(id) ?? []).length > 0).map(({ id }) => id);
  const allOpen = withItems.length > 0 && withItems.every((id) => manual[id] === true);
  const setAll = (value: boolean) => setManual(Object.fromEntries(withItems.map((id) => [id, value])));
  return (
    <section className="agent-activity" aria-label="Agent activity">
      {withItems.length > 0 && (
        <div className="activity-tools">
          <button className="link" onClick={() => setAll(!allOpen)}>{allOpen ? "Collapse all stages" : "Expand all stages (thinking, tool calls)"}</button>
        </div>
      )}
      <ol className="steps" aria-label="Pipeline stages">
        {rows.map(({ id, label }) => {
          const state: StageState = states.get(id) ?? (id === "pipeline" && status === "FAILED" ? "failed" : "pending");
          const [name, ...rest] = label.split(" · ");
          const current = state === "active" || (live && id === activeStage);
          const open = manual[id] ?? (current || state === "failed");
          const ms = per.get(id);
          const time = ms === undefined ? "" : `${fmtDuration(ms)}${state === "active" ? " so far" : ""}`;
          return (
            <Step key={id} id={id} name={name} sub={rest.join(" · ")} state={state} outcome={stageOutcome(id, events)} time={time}
              items={activity.get(id) ?? []} live={live} open={open} onToggle={() => setManual({ ...manual, [id]: !open })} />
          );
        })}
      </ol>
    </section>
  );
}
