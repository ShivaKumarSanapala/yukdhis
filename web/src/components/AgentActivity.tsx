import type { RunEvent, ToolCallTrace } from "../types";
import { STAGES } from "../run-utils";
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

/** Live view of what each pipeline agent is thinking and which tools it calls (inputs and outputs). */
export default function AgentActivity({ events, live, activeStage }: { events: RunEvent[]; live: boolean; activeStage?: string | null }) {
  const stages = activityByStage(events, live);
  if (!stages.size) return live ? <p className="muted activity-wait" role="status">Waiting for the first agent to start…</p> : null;
  const order = [...STAGES.map(([id]) => id), ...[...stages.keys()].filter((s) => !LABELS.has(s) || s === "pipeline")];
  return (
    <section className="agent-activity" aria-label="Agent activity">
      <h3>Agent activity</h3>
      {order.filter((s, i) => stages.has(s) && order.indexOf(s) === i).map((stage) => {
        const items = stages.get(stage)!;
        const current = live && stage === activeStage;
        const tools = items.filter((i) => i.kind === "tool").length;
        return (
          <details key={stage} className="activity-stage" open={current || undefined}>
            <summary>
              <b>{(LABELS.get(stage) ?? stage).split(" · ")[0]}</b>
              <span className="muted"> · {tools} tool call{tools === 1 ? "" : "s"}</span>
              {current && <span className="pill info" role="status">working…</span>}
            </summary>
            <div className="activity-items" aria-live={current ? "polite" : undefined}>
              {items.map((item) => {
                if (item.kind === "thinking") return <Thinking key={item.key} text={item.text} live={live && !item.done} label={`${item.agent} thinking`} />;
                if (item.kind === "tool") return <ul key={item.key} className="tool-calls"><ToolCall call={item.call} /></ul>;
                if (item.kind === "message") return <div key={item.key} className="activity-message"><span className="muted">{item.agent}:</span><Markdown text={item.text} /></div>;
                return <p key={item.key} className={`activity-note ${item.tone ?? ""}`}>{item.text}</p>;
              })}
            </div>
          </details>
        );
      })}
    </section>
  );
}
