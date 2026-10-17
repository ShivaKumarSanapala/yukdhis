import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, followRun } from "../api";
import type { Config, ImpactReport, RunEvent, RunInfo, Sample, StartedRuns } from "../types";
import { extractedRules, isActive, runStatusMeta, runTitle } from "../run-utils";
import NewAnalysis from "./NewAnalysis";
import RunsBoard, { FILTERS, type FilterKey } from "./RunsBoard";
import RunTimeline from "./RunTimeline";
import RuleReview from "./RuleReview";
import RunSummary from "./RunSummary";
import CompareView from "./CompareView";
import { Skeleton } from "./Motif";

const MAX_FOLLOW = 4;            // live event streams besides the selected run; the rest are refreshed by list polling
const SELECTED_KEY = "yukdhis.analyze.selectedRun";
const NO_EVENTS = 1_000_000_000; // ?after= value that returns the run without its events
const RUN_ID = /^[A-Za-z0-9_.:-]{1,64}$/;

function initialSelection(): string | null {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("run");
  const stored = (() => { try { return localStorage.getItem(SELECTED_KEY); } catch { return null; } })();
  const id = fromHash ?? stored;
  return id && RUN_ID.test(id) ? id : null;
}

function remember(id: string | null) {
  try { if (id) localStorage.setItem(SELECTED_KEY, id); else localStorage.removeItem(SELECTED_KEY); } catch { /* private mode */ }
  const params = new URLSearchParams(location.hash.slice(1));
  if (id) params.set("run", id); else params.delete("run");
  const hash = params.toString();
  history.replaceState(null, "", `${location.pathname}${location.search}${hash ? `#${hash}` : ""}`);
}

function notify(run: RunInfo | undefined, status: string) {
  if (!run?.is_mine || !document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(`Analysis ${runStatusMeta(status).label.toLowerCase()}`, { body: runTitle(run), tag: run.run_id }); } catch { /* unsupported */ }
}

/** Analyze tab: start analyses, follow several runs, review extracted rules, and land on the result. */
export default function AnalyzePanel({ config, onOpenInReports }: { config: Config; onOpenInReports: (reportId: string) => void }) {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [inbox, setInbox] = useState<RunInfo[]>([]);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [mine, setMine] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(initialSelection);
  const [selected, setSelected] = useState<RunInfo | null>(null);
  const [events, setEvents] = useState<Record<string, RunEvent[]>>({});
  const [report, setReport] = useState<ImpactReport | null>(null);
  const [view, setView] = useState<"summary" | "compare">("summary");
  const [pair, setPair] = useState<[ImpactReport, ImpactReport] | null>(null);
  const [prefill, setPrefill] = useState<{ url: string; nonce: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const followers = useRef(new Map<string, () => void>());
  const loaded = useRef(new Set<string>());  // runs whose full event stream has been received
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const runsRef = useRef<RunInfo[]>([]);
  runsRef.current = runs;

  const loadSelected = useCallback(async (id: string) => {
    try {
      const { run } = await api.run(id, NO_EVENTS);
      if (selectedRef.current === id) setSelected(run);
    } catch (e) {
      if (selectedRef.current === id) { setSelected(null); setSelectedId(null); remember(null); setError((e as Error).message); }
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [list, watcher] = await Promise.all([
        api.runs({ status: FILTERS[filter].status || undefined, mine, limit: 30 }),
        api.runs({ sourceKind: "watcher", limit: 5 }),
      ]);
      setRuns(list);
      setInbox(watcher.filter((r) => r.created_by === "watcher" && !r.rerun_of));
    } catch (e) { setError((e as Error).message); }
    const id = selectedRef.current;
    if (id) await loadSelected(id);
  }, [filter, mine, loadSelected]);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  const follow = useCallback((id: string) => {
    followers.current.get(id)?.();
    loaded.current.delete(id);
    setEvents((all) => ({ ...all, [id]: [] }));
    const stop = followRun(id, (event) => setEvents((all) => ({ ...all, [id]: [...(all[id] ?? []), event] })), (status) => {
      followers.current.delete(id);
      if (selectedRef.current === id) loaded.current.add(id);
      else setEvents((all) => { const { [id]: _drop, ...rest } = all; return rest; });  // keep memory bounded
      notify(runsRef.current.find((r) => r.run_id === id), status);
      void refreshRef.current();
    });
    followers.current.set(id, stop);
  }, []);

  useEffect(() => { api.samples().then(setSamples).catch((e) => setError(e.message)); }, []);
  useEffect(() => {  // deep links: #run=<id>
    const onHash = () => {
      const id = new URLSearchParams(location.hash.slice(1)).get("run");
      if (id && RUN_ID.test(id) && id !== selectedRef.current) { loaded.current.delete(id); setSelectedId(id); }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => () => { followers.current.forEach((stop) => stop()); followers.current.clear(); loaded.current.clear(); }, []);

  const anyActive = runs.some(isActive) || inbox.some(isActive) || (selected ? isActive(selected) : false);
  useEffect(() => {  // list polling: quick while something runs, slow otherwise
    const timer = setInterval(() => void refresh(), anyActive ? 3000 : 15000);
    return () => clearInterval(timer);
  }, [anyActive, refresh]);
  useEffect(() => {
    if (!anyActive) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [anyActive]);

  // Follow the selected run plus a few of my active runs (status, timings and completion notifications).
  const wanted = useMemo(() => {
    const ids = runs.filter((r) => r.is_mine && isActive(r)).slice(0, MAX_FOLLOW).map((r) => r.run_id);
    return [...new Set([...(selectedId ? [selectedId] : []), ...ids])];
  }, [runs, selectedId]);
  const wantedKey = wanted.join(",");
  useEffect(() => {
    for (const id of wanted) if (!followers.current.has(id) && !loaded.current.has(id)) follow(id);
    for (const [id, stop] of followers.current) if (!wanted.includes(id)) { stop(); followers.current.delete(id); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantedKey, follow]);

  // Selection: persisted in the URL hash and localStorage so it survives tab switches and reloads.
  useEffect(() => {
    remember(selectedId);
    setReport(null); setPair(null); setView("summary"); setSelected(null);
    if (selectedId) void loadSelected(selectedId);
  }, [selectedId, loadSelected]);
  const reportId = selected?.status === "SUCCEEDED" ? selected.report_id : null;
  useEffect(() => {
    if (!reportId) return;
    let live = true;
    api.report(reportId).then((r) => { if (live) setReport(r); }).catch((e) => setError(e.message));
    return () => { live = false; };
  }, [reportId]);

  const select = (id: string) => {
    setError("");
    if (id === selectedId) { if (!followers.current.has(id) && !loaded.current.has(id)) follow(id); return; }
    loaded.current.delete(id);
    setSelectedId(id);
    requestAnimationFrame(() => document.getElementById("run-detail")?.scrollIntoView({ block: "start", behavior: "smooth" }));
  };
  const onStarted = (started: StartedRuns) => { void refresh(); select(started.run_ids[0] ?? started.run_id); };
  const act = async (run: RunInfo, work: () => Promise<unknown>) => {
    setBusyId(run.run_id); setError("");
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusyId(""); }
  };
  const cancel = (run: RunInfo) => act(run, async () => {
    await api.cancelRun(run.run_id);
    if (run.run_id === selectedRef.current && !followers.current.has(run.run_id)) follow(run.run_id);  // e.g. paused run
    await refresh();
  });
  const rerun = (run: RunInfo, asOf?: string) => act(run, async () => {
    const started = await api.rerun(run.run_id, { asOf: asOf || run.as_of || undefined, mode: run.mode });
    onStarted(started);
  });
  const compare = (run: RunInfo) => act(run, async () => {
    if (!run.report_id || !run.previous_report_id) return;
    const [before, after] = await Promise.all([api.report(run.previous_report_id), api.report(run.report_id)]);
    if (selectedId !== run.run_id) setSelectedId(run.run_id);
    setPair([before, after]); setView("compare");
    requestAnimationFrame(() => document.getElementById("run-result")?.scrollIntoView({ block: "start", behavior: "smooth" }));
  });
  const afterReview = () => { if (selectedId) follow(selectedId); void refresh(); };
  const analyseLink = (url: string) => { setPrefill({ url, nonce: Date.now() }); window.scrollTo({ top: 0, behavior: "smooth" }); };

  const selectedEvents = selectedId ? events[selectedId] ?? [] : [];
  const handlers = { onOpen: (r: RunInfo) => select(r.run_id), onCancel: cancel, onRerun: rerun, onCompare: compare };
  const followed = selected && selected.status !== "SUCCEEDED" ? selected : null;
  return (
    <section>
      <NewAnalysis config={config} samples={samples} prefillUrl={prefill} onStarted={onStarted} />
      {error && <div className="banner error" role="alert">{error}</div>}
      <RunsBoard runs={runs} inbox={inbox} selectedId={selectedId} now={now} busyId={busyId} filter={filter} mine={mine}
        onFilter={setFilter} onMine={setMine} h={handlers} />
      {selected && (
        <div id="run-detail" className="run-detail">
          <div className="row between no-print">
            <h2 className="detail-title">{runTitle(selected)}</h2>
            <div className="buttons">
              {followed && isActive(followed) && followed.is_mine && (
                <button className="danger" disabled={busyId === followed.run_id || followed.status === "CANCEL_REQUESTED"} onClick={() => cancel(followed)}>Cancel run</button>
              )}
              <button className="ghost" onClick={() => setSelectedId(null)}>Close</button>
            </div>
          </div>
          <RunTimeline run={selected} events={selectedEvents} />
          {selected.status === "AWAITING_REVIEW" && (
            <RuleReview key={`${selected.run_id}-${extractedRules(selected, selectedEvents).length}`} run={selected}
              rules={extractedRules(selected, selectedEvents)} onDone={afterReview} />
          )}
          {selected.status === "SUCCEEDED" && report && (
            <RunSummary run={selected} report={report} onOpenInReports={() => onOpenInReports(report.report_id)}
              onCompare={selected.previous_report_id ? () => compare(selected) : undefined} onAnalyseLink={analyseLink} />
          )}
          <div id="run-result">
            {view === "compare" && pair && (
              <><button className="ghost back no-print" onClick={() => setView("summary")}>Hide comparison</button><CompareView a={pair[0]} b={pair[1]} /></>
            )}
          </div>
        </div>
      )}
      {selectedId && !selected && !error && <div className="card"><Skeleton rows={4} label="Loading run" /></div>}
    </section>
  );
}
