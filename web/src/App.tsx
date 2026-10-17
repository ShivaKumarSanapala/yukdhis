import { lazy, Suspense, useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { api } from "./api";
import { authEnabled, logOut, signIn, watchUser } from "./auth";
import type { Config } from "./types";
import AnalyzePanel from "./components/AnalyzePanel";
import ReportsPanel from "./components/ReportsPanel";
import PrivacyPanel from "./components/PrivacyPanel";
import { Skeleton } from "./components/Motif";

const GraphView = lazy(() => import("./components/GraphView"));
const AssistantDrawer = lazy(() => import("./components/AssistantDrawer"));
import Icon from "./components/Icons";
import { Motif } from "./components/Motif";

const TABS = ["Analyze", "Reports", "Knowledge graph", "Privacy shield"] as const;
type Tab = (typeof TABS)[number];
const ICONS: Record<Tab, string> = { Analyze: "analyze", Reports: "reports", "Knowledge graph": "graph", "Privacy shield": "shield" };

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(!authEnabled);
  const [config, setConfig] = useState<Config | null>(null);
  const [tab, setTab] = useState<Tab>("Analyze");
  const [error, setError] = useState("");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [openReportId, setOpenReportId] = useState<string | null>(null);
  const [reportToOpen, setReportToOpen] = useState<string | null>(null);
  const openInReports = (reportId: string) => { setReportToOpen(reportId); setTab("Reports"); window.scrollTo({ top: 0 }); };
  const [menuOpen, setMenuOpen] = useState(false);
  const go = (t: Tab) => { setReportToOpen(null); setTab(t); setMenuOpen(false); };

  useEffect(() => watchUser((u) => { setUser(u); setReady(true); }), []);
  useEffect(() => {
    if (ready && (!authEnabled || user)) api.config().then(setConfig).catch((e) => setError(e.message));
  }, [ready, user]);

  if (authEnabled && ready && !user) {
    return (
      <main className="signin" id="main">
        <Motif className="signin-motif" width={216} height={76} />
        <h1>Yukdhis</h1>
        <p className="muted">Autonomous Compliance Engineer for banking</p>
        <button className="primary" onClick={() => signIn()}>Sign in with Google</button>
      </main>
    );
  }

  return (
    <>
    <div className="app" inert={assistantOpen}>
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className={`sidebar no-print${menuOpen ? " open" : ""}`}>
        <div className="sidebar-head">
          <Motif className="sidebar-motif" width={232} height={84} />
          <div className="brand">
            <span className="brand-mark" aria-hidden="true"><Icon name="graph" size={15} /></span>
            <div><strong>Yukdhis</strong><span>Autonomous Compliance Engineer</span></div>
          </div>
          <button className="menu-btn icon" aria-expanded={menuOpen} aria-controls="sidebar-body" aria-label="Menu" onClick={() => setMenuOpen(!menuOpen)}>
            <Icon name={menuOpen ? "close" : "menu"} size={18} />
          </button>
        </div>
        <div id="sidebar-body" className="sidebar-body">
          <nav aria-label="Sections">
            <ul className="nav-list">
              {TABS.map((t) => (
                <li key={t}>
                  <button className={t === tab ? "nav-item active" : "nav-item"} aria-current={t === tab ? "page" : undefined} onClick={() => go(t)}>
                    <Icon name={ICONS[t]} size={16} /><span>{t}</span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>
          <div className="who">
            {config && <span className="pill neutral mono" title="Tenant">{config.tenant_id}</span>}
            {config && <span className={config.llm_ready ? "pill ok" : "pill warn"} role="status">{config.llm_ready ? config.reasoning_model : "LLM offline"}</span>}
            {authEnabled && <button className="ghost signout" onClick={() => logOut()}><Icon name="signout" size={14} /> Sign out</button>}
          </div>
        </div>
      </aside>
      <div className="content">
        {error && <div className="banner error" role="alert">{error}</div>}
        <main id="main" tabIndex={-1}>
          <h1 className="sr-only">{tab}</h1>
          {tab === "Analyze" && config && <AnalyzePanel config={config} onOpenInReports={openInReports} />}
          {tab === "Reports" && <ReportsPanel onOpenReportChange={setOpenReportId} initialReportId={reportToOpen} />}
          {tab === "Knowledge graph" && config && <Suspense fallback={<div className="card"><Skeleton rows={6} label="Loading graph" /></div>}><GraphView defaultAsOf={config.as_of} /></Suspense>}
          {tab === "Privacy shield" && <PrivacyPanel />}
        </main>
        <footer className="no-print">
          Gemini 2.5 Pro on Vertex AI · Google ADK · Spanner Graph · Cloud SQL · Document AI · Cloud DLP + Gemma · Cloud Run · Firebase
          {config && <span className="mono"> · backends: {Object.entries(config.backends).map(([k, v]) => `${k}=${v}`).join(", ")}</span>}
        </footer>
      </div>
    </div>
    {config && (
      <Suspense fallback={null}>
        <AssistantDrawer open={assistantOpen} onOpenChange={setAssistantOpen} llmReady={config.llm_ready}
          reportId={tab === "Reports" ? openReportId : null} />
      </Suspense>
    )}
    </>
  );
}
