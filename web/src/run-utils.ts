import type { RunEvent, RunInfo } from "./types";
import type { StatusMeta } from "./report-utils";

export const STAGES: [string, string][] = [
  ["reader", "Reader · Document AI + Gemini"],
  ["mapper", "Mapper · BIAN entity resolution"],
  ["graphrag", "GraphRAG · Spanner Graph traversal"],
  ["precedent", "Precedent · Memory Bank"],
  ["data_analyst", "Data Analyst · guarded SQL"],
  ["evaluator", "Evaluator · deterministic checks"],
  ["orchestrator", "Orchestrator · report & actions"],
];
export const stageLabel = (id: string | null | undefined) =>
  id === "review" ? "Human review" : (STAGES.find(([s]) => s === id)?.[1].split(" · ")[0] ?? id ?? "");

export const ACTIVE = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);
export const isActive = (run: Pick<RunInfo, "status">) => ACTIVE.has(run.status);
export const canCancel = (run: RunInfo) => Boolean(run.is_mine) && (ACTIVE.has(run.status) || run.status === "AWAITING_REVIEW") && run.status !== "CANCEL_REQUESTED";
export const canRerun = (run: RunInfo) => (Boolean(run.is_mine) || run.created_by === "watcher") && !ACTIVE.has(run.status) && run.status !== "AWAITING_REVIEW";

const RUN_STATUS: Record<string, StatusMeta> = {
  QUEUED: { label: "Queued", tone: "neutral", hint: "Waiting for a free slot or for another run of the same document." },
  RUNNING: { label: "Running", tone: "info", hint: "" },
  CANCEL_REQUESTED: { label: "Cancelling", tone: "warn", hint: "Stops at the next step." },
  AWAITING_REVIEW: { label: "Needs review", tone: "warn", hint: "Rules were extracted; approve or reject them to continue." },
  SUCCEEDED: { label: "Done", tone: "ok", hint: "" },
  FAILED: { label: "Failed", tone: "bad", hint: "" },
  CANCELLED: { label: "Cancelled", tone: "neutral", hint: "" },
  REJECTED: { label: "Rejected", tone: "neutral", hint: "The extracted rules were rejected in review." },
  STARTING: { label: "Starting", tone: "info", hint: "" },
};
export const runStatusMeta = (status: string): StatusMeta => RUN_STATUS[status] ?? { label: status.replaceAll("_", " "), tone: "neutral", hint: "" };

export const SOURCE_LABEL: Record<string, string> = { upload: "Upload", sample: "Sample", text: "Pasted text", url: "Link", watcher: "Watcher" };
export const runTitle = (run: RunInfo) => run.filename ?? (run.document?.filename as string | undefined) ?? run.run_id;

