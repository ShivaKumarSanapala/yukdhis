import { useState } from "react";
import { api } from "../api";
import type { Action, ImpactReport } from "../types";
import BarList from "./BarList";
import { CheckPill, ModalityBadge, OverallBadge } from "./StatusBadges";
import {
  downloadCsv, inr, isMeasured, isUnresolved, modalityOf, overallKey, overallMeta, printReport, radiusFor, ruleTitle, short, unexecutableOf,
  unresolvedRadii,
} from "../report-utils";

const SCOPE_DEFAULT = "Scope could not be resolved from the circular's wording; no products assessed.";

export default function ReportView({ report, onChange }: { report: ImpactReport; onChange?: (r: ImpactReport) => void }) {
  const s = report.summary;
  const overall = overallMeta(s);
  const unexecutable = unexecutableOf(report);
  // Obligations that cannot be machine-checked make the report INCONCLUSIVE even when the other checks were measured.
  const measured = (report.results ?? []).some((r) => isMeasured(r.status));
  const unassessed = ["INCONCLUSIVE", "UPCOMING"].includes(overallKey(s)) && !(unexecutable.length > 0 && measured);
  const unresolved = unresolvedRadii(report);
  const shownRadius = (report.blast_radius ?? []).find((r) => !isUnresolved(r));
  const kpi = (value: number | string) => (unassessed ? "—" : value);
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
      <p className="print-only">Yukdhis impact report {report.report_id} · printed {new Date().toLocaleString()}</p>
      <div className="card">
        <div className="report-head">
          <div>
            <h2>{report.policy.title}</h2>
            <p className="muted">{report.policy.issuer} · {report.policy.reference} · effective {report.policy.effective_date} · analysed as of {report.as_of} · {report.mode}</p>
          </div>
          <div className="head-side">
            <div role="status" aria-label="Overall status"><OverallBadge summary={s} large /></div>
            <div className="buttons no-print">
              <button onClick={() => downloadCsv(report)}>Download CSV</button>
              <button onClick={() => printReport(report)}>Print / Save as PDF</button>
            </div>
          </div>
        </div>
        {overall.hint && <p className={`muted status-hint ${overall.tone}`}>{overall.hint}{unassessed && " Figures below are shown as — because nothing was assessed."}</p>}
        <div className="kpis">
          <Kpi label="Loans with violations" value={kpi(s.loans_with_violations)} />
          <Kpi label="Customers affected" value={kpi(s.customers_with_violations)} />
          <Kpi label="Statutory penalty exposure" value={kpi(inr(s.statutory_penalty_inr))} />
          <Kpi label="Control gaps" value={kpi(s.control_gaps.length)} />
          <Kpi label="Loans on gapped products" value={kpi(s.loans_exposed_to_control_gaps)} />
          <Kpi label="Products in blast radius" value={s.products_in_scope} />
        </div>
        {msg && <div className="banner" role="status">{msg}</div>}
      </div>

      {unresolved.length > 0 && (
        <div className="banner warn" role="status">
          <b>Scope unresolved for {unresolved.length} rule{unresolved.length > 1 ? "s" : ""}.</b> {SCOPE_DEFAULT}
          <ul>
            {unresolved.map((r) => (
              <li key={r.rule_id}>
                <b>{ruleTitle(report, r.rule_id)}</b>
                {(r.scope_notes ?? []).length > 0 && <ul>{(r.scope_notes ?? []).map((n, i) => <li key={i}>{n}</li>)}</ul>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {unexecutable.length > 0 && (
        <div className="card unexecutable" role="region" aria-label="Not machine-checkable obligations">
          <h3>Not machine-checkable <span className="pill warn">{unexecutable.length}</span></h3>
          <p className="muted">
            The engine cannot test {unexecutable.length === 1 ? "this obligation" : "these obligations"} with its check kinds or the
            data onboarded, so the report cannot be COMPLIANT. Review {unexecutable.length === 1 ? "it" : "them"} manually.
          </p>
          <ul>
            {unexecutable.map((u, i) => (
              <li key={i}>
                {u.citation && <blockquote>“{u.citation}”</blockquote>}
                {u.reason && <p><b>Why not:</b> {u.reason}</p>}
                {u.missing_data.length > 0 && (
                  <p><b>Missing data:</b> {u.missing_data.map((d, j) => <span key={d}>{j > 0 && ", "}<code>{d}</code></span>)}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.narrative && <div className="card narrative"><h3>Executive summary (Orchestrator agent)</h3>{report.narrative.split("\n").map((l, i) => <p key={i}>{l}</p>)}</div>}

      <div className="grid2">
        <div className="card">
          <h3>Violations by branch</h3>
          <BarList data={s.by_branch} />
        </div>
        <div className="card">
          <h3>Blast radius</h3>
          {!shownRadius && <p className="muted">{unresolved.length > 0 ? "No products assessed: scope could not be resolved." : "No blast radius recorded."}</p>}
          {shownRadius && [shownRadius].map((r) => (
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
                <b>{rule.title}</b> <span className={`pill risk-${rule.penalty_risk}`}>{rule.penalty_risk}</span> <ModalityBadge modality={modalityOf(rule)} />
                {isUnresolved(radiusFor(report, rule.rule_id)) && <> <span className="pill warn" title="Scope could not be resolved">SCOPE UNRESOLVED</span></>}
                <blockquote>“{rule.citation.snippet}”</blockquote>
              </div>
              <div className="buttons no-print">
                <button onClick={() => review(rule.rule_id, "APPROVED")}>Approve rule</button>
                <button className="ghost" onClick={() => review(rule.rule_id, "REJECTED")}>Reject</button>
              </div>
            </div>
            <div className="table-wrap" tabIndex={0} role="region" aria-label={`Checks for ${rule.title}`}>
            <table>
              <thead><tr><th scope="col">Check</th><th scope="col">Status</th><th scope="col">Violations / in scope</th><th scope="col">Penalty</th><th scope="col">Evidence</th></tr></thead>
              <tbody>
                {report.results.filter((r) => r.rule_id === rule.rule_id).map((r) => (
                  <tr key={r.check_index}>
                    <td>{r.description}<br /><small className="muted">{r.kind}</small></td>
                    <td><CheckPill status={r.status} /></td>
                    <td>{isMeasured(r.status) ? `${r.violations} / ${r.in_scope}` : "—"}</td>
                    <td>{isMeasured(r.status) && r.penalty_inr ? inr(r.penalty_inr) : "—"}</td>
                    <td><small>{r.evidence}</small>{r.products_missing_control.length > 0 && <small><br />missing on: {r.products_missing_control.map(short).join(", ")}</small>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Remediation awaiting human approval</h3>
        {report.actions.length === 0 && <p className="muted">No remediation actions proposed.</p>}
        {report.actions.map((a) => {
          const blocked = isUnresolved(radiusFor(report, a.rule_id));
          const reasonId = `why-${a.action_id}`;
          return (
          <div key={a.action_id} className="action">
            <div className="action-head">
              <span><b>{a.workflow_id.replaceAll("_", " ")}</b> · {a.target_count} target(s) · owner {a.owner} · <small>{short(a.rule_id)}</small></span>
              {a.status === "PENDING_APPROVAL" ? (
                <span className="buttons no-print">
                  <button className="primary" disabled={busy === a.action_id || blocked} aria-describedby={blocked ? reasonId : undefined} onClick={() => decide(a, "APPROVED")}>Approve & dispatch</button>
                  <button className="ghost" disabled={busy === a.action_id} onClick={() => decide(a, "REJECTED")}>Reject</button>
                </span>
              ) : <span className={`pill ${a.status === "REJECTED" ? "bad" : "ok"}`}>{a.status}{a.decided_by ? ` · ${a.decided_by}` : ""}</span>}
            </div>
            {blocked && a.status === "PENDING_APPROVAL" && (
              <p id={reasonId} className="banner warn" role="status">Approval disabled: the scope of this rule is unresolved, so the targets cannot be trusted. You can still reject it.</p>
            )}
            <p className="template">{a.notice_template}</p>
            {outbox[a.action_id] && (
              <details open><summary>Hydrated outbox ({outbox[a.action_id].length}) — PII resolved inside the VPC only</summary>
                <ul className="log">{outbox[a.action_id].slice(0, 5).map((o, i) => <li key={i}><code>{o.channel} → {o.recipient}</code> {o.body}</li>)}</ul>
              </details>
            )}
          </div>
          );
        })}
      </div>

      <div className="card">
        <h3>Audit trail</h3>
        <p className="muted">Extraction: {String(report.extraction_provenance?.method ?? "")} {report.extraction_provenance?.gemini_agreement !== undefined && `· Gemini agreement ${Math.round(Number(report.extraction_provenance.gemini_agreement) * 100)}%`}</p>
        {report.data_profile?.executed_sql && <><p><b>Guarded SQL executed</b></p><pre>{report.data_profile.executed_sql}</pre></>}
        {report.blast_radius?.[0] && report.blast_radius[0].queries?.length > 0 && <><p><b>Graph queries ({report.blast_radius[0].query_language})</b></p><pre>{report.blast_radius[0].queries.join("\n\n")}</pre></>}
        {report.validation_issues.length > 0 && <ul>{report.validation_issues.map((v, i) => <li key={i}><span className="pill warn">{v.severity}</span> {v.message}</li>)}</ul>}
        <ul className="muted">{report.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </div>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string | number }) {
  return <div className="kpi"><div className="kpi-value">{value}</div><div className="kpi-label">{label}</div></div>;
}
