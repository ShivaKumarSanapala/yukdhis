import type { AnalyticalFinding, ImpactReport } from "../types";
import Markdown from "./Markdown";
import MetricStrip from "./MetricStrip";
import { formatDateTime } from "../format";
import { OverallBadge } from "./StatusBadges";
import { exportPdf } from "./PrintHost";
import {
  inr, isMeasured, overallKey, overallMeta, ruleTitle, unexecutableOf, unresolvedRadii,
} from "../report-utils";

const SCOPE_DEFAULT = "Scope could not be resolved from the circular's wording; no products assessed.";

const cellText = (v: unknown) => (v === null || v === undefined ? "" : typeof v === "number" ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : String(v));

/** SQL findings the Data Analyst recorded for the circular; the rows are the query result, not model text. */
function AnalyticalInsights({ findings, objectives }: { findings: AnalyticalFinding[]; objectives: string[] }) {
  if (!findings.length && !objectives.length) return null;
  return (
    <div className="card analytical" role="region" aria-label="Analytical insights">
      <h3>Analytical insights {findings.length > 0 && <span className="pill info">{findings.length}</span>}</h3>
      {!findings.length && (
        <>
          <p className="muted">These analyses were suggested from the circular but none was run or could be measured with the onboarded data.</p>
          <ul>{objectives.map((o) => <li key={o}>{o}</li>)}</ul>
        </>
      )}
      {findings.map((f) => (
        <section key={f.finding_id} className="finding">
          <h4>{f.objective}</h4>
          <p>{f.summary}</p>
          {f.rows.length > 0 && (
            <div className="table-wrap" tabIndex={0} role="region" aria-label={`Result table for ${f.objective}`}>
              <table>
                <thead><tr>{f.columns.map((c) => <th key={c} scope="col" className={typeof f.rows[0][c] === "number" ? "num" : undefined}>{c.replaceAll("_", " ")}</th>)}</tr></thead>
                <tbody>{f.rows.map((row, i) => (
                  <tr key={i}>{f.columns.map((c) => <td key={c} className={typeof row[c] === "number" ? "num" : undefined}>{cellText(row[c])}</td>)}</tr>
                ))}</tbody>
              </table>
            </div>
          )}
          {f.truncated && <p className="muted">Showing the first {f.rows.length} of {f.row_count} rows.</p>}
          <details><summary>Query</summary><pre className="wrap">{f.sql}</pre></details>
        </section>
      ))}
    </div>
  );
}

