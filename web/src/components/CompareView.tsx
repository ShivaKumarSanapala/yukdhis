import { useMemo, useState } from "react";
import type { ImpactReport } from "../types";
import { compareReports, inr, isMeasured, kpiRows, overallKey } from "../report-utils";
import { CheckPill, OverallBadge } from "./StatusBadges";
import { formatDateTime } from "../format";

const show = (n: number, money?: boolean) => (money ? inr(n) : String(n));
const delta = (a: number, b: number, money?: boolean) => (a === b ? "no change" : `${b > a ? "+" : "−"}${show(Math.abs(b - a), money)}`);

function Head({ label, r }: { label: string; r: ImpactReport }) {
  return (
    <div className="card">
      <p className="muted">{label}</p>
      <h3>{r.policy.title}</h3>
      <p className="meta mono">{r.report_id} · generated <time dateTime={r.generated_at} title={r.generated_at}>{formatDateTime(r.generated_at)}</time> · as of {formatDateTime(r.as_of)} · {r.mode}</p>
      <OverallBadge summary={r.summary} large />
    </div>
  );
}

function CheckCell({ c }: { c?: ImpactReport["results"][number] }) {
  if (!c) return <span className="muted">not present</span>;
  return <><CheckPill status={c.status} /> <small>{isMeasured(c.status) ? `${c.violations} / ${c.in_scope}` : "—"}</small></>;
}

/** Client-side comparison of two saved reports; the earlier run is on the left. */
export default function CompareView({ a, b }: { a: ImpactReport; b: ImpactReport }) {
  const [onlyChanged, setOnlyChanged] = useState(true);
  const cmp = useMemo(() => compareReports(a, b), [a, b]);
  const kpis = useMemo(() => kpiRows(a, b), [a, b]);
  const statusChanged = overallKey(a.summary) !== overallKey(b.summary);
  const rows = onlyChanged ? cmp.checks.filter((c) => c.changed) : cmp.checks;

  return (
    <div className="report compare">
      <h2>Run comparison</h2>
      <div className="grid2"><Head label="Run A (earlier)" r={a} /><Head label="Run B (later)" r={b} /></div>
      {statusChanged && <div className="banner warn" role="status">Overall status changed between these runs. Review the differences below before relying on either report.</div>}

      <div className="card">
        <h3>Summary</h3>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Summary comparison">
          <table>
            <thead><tr><th scope="col">Metric</th><th scope="col" className="num">Run A</th><th scope="col" className="num">Run B</th><th scope="col" className="num">Change</th></tr></thead>
            <tbody>
              <tr className={statusChanged ? "diff" : ""}>
                <th scope="row">Overall status</th><td><OverallBadge summary={a.summary} /></td><td><OverallBadge summary={b.summary} /></td><td>{statusChanged ? "changed" : "no change"}</td>
              </tr>
              {kpis.map((k) => (
                <tr key={k.label} className={k.a !== k.b ? "diff" : ""}>
                  <th scope="row">{k.label}</th><td className="num">{show(k.a, k.money)}</td><td className="num">{show(k.b, k.money)}</td><td className="num">{delta(k.a, k.b, k.money)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h3>Rules</h3>
        <div className="grid2">
          <div>
            <p><b>Added in run B</b> ({cmp.rulesAdded.length})</p>
            {cmp.rulesAdded.length ? <ul>{cmp.rulesAdded.map((t) => <li key={t} className="diff-add">+ {t}</li>)}</ul> : <p className="muted">None</p>}
          </div>
          <div>
            <p><b>Removed from run A</b> ({cmp.rulesRemoved.length})</p>
            {cmp.rulesRemoved.length ? <ul>{cmp.rulesRemoved.map((t) => <li key={t} className="diff-del">− {t}</li>)}</ul> : <p className="muted">None</p>}
          </div>
        </div>
        <p className="muted">Rules are matched by title; extraction can word the same obligation differently between runs.</p>
      </div>

      <div className="card">
        <div className="row between">
          <h3>Checks</h3>
          <label className="inline"><input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} /> Show only differences ({cmp.checks.filter((c) => c.changed).length} of {cmp.checks.length})</label>
        </div>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Check comparison">
          <table>
            <thead><tr><th scope="col">Rule</th><th scope="col">Check</th><th scope="col">Run A (status, violations / in scope)</th><th scope="col">Run B (status, violations / in scope)</th><th scope="col">Difference</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.key} className={c.changed ? "diff" : ""}>
                  <td>{c.rule}</td><td>{c.description}</td>
                  <td><CheckCell c={c.a} /></td><td><CheckCell c={c.b} /></td>
                  <td>{!c.a ? "only in B" : !c.b ? "only in A" : c.changed ? (c.a.status !== c.b.status ? `status ${c.a.status} → ${c.b.status}` : `violations ${c.a.violations} → ${c.b.violations}`) : "same"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!rows.length && <p className="muted">No differences in checks.</p>}
      </div>
    </div>
  );
}