const parse = (value?: string | null) => { const t = value ? Date.parse(value) : NaN; return Number.isNaN(t) ? null : t; };
export const fmtDuration = (ms: number) => {
  const s = Math.max(0, Math.round(ms / 1000));
  if (ms < 10_000) return `${(Math.max(0, ms) / 1000).toFixed(1)}s`;
  if (s < 3600) return `${Math.floor(s / 60) ? `${Math.floor(s / 60)}m ` : ""}${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};
/** Running time (started → finished/now); queued runs show how long they have waited. */
export function elapsed(run: RunInfo, now: number): string {
  const start = parse(run.started_at) ?? parse(run.created_at);
  if (start === null) return "";
  const end = parse(run.finished_at) ?? (ACTIVE.has(run.status) ? now : parse(run.updated_at) ?? now);
  return fmtDuration(end - start);
}

// ---------------------------------------------------------------- readable timeline

type Out = Record<string, any>;
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Stage outputs, from deterministic stage_completed events or agentic tool results (truncated ones are skipped). */
function outputs(events: RunEvent[]): Out[] {
  const out: Out[] = [];
  for (const e of events) {
    const p = e.payload as Out;
    const value = e.kind === "stage_completed" ? p.output : e.kind === "tool_result" ? p.output : null;
    if (value && typeof value === "object" && !value.truncated && value.status !== "error") out.push(value);
  }
  return out;
}

/** One-line outcome per stage, derived from its events. */
export function stageOutcome(stage: string, events: RunEvent[]): string {
  const mine = events.filter((e) => e.stage === stage);
  const parts: string[] = [];
  const outs = outputs(mine);
  const skipped = mine.find((e) => e.kind === "stage_skipped");
  const classified = mine.find((e) => e.kind === "document_classified")?.payload as Out | undefined;
  const applicability = mine.find((e) => e.kind === "applicability")?.payload as Out | undefined;
  if (classified?.doc_type) {
    const pct = typeof classified.confidence === "number" ? ` (${Math.round(classified.confidence * 100)}%)` : "";
    parts.push(`classified as ${String(classified.doc_type).replaceAll("_", " ").toLowerCase()}${pct}`);
  }
  if (applicability) {
    const reason = Array.isArray(applicability.reasons) && applicability.reasons[0] ? `: ${applicability.reasons[0]}` : "";
    parts.push(applicability.applicable === true ? "applies to this bank" : applicability.applicable === false ? `not applicable${reason}` : `applicability unknown${reason}`);
  }
  for (const o of outs) {
    if (stage === "reader") {
      if (Array.isArray(o.rules) && o.policy) {
        const warnings = (o.issues ?? []).filter((i: Out) => i.severity === "WARNING").length;
        parts.push(`${plural(o.rules.length, "rule")} extracted${warnings ? `, ${plural(warnings, "warning")}` : ""}`);
      } else if (typeof o.chunks === "number") parts.push(`${plural(o.chunks, "section")} parsed`);
    } else if (stage === "mapper" && Array.isArray(o.mappings)) {
      const unresolved = o.mappings.filter((m: Out) => m.scope_status === "UNRESOLVED").length;
      parts.push(unresolved ? `scope unresolved for ${plural(unresolved, "rule")}` : `${plural(o.mappings.length, "rule")} mapped`);
      const decisions = o.mappings.flatMap((m: Out) => (Array.isArray(m.control_decisions) ? m.control_decisions : []));
      const created = new Set(decisions.filter((d: Out) => d?.decision === "NEW").map((d: Out) => d.control_id)).size;
      if (created) parts.push(`${plural(created, "new control")} pending review`);
    } else if (stage === "graphrag" && Array.isArray(o.rules)) {
      const products = new Set(o.rules.flatMap((r: Out) => r.products ?? []));
      const gaps = o.rules.reduce((n: number, r: Out) => n + Object.keys(r.control_gaps ?? {}).length, 0);
      parts.push(`${plural(products.size, "product")} in scope${gaps ? `, ${plural(gaps, "control gap")}` : ""}`);
    } else if (stage === "precedent" && Array.isArray(o.prior_reports)) {
      parts.push(`${plural(o.prior_reports.length, "prior report")}, ${plural((o.open_actions ?? []).length, "open action")}`);
    } else if (stage === "data_analyst" && Array.isArray(o.rows)) {
      const loans = o.rows.reduce((n: number, r: Out) => n + (Number(r.loans) || 0), 0);
      parts.push(loans ? `${loans.toLocaleString("en-IN")} loans profiled` : "no loans in scope");
    } else if (stage === "evaluator" && Array.isArray(o.results)) {
      const gaps = o.results.filter((r: Out) => r.status === "GAP");
      const violations = gaps.reduce((n: number, r: Out) => n + (Number(r.violations) || 0), 0);
      const upcoming = o.results.filter((r: Out) => r.status === "UPCOMING").length;
      parts.push(gaps.length ? `${plural(gaps.length, "check")} with gaps, ${plural(violations, "violation")}`
        : upcoming ? `${plural(upcoming, "check")} upcoming` : `no gaps in ${plural(o.results.length, "check")}`);
    } else if (stage === "orchestrator" && typeof o.actions === "number") {
      const loans = Number(o.summary?.loans_with_violations) || 0;
      if (loans) parts.push(`${plural(loans, "loan")} in violation`);
      parts.push(`${plural(o.actions, "action")} proposed`);
    }
  }
  const reviewed = mine.find((e) => e.kind === "rules_reviewed")?.payload as Out | undefined;
  if (reviewed) {
    const edits = [reviewed.edited?.length ? `${reviewed.edited.length} edited` : "", reviewed.removed?.length ? `${reviewed.removed.length} removed` : ""].filter(Boolean);
    parts.push(`${plural(reviewed.rules ?? 0, "rule")} approved${edits.length ? ` (${edits.join(", ")})` : ""}`);
  } else if (skipped && !parts.length) parts.push("skipped: rules supplied after review");
  return [...new Set(parts)].join(" · ");
}

export type StageState = "pending" | "active" | "done" | "failed" | "cancelled" | "paused";

/** Where the run is (or stopped) by stage. */
export function stageStates(events: RunEvent[], status: string): Map<string, StageState> {
  const seen = new Set(events.filter((e) => e.stage !== "pipeline").map((e) => e.stage));
  const last = [...events].reverse().find((e) => STAGES.some(([s]) => s === e.stage))?.stage;
  const failed = events.find((e) => e.kind === "run_failed")?.payload as Out | undefined;
  const cancelled = events.find((e) => e.kind === "run_cancelled")?.payload as Out | undefined;
  const states = new Map<string, StageState>();
  for (const [id] of STAGES) {
    let state: StageState = seen.has(id) ? "done" : "pending";
    if (id === last) {
      if (status === "RUNNING" || status === "CANCEL_REQUESTED") state = "active";
      else if (status === "FAILED") state = "failed";
      else if (status === "CANCELLED") state = "cancelled";
      else if (status === "AWAITING_REVIEW") state = "paused";
    }
    if (failed?.stage === id) state = "failed";
    if (cancelled?.stage === id && status === "CANCELLED") state = "cancelled";
    states.set(id, state);
  }
  return states;
}

const WAITS = new Set(["run_queued", "run_waiting", "rules_extracted", "awaiting_review"]);

/** Working time per stage: each gap between consecutive events counts for the earlier event's stage. Time spent
 *  queued or paused for review counts for no stage and is left out of the total. */
export function stageTimings(events: RunEvent[]) {
  const stamped = events.map((e) => ({ e, t: Date.parse(e.ts) })).filter((x) => !Number.isNaN(x.t));
  const per = new Map<string, number>();
  let total: number | null = stamped.length ? 0 : null;
  stamped.forEach(({ e, t }, i) => {
    if (e.stage !== "pipeline" && !per.has(e.stage)) per.set(e.stage, 0);
    const next = stamped[i + 1];
    if (!next || WAITS.has(e.kind)) return;
    const dt = Math.max(0, next.t - t);
    total = (total ?? 0) + dt;
    if (e.stage !== "pipeline") per.set(e.stage, (per.get(e.stage) ?? 0) + dt);
  });
  return { per, total };
}

/** Plain-language line for queued / waiting / paused / ended runs. */
export function statusLine(run: RunInfo, events: RunEvent[]): string {
  const latest = (kind: string) => [...events].reverse().find((e) => e.kind === kind)?.payload as Out | undefined;
  switch (run.status) {
    case "QUEUED": {
      const waiting = latest("run_waiting");
      const queued = latest("run_queued");
      if (waiting && !queued) return `Waiting for run ${waiting.blocking_run_id} on the same document, so its policy is not mapped twice at once.`;
      if (run.queued_position) return `Queued at position ${run.queued_position}. ${queued?.limit ?? "A few"} analyses run at once.`;
      return events.some((e) => e.kind === "run_waiting") ? "Waiting for another run of the same document." : "Queued; starts shortly.";
    }
    case "CANCEL_REQUESTED": return "Cancelling: the run stops at its next step.";
    case "AWAITING_REVIEW": return "Rules were extracted. Review them below, then approve to continue or reject to stop.";
    case "REJECTED": return "The extracted rules were rejected; no analysis was run.";
    case "CANCELLED": {
      const stage = latest("run_cancelled")?.stage;
      return `Cancelled${stage ? ` during ${stageLabel(stage)}` : ""}. Nothing after that point was written.`;
    }
    case "FAILED": {
      const failed = latest("run_failed");
      return `Failed${failed?.stage ? ` at ${stageLabel(failed.stage)}` : ""}: ${failed?.error ?? run.error ?? "unknown error"}`;
    }
    default: return "";
  }
}