// `onChange` is accepted for callers that still pass it; the report is read-only now (no per-rule or action buttons).
export default function ReportView({ report, embedded }: { report: ImpactReport; onChange?: (r: ImpactReport) => void; embedded?: boolean }) {
  const s = report.summary;
  const overall = overallMeta(s);
  const unexecutable = unexecutableOf(report);
  // Obligations that cannot be machine-checked make the report INCONCLUSIVE even when the other checks were measured.
  const measured = (report.results ?? []).some((r) => isMeasured(r.status));
  const unassessed = ["INCONCLUSIVE", "UPCOMING", "ANALYTICAL_INSIGHTS"].includes(overallKey(s)) && !(unexecutable.length > 0 && measured);
  const unresolved = unresolvedRadii(report);
  const radii = (report.blast_radius ?? []).filter((r) => r.scope_status !== "UNRESOLVED");
  const uniq = <T,>(items: T[]) => [...new Set(items)];
  const blast = {
    products: uniq(radii.flatMap((r) => r.products.map((p) => p.name))),
    processes: uniq(radii.flatMap((r) => r.processes.map((p) => `${p.name} (${p.bian_domain})`))),
    owners: uniq(radii.flatMap((r) => r.owners.map((o) => o.team))),
    segments: uniq(radii.flatMap((r) => r.segments.map((x) => x.name))),
  };
  const kpi = (value: number | string) => (unassessed ? "—" : value);
  return (
    <div className="report">
      <p className="print-only">Yukdhis impact report {report.report_id} · printed {new Date().toLocaleString()}</p>
      {!embedded && <div className="card">
        <div className="report-head">
          <div>
            <h2>{report.policy.title}</h2>
            <p className="meta mono">{report.policy.issuer} · {report.policy.reference} · effective {formatDateTime(report.policy.effective_date)} · as of {formatDateTime(report.as_of)} · {report.mode} · {report.report_id} · generated <time dateTime={report.generated_at} title={report.generated_at}>{formatDateTime(report.generated_at)}</time></p>
          </div>
          <div className="head-side toolbar">
            <div role="status" aria-label="Overall status"><OverallBadge summary={s} large /></div>
            <div className="buttons no-print">
              <button className="primary" onClick={() => exportPdf([report])}>Export PDF</button>
            </div>
          </div>
        </div>
        {overall.hint && <p className={`muted status-hint ${overall.tone}`}>{overall.hint}{unassessed && " Figures below are shown as — because nothing was assessed."}</p>}
        <MetricStrip metrics={[
          { label: "Rules", value: s.rules, sub: `${s.products_in_scope} products in blast radius` },
          { label: "Checks with gaps", value: kpi(s.gaps) },
          { label: "Loans with violations", value: kpi(s.loans_with_violations) },
          { label: "Customers affected", value: kpi(s.customers_with_violations) },
          { label: "Penalty exposure", value: kpi(inr(s.statutory_penalty_inr)) },
          { label: "Control gaps", value: kpi(s.control_gaps.length), sub: unassessed ? undefined : `${s.loans_exposed_to_control_gaps} loans on gapped products` },
        ]} />
      </div>}

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

      {(s.controls_pending_review ?? []).length > 0 && (
        <div className="banner warn" role="status">
          <b>{s.controls_pending_review!.length} proposed control{s.controls_pending_review!.length > 1 ? "s" : ""} awaiting review.</b>{" "}
          The mapper created {s.controls_pending_review!.length > 1 ? "them" : "it"} for this document, so no product can provide
          {s.controls_pending_review!.length > 1 ? " them" : " it"} yet. Checks that depend on {s.controls_pending_review!.length > 1 ? "them are" : "it is"} not
          evaluated and raise no gaps or actions until a reviewer approves {s.controls_pending_review!.length > 1 ? "them" : "it"}.
          <ul>{s.controls_pending_review!.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
        </div>
      )}

      <AnalyticalInsights findings={report.analytical_findings ?? []} objectives={report.analytical_objectives ?? []} />

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

      {report.narrative && <div className="card narrative"><h3>Executive summary</h3><Markdown text={report.narrative} /></div>}

      <div className="card">
        <h3>Blast radius</h3>
        {!radii.length && <p className="muted">{unresolved.length > 0 ? "No products assessed: scope could not be resolved." : "No blast radius recorded."}</p>}
        {radii.length > 0 && (
          <div className="radius">
            <p><b>Products:</b> {blast.products.join(", ") || "none"}</p>
            <p><b>Processes:</b> {blast.processes.join(", ") || "none"}</p>
            <p><b>Owners:</b> {blast.owners.join(", ") || "none"}</p>
            <p><b>Segments:</b> {blast.segments.join(", ") || "none"}</p>
          </div>
        )}
        {s.control_gaps.length > 0 && (
          <>
            <p><b>Control gaps:</b></p>
            <ul>{s.control_gaps.map((g) => <li key={g}><code>{g}</code></li>)}</ul>
          </>
        )}
      </div>

      <details className="card audit" open>
        <summary><h3>Audit trail</h3></summary>
        <p className="muted">Extraction: {String(report.extraction_provenance?.method ?? "")} {report.extraction_provenance?.gemini_agreement !== undefined && `· Gemini agreement ${Math.round(Number(report.extraction_provenance.gemini_agreement) * 100)}%`}</p>
        {report.data_profile?.executed_sql && <><p><b>Guarded SQL executed</b></p><pre>{report.data_profile.executed_sql}</pre></>}
        {report.blast_radius?.[0] && report.blast_radius[0].queries?.length > 0 && <><p><b>Graph queries ({report.blast_radius[0].query_language})</b></p><pre>{report.blast_radius[0].queries.join("\n\n")}</pre></>}
        {report.validation_issues.length > 0 && <ul>{report.validation_issues.map((v, i) => <li key={i}><span className="pill warn">{v.severity}</span> {v.message}</li>)}</ul>}
        <ul className="muted">{report.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </details>
    </div>
  );
}
