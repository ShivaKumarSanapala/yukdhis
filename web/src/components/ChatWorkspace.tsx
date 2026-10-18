import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState, type DragEvent } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api, followRun } from "../api";
import { askAssistant, attachAssistant, nodeContextLine, type NodeContext } from "../chat-stream";
import type { ChatMessage, ImpactReport, RunEvent, RunInfo, StartedRuns } from "../types";
import { markWatcherSeen } from "../links";
import {
  MAX_BATCH, MAX_QUESTION, MAX_TEXT_BYTES, detectIntent, isCircularUrl, textBytes, validateFiles, type Attachment, type Intent,
} from "../intake";
import { SOURCE_LABEL, canCancel, canRerun, isActive, runStatusMeta, runTitle } from "../run-utils";
import { formatDateTime } from "../format";
import { useApp } from "../store";
import { OverallBadge } from "./StatusBadges";
import ChatMessageView from "./ChatMessageView";
import RunTimeline from "./RunTimeline";
import ReportCard from "./ReportCard";
import OmniInput, { type ContextPill } from "./OmniInput";
import { Greeting, Suggestions } from "./EmptyHero";
import Icon from "./Icons";
import { Skeleton } from "./Motif";

const CompareView = lazy(() => import("./CompareView"));

const NO_EVENTS = 1_000_000_000; // ?after= value that returns the run without its events
const BIAS = 28;                  // the centred input sits this far above the middle
let attachSeq = 0;
const nextId = () => `a${++attachSeq}`;

