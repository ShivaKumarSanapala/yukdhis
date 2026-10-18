import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import Icon from "./Icons";

const SAMPLE = "Customer Rahul Verma (PAN ABCPV1234K, +91 98450 12345, rahul.verma@example.com) says his home loan account ending in 4427 was closed on 2 March but the sale deed is still at the Koramangala branch. My wife Priya Verma visited twice.";
const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Centred modal: shows exactly what Gemini would receive for a text and offers the sanitized version for the input. */
export default function PrivacyModal({ initialText, onInsert, onClose }: { initialText: string; onInsert: (text: string) => void; onClose: () => void }) {
  const [text, setText] = useState(initialText || SAMPLE);
  const [out, setOut] = useState<{ sanitized: string; entity_counts: Record<string, number>; engine: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>("textarea")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); closeRef.current(); return; }
      if (e.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const [first, last] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); opener?.focus(); };
  }, []);

  const run = async () => {
    setBusy(true); setError("");
    try { setOut(await api.sanitize(text)); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const total = out ? Object.values(out.entity_counts).reduce((n, v) => n + v, 0) : 0;
  // Portal to <body>: the composer dock is transformed, which would make this fixed dialog position against the dock
  // (backdrop and buttons pushed below the viewport) instead of the window.
  return createPortal(
    <>
      <div className="modal-backdrop no-print" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className="modal no-print" role="dialog" aria-modal="true" aria-labelledby="privacy-title">
        <div className="modal-head">
          <h2 id="privacy-title"><Icon name="shield" size={16} /> Privacy shield</h2>
          <button className="icon ghost" onClick={onClose} aria-label="Close privacy shield"><Icon name="close" size={16} /></button>
        </div>
        <p className="muted">Exactly what Gemini receives. Pass 1: Cloud DLP infoTypes. Pass 2: Gemma in the VPC catches contextual PII. Tokens are reversible only inside the action engine.</p>
        <label>Text to check
          <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <div className="buttons">
          <button className="primary" onClick={() => void run()} disabled={busy || !text.trim()}>{busy ? "Sanitizing…" : "Sanitize"}</button>
        </div>
        {error && <div className="banner error" role="alert">{error}</div>}
        {out && (
          <div className="grid2 privacy-out">
            <div><h3>Sent to Gemini</h3><pre className="wrap">{out.sanitized}</pre></div>
            <div>
              <h3>Redactions ({out.engine}) · {total}</h3>
              <ul>{Object.entries(out.entity_counts).map(([k, v]) => <li key={k}><code>{k}</code> × {v}</li>)}</ul>
              {!total && <p className="muted">Nothing needed redacting.</p>}
            </div>
          </div>
        )}
        <div className="modal-foot">
          <button onClick={onClose}>Close</button>
          <button className="primary" disabled={!out} onClick={() => { if (out) { onInsert(out.sanitized); onClose(); } }}>Insert sanitized text</button>
        </div>
      </div>
    </>,
    document.body,
  );
}
