import { useState } from "react";
import { api } from "../api";
import type { Action, ImpactReport } from "../types";
import BarList from "./BarList";

const inr = (n: number) => `₹${Math.round(n).toLocaleString("en-IN")}`;
const short = (id: string) => id.split(":").pop() ?? id;

export default function ReportView({ report, onChange }: { report: ImpactReport; onChange?: (r: ImpactReport) => void }) {
  const s = report.summary;
  const [busy, setBusy] = useState("");
  const [outbox, setOutbox] = useState<Record<string, { recipient: string; body: string; channel: string }[]>>({});
  const [msg, setMsg] = useState("");

  const refresh = async () => onChange?.(await api.report(report.report_id));
  const decide = async (a: Action, decision: "APPROVED" | "REJECTED") => {
    setBusy(a.action_id);
    try {
      await api.decide(a.action_id, decision);
      if (decision === "APPROVED") setOutbox({ ...outbox, [a.action_id]: await api.outbox(a.action_id) });
      await refresh();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(""); }
  };
  const review = async (ruleId: string, decision: "APPROVED" | "REJECTED") => {
    try {
      const r = await api.reviewRule(ruleId, decision);
      setMsg(`${short(ruleId)} ${r.status.toLowerCase()} · ${r.edges_versioned} graph edges re-versioned`);
    } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <div className="report">
      <div className="card">
        <div className="report-head">
          <div>
            <h2>{report.policy.title}</h2>
            <p className="muted">{report.policy.issuer} · {report.policy.reference} · effective {report.policy.effective_date} · analysed as of {report.as_of} · {report.mode}</p>
          </div>
          <span className={`status ${s.overall_status === "COMPLIANT" ? "ok" : "bad"}`}>{s.overall_status.replace("_", " ")}</span>
        </div>
        <div className="kpis">
          <Kpi label="Loans with violations" value={s.loans_with_violations} />
          <Kpi label="Customers affected" value={s.customers_with_violations} />
          <Kpi label="Statutory penalty exposure" value={inr(s.statutory_penalty_inr)} />
          <Kpi label="Control gaps" value={s.control_gaps.length} />
          <Kpi label="Loans on gapped products" value={s.loans_exposed_to_control_gaps} />
          <Kpi label="Products in blast radius" value={s.products_in_scope} />
        </div>
        {msg && <div className="banner">{msg}</div>}
      </div>

      {report.narrative && <div className="card narrative"><h3>Executive summary (Orchestrator agent)</h3>{report.narrative.split("\n").map((l, i) => <p key={i}>{l}</p>)}</div>}

      <div className="grid2">
        <div className="card">
          <h3>Violations by branch</h3>
          <BarList data={s.by_branch} />
        </div>
        <div className="card">
          <h3>Blast radius</h3>
          {report.blast_radius.slice(0, 1).map((r) => (
            <div key={r.rule_id} className="radius">
              <p><b>Products:</b> {r.products.map((p) => p.name).join(", ") || "none"}</p>
              <p><b>Processes:</b> {r.processes.map((p) => `${p.name} (${p.bian_domain})`).join(", ")}</p>
              <p><b>Owners:</b> {[...new Set(r.owners.map((o) => o.team))].join(", ")}</p>
              <p><b>Segments:</b> {r.segments.map((x) => x.name).join(", ")}</p>
            </div>
          ))}
          <p><b>Control gaps:</b></p>
          <ul>{s.control_gaps.map((g) => <li key={g}><code>{g}</code></li>)}</ul>
        </div>
      </div>

      <div className="card">
        <h3>Rules and checks</h3>
        {report.rules.map((rule) => (
          <div key={rule.rule_id} className="rule">
            <div className="rule-head">
              <div>
                <b>{rule.title}</b> <span className={`pill risk-${rule.penalty_risk}`}>{rule.penalty_risk}</span>
                <blockquote>“{rule.citation.snippet}”</blockquote>
              </div>
              <div className="buttons">
                <button onClick={() => review(rule.rule_id, "APPROVED")}>Approve rule</button>
                <button className="ghost" onClick={() => review(rule.rule_id, "REJECTED")}>Reject</button>
              </div>
            </div>
            <table>
              <thead><tr><th>Check</th><th>Status</th><th>Violations / in scope</th><th>Penalty</th><th>Evidence</th></tr></thead>
              <tbody>
                {report.results.filter((r) => r.rule_id === rule.rule_id).map((r) => (
                  <tr key={r.check_index}>
                    <td>{r.description}<br /><small className="muted">{r.kind}</small></td>
                    <td><span className={`pill ${r.status === "GAP" ? "bad" : "ok"}`}>{r.status}</span></td>
                    <td>{r.violations} / {r.in_scope}</td>
                    <td>{r.penalty_inr ? inr(r.penalty_inr) : "—"}</td>
                    <td><small>{r.evidence}</small>{r.products_missing_control.length > 0 && <small><br />missing on: {r.products_missing_control.map(short).join(", ")}</small>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Remediation awaiting human approval</h3>
        {report.actions.map((a) => (
          <div key={a.action_id} className="action">
            <div className="action-head">
              <span><b>{a.workflow_id.replaceAll("_", " ")}</b> · {a.target_count} target(s) · owner {a.owner} · <small>{short(a.rule_id)}</small></span>
              {a.status === "PENDING_APPROVAL" ? (
                <span className="buttons">
                  <button className="primary" disabled={busy === a.action_id} onClick={() => decide(a, "APPROVED")}>Approve & dispatch</button>
                  <button className="ghost" disabled={busy === a.action_id} onClick={() => decide(a, "REJECTED")}>Reject</button>
                </span>
              ) : <span className={`pill ${a.status === "REJECTED" ? "bad" : "ok"}`}>{a.status}{a.decided_by ? ` · ${a.decided_by}` : ""}</span>}
            </div>
            <p className="template">{a.notice_template}</p>
            {outbox[a.action_id] && (
              <details open><summary>Hydrated outbox ({outbox[a.action_id].length}) — PII resolved inside the VPC only</summary>
                <ul className="log">{outbox[a.action_id].slice(0, 5).map((o, i) => <li key={i}><code>{o.channel} → {o.recipient}</code> {o.body}</li>)}</ul>
              </details>
            )}
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Audit trail</h3>
        <p className="muted">Extraction: {String(report.extraction_provenance?.method ?? "")} {report.extraction_provenance?.gemini_agreement !== undefined && `· Gemini agreement ${Math.round(Number(report.extraction_provenance.gemini_agreement) * 100)}%`}</p>
        {report.data_profile?.executed_sql && <><p><b>Guarded SQL executed</b></p><pre>{report.data_profile.executed_sql}</pre></>}
        {report.blast_radius[0] && <><p><b>Graph queries ({report.blast_radius[0].query_language})</b></p><pre>{report.blast_radius[0].queries.join("\n\n")}</pre></>}
        {report.validation_issues.length > 0 && <ul>{report.validation_issues.map((v, i) => <li key={i}><span className="pill warn">{v.severity}</span> {v.message}</li>)}</ul>}
        <ul className="muted">{report.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </div>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string | number }) {
  return <div className="kpi"><div className="kpi-value">{value}</div><div className="kpi-label">{label}</div></div>;
}