type Kind = "new" | "run" | "conv" | "report";
const kindOf = (id?: string): Kind => (!id ? "new" : id.startsWith("run-") ? "run" : id.startsWith("conv-") ? "conv" : "report");
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** One workspace for everything: the empty start screen, an analysis run, a report, or an assistant conversation. */
export default function ChatWorkspace() {
  const { id } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { config, samples, runs, conversations, startAnalysis, refreshHistory, setConversations } = useApp();
  const kind = kindOf(id);

  // thread
  const [run, setRun] = useState<RunInfo | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [report, setReport] = useState<ImpactReport | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [convId, setConvId] = useState<string>();
  const [pair, setPair] = useState<[ImpactReport, ImpactReport] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  // composer
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [override, setOverride] = useState<Intent | null>(null);
  const [notices, setNotices] = useState<string[]>([]);
  const [nodeCtx, setNodeCtx] = useState<NodeContext | null>(null);
  const [ctxDismissed, setCtxDismissed] = useState(false);
  const [dragging, setDragging] = useState(false);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const stopFollow = useRef<(() => void) | null>(null);
  const skipLoad = useRef<string | null>(null);
  const dragDepth = useRef(0);
  const pinned = useRef(true);  // the feed follows new content only while the reader is at the bottom
  const quiet = useRef(0);      // growth the reader caused (expanding the report) is not "new activity" until this time
  const runRef = useRef<RunInfo | null>(null);
  runRef.current = run;

  // ------------------------------------------------------------------ thread loading
  const follow = useCallback((runId: string) => {
    stopFollow.current?.();
    setEvents([]);
    stopFollow.current = followRun(runId, (e) => setEvents((all) => [...all, e]), () => {
      stopFollow.current = null;
      api.run(runId, NO_EVENTS).then(({ run: r }) => { if (runRef.current?.run_id === runId) setRun(r); }).catch(() => undefined);
      void refreshHistory();
    });
  }, [refreshHistory]);

  // A reply keeps being written on the server after you leave the conversation; coming back re-attaches to it and
  // replays what it has produced so far, then follows it live. When it ends, the saved conversation replaces the draft.
  const attach = useCallback((cid: string, live: () => boolean) => {
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    void attachAssistant({ conversationId: cid, setMessages, signal: controller.signal }).then(async () => {
      if (abortRef.current !== controller) return;  // the reader moved on
      abortRef.current = null; setStreaming(false);
      try { const saved = await api.conversation(cid); if (live()) setMessages(saved.messages); } catch { /* keep the streamed draft */ }
      void refreshHistory();
    });
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // The caller's own conversation about this run or report (the server keeps one per user and thread).
  const loadThread = useCallback((threadId: string, live: () => boolean) => {
    api.threadConversation(threadId).then(({ conversation: c }) => {
      if (live() && c) { setConvId(c.conversation_id); setMessages(c.messages); if (c.active) attach(c.conversation_id, live); }
    }).catch(() => undefined);
  }, [attach]);

  useEffect(() => {
    if (id && skipLoad.current === id) { skipLoad.current = null; return; }  // our own navigation after a conversation was created
    abortRef.current?.abort();
    stopFollow.current?.(); stopFollow.current = null;
    setRun(null); setEvents([]); setReport(null); setMessages([]); setConvId(undefined); setPair(null); setLoadError(""); setError("");
    setCtxDismissed(false); setStreaming(false);
    pinned.current = true;
    if (!id) return;
    let live = true;
    const alive = () => live;
    const k = kindOf(id);
    if (k === "run") {
      setLoading(true);
      api.run(id, NO_EVENTS).then(({ run: r }) => {
        if (!live) return;
        setRun(r); setLoading(false); markWatcherSeen(id); follow(id);
        loadThread(id, alive);
      }).catch((e) => { if (live) { setLoadError((e as Error).message); setLoading(false); } });
    } else if (k === "conv") {
      setLoading(true); setConvId(id);
      api.conversation(id).then((c) => {
        if (!live) return;
        if (c.thread_id) { navigate(`/chat/${c.thread_id}`, { replace: true }); return; }  // it belongs to a run or report
        setMessages(c.messages); setLoading(false);
        if (c.active) attach(id, alive);
      })
        .catch((e) => { if (live) { setLoadError((e as Error).message); setLoading(false); } });
    } else {
      setLoading(true);
      api.report(id).then((r) => {
        if (!live) return;
        setReport(r); setLoading(false);
        loadThread(id, alive);
      }).catch((e) => { if (live) { setLoadError((e as Error).message); setLoading(false); } });
    }
    return () => { live = false; stopFollow.current?.(); stopFollow.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  useEffect(() => () => abortRef.current?.abort(), []);

  // A report opened from the Reports tab becomes its run's thread when that run is known.
  useEffect(() => {
    if (kind !== "report" || !id) return;
    const match = runs.find((r) => r.report_id === id && r.status === "SUCCEEDED");
    if (match) navigate(`/chat/${match.run_id}`, { replace: true });
  }, [kind, id, runs, navigate]);

  // Poll the run while it is active (status, queue position, stage); events come from the stream.
  const active = run ? isActive(run) : false;
  useEffect(() => {
    if (!run || !active) return;
    const runId = run.run_id;
    const timer = setInterval(() => {
      api.run(runId, NO_EVENTS).then(({ run: r }) => { if (runRef.current?.run_id === runId) setRun(r); }).catch(() => undefined);
    }, 2500);
    return () => clearInterval(timer);
  }, [run?.run_id, active]);  // eslint-disable-line react-hooks/exhaustive-deps

  const reportId = run?.status === "SUCCEEDED" ? run.report_id : null;
  useEffect(() => {
    if (!reportId) return;
    let live = true;
    api.report(reportId).then((r) => { if (live) setReport(r); }).catch((e) => { if (live) setError((e as Error).message); });
    return () => { live = false; };
  }, [reportId]);

  // Entering from another page: a graph node to ask about, or "New chat".
  useEffect(() => {
    const state = location.state as { node?: NodeContext; prefill?: string; fresh?: number } | null;
    if (!state) return;
    if (state.node) { setNodeCtx(state.node); setText(state.prefill ?? ""); requestAnimationFrame(() => inputRef.current?.focus()); }
    else if (state.fresh && !id) { setText(""); setAttachments([]); setNodeCtx(null); setOverride(null); setNotices([]); requestAnimationFrame(() => inputRef.current?.focus()); }
    navigate(location.pathname, { replace: true, state: null });
  }, [location.key]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------------ derived view state
  const title = run ? runTitle(run) : report ? report.policy.title : conversations.find((c) => c.conversation_id === convId)?.title ?? (kind === "conv" ? "Conversation" : "");
  useEffect(() => { document.title = title ? `${title} · Anuvi` : "Anuvi: AI compliance workspace"; return () => { document.title = "Anuvi: AI compliance workspace"; }; }, [title]);
  const empty = kind === "new" && !messages.length && !streaming;
  const runActive = Boolean(run && isActive(run) && run.is_mine);
  const pill: ContextPill | null = nodeCtx
    ? { label: `${nodeCtx.name} (${nodeCtx.type})`, kind: "node" }
    : report && !ctxDismissed ? { label: report.policy.title, kind: "report" } : null;
  const detected = detectIntent(text, attachments);
  const intent: Intent = override ?? detected;

  const urlAttachments = attachments.filter((a): a is Extract<Attachment, { kind: "url" }> => a.kind === "url");
  const textRun = !attachments.length && intent === "analyse" && text.trim() && !isCircularUrl(text) ? text : "";
  const analysisCount = attachments.length + (isCircularUrl(text) ? 1 : 0) + (textRun ? 1 : 0);
  const blocked = (() => {
    if (!text.trim() && !attachments.length) return "";
    if (intent === "analyse") {
      if (analysisCount > MAX_BATCH) return `At most ${MAX_BATCH} analyses can start at once.`;
      if (textRun && textBytes(textRun) > MAX_TEXT_BYTES) return "The pasted text is larger than 500 KB.";
      return "";
    }
    if (attachments.length) return "Attachments start an analysis: switch to Analyse circular or remove them.";
    if (text.length > MAX_QUESTION) return `Questions are limited to ${MAX_QUESTION.toLocaleString()} characters. Switch to Analyse circular to analyse pasted text.`;
    return "";
  })();

  // ------------------------------------------------------------------ attachments
  const addFiles = (files: File[]) => {
    const { ok, bad } = validateFiles(files, attachments);
    setNotices(bad);
    if (ok.length) { setAttachments((a) => [...a, ...ok.map((file) => ({ id: nextId(), kind: "file" as const, file }))]); setOverride(null); }
    inputRef.current?.focus();
  };
  const addSample = (s: { sample_id: string; title: string }) => { setAttachments((a) => [...a, { id: nextId(), kind: "sample", sampleId: s.sample_id, title: s.title }]); setOverride(null); };
  const addUrl = (url: string) => { setAttachments((a) => [...a, { id: nextId(), kind: "url", url }]); setOverride(null); };

  const onDrop = (e: DragEvent) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault(); dragDepth.current = 0; setDragging(false);
    addFiles(Array.from(e.dataTransfer.files));
  };
  const onDrag = (e: DragEvent, enter: boolean) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dragDepth.current += enter ? 1 : -1;
    setDragging(dragDepth.current > 0);
  };

  // ------------------------------------------------------------------ actions
  const openRun = (started: StartedRuns) => navigate(`/chat/${started.run_ids[0] ?? started.run_id}`);

  const analyse = async (input: { sampleIds?: string[]; files?: File[]; texts?: string[]; urls?: string[] }) => {
    setError(""); setBusy(true);
    try {
      const calls = [{ sampleIds: input.sampleIds, files: input.files, text: input.texts?.[0], url: input.urls?.[0] }];
      for (const url of (input.urls ?? []).slice(1)) calls.push({ sampleIds: undefined, files: undefined, text: undefined, url });
      for (const t of (input.texts ?? []).slice(1)) calls.push({ sampleIds: undefined, files: undefined, text: t, url: undefined });
      const started: StartedRuns[] = [];
      for (const call of calls) started.push(await startAnalysis(call));
      setText(""); setAttachments([]); setOverride(null); setNotices([]);
      openRun({ ...started[0], run_ids: started.flatMap((s) => s.run_ids) });
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const sendAnalysis = () => {
    const files = attachments.flatMap((a) => (a.kind === "file" ? [a.file] : []));
    const sampleIds = attachments.flatMap((a) => (a.kind === "sample" ? [a.sampleId] : []));
    const urls = [...urlAttachments.map((a) => a.url), ...(isCircularUrl(text) && !attachments.length ? [text.trim()] : [])];
    void analyse({ sampleIds, files, urls, texts: textRun ? [textRun] : undefined });
  };

  const stickToBottom = () => { pinned.current = true; };
  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || streaming) return;
    setText(""); setOverride(null); setError(""); setStreaming(true); stickToBottom();
    const threadId = run?.run_id ?? (kind === "report" ? id : undefined);
    const node = nodeCtx;
    const controller = new AbortController();
    abortRef.current = controller;
    await askAssistant({
      display: q, wire: node ? `${nodeContextLine(node)}\n\n${q}` : q, conversationId: convId, threadId,
      reportId: !ctxDismissed || node ? report?.report_id : undefined, setMessages, signal: controller.signal,
      onStart: (d) => {
        setConvId(d.conversation_id);
        const now = new Date().toISOString();
        setConversations((list) => list.some((c) => c.conversation_id === d.conversation_id) ? list
          : [{ conversation_id: d.conversation_id, title: d.title, created_at: now, updated_at: now }, ...list]);
        if (!threadId && kind === "new") { skipLoad.current = d.conversation_id; navigate(`/chat/${d.conversation_id}`, { replace: true }); }
      },
    });
    if (abortRef.current !== controller) return;  // you switched conversation: the reply continues on the server
    abortRef.current = null;
    setStreaming(false);
    void refreshHistory();
  };

  const send = () => {
    if (blocked || streaming) return;
    if (intent === "analyse") sendAnalysis(); else void ask(text);
  };

  const cancelRun = async () => {
    const r = runRef.current;
    if (!r) return;
    setBusy(true); setError("");
    try {
      await api.cancelRun(r.run_id);
      if (!stopFollow.current) follow(r.run_id);  // e.g. a run paused for review
      const next = await api.run(r.run_id, NO_EVENTS);
      setRun(next.run); void refreshHistory();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  // Stop is explicit: leaving the conversation only detaches, the server keeps writing the reply.
  const stop = () => {
    if (!streaming) { void cancelRun(); return; }
    if (convId) void api.stopConversation(convId).catch(() => abortRef.current?.abort());
    else abortRef.current?.abort();
  };

  const retry = async () => {
    if (!run) return;
    setBusy(true); setError("");
    try { openRun(await api.rerun(run.run_id, { asOf: run.as_of || undefined, mode: run.mode })); void refreshHistory(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const compare = async () => {
    if (!report || !run?.previous_report_id) return;
    setBusy(true); setError("");
    try {
      const before = await api.report(run.previous_report_id);
      setPair([before, report]); pinned.current = false;
      requestAnimationFrame(() => document.getElementById("feed-compare")?.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" }));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const share = () => { void navigator.clipboard?.writeText(`${window.location.origin}${location.pathname}`).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600); }); };

  // ------------------------------------------------------------------ scroll behaviour
  const feedRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    const inner = innerRef.current;
    if (!inner) return;
    let last = inner.offsetHeight;
    const observer = new ResizeObserver(() => {
      const el = feedRef.current;
      if (!el) return;
      const grew = inner.offsetHeight > last;
      last = inner.offsetHeight;
      if (pinned.current) el.scrollTop = el.scrollHeight;
      else if (el.scrollHeight <= el.clientHeight + 80) { pinned.current = true; setFresh(false); }  // nothing to scroll to
      else if (grew && Date.now() > quiet.current) setFresh(true);  // only new content below counts, not a card collapsing
    });
    observer.observe(inner);
    return () => observer.disconnect();
  }, [empty, loading]);
  const onFeedScroll = () => {
    const el = feedRef.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (pinned.current) setFresh(false);
  };
  const toBottom = () => { const el = feedRef.current; if (el) el.scrollTo({ top: el.scrollHeight, behavior: reducedMotion() ? "auto" : "smooth" }); pinned.current = true; setFresh(false); };

  // ------------------------------------------------------------------ centred ↔ docked composer
  const stageRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  const [geom, setGeom] = useState({ stage: 0, dock: 0 });
  const [animate, setAnimate] = useState(false);  // no slide on first paint: only later empty <-> docked changes animate
  useLayoutEffect(() => {
    const measure = () => setGeom({ stage: stageRef.current?.clientHeight ?? 0, dock: dockRef.current?.offsetHeight ?? 0 });
    measure();
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => setAnimate(true)));
    const observer = new ResizeObserver(measure);
    if (stageRef.current) observer.observe(stageRef.current);
    if (dockRef.current) observer.observe(dockRef.current);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, []);
  const lift = empty ? -(geom.stage / 2) + geom.dock / 2 - BIAS : 0;

  const header = kind !== "new" && (
    <header className="stage-head no-print">
      <div className="stage-title">
        <h2 title={title}>{title || (loading ? "Loading…" : "Chat")}</h2>
        {run && <span className={`pill ${runStatusMeta(run.status).tone}`} role="status">{runStatusMeta(run.status).label}</span>}
        {!run && report && <OverallBadge summary={report.summary} />}
      </div>
      <div className="buttons">
        {report && <button aria-label="Open in Reports" title="Open in Reports" onClick={() => navigate(`/reports/${encodeURIComponent(report.report_id)}`)}><Icon name="reports" size={14} /><span className="btn-label">Open in Reports</span></button>}
        {report && run?.previous_report_id && !pair && <button aria-label="Compare with previous" title="Compare with previous" disabled={busy} onClick={() => void compare()}><Icon name="compare" size={14} /><span className="btn-label">Compare with previous</span></button>}
        {run && canCancel(run) && <button className="danger" disabled={busy || run.status === "CANCEL_REQUESTED"} onClick={() => void cancelRun()}>Cancel</button>}
        {run && canRerun(run) && run.status !== "SUCCEEDED" && <button disabled={busy} onClick={() => void retry()}><Icon name="refresh" size={14} /><span className="btn-label">Retry</span></button>}
        {run && canRerun(run) && run.status === "SUCCEEDED" && <button disabled={busy} onClick={() => void retry()} title="Run the analysis again with today's data"><Icon name="refresh" size={14} /><span className="btn-label">Re-run</span></button>}
        <button className="ghost" onClick={share} aria-label="Share link"><Icon name="share" size={14} /><span className="btn-label">{copied ? "Link copied" : "Share link"}</span></button>
      </div>
    </header>
  );

  const analyseLink = (url: string) => { setAttachments([]); setText(url); setOverride("analyse"); navigate("/"); requestAnimationFrame(() => inputRef.current?.focus()); };

  const startSample = (sampleId: string) => void analyse({ sampleIds: [sampleId] });
  const suggestionBusy = busy;

  return (
    <div ref={stageRef} className={`stage${empty ? " is-empty" : ""}${dragging ? " is-dragging" : ""}`}
      style={{ ["--dock-h" as string]: `${geom.dock}px`, ["--bias" as string]: `${BIAS}px` }}
      onDragEnter={(e) => onDrag(e, true)} onDragLeave={(e) => onDrag(e, false)} onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) e.preventDefault(); }} onDrop={onDrop}>
      {header}
      {empty && (
        <>
          <div className="hero-top"><Greeting /></div>
          <div className="hero-bottom"><Suggestions samples={samples} busy={suggestionBusy} onSample={startSample} onQuestion={(q) => void ask(q)} /></div>
        </>
      )}
      {!empty && (
        <div className="feed-wrap">
          <div ref={feedRef} className="feed" onScroll={onFeedScroll} role="log" aria-label="Conversation" aria-busy={loading || streaming}>
            <div ref={innerRef} className="feed-inner">
              {loading && <Skeleton rows={5} label="Loading conversation" />}
              {loadError && <div className="banner error" role="alert">{loadError}</div>}
              {run && (
                <>
                  <div className="msg user file">
                    <span className="file-chip" title={run.created_by ? `Triggered by ${run.created_by}${run.is_mine ? " (you)" : ""}` : undefined}>
                      <Icon name={run.source_kind === "url" ? "link" : "file"} size={16} />
                      <span><b>{runTitle(run)}</b><small>{SOURCE_LABEL[run.source_kind ?? ""] ?? "Upload"}{run.as_of ? ` · as of ${formatDateTime(run.as_of)}` : ""}</small></span>
                    </span>
                  </div>
                  <RunTimeline key={run.run_id} run={run} events={events} />
                </>
              )}
              {report && (
                <ReportCard id={run?.run_id ?? report.report_id} report={report} onChange={setReport}
                  onOpenFull={() => navigate(`/reports/${encodeURIComponent(report.report_id)}`)}
                  onCompare={run?.previous_report_id ? () => void compare() : undefined} onAnalyseLink={analyseLink} onExpandToggle={() => { pinned.current = false; quiet.current = Date.now() + 1500; }} />
              )}
              {pair && (
                <div id="feed-compare" className="feed-compare">
                  <button className="ghost back no-print" onClick={() => setPair(null)}>Hide comparison</button>
                  <Suspense fallback={<Skeleton rows={4} label="Loading comparison" />}><CompareView a={pair[0]} b={pair[1]} /></Suspense>
                </div>
              )}
              {messages.map((m) => <ChatMessageView key={m.message_id} message={m} />)}
              {error && <div className="banner error" role="alert">{error}</div>}
            </div>
          </div>
          {fresh && <button className="new-activity" onClick={toBottom}><Icon name="arrowDown" size={14} /> New activity</button>}
        </div>
      )}
      <div ref={dockRef} className={`dock${empty ? " centred" : ""}`} style={{ transform: `translateY(${lift}px)`, ...(animate ? {} : { transition: "none" }) }}>
        {empty && error && <div className="banner error" role="alert">{error}</div>}
        <OmniInput
          value={text} onValue={setText} attachments={attachments} onAddFiles={addFiles} onAddSample={addSample} onAddUrl={addUrl}
          onRemove={(rid) => setAttachments((a) => a.filter((x) => x.id !== rid))} samples={samples}
          intent={intent} overridden={override !== null} onFlipIntent={() => setOverride(intent === "ask" ? "analyse" : "ask")}
          context={pill} onClearContext={() => { if (nodeCtx) setNodeCtx(null); else setCtxDismissed(true); }}
          streaming={streaming} runActive={runActive} onSend={send} onStop={stop} blocked={blocked}
          notices={notices} onDismissNotices={() => setNotices([])}
          inputRef={inputRef} llmReady={config.llm_ready}
          placeholder={report ? "Ask about this report" : run ? "Ask about this document or your bank" : undefined}
        />
      </div>
      {dragging && (
        <div className="drop-overlay" aria-hidden="true"><div><Icon name="analyze" size={22} /><b>Drop to analyse</b><span>PDF, DOCX, HTML, Markdown or text · up to 10 MB each</span></div></div>
      )}
    </div>
  );
}
