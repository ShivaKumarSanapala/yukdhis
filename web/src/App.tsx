import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { api } from "./api";
import { authEnabled, logOut, signIn, watchUser } from "./auth";
import type { Config } from "./types";
import AnalyzePanel from "./components/AnalyzePanel";
import ReportsPanel from "./components/ReportsPanel";
import GraphView from "./components/GraphView";
import PrivacyPanel from "./components/PrivacyPanel";
import ChatPanel from "./components/ChatPanel";

const TABS = ["Analyze", "Reports", "Knowledge graph", "Privacy shield", "Assistant"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(!authEnabled);
  const [config, setConfig] = useState<Config | null>(null);
  const [tab, setTab] = useState<Tab>("Analyze");
  const [error, setError] = useState("");

  useEffect(() => watchUser((u) => { setUser(u); setReady(true); }), []);
  useEffect(() => {
    if (ready && (!authEnabled || user)) api.config().then(setConfig).catch((e) => setError(e.message));
  }, [ready, user]);

  if (authEnabled && ready && !user) {
    return (
      <main className="signin">
        <h1>Yukdhis</h1>
        <p>Autonomous Compliance Engineer for banking</p>
        <button className="primary" onClick={() => signIn()}>Sign in with Google</button>
      </main>
    );
  }

  return (
    <div className="app">
      <header>
        <div className="brand">
          <strong>Yukdhis</strong>
          <span>Autonomous Compliance Engineer</span>
        </div>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>{t}</button>
          ))}
        </nav>
        <div className="who">
          {config && <span className="pill">{config.tenant_id}</span>}
          {config && <span className={config.llm_ready ? "pill ok" : "pill warn"}>{config.llm_ready ? config.reasoning_model : "LLM offline"}</span>}
          {authEnabled && <button className="link" onClick={() => logOut()}>Sign out</button>}
        </div>
      </header>
      {error && <div className="banner error">{error}</div>}
      <main>
        {tab === "Analyze" && config && <AnalyzePanel config={config} />}
        {tab === "Reports" && <ReportsPanel />}
        {tab === "Knowledge graph" && config && <GraphView defaultAsOf={config.as_of} />}
        {tab === "Privacy shield" && <PrivacyPanel />}
        {tab === "Assistant" && <ChatPanel enabled={Boolean(config?.llm_ready)} />}
      </main>
      <footer>
        Gemini 2.5 Pro on Vertex AI · Google ADK · Spanner Graph · Cloud SQL · Document AI · Cloud DLP + Gemma · Cloud Run · Firebase
        {config && <span> · backends: {Object.entries(config.backends).map(([k, v]) => `${k}=${v}`).join(", ")}</span>}
      </footer>
    </div>
  );
}
