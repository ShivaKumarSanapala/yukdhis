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
    void import("./ReportView").then(() => requestAnimationFrame(() => requestAnimationFrame(() => { if (live) window.print(); })));
    return () => { live = false; window.removeEventListener("afterprint", done); document.body.classList.remove("printing-reports"); document.title = previous; };
  }, [reports]);
  if (!reports.length) return null;
  return createPortal(
    <div className="print-sheet" aria-hidden="true">
      <Suspense fallback={null}>
        {reports.map((r) => (
          <div key={r.report_id} className="print-report"><ReportView report={r} /></div>
        ))}
      </Suspense>
    </div>,
    document.body,
  );
}
