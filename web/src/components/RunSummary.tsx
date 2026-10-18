import type { ImpactReport, RunInfo } from "../types";
import { inr, overallKey, overallMeta } from "../report-utils";
import { OverallBadge } from "./StatusBadges";
import MetricStrip from "./MetricStrip";

type Reference = { ref?: string; url?: string; relation?: string };

/** Completion card: overall status, key KPIs and where to go next. */
export default function RunSummary({ run, report, onOpenInReports, onCompare, onAnalyseLink, expanded, onToggleExpand }: {
  run: Pick<RunInfo, "run_id">; report: ImpactReport; onOpenInReports: () => void; onCompare?: () => void;
  onAnalyseLink?: (url: string) => void; expanded?: boolean; onToggleExpand?: () => void;
}) {
  const s = report.summary;
  const unassessed = ["INCONCLUSIVE", "UPCOMING", "ANALYTICAL_INSIGHTS"].includes(overallKey(s));
  const dash = (v: string | number) => (unassessed ? "—" : v);
  const references = ((report as unknown as { references?: Reference[] }).references ?? []).filter((r) => r?.url);
  const notApplicable = (s as unknown as { not_applicable_rules?: unknown[] }).not_applicable_rules?.length ?? 0;
  return (
    <section className="card summary-card" aria-labelledby={`summary-${run.run_id}`}>
      <div className="report-head">
        <div>
          <h2 id={`summary-${run.run_id}`}>{report.policy.title}</h2>
          <p className="meta mono">as of {report.as_of} · {report.mode} · {report.report_id}</p>
        </div>
        <div className="head-side" role="status" aria-label="Overall status"><OverallBadge summary={s} large /></div>
      </div>
      {overallMeta(s).hint && <p className="muted status-hint">{overallMeta(s).hint}</p>}
      {(s.analytical_findings ?? 0) > 0 && <p className="muted">{s.analytical_findings} analytical finding{s.analytical_findings === 1 ? "" : "s"} from the portfolio data, shown in the full report.</p>}
      <MetricStrip metrics={[
        { label: "Rules", value: s.rules },
        { label: "Checks with gaps", value: dash(s.gaps) },
        { label: "Loans with violations", value: dash(s.loans_with_violations) },
        { label: "Penalty exposure", value: dash(inr(s.statutory_penalty_inr)) },
        { label: "Actions proposed", value: report.actions?.length ?? 0 },
        { label: notApplicable ? "Rules not applicable" : "Products in scope", value: notApplicable || s.products_in_scope },
      ]} />
      <div className="buttons summary-actions">
        {onToggleExpand && <button className="primary" aria-expanded={expanded} onClick={onToggleExpand}>{expanded ? "Hide full report" : "Show full report"}</button>}
        <button className={onToggleExpand ? "" : "primary"} onClick={onOpenInReports}>Open full page in Reports</button>
        {onCompare && <button onClick={onCompare}>Compare with previous run</button>}
      </div>
      {references.length > 0 && onAnalyseLink && (
        <div className="references">
          <p className="muted">Circulars referenced by this document:</p>
          <ul>
            {references.slice(0, 6).map((r) => (
              <li key={r.url}>
                <button className="link" onClick={() => onAnalyseLink(r.url!)}>Analyse linked circular{r.ref ? `: ${r.ref}` : ""}</button>
                {r.relation && <span className="muted"> ({r.relation.toLowerCase().replaceAll("_", " ")})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
