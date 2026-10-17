import type { ImpactReport, RunInfo } from "../types";
import { inr, overallKey, overallMeta } from "../report-utils";
import { OverallBadge } from "./StatusBadges";

type Reference = { ref?: string; url?: string; relation?: string };

/** Completion card: overall status, key KPIs and where to go next. */
export default function RunSummary({ run, report, onOpen, onOpenInReports, onCompare, onAnalyseLink }: {
  run: RunInfo; report: ImpactReport; onOpen: () => void; onOpenInReports: () => void; onCompare?: () => void;
  onAnalyseLink?: (url: string) => void;
}) {
  const s = report.summary;
  const unassessed = ["INCONCLUSIVE", "UPCOMING"].includes(overallKey(s));
  const dash = (v: string | number) => (unassessed ? "—" : v);
  const references = ((report as unknown as { references?: Reference[] }).references ?? []).filter((r) => r?.url);
  const notApplicable = (s as unknown as { not_applicable_rules?: unknown[] }).not_applicable_rules?.length ?? 0;
  return (
    <section className="card summary-card" aria-labelledby={`summary-${run.run_id}`}>
      <div className="report-head">
        <div>
          <h2 id={`summary-${run.run_id}`}>{report.policy.title}</h2>
          <p className="muted">Analysed as of {report.as_of} · {report.mode} · {report.report_id}</p>
        </div>
        <div className="head-side" role="status" aria-label="Overall status"><OverallBadge summary={s} large /></div>
      </div>
      {overallMeta(s).hint && <p className="muted status-hint">{overallMeta(s).hint}</p>}
      <div className="kpis">
        <Kpi label="Rules" value={s.rules} />
        <Kpi label="Checks with gaps" value={dash(s.gaps)} />
        <Kpi label="Loans with violations" value={dash(s.loans_with_violations)} />
        <Kpi label="Penalty exposure" value={dash(inr(s.statutory_penalty_inr))} />
        <Kpi label="Actions proposed" value={report.actions?.length ?? 0} />
        <Kpi label={notApplicable ? "Rules not applicable" : "Products in scope"} value={notApplicable || s.products_in_scope} />
      </div>
      <div className="buttons summary-actions">
        <button className="primary" onClick={onOpen}>Open report</button>
        <button onClick={onOpenInReports}>Open in Reports</button>
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

function Kpi({ label, value }: { label: string; value: string | number }) {
  return <div className="kpi"><div className="kpi-value">{value}</div><div className="kpi-label">{label}</div></div>;
}
