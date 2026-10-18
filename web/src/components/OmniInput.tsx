import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent, type RefObject } from "react";
import type { Sample } from "../types";
import { ACCEPT, ALLOWED, MAX_INPUT, extension, sizeText, urlProblem, type Attachment, type Intent } from "../intake";
import Icon from "./Icons";
import PrivacyModal from "./PrivacyModal";

export interface ContextPill { label: string; kind: string }

interface Props {
  value: string;
  onValue: (value: string) => void;
  attachments: Attachment[];
  onAddFiles: (files: File[]) => void;
  onAddSample: (sample: Sample) => void;
  onAddUrl: (url: string) => void;
  onRemove: (id: string) => void;
  samples: Sample[];
  /** What the box will do when sent, and whether the user chose it. */
  intent: Intent;
  overridden: boolean;
  onFlipIntent: () => void;
  context: ContextPill | null;
  onClearContext: () => void;
  /** An answer is streaming. */
  streaming: boolean;
  /** An analysis is in progress (questions can still be asked). */
  runActive: boolean;
  onSend: () => void;
  onStop: () => void;
  /** Why sending is blocked right now, if it is. */
  blocked: string;
  notices: string[];
  onDismissNotices: () => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  placeholder?: string;
  llmReady: boolean;
}

/** One box for questions and analyses: attachments, intent chip, privacy shield, send or stop. */
export default function OmniInput(p: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [view, setView] = useState<"menu" | "url">("menu");
  const [url, setUrl] = useState("");
  const [shield, setShield] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const attachRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const hasContent = Boolean(p.value.trim()) || p.attachments.length > 0;
  const stopMode = p.streaming || (p.runActive && !hasContent);

  const grow = useCallback(() => {  // auto-grow up to the CSS max-height, then scroll inside
    const el = p.inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    if (el.value) el.style.height = `${el.scrollHeight}px`;
    el.style.overflowY = el.scrollHeight > el.clientHeight + 1 ? "auto" : "hidden";
  }, [p.inputRef]);
  useLayoutEffect(grow, [p.value, grow]);
  useEffect(() => {  // wrapping changes with the width
    const el = p.inputRef.current;
    if (!el) return;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => { if (el.clientWidth !== width) { width = el.clientWidth; grow(); } });
    observer.observe(el);
    return () => observer.disconnect();
  }, [p.inputRef, grow]);

  const closeMenu = (returnFocus = true) => { setMenuOpen(false); setView("menu"); if (returnFocus) attachRef.current?.focus(); };
  useEffect(() => {
    if (!menuOpen) return;
    const outside = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node) && !attachRef.current?.contains(e.target as Node)) closeMenu(false); };
    document.addEventListener("mousedown", outside);
    menuRef.current?.querySelector<HTMLElement>("button, input")?.focus();
    return () => document.removeEventListener("mousedown", outside);
  }, [menuOpen, view]);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!stopMode && hasContent && !p.blocked) p.onSend();
    }
  };
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData.files);
    if (files.length) { e.preventDefault(); p.onAddFiles(files); }
  };
  const onMenuKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); closeMenu(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemcheckbox"]') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (!items.length) return;
    e.preventDefault();
    items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
  };
  const urlError = urlProblem(url);
  const addUrl = () => { if (url.trim() && !urlError) { p.onAddUrl(url.trim()); setUrl(""); closeMenu(); p.inputRef.current?.focus(); } };

  return (
    <div className="omni">
      {p.context && (
        <div className="context-pill" role="status">
          <Icon name="link" size={13} />
          <span className="ctx-text">Chatting about: <b>{p.context.label}</b></span>
          <button className="icon ghost" onClick={p.onClearContext} aria-label={`Remove context: ${p.context.label}`}><Icon name="close" size={12} /></button>
        </div>
      )}
      {p.notices.length > 0 && (
        <div className="banner error omni-notice" role="alert">
          <span><b>Not added:</b> {p.notices.join("; ")}</span>
          <button className="ghost" onClick={p.onDismissNotices}>Dismiss</button>
        </div>
      )}
      <div className="omni-box">
        {p.attachments.length > 0 && (
          <ul className="chips" aria-label="Attachments">
            {p.attachments.map((a) => (
              <li key={a.id} className="chip">
                <Icon name={a.kind === "url" ? "link" : "file"} size={14} />
                <span className="chip-main">
                  <b>{a.kind === "file" ? a.file.name : a.kind === "sample" ? a.title : a.url}</b>
                  <small>{a.kind === "file" ? `${ALLOWED[extension(a.file.name) as keyof typeof ALLOWED] ?? "File"} · ${sizeText(a.file.size)}` : a.kind === "sample" ? "Sample circular" : "Link"}</small>
                </span>
                <button className="icon ghost" onClick={() => p.onRemove(a.id)} aria-label={`Remove ${a.kind === "file" ? a.file.name : a.kind === "sample" ? a.title : a.url}`}><Icon name="close" size={12} /></button>
              </li>
            ))}
          </ul>
        )}
        <textarea
          ref={p.inputRef} rows={1} value={p.value} maxLength={MAX_INPUT} aria-label="Message or circular to analyse"
          placeholder={p.placeholder ?? "Ask a question or drop a circular"}
          onChange={(e) => p.onValue(e.target.value)} onKeyDown={onKey} onPaste={onPaste}
        />
        <div className="omni-bar">
          <div className="omni-tools">
            <button ref={attachRef} className="icon round" aria-haspopup="menu" aria-expanded={menuOpen} aria-controls={menuOpen ? menuId : undefined}
              aria-label="Attach" title="Attach a circular" onClick={() => (menuOpen ? closeMenu() : setMenuOpen(true))}>
              <Icon name="plus" size={18} />
            </button>
            <button className="icon round" aria-label="Privacy shield: check what the model would receive" title="Privacy shield" onClick={() => setShield(true)}>
              <Icon name="shield" size={16} />
            </button>
            <button className={`mode-chip${p.overridden ? " manual" : ""}`} onClick={p.onFlipIntent} aria-label={`Mode: ${p.intent === "analyse" ? "analyse circular" : "ask assistant"}. Switch mode`}
              title={p.overridden ? "You chose this mode. Click to switch." : "Chosen from what you typed. Click to switch."}>
              <Icon name={p.intent === "analyse" ? "analyze" : "sparkle"} size={13} />
              {p.intent === "analyse" ? "Analyse circular" : "Ask assistant"}
            </button>
          </div>
          {stopMode ? (
            <button className="send stop" onClick={p.onStop} aria-label={p.streaming ? "Stop generating" : "Stop analysis"} title="Stop"><Icon name="stop" size={18} /></button>
          ) : (
            <button className="send" onClick={p.onSend} disabled={!hasContent || Boolean(p.blocked)} aria-label={p.intent === "analyse" ? "Start analysis" : "Send question"} title={p.blocked || "Send"}>
              <Icon name="send" size={18} />
            </button>
          )}
        </div>
        {menuOpen && (
          <div id={menuId} ref={menuRef} className="omni-menu" role="menu" aria-label="Attach" onKeyDown={onMenuKey}>
            {view === "menu" && (
              <>
                <button role="menuitem" onClick={() => { fileRef.current?.click(); closeMenu(false); }}><Icon name="attach" size={15} /> Upload file(s)</button>
                <button role="menuitem" onClick={() => setView("url")}><Icon name="link" size={15} /> From URL</button>
              </>
            )}
            {view === "url" && (
              <form className="menu-url" onSubmit={(e) => { e.preventDefault(); addUrl(); }}>
                <button type="button" role="menuitem" className="ghost back-item" onClick={() => setView("menu")}>← From URL</button>
                <input type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Link to a circular" aria-invalid={Boolean(urlError)}
                  placeholder="https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=…" />
                <span className={urlError ? "field-error" : "muted"}>{urlError || "The server fetches the page and analyses it like an upload."}</span>
                <button type="submit" className="primary" disabled={!url.trim() || Boolean(urlError)}>Add link</button>
              </form>
            )}
          </div>
        )}
        <input ref={fileRef} type="file" multiple accept={ACCEPT} className="file-input" tabIndex={-1} aria-hidden="true"
          onChange={(e) => { if (e.target.files) p.onAddFiles(Array.from(e.target.files)); e.target.value = ""; }} />
      </div>
      <p className="omni-hint muted" id="omni-hint">
        {p.blocked ? <span className="field-error" role="status">{p.blocked}</span> : (
          <>
            <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line · drop or paste a circular to analyse
            {!p.llmReady && " · assistant offline, analyses run without the LLM"}
          </>
        )}
      </p>
      {shield && <PrivacyModal initialText={p.value} onInsert={(t) => { p.onValue(t); requestAnimationFrame(() => p.inputRef.current?.focus()); }} onClose={() => setShield(false)} />}
    </div>
  );
}
