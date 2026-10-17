import { useId, useState } from "react";
import type { ChatMessage, ToolCallTrace } from "../types";
import Markdown from "./Markdown";

const pretty = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value, null, 2));
const STATUS_TONE: Record<string, string> = { ok: "ok", error: "bad", running: "info", cancelled: "warn" };

export function Thinking({ text, live, label = "Thinking" }: { text: string; live: boolean; label?: string }) {
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? live; // streams open, collapses once the answer starts unless the user toggled it
  const id = useId();
  return (
    <div className="trace-block">
      <button className="trace-toggle" aria-expanded={open} aria-controls={id} onClick={() => setManual(!open)}>
        <span className="chevron" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className={`dot ${live ? "info live" : "neutral"}`} aria-hidden="true" />
        <span>{live ? `${label}…` : label}</span>
      </button>
      {open && <div id={id} className="thinking-text"><Markdown text={text} /></div>}
    </div>
  );
}

export function ToolCall({ call }: { call: ToolCallTrace }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const output = call.output as { truncated?: boolean; chars?: number; preview?: string } | undefined;
  const truncated = Boolean(output && typeof output === "object" && output.truncated);
  return (
    <li className="tool-call">
      <button className="trace-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <span className="chevron" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className={`dot ${STATUS_TONE[call.status] ?? "neutral"}${call.status === "running" ? " live" : ""}`} aria-hidden="true" />
        <code className="tc-name">{call.name}</code>
        <span className="tc-status">{call.status === "running" ? "running…" : call.status}</span>
        {typeof call.duration_ms === "number" && <span className="tc-dur mono">{(call.duration_ms / 1000).toFixed(2)}s</span>}
      </button>
      {open && (
        <div id={id} className="tool-io">
          <div className="io-label">Input</div>
          <pre>{pretty(call.args ?? {})}</pre>
          <div className="io-label">Output{truncated && ` (truncated, ${output?.chars ?? "?"} chars)`}</div>
          <pre>{call.output === undefined ? (call.status === "running" ? "Waiting for result…" : "No output") : truncated ? output?.preview : pretty(call.output)}</pre>
        </div>
      )}
    </li>
  );
}

export default function ChatMessageView({ message }: { message: ChatMessage }) {
  if (message.role === "user") return <div className="msg user"><p>{message.content}</p></div>;
  const streaming = message.status === "streaming";
  const thinking = message.trace?.thinking ?? "";
  const tools = message.trace?.tool_calls ?? [];
  const waiting = streaming && !message.content;
  return (
    <article className="msg assistant" aria-label="Assistant answer" aria-busy={streaming}>
      {thinking && <Thinking text={thinking} live={waiting} />}
      {tools.length > 0 && (
        <ul className="tool-calls" aria-label={`Tool calls (${tools.length})`}>
          {tools.map((call) => <ToolCall key={call.id} call={call} />)}
        </ul>
      )}
      {message.content && <Markdown text={message.content} />}
      {waiting && <span className="typing" role="status" aria-label="Assistant is responding"><i /><i /><i /></span>}
      {message.status === "stopped" && <p className="muted">Stopped before the answer finished.</p>}
      {message.error && <div className="banner error" role="alert">{message.error}</div>}
      {message.status === "error" && !message.error && !message.content && (
        <div className="banner error" role="alert">The assistant could not answer this question.</div>
      )}
    </article>
  );
}
