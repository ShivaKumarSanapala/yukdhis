import { lazy, Suspense, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { ImpactReport } from "../types";

const ReportView = lazy(() => import("./ReportView"));
const EVENT = "yukdhis:export-pdf";

/** Export one or more reports as a PDF through the browser's print dialog ("Save as PDF"). Prints only the reports,
 *  whatever page it is called from, and sets the document title (the suggested file name). */
export function exportPdf(reports: ImpactReport[]) {
  if (reports.length) window.dispatchEvent(new CustomEvent<ImpactReport[]>(EVENT, { detail: reports }));
}

const sheet = () => document.querySelector<HTMLElement>(".print-sheet");

/** Resolves when the print sheet has stopped growing: the report body, its Markdown summary (and any diagram) load
 *  lazily, and printing before they arrive produced an incomplete PDF. Gives up after 8 s. Also opens every <details>
 *  (the SQL behind each finding, the audit trail) so the PDF has the complete report. */
function settled(): Promise<void> {
  return new Promise((resolve) => {
    const started = Date.now();
    let last = -1, since = Date.now();
    const tick = () => {
      const el = sheet();
      const height = el?.scrollHeight ?? 0;
      el?.querySelectorAll("details").forEach((d) => { d.open = true; });
      if (height !== last) { last = height; since = Date.now(); }
      const quiet = Date.now() - since >= 500 && height > 0;
      if (quiet || Date.now() - started > 8000) { void document.fonts?.ready.then(() => resolve()); return; }
      setTimeout(tick, 100);
    };
    requestAnimationFrame(tick);
  });
}

/** Mounted once. Renders the reports into a print-only sheet outside the app root and opens the print dialog. */
export default function PrintHost() {
  const [reports, setReports] = useState<ImpactReport[]>([]);
  useEffect(() => {
    const on = (e: Event) => setReports((e as CustomEvent<ImpactReport[]>).detail);
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  useEffect(() => {
    if (!reports.length) return;
    const previous = document.title;
    document.title = reports.length === 1 ? `Yukdhis ${reports[0].report_id} ${reports[0].policy.title}` : `Yukdhis reports (${reports.length})`;
    document.body.classList.add("printing-reports");
    const done = () => { document.body.classList.remove("printing-reports"); document.title = previous; setReports([]); };
    window.addEventListener("afterprint", done, { once: true });
    let live = true;
    void import("./ReportView").then(() => settled()).then(() => { if (live) window.print(); });
    return () => { live = false; window.removeEventListener("afterprint", done); document.body.classList.remove("printing-reports"); document.title = previous; };
  }, [reports]);
  if (!reports.length) return null;
  return createPortal(<PrintSheet reports={reports} />, document.body);
}

/** The reports as they print: the same ReportView as the full-page report, one report per page. */
export function PrintSheet({ reports }: { reports: ImpactReport[] }) {
  return (
    <div className="print-sheet" aria-hidden="true">
      <Suspense fallback={null}>
        {reports.map((r) => (
          <div key={r.report_id} className="print-report page"><ReportView report={r} /></div>
        ))}
      </Suspense>
    </div>
  );
}
