import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { ImpactReport, ReportListItem } from "../types";
import ReportView from "./ReportView";
import CompareView from "./CompareView";
import When from "./When";
import { OverallBadge } from "./StatusBadges";
import { EmptyState, Skeleton } from "./Motif";
import { downloadReportList, inr, overallKey, overallMeta } from "../report-utils";

const time = (value: string) => { const t = Date.parse(value); return Number.isNaN(t) ? 0 : t; };
const FILTER_KEY = "yukdhis.reports.filters";
const STATUSES = ["NON_COMPLIANT", "COMPLIANT", "INCONCLUSIVE", "UPCOMING"] as const;
const STATUS_LABEL: Record<string, string> = { NON_COMPLIANT: "Non compliant", COMPLIANT: "Compliant", INCONCLUSIVE: "Cannot assess", UPCOMING: "Upcoming" };

type SortKey = "created" | "status" | "violations" | "penalty" | "policy";
interface Filters { q: string; statuses: string[]; from: string; to: string; mode: string; sort: SortKey; dir: "asc" | "desc" }
const DEFAULTS: Filters = { q: "", statuses: [], from: "", to: "", mode: "", sort: "created", dir: "desc" };

function loadFilters(): Filters {
  try { return { ...DEFAULTS, ...JSON.parse(sessionStorage.getItem(FILTER_KEY) ?? "{}") }; } catch { return DEFAULTS; }
}

const unassessedOf = (r: ReportListItem) => ["INCONCLUSIVE", "UPCOMING"].includes(overallKey(r.summary));

function SortTh({ k, children, num, f, onSort }: { k: SortKey; children: string; num?: boolean; f: Filters; onSort: (k: SortKey) => void }) {
  const sort = f.sort === k ? (f.dir === "asc" ? "ascending" : "descending") : "none";
  return (
    <th scope="col" className={num ? "num" : undefined} aria-sort={sort}>
      <button className="th-sort" onClick={() => onSort(k)}>{children}<span aria-hidden="true" className="sort-ind">{f.sort === k ? (f.dir === "asc" ? "↑" : "↓") : ""}</span></button>
    </th>
  );
}

