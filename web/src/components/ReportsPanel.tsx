import { useEffect, useState } from "react";
import { api } from "../api";
import type { ImpactReport, ReportListItem } from "../types";
import ReportView from "./ReportView";

export default function ReportsPanel() {
  const [items, setItems] = useState<ReportListItem[]>([]);
  const [open, setOpen] = useState<ImpactReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.reports().then(setItems).catch((e) => setError(e.message)); }, []);
  if (open) return <section><button className="link" onClick={() => setOpen(null)}>← All reports</button><ReportView report={open} onChange={setOpen} /></section>;
  return (
    <section className="card">
      <h2>Impact reports</h2>
      {error && <div className="banner error">{error}</div>}
      <table>
        <thead><tr><th>Policy</th><th>Created</th><th>Mode</th><th>Status</th><th>Violations</th><th>Penalty</th></tr></thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.report_id} className="clickable" onClick={() => api.report(r.report_id).then(setOpen)}>
              <td>{r.title}</td><td>{r.created_at}</td><td>{r.mode}</td>
              <td><span className={`pill ${r.summary.overall_status === "COMPLIANT" ? "ok" : "bad"}`}>{r.summary.overall_status}</span></td>
              <td>{r.summary.loans_with_violations}</td><td>₹{Math.round(r.summary.statutory_penalty_inr).toLocaleString("en-IN")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && <p className="muted">No reports yet — run an analysis.</p>}
    </section>
  );
}
