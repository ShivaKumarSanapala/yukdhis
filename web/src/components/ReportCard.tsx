import { lazy, Suspense, useState } from "react";
import type { ImpactReport } from "../types";
import RunSummary from "./RunSummary";
import { Skeleton } from "./Motif";

const ReportView = lazy(() => import("./ReportView"));

/** The report inside the feed: summary header and metric strip, the full report body on demand, and a link to the full page. */
export default function ReportCard({ id, report, onChange, onOpenFull, onCompare, onAnalyseLink, onExpandToggle }: {
  id: string; report: ImpactReport; onChange: (r: ImpactReport) => void; onOpenFull: () => void; onCompare?: () => void;
  onAnalyseLink: (url: string) => void; onExpandToggle?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="report-card">
      <RunSummary run={{ run_id: id }} report={report} onOpenInReports={onOpenFull} onCompare={onCompare} onAnalyseLink={onAnalyseLink}
        expanded={expanded} onToggleExpand={() => { onExpandToggle?.(); setExpanded((v) => !v); }} />
      {expanded && (
        <Suspense fallback={<div className="card"><Skeleton rows={6} label="Loading report" /></div>}>
          <ReportView report={report} onChange={onChange} embedded />
        </Suspense>
      )}
    </div>
  );
}
