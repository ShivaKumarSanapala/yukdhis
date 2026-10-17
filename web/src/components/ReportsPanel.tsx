import { useEffect, useState } from "react";
import { api } from "../api";
import type { ImpactReport, ReportListItem } from "../types";
import ReportView from "./ReportView";
import CompareView from "./CompareView";
import { OverallBadge } from "./StatusBadges";
import { overallKey } from "../report-utils";

const time = (value: string) => { const t = Date.parse(value); return Number.isNaN(t) ? 0 : t; };

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
  useEffect(() => { api.reports().then(setItems).catch((e) => setError(e.message)); }, []);
  useEffect(() => {  // "Open in Reports" from the Analyze tab
    if (initialReportId) api.report(initialReportId).then(setOpen).catch((e) => setError(e.message));
  }, [initialReportId]);

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

  if (pair) return <section><button className="link no-print" onClick={() => setPair(null)}>← All reports</button><CompareView a={pair[0]} b={pair[1]} /></section>;
  if (open) return <section><button className="link no-print" onClick={() => setOpen(null)}>← All reports</button><ReportView report={open} onChange={setOpen} /></section>;
  return (
    <section className="card">
      <div className="row between">
        <h2>Impact reports</h2>
        <span className="buttons">
          <span className="muted" role="status">{picked.length === 2 ? "2 selected" : `Tick two reports to compare (${picked.length}/2)`}</span>
          <button className="primary" disabled={picked.length !== 2 || busy} onClick={compare}>{busy ? "Loading…" : "Compare selected"}</button>
        </span>
      </div>
      {error && <div className="banner error" role="alert">{error}</div>}
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Impact reports">
        <table>
          <thead><tr><th scope="col">Compare</th><th scope="col">Policy</th><th scope="col">Created</th><th scope="col">Mode</th><th scope="col">Status</th><th scope="col">Violations</th><th scope="col">Penalty</th></tr></thead>
          <tbody>
            {items.map((r) => {
              const unassessed = ["INCONCLUSIVE", "UPCOMING"].includes(overallKey(r.summary));
              return (
              <tr key={r.report_id} className="clickable" onClick={() => openReport(r.report_id)}>
                <td className="check-cell" onClick={(e) => e.stopPropagation()}>
                  <label className="tick">
                    <input type="checkbox" checked={picked.includes(r.report_id)} onChange={() => toggle(r.report_id)} aria-label={`Select ${r.title} (${r.created_at}) for comparison`} />
                  </label>
                </td>
                <td><button className="link" onClick={(e) => { e.stopPropagation(); void openReport(r.report_id); }}>{r.title}</button></td>
                <td>{r.created_at}</td><td>{r.mode}</td>
                <td><OverallBadge summary={r.summary} /></td>
                <td>{unassessed ? "—" : r.summary.loans_with_violations}</td><td>{unassessed ? "—" : `₹${Math.round(r.summary.statutory_penalty_inr).toLocaleString("en-IN")}`}</td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!items.length && <p className="muted">No reports yet — run an analysis.</p>}
    </section>
  );
}
