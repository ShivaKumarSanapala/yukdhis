import { useState } from "react";
import { EmptyState } from "./Motif";
import { formatDateTime } from "../format";
import type { RunInfo } from "../types";
import { SOURCE_LABEL, canCancel, canRerun, elapsed, isActive, runStatusMeta, runTitle, stageLabel } from "../run-utils";

export const FILTERS = {
  all: { label: "All runs", status: "" },
  active: { label: "In progress", status: "QUEUED,RUNNING,CANCEL_REQUESTED" },
  review: { label: "Needs review", status: "AWAITING_REVIEW" },
  done: { label: "Done", status: "SUCCEEDED" },
  stopped: { label: "Failed or stopped", status: "FAILED,CANCELLED,REJECTED" },
} as const;
export type FilterKey = keyof typeof FILTERS;

interface Handlers {
  onOpen: (run: RunInfo) => void;
  onCancel: (run: RunInfo) => void;
  onRerun: (run: RunInfo, asOf?: string) => void;
  onCompare: (run: RunInfo) => void;
}

function RunRow({ run, selected, now, busy, h }: { run: RunInfo; selected: boolean; now: number; busy: boolean; h: Handlers }) {
  const [dateOpen, setDateOpen] = useState(false);
  const [asOf, setAsOf] = useState(run.as_of ?? "");
  const meta = runStatusMeta(run.status);
  const stage = run.status === "AWAITING_REVIEW" ? "Human review"
    : run.status === "RUNNING" || run.status === "CANCEL_REQUESTED" ? stageLabel(run.current_stage) || "Starting" : "";
  const title = runTitle(run);
  return (
    <li className={`run-row${selected ? " selected" : ""}`} aria-current={selected ? "true" : undefined}>
      <div className="run-main">
        <span className={`pill ${meta.tone}`} title={meta.hint || undefined}>{meta.label}{run.status === "QUEUED" && run.queued_position ? ` #${run.queued_position}` : ""}</span>
        <button className="link run-title" onClick={() => h.onOpen(run)} aria-label={`Open run ${title}`}>{title}</button>
      </div>
      <div className="run-meta muted">
        <span>{SOURCE_LABEL[run.source_kind ?? ""] ?? run.source_kind ?? "Upload"}</span>
        <span>by {run.is_mine ? "you" : run.created_by ?? "unknown"}</span>
        {run.as_of && <span>as of {formatDateTime(run.as_of)}</span>}
        {run.created_at && <span><time dateTime={run.created_at} title={run.created_at}>{formatDateTime(run.created_at)}</time></span>}
        <span>{run.mode}</span>
        {stage && <span>{stage}</span>}
        {elapsed(run, now) && <span>{isActive(run) && run.status === "QUEUED" ? "waiting " : ""}{elapsed(run, now)}</span>}
      </div>
      <div className="buttons run-actions">
        <button onClick={() => h.onOpen(run)} aria-label={`Open ${title}`}>Open</button>
        {canCancel(run) && <button className="danger" disabled={busy} onClick={() => h.onCancel(run)} aria-label={`Cancel ${title}`}>Cancel</button>}
        {canRerun(run) && run.status !== "SUCCEEDED" && <button disabled={busy} onClick={() => h.onRerun(run)} aria-label={`Retry ${title}`}>Retry</button>}
        {canRerun(run) && <button disabled={busy} aria-expanded={dateOpen} onClick={() => setDateOpen((o) => !o)}>Re-run with another date</button>}
        {run.report_id && run.previous_report_id && <button onClick={() => h.onCompare(run)}>Compare with previous run</button>}
      </div>
      {dateOpen && (
        <form className="row rerun-date" onSubmit={(e) => { e.preventDefault(); setDateOpen(false); h.onRerun(run, asOf); }}>
          <label>New analysis date
            <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} required />
          </label>
          <button className="primary" type="submit" disabled={!asOf || busy}>Start re-run</button>
          <button type="button" onClick={() => setDateOpen(false)}>Close</button>
        </form>
      )}
    </li>
  );
}

/** Recent runs (newest first) with watcher-started circulars on top. */
export default function RunsBoard({ runs, inbox, selectedId, now, busyId, filter, mine, onFilter, onMine, h }: {
  runs: RunInfo[]; inbox: RunInfo[]; selectedId: string | null; now: number; busyId: string; filter: FilterKey; mine: boolean;
  onFilter: (f: FilterKey) => void; onMine: (m: boolean) => void; h: Handlers;
}) {
  const active = runs.filter(isActive).length;
  return (
    <section className="card runs-board no-print" aria-labelledby="runs-title">
      {inbox.length > 0 && (
        <div className="inbox" aria-labelledby="inbox-title">
          <h3 id="inbox-title">New circulars from the watcher</h3>
          <ul className="run-list">
            {inbox.map((r) => <RunRow key={r.run_id} run={r} selected={r.run_id === selectedId} now={now} busy={busyId === r.run_id} h={h} />)}
          </ul>
        </div>
      )}
      <div className="row between">
        <h2 id="runs-title">Analysis runs {active > 0 && <span className="muted">· {active} in progress</span>}</h2>
        <div className="row board-filters">
          <label>Show
            <select value={filter} onChange={(e) => onFilter(e.target.value as FilterKey)}>
              {Object.entries(FILTERS).map(([k, f]) => <option key={k} value={k}>{f.label}</option>)}
            </select>
          </label>
          <label className="inline"><input type="checkbox" checked={mine} onChange={(e) => onMine(e.target.checked)} /> Only mine</label>
        </div>
      </div>
      {runs.length ? (
        <ul className="run-list" aria-label="Analysis runs">
          {runs.map((r) => <RunRow key={r.run_id} run={r} selected={r.run_id === selectedId} now={now} busy={busyId === r.run_id} h={h} />)}
        </ul>
      ) : <EmptyState title={filter !== "all" || mine ? "No runs match this filter" : "No runs yet"}
          hint={filter !== "all" || mine ? "Try another filter." : "Start an analysis above to see its agent pipeline here."} />}
    </section>
  );
}
