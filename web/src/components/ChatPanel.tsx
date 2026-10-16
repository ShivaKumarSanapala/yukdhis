import { useState } from "react";
import { api } from "../api";

interface Turn { role: "user" | "agent"; text: string; tools?: string[] }

export default function ChatPanel({ enabled }: { enabled: boolean }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [session, setSession] = useState<string>();
  const [busy, setBusy] = useState(false);
  const send = async () => {
    if (!text.trim()) return;
    const question = text;
    setTurns((t) => [...t, { role: "user", text: question }]);
    setText(""); setBusy(true);
    try {
      const r = await api.chat(question, session);
      setSession(r.session_id);
      setTurns((t) => [...t, { role: "agent", text: r.reply, tools: r.tools_used }]);
    } catch (e) {
      setTurns((t) => [...t, { role: "agent", text: `Error: ${(e as Error).message}` }]);
    } finally { setBusy(false); }
  };
  return (
    <section className="card chat">
      <h2>Compliance assistant</h2>
      {!enabled && <div className="banner">Chat needs Gemini on Vertex AI. Set GOOGLE_CLOUD_PROJECT on the API.</div>}
      <div className="turns">
        {turns.map((t, i) => (
          <div key={i} className={`turn ${t.role}`}>
            {t.text.split("\n").map((l, j) => <p key={j}>{l}</p>)}
            {t.tools && t.tools.length > 0 && <small className="muted">tools: {t.tools.join(", ")}</small>}
          </div>
        ))}
      </div>
      <div className="row">
        <input className="grow" value={text} placeholder="e.g. Which branch has the most overdue document releases?" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} disabled={!enabled || busy} />
        <button className="primary" onClick={send} disabled={!enabled || busy}>{busy ? "Thinking…" : "Ask"}</button>
      </div>
    </section>
  );
}
