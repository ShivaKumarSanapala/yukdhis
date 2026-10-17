import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { api, streamChat } from "../api";
import type { ChatMessage, Conversation } from "../types";
import ChatMessageView from "./ChatMessageView";
import Icon from "./Icons";
import { EmptyState } from "./Motif";

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  llmReady: boolean;
  /** Light context: the impact report currently open in the Reports tab. */
  reportId?: string | null;
}

export default function AssistantDrawer({ open, onOpenChange, llmReady, reportId }: Props) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [showList, setShowList] = useState(false);
  const [text, setText] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<{ id: string; title: string } | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const fabRef = useRef<HTMLButtonElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const closeRef = useRef(() => onOpenChange(false));
  closeRef.current = () => onOpenChange(false);

  const refreshList = useCallback(() => api.conversations().then(setConversations).catch((e) => setError(e.message)), []);
  useEffect(() => { if (open) void refreshList(); }, [open, refreshList]);
  useEffect(() => () => abortRef.current?.abort(), []);

  // Dialog behaviour: initial focus, Esc to close, focus trapped inside, focus back to the launcher.
  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    (composerRef.current && !composerRef.current.disabled ? composerRef.current : panel?.querySelector<HTMLElement>(FOCUSABLE))?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); closeRef.current(); return; }
      if (e.key !== "Tab" || !panel) return;
      const items = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (!items.length) { e.preventDefault(); return; }
      const [first, last] = [items[0], items[items.length - 1]];
      if (!panel.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); fabRef.current?.focus(); };
  }, [open]);

  // Follow the stream only while the reader is already near the bottom.
  const pinned = useRef(true);
  useLayoutEffect(() => {
    const el = threadRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  const onScroll = () => {
    const el = threadRef.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const stop = () => abortRef.current?.abort();

  const openConversation = async (id: string) => {
    stop(); setError(""); setShowList(false); setActiveId(id); setMessages([]); pinned.current = true;
    try { setMessages((await api.conversation(id)).messages); } catch (e) { setError((e as Error).message); }
  };

  const newChat = () => {
    stop(); setActiveId(undefined); setMessages([]); setShowList(false); setError("");
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const send = async () => {
    const question = text.trim();
    if (!question || streaming || !llmReady) return;
    setText(""); setError(""); setStreaming(true); pinned.current = true;
    const stamp = Date.now();
    const draftId = `draft-${stamp}`;
    setMessages((m) => [...m,
      { message_id: `local-${stamp}`, role: "user", content: question, status: "complete" },
      { message_id: draftId, role: "assistant", content: "", status: "streaming", trace: { thinking: "", tool_calls: [] } }]);
    const update = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((all) => all.map((m) => (m.message_id === draftId ? fn(m) : m)));
    const trace = (m: ChatMessage) => m.trace ?? { thinking: "", tool_calls: [] };
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat({ text: question, conversation_id: activeId, context: reportId ? { report_id: reportId } : undefined }, {
        onStart: (d) => {
          setActiveId(d.conversation_id);
          setConversations((list) => {
            const existing = list.find((c) => c.conversation_id === d.conversation_id);
            const now = new Date().toISOString();
            const item = existing ?? { conversation_id: d.conversation_id, title: d.title, created_at: now, updated_at: now };
            return [{ ...item, updated_at: now }, ...list.filter((c) => c.conversation_id !== d.conversation_id)];
          });
        },
        onThinking: (delta) => update((m) => ({ ...m, trace: { ...trace(m), thinking: trace(m).thinking + delta } })),
        onText: (delta) => update((m) => ({ ...m, content: m.content + delta })),
        onToolCall: (call) => update((m) => ({ ...m, trace: { ...trace(m), tool_calls: [...trace(m).tool_calls, { ...call, status: "running" }] } })),
        onToolResult: (result) => update((m) => ({
          ...m, trace: { ...trace(m), tool_calls: trace(m).tool_calls.some((c) => c.id === result.id)
            ? trace(m).tool_calls.map((c) => (c.id === result.id ? { ...c, ...result } : c))
            : [...trace(m).tool_calls, { args: {}, ...result }] },
        })),
        onError: (message) => update((m) => ({ ...m, error: message })),
        onDone: (d) => update((m) => ({ ...m, message_id: d.message_id, status: d.status })),
      }, controller.signal);
      // A stream that ends without "done" (e.g. the no-LLM error) is not a complete answer.
      update((m) => (m.status === "streaming" ? { ...m, status: m.error ? "error" : "stopped" } : m));
    } catch (e) {
      const aborted = (e as Error).name === "AbortError";
      update((m) => ({
        ...m, status: aborted ? "stopped" : "error", error: aborted ? m.error : (e as Error).message,
        trace: { ...trace(m), tool_calls: trace(m).tool_calls.map((c) => (c.status === "running" ? { ...c, status: "cancelled" } : c)) },
      }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setStreaming(false);
    }
  };

  const onComposerKey = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
  };

  const saveTitle = async () => {
    if (!editing) return;
    const title = editing.title.trim();
    setEditing(null);
    if (!title) return;
    try {
      const updated = await api.renameConversation(editing.id, title);
      setConversations((list) => list.map((c) => (c.conversation_id === updated.conversation_id ? { ...c, title: updated.title } : c)));
    } catch (e) { setError((e as Error).message); }
  };

  const remove = async (c: Conversation) => {
    if (!window.confirm(`Delete "${c.title}"? This cannot be undone.`)) return;
    try {
      await api.deleteConversation(c.conversation_id);
      setConversations((list) => list.filter((x) => x.conversation_id !== c.conversation_id));
      if (c.conversation_id === activeId) newChat();
    } catch (e) { setError((e as Error).message); }
  };

  const active = conversations.find((c) => c.conversation_id === activeId);

  return (
    <>
      {!open && (
        <button ref={fabRef} className="assistant-fab no-print" onClick={() => onOpenChange(true)} aria-haspopup="dialog" aria-label="Open compliance assistant">
          <Icon name="sparkle" size={15} /> Assistant
        </button>
      )}
      {open && (
        <>
          <div className="drawer-backdrop no-print" aria-hidden="true" onClick={() => onOpenChange(false)} />
          <div ref={panelRef} className="drawer no-print" role="dialog" aria-modal="true" aria-labelledby="assistant-title">
            <div className="drawer-head">
              <div className="drawer-title">
                <h2 id="assistant-title">Compliance assistant</h2>
                <span className="muted" title={active?.title}>{active ? active.title : "New conversation"}</span>
              </div>
              <button aria-expanded={showList} aria-controls="assistant-conversations" onClick={() => setShowList(!showList)} aria-label="Show conversations">History</button>
              <button onClick={newChat} aria-label="Start a new chat">New</button>
              <button className="icon" onClick={() => onOpenChange(false)} aria-label="Close assistant"><Icon name="close" size={16} /></button>
            </div>
            {error && <div className="banner error" role="alert">{error}</div>}
            {showList ? (
              <nav id="assistant-conversations" className="conversation-list" aria-label="Your conversations">
                {!conversations.length && <p className="muted">No conversations yet.</p>}
                <ul>
                  {conversations.map((c) => (
                    <li key={c.conversation_id} className={c.conversation_id === activeId ? "active" : undefined}>
                      {editing?.id === c.conversation_id ? (
                        <input
                          autoFocus aria-label="Conversation title" value={editing.title} maxLength={120}
                          onChange={(e) => setEditing({ id: c.conversation_id, title: e.target.value })}
                          onBlur={() => void saveTitle()}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void saveTitle();
                            if (e.key === "Escape") { e.stopPropagation(); setEditing(null); }
                          }}
                        />
                      ) : (
                        <button className="link conversation-open" onClick={() => void openConversation(c.conversation_id)} aria-current={c.conversation_id === activeId ? "true" : undefined}>
                          <span>{c.title}</span>
                          <small className="muted">{new Date(c.updated_at).toLocaleString()}{c.message_count ? ` · ${c.message_count} messages` : ""}</small>
                        </button>
                      )}
                      <button className="icon" onClick={() => setEditing({ id: c.conversation_id, title: c.title })} aria-label={`Rename ${c.title}`}><Icon name="edit" size={14} /></button>
                      <button className="icon danger" onClick={() => void remove(c)} aria-label={`Delete ${c.title}`}><Icon name="trash" size={14} /></button>
                    </li>
                  ))}
                </ul>
              </nav>
            ) : (
              <div ref={threadRef} className="thread" role="log" aria-label="Conversation" aria-live="polite" aria-busy={streaming} onScroll={onScroll}>
                {!messages.length && (
                  <EmptyState title="Ask the compliance assistant" hint="Ask about impact reports, rule scope, branches or penalties. Answers cite the report, rule and tool behind every figure." />
                )}
                {messages.map((m) => <ChatMessageView key={m.message_id} message={m} />)}
              </div>
            )}
            <div className="composer">
              {!llmReady && <div className="banner warn" role="status">The assistant is offline: the API has no Gemini model configured (set GOOGLE_CLOUD_PROJECT).</div>}
              {reportId && <p className="muted context-chip">Context: report <code>{reportId}</code></p>}
              <div className="composer-row">
                <textarea
                  ref={composerRef} rows={2} value={text} disabled={!llmReady} aria-label="Ask the compliance assistant"
                  placeholder="e.g. Which branch has the most overdue document releases?"
                  onChange={(e) => setText(e.target.value)} onKeyDown={onComposerKey}
                />
                {streaming
                  ? <button className="danger" onClick={stop} aria-label="Stop generating">Stop</button>
                  : <button className="primary" onClick={() => void send()} disabled={!llmReady || !text.trim()}>Send</button>}
              </div>
              <small className="muted">Enter to send · Shift+Enter for a new line</small>
            </div>
          </div>
        </>
      )}
    </>
  );
}
