import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { User } from "firebase/auth";
import { api } from "./api";
import { authEnabled, logOut, signIn, watchUser } from "./auth";
import type { Config } from "./types";
import AnalyzePanel from "./components/AnalyzePanel";
import ReportsPanel from "./components/ReportsPanel";
import GraphView from "./components/GraphView";
import PrivacyPanel from "./components/PrivacyPanel";
import AssistantDrawer from "./components/AssistantDrawer";

const TABS = ["Analyze", "Reports", "Knowledge graph", "Privacy shield"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(!authEnabled);
  const [config, setConfig] = useState<Config | null>(null);
  const [tab, setTab] = useState<Tab>("Analyze");
  const [error, setError] = useState("");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [openReportId, setOpenReportId] = useState<string | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const onTabKey = (e: KeyboardEvent, i: number) => {
    const next = e.key === "ArrowRight" ? (i + 1) % TABS.length : e.key === "ArrowLeft" ? (i + TABS.length - 1) % TABS.length : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    setTab(TABS[next]);
    tabRefs.current[next]?.focus();
  };

  useEffect(() => watchUser((u) => { setUser(u); setReady(true); }), []);
  useEffect(() => {
    if (ready && (!authEnabled || user)) api.config().then(setConfig).catch((e) => setError(e.message));
  }, [ready, user]);

  if (authEnabled && ready && !user) {
    return (
      <main className="signin" id="main">
        <h1>Yukdhis</h1>
        <p>Autonomous Compliance Engineer for banking</p>
        <button className="primary" onClick={() => signIn()}>Sign in with Google</button>
      </main>
    );
  }

  return (
    <>
    <div className="app" inert={assistantOpen}>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="no-print">
        <div className="brand">
          <strong>Yukdhis</strong>
          <span>Autonomous Compliance Engineer</span>
        </div>
        <nav aria-label="Sections">
          <div role="tablist" aria-label="Sections" className="tablist">
            {TABS.map((t, i) => (
              <button
                key={t} ref={(el) => { tabRefs.current[i] = el; }}
                role="tab" id={`tab-${i}`} aria-selected={t === tab} aria-controls="tabpanel" tabIndex={t === tab ? 0 : -1}
                className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)} onKeyDown={(e) => onTabKey(e, i)}
              >{t}</button>
            ))}
          </div>
        </nav>
        <div className="who">
          {config && <span className="pill">{config.tenant_id}</span>}
          {config && <span className={config.llm_ready ? "pill ok" : "pill warn"} role="status">{config.llm_ready ? config.reasoning_model : "LLM offline"}</span>}
          {authEnabled && <button className="link" onClick={() => logOut()}>Sign out</button>}
        </div>
      </header>
      {error && <div className="banner error" role="alert">{error}</div>}
      <main id="main" tabIndex={-1}>
        <div role="tabpanel" id="tabpanel" aria-labelledby={`tab-${TABS.indexOf(tab)}`}>
        {tab === "Analyze" && config && <AnalyzePanel config={config} />}
        {tab === "Reports" && <ReportsPanel onOpenReportChange={setOpenReportId} />}
        {tab === "Knowledge graph" && config && <GraphView defaultAsOf={config.as_of} />}
        {tab === "Privacy shield" && <PrivacyPanel />}
        </div>
      </main>
      <footer className="no-print">
        Gemini 2.5 Pro on Vertex AI · Google ADK · Spanner Graph · Cloud SQL · Document AI · Cloud DLP + Gemma · Cloud Run · Firebase
        {config && <span> · backends: {Object.entries(config.backends).map(([k, v]) => `${k}=${v}`).join(", ")}</span>}
      </footer>
    </div>
    {config && (
      <AssistantDrawer open={assistantOpen} onOpenChange={setAssistantOpen} llmReady={config.llm_ready}
        reportId={tab === "Reports" ? openReportId : null} />
    )}
    </>
  );
}
