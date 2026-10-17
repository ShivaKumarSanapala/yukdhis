import { useEffect, useRef, useState } from "react";
import { api, followRun } from "../api";
import type { Config, ImpactReport, RunEvent, Sample } from "../types";
import RunTimeline from "./RunTimeline";
import ReportView from "./ReportView";

export default function AnalyzePanel({ config }: { config: Config }) {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [sampleId, setSampleId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState(config.default_mode);
  const [asOf, setAsOf] = useState(config.as_of);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [status, setStatus] = useState("");
  const [report, setReport] = useState<ImpactReport | null>(null);
  const [error, setError] = useState("");
  const stop = useRef<() => void>(() => undefined);

  useEffect(() => {
    api.samples().then((s) => { setSamples(s); if (s[0]) setSampleId(s[0].sample_id); }).catch((e) => setError(e.message));
    return () => stop.current();
  }, []);

  const start = async () => {
    setError(""); setEvents([]); setReport(null); setStatus("STARTING");
    try {
      const { run_id } = await api.startRun({ sampleId: file ? undefined : sampleId, file: file ?? undefined, mode, asOf });
      setStatus("RUNNING");
      stop.current = followRun(run_id, (e) => setEvents((prev) => [...prev, e]), async (final) => {
        setStatus(final);
        const { run } = await api.run(run_id);
        if (run.report_id) setReport(await api.report(run.report_id));
        else if (run.error) setError(run.error);
      });
    } catch (e) {
      setStatus(""); setError((e as Error).message);
    }
  };

  return (
    <section>
      <div className="card controls no-print">
        <h2>New regulatory circular</h2>
        <div className="row">
          <label>Sample
            <select value={sampleId} onChange={(e) => { setSampleId(e.target.value); setFile(null); }} disabled={Boolean(file)}>
              {samples.map((s) => <option key={s.sample_id} value={s.sample_id}>{s.title}</option>)}
            </select>
          </label>
          <label>or upload (PDF via Document AI, Markdown)
            <input type="file" accept=".pdf,.md,.txt,.html,.docx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
        </div>
        <div className="row">
          <label>Mode
            <select value={mode} onChange={(e) => setMode(e.target.value as Config["default_mode"])}>
              <option value="agentic" disabled={!config.llm_ready}>Agentic (ADK + Gemini)</option>
              <option value="deterministic">Deterministic (no LLM)</option>
            </select>
          </label>
          <label>Analysis date (bitemporal as-of)
            <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          </label>
          <button className="primary" onClick={start} disabled={status === "RUNNING" || status === "STARTING"}>
            {status === "RUNNING" ? "Analysing…" : "Run impact analysis"}
          </button>
        </div>
        {error && <div className="banner error" role="alert">{error}</div>}
      </div>
      {events.length > 0 && <RunTimeline events={events} status={status} />}
      {report && <ReportView report={report} onChange={setReport} />}
    </section>
  );
}
