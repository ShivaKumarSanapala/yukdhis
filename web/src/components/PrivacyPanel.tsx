import { useState } from "react";
import { api } from "../api";

const SAMPLE = "Customer Rahul Verma (PAN ABCPV1234K, +91 98450 12345, rahul.verma@example.com) says his home loan account ending in 4427 was closed on 2 March but the sale deed is still at the Koramangala branch. My wife Priya Verma visited twice.";

export default function PrivacyPanel() {
  const [text, setText] = useState(SAMPLE);
  const [out, setOut] = useState<{ sanitized: string; entity_counts: Record<string, number>; engine: string } | null>(null);
  const [error, setError] = useState("");
  return (
    <section className="card">
      <h2>Privacy shield</h2>
      <p className="muted">Exactly what Gemini receives. Pass 1: Cloud DLP infoTypes. Pass 2: Gemma in the VPC catches contextual PII. Tokens are reversible only inside the action engine.</p>
      <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} />
      <button className="primary" onClick={() => api.sanitize(text).then(setOut).catch((e) => setError(e.message))}>Sanitize</button>
      {error && <div className="banner error">{error}</div>}
      {out && (
        <div className="grid2">
          <div><h3>Sent to Gemini</h3><pre className="wrap">{out.sanitized}</pre></div>
          <div><h3>Redactions ({out.engine})</h3><ul>{Object.entries(out.entity_counts).map(([k, v]) => <li key={k}><code>{k}</code> × {v}</li>)}</ul></div>
        </div>
      )}
    </section>
  );
}