export default function ReportsPanel({ onOpenReportChange, initialReportId }: {
  onOpenReportChange?: (reportId: string | null) => void; initialReportId?: string | null;
}) {
  const [items, setItems] = useState<ReportListItem[]>([]);
  const [open, setOpen] = useState<ImpactReport | null>(null);
  const openId = open?.report_id ?? null;
  useEffect(() => { onOpenReportChange?.(openId); }, [openId, onOpenReportChange]);
  const [picked, setPicked] = useState<string[]>([]);
  const [pair, setPair] = useState<[ImpactReport, ImpactReport] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [f, setF] = useState<Filters>(loadFilters);
  const [statusOpen, setStatusOpen] = useState(false);
  const statusRef = useRef<HTMLDivElement>(null);
  useEffect(() => { try { sessionStorage.setItem(FILTER_KEY, JSON.stringify(f)); } catch { /* private mode */ } }, [f]);
  useEffect(() => { api.reports().then(setItems).catch((e) => setError(e.message)).finally(() => setLoaded(true)); }, []);
  useEffect(() => {  // "Open in Reports" from the Analyze tab
    if (initialReportId) api.report(initialReportId).then(setOpen).catch((e) => setError(e.message));
  }, [initialReportId]);
  useEffect(() => {
    if (!statusOpen) return;
    const close = (e: MouseEvent) => { if (!statusRef.current?.contains(e.target as Node)) setStatusOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [statusOpen]);

  const rows = useMemo(() => {
    const q = f.q.trim().toLowerCase();
    const from = f.from ? new Date(`${f.from}T00:00:00`).getTime() : null;
    const to = f.to ? new Date(`${f.to}T00:00:00`).getTime() + 86_400_000 : null;
    const out = items.filter((r) => {
      if (q && !`${r.title} ${r.report_id}`.toLowerCase().includes(q)) return false;
      if (f.statuses.length && !f.statuses.includes(overallKey(r.summary))) return false;
      if (f.mode && r.mode !== f.mode) return false;
      const t = time(r.created_at);
      if (from !== null && t < from) return false;
      if (to !== null && t >= to) return false;
      return true;
    });
    const dir = f.dir === "asc" ? 1 : -1;
    const val = (r: ReportListItem): number | string => {
      switch (f.sort) {
        case "created": return time(r.created_at);
        case "status": return overallMeta(r.summary).label;
        case "violations": return unassessedOf(r) ? -1 : r.summary.loans_with_violations;
        case "penalty": return unassessedOf(r) ? -1 : r.summary.statutory_penalty_inr;
        default: return r.title.toLowerCase();
      }
    };
    return [...out].sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
  }, [items, f]);

  const modes = useMemo(() => [...new Set(items.map((r) => r.mode))].sort(), [items]);
  const filtered = Boolean(f.q || f.statuses.length || f.from || f.to || f.mode);
  const set = (patch: Partial<Filters>) => setF((p) => ({ ...p, ...patch }));
  const clear = () => setF((p) => ({ ...DEFAULTS, sort: p.sort, dir: p.dir }));
  const sortBy = (sort: SortKey) => setF((p) => ({ ...p, sort, dir: p.sort === sort && p.dir === "desc" ? "asc" : "desc" }));
  const toggleStatus = (s: string) => set({ statuses: f.statuses.includes(s) ? f.statuses.filter((x) => x !== s) : [...f.statuses, s] });

  const openReport = (id: string) => api.report(id).then(setOpen).catch((e) => setError(e.message));
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= 2 ? [p[1], id] : [...p, id]));
  const compare = async () => {
    setBusy(true); setError("");
    try {
      const [a, b] = await Promise.all(picked.map((id) => api.report(id)));
      // earlier run on the left so changes read as "before → after"
      setPair(time(a.generated_at) <= time(b.generated_at) ? [a, b] : [b, a]);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  if (pair) return <section><button className="ghost back no-print" onClick={() => setPair(null)}>← All reports</button><CompareView a={pair[0]} b={pair[1]} /></section>;
  if (open) return <section><button className="ghost back no-print" onClick={() => setOpen(null)}>← All reports</button><ReportView report={open} onChange={setOpen} /></section>;

  const pickedItems = items.filter((r) => picked.includes(r.report_id));
  return (
    <section className="card flush">
      <div className="card-head pad">
        <h2>Impact reports</h2>
        <span className="muted mono" role="status">Showing {rows.length} of {items.length} reports</span>
      </div>
      <div className="filters no-print" role="search" aria-label="Filter reports">
        <label className="search">
          <span className="sr-only">Search reports</span>
          <input type="search" placeholder="Search policy or report id" value={f.q} onChange={(e) => set({ q: e.target.value })} />
        </label>
        <div className="menu" ref={statusRef}>
          <button aria-haspopup="true" aria-expanded={statusOpen} onClick={() => setStatusOpen(!statusOpen)}
            onKeyDown={(e) => { if (e.key === "Escape") setStatusOpen(false); }}>
            Status{f.statuses.length ? ` (${f.statuses.length})` : ""} <span aria-hidden="true">▾</span>
          </button>
          {statusOpen && (
            <div className="menu-pop" role="group" aria-label="Filter by status" onKeyDown={(e) => { if (e.key === "Escape") setStatusOpen(false); }}>
              {STATUSES.map((s) => (
                <label key={s} className="inline">
                  <input type="checkbox" checked={f.statuses.includes(s)} onChange={() => toggleStatus(s)} /> {STATUS_LABEL[s]}
                </label>
              ))}
            </div>
          )}
        </div>
        <label className="inline-field">From <input type="date" value={f.from} max={f.to || undefined} onChange={(e) => set({ from: e.target.value })} /></label>
        <label className="inline-field">To <input type="date" value={f.to} min={f.from || undefined} onChange={(e) => set({ to: e.target.value })} /></label>
        <label className="inline-field">Mode
          <select value={f.mode} onChange={(e) => set({ mode: e.target.value })}>
            <option value="">All</option>
            {modes.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        {filtered && <button className="ghost" onClick={clear}>Clear filters</button>}
      </div>
      {error && <div className="banner error" role="alert">{error}</div>}
      <div className="table-wrap grid-wrap" tabIndex={0} role="region" aria-label="Impact reports">
        <table className="grid">
          <thead>
            <tr>
              <th scope="col" className="check-cell"><span className="sr-only">Select</span></th>
              <SortTh k="policy" f={f} onSort={sortBy}>Policy</SortTh>
              <SortTh k="created" f={f} onSort={sortBy}>Created</SortTh>
              <th scope="col">Mode</th>
              <SortTh k="status" f={f} onSort={sortBy}>Status</SortTh>
              <SortTh k="violations" num f={f} onSort={sortBy}>Violations</SortTh>
              <SortTh k="penalty" num f={f} onSort={sortBy}>Penalty</SortTh>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const unassessed = unassessedOf(r);
              return (
                <tr key={r.report_id} className={`clickable${picked.includes(r.report_id) ? " selected" : ""}`} onClick={() => openReport(r.report_id)}>
                  <td className="check-cell" onClick={(e) => e.stopPropagation()}>
                    <label className="tick">
                      <input type="checkbox" checked={picked.includes(r.report_id)} onChange={() => toggle(r.report_id)} aria-label={`Select ${r.title} (${r.created_at}) for comparison`} />
                    </label>
                  </td>
                  <td>
                    <button className="link cell-title" onClick={(e) => { e.stopPropagation(); void openReport(r.report_id); }}>{r.title}</button>
                    <span className="cell-sub mono">{r.report_id}</span>
                  </td>
                  <td className="nowrap"><When value={r.created_at} /></td>
                  <td className="mono">{r.mode}</td>
                  <td><OverallBadge summary={r.summary} /></td>
                  <td className="num">{unassessed ? "—" : r.summary.loans_with_violations}</td>
                  <td className="num">{unassessed ? "—" : inr(r.summary.statutory_penalty_inr)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!loaded && !error && <Skeleton rows={5} label="Loading reports" />}
      {loaded && !items.length && <EmptyState title="No reports yet" hint="Run an analysis on the Analyze tab and its impact report will appear here." />}
      {loaded && items.length > 0 && !rows.length && (
        <EmptyState title="No reports match these filters" hint="Widen the date range or clear the filters."><button onClick={clear}>Clear filters</button></EmptyState>
      )}
      {picked.length > 0 && (
        <div className="action-bar no-print" role="region" aria-label="Selected reports">
          <span className="mono" role="status" aria-live="polite">
            {picked.length === 1 ? "1 report selected · select one more to compare" : "2 reports selected"}
          </span>
          <button className="primary" disabled={picked.length !== 2 || busy} onClick={compare}>{busy ? "Loading…" : "Compare diff"}</button>
          <button onClick={() => downloadReportList(pickedItems)}>Export selected</button>
          <button className="ghost" onClick={() => setPicked([])}>Deselect</button>
        </div>
      )}
    </section>
  );
}
