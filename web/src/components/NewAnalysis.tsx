import { useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { api } from "../api";
import type { Config, Sample, StartedRuns } from "../types";

const SOURCES = ["Samples & files", "Paste text", "From URL"] as const;
type Source = (typeof SOURCES)[number];
const ALLOWED = { ".pdf": "PDF", ".docx": "Word", ".html": "HTML", ".htm": "HTML", ".md": "Markdown", ".markdown": "Markdown", ".txt": "Text" } as const;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_TEXT_BYTES = 500 * 1024;
export const MAX_BATCH = 10;

const extension = (name: string) => (name.match(/\.[^.]+$/)?.[0] ?? "").toLowerCase();
const size = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
const bytes = (text: string) => new TextEncoder().encode(text).length;
function urlProblem(value: string): string {
  if (!value.trim()) return "";
  try {
    const url = new URL(value.trim());
    if (!/^https?:$/.test(url.protocol)) return "Use an http(s) link.";
    if (!/(^|\.)rbi\.org\.in$/i.test(url.hostname)) return "Only rbi.org.in circular and notification links are supported.";
    return "";
  } catch { return "Enter a full link, e.g. https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12524"; }
}

/** Start one or more analyses: several samples and files at once, pasted text, or an rbi.org.in link. */
export default function NewAnalysis({ config, samples, prefillUrl, onStarted }: {
  config: Config; samples: Sample[]; prefillUrl?: { url: string; nonce: number } | null; onStarted: (started: StartedRuns) => void;
}) {
  const [source, setSource] = useState<Source>("Samples & files");
  const [picked, setPicked] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState(config.default_mode);
  const [asOf, setAsOf] = useState(config.as_of);
  const [review, setReview] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [seenPrefill, setSeenPrefill] = useState(0);
  if (prefillUrl && prefillUrl.nonce !== seenPrefill) {  // "Analyse linked circular" from a summary card
    setSeenPrefill(prefillUrl.nonce);
    setSource("From URL"); setUrl(prefillUrl.url);
  }

  const addFiles = (list: FileList | File[]) => {
    const ok: File[] = [], bad: string[] = [];
    for (const f of Array.from(list)) {
      const ext = extension(f.name);
      if (!(ext in ALLOWED)) bad.push(`${f.name}: use PDF, DOCX, HTML, Markdown or text`);
      else if (f.size > MAX_FILE_BYTES) bad.push(`${f.name}: larger than 10 MB`);
      else if (f.size === 0) bad.push(`${f.name}: empty file`);
      else ok.push(f);
    }
    setFiles((prev) => [...prev, ...ok.filter((f) => !prev.some((p) => p.name === f.name && p.size === f.size))]);
    setRejected(bad);
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); };
  const toggleSample = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const textBytes = bytes(text);
  const urlError = urlProblem(url);
  const count = source === "Samples & files" ? picked.length + files.length : source === "Paste text" ? (text.trim() ? 1 : 0) : (url.trim() && !urlError ? 1 : 0);
  const blocked = !count || count > MAX_BATCH || (source === "Paste text" && textBytes > MAX_TEXT_BYTES) || busy;

  const start = async () => {
    setError("");
    if ("Notification" in window && Notification.permission === "default") void Notification.requestPermission().catch(() => undefined);
    setBusy(true);
    try {
      const started = await api.startRuns({
        sampleIds: source === "Samples & files" ? picked : undefined, files: source === "Samples & files" ? files : undefined,
        text: source === "Paste text" ? text : undefined, url: source === "From URL" ? url.trim() : undefined,
        mode, asOf, reviewRules: review,
      });
      onStarted(started);
      if (source === "Samples & files") { setFiles([]); setPicked([]); setRejected([]); }
      else if (source === "Paste text") setText("");
      else setUrl("");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const onTabKey = (e: KeyboardEvent, i: number) => {
    const n = SOURCES.length;
    const next = e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i + n - 1) % n : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : -1;
    if (next < 0) return;
    e.preventDefault(); setSource(SOURCES[next]); tabRefs.current[next]?.focus();
  };

  return (
    <div className="card controls no-print">
      <h2>New analysis</h2>
      <div role="tablist" aria-label="Circular source" className="segmented">
        {SOURCES.map((s, i) => (
          <button key={s} ref={(el) => { tabRefs.current[i] = el; }} role="tab" id={`src-${i}`} aria-selected={s === source} aria-controls="src-panel"
            tabIndex={s === source ? 0 : -1} className={s === source ? "tab active" : "tab"} onClick={() => setSource(s)} onKeyDown={(e) => onTabKey(e, i)}>{s}</button>
        ))}
      </div>
      <div role="tabpanel" id="src-panel" aria-labelledby={`src-${SOURCES.indexOf(source)}`} className="src-panel">
        {source === "Samples & files" && (
          <div className="grid2">
            <fieldset className="samples">
              <legend>Sample circulars</legend>
              {samples.map((s) => (
                <label key={s.sample_id} className="inline">
                  <input type="checkbox" checked={picked.includes(s.sample_id)} onChange={() => toggleSample(s.sample_id)} /> {s.title}
                </label>
              ))}
              {!samples.length && <p className="muted">No samples on this server.</p>}
            </fieldset>
            <div>
              <label className={`dropzone${dragging ? " dragging" : ""}`} onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)} onDrop={onDrop}>
                <span><b>Drop circulars here</b> or choose files</span>
                <span className="muted">PDF, DOCX or HTML (parsed by Document AI), Markdown or text · up to 10 MB each</span>
                <input type="file" multiple accept={Object.keys(ALLOWED).join(",")} className="file-input"
                  onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
              </label>
              {files.length > 0 && (
                <ul className="chosen" aria-label="Chosen files">
                  {files.map((f) => (
                    <li key={`${f.name}-${f.size}`}>
                      <span className="grow"><b>{f.name}</b> <span className="muted">{ALLOWED[extension(f.name) as keyof typeof ALLOWED]} · {size(f.size)}</span></span>
                      <button className="link" onClick={() => setFiles((p) => p.filter((x) => x !== f))} aria-label={`Remove ${f.name}`}>Remove</button>
                    </li>
                  ))}
                </ul>
              )}
              {rejected.length > 0 && <div className="banner error" role="alert"><b>Not added:</b><ul>{rejected.map((r) => <li key={r}>{r}</li>)}</ul></div>}
            </div>
          </div>
        )}
        {source === "Paste text" && (
          <label>Circular text (Markdown or plain text)
            <textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} placeholder={"# RBI/2023-24/60 …\n\n- Reference: …\n- Regulated entities shall …"}
              aria-describedby="text-help" aria-invalid={textBytes > MAX_TEXT_BYTES} />
            <span id="text-help" className={textBytes > MAX_TEXT_BYTES ? "field-error" : "muted"}>
              {size(textBytes)} of 500 KB. Headings and bullet points help the parser keep each clause with its section.
            </span>
          </label>
        )}
        {source === "From URL" && (
          <label>Link to an RBI circular or notification
            <input type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=…"
              aria-describedby="url-help" aria-invalid={Boolean(urlError)} />
            <span id="url-help" className={urlError ? "field-error" : "muted"}>{urlError || "The server fetches the page from rbi.org.in and analyses it like an upload."}</span>
          </label>
        )}
      </div>
      <div className="row options">
        <label>Mode
          <select value={mode} onChange={(e) => setMode(e.target.value as Config["default_mode"])} aria-describedby="mode-help">
            <option value="agentic" disabled={!config.llm_ready}>Agentic (ADK + Gemini)</option>
            <option value="deterministic">Deterministic (no LLM)</option>
          </select>
          <span id="mode-help" className="muted help">
            {mode === "agentic" ? "Gemini agents run each stage and explain it: slower, with a richer narrative." : "The same stages without an LLM: fast, identical numbers, no narrative."}
            {!config.llm_ready && " Agentic needs Gemini, which is offline on this server."}
          </span>
        </label>
        <label>Analysis date (as of)
          <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} aria-describedby="asof-help" />
          <span id="asof-help" className="muted help">
            The bank's products, controls and loans as they were on this date. Rules that take effect after it come out UPCOMING and are not evaluated.
          </span>
        </label>
      </div>
      <label className="inline">
        <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} aria-describedby="review-help" /> Review extracted rules before continuing
      </label>
      <p id="review-help" className="muted help">The run pauses after extraction so you can edit or remove rules before they are mapped into the graph.</p>
      <div className="row between">
        <span className="muted" role="status">{count > MAX_BATCH ? `At most ${MAX_BATCH} at once` : count ? `${count} ${count === 1 ? "analysis" : "analyses"} ready` : "Choose what to analyse"}</span>
        <button className="primary" onClick={start} disabled={blocked}>
          {busy ? "Starting…" : count > 1 ? `Run ${count} analyses` : "Run impact analysis"}
        </button>
      </div>
      {error && <div className="banner error" role="alert">{error}</div>}
    </div>
  );
}
