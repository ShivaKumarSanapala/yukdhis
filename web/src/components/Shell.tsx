import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../store";
import type { NodeContext } from "../chat-stream";
import Sidebar from "./Sidebar";
import ChatWorkspace from "./ChatWorkspace";
const ReportsPanel = lazy(() => import("./ReportsPanel"));
import Icon, { BrandMark } from "./Icons";
import { Skeleton } from "./Motif";

const GraphView = lazy(() => import("./GraphView"));

const COLLAPSE_KEY = "yukdhis.sidebar.collapsed";
const NARROW = "(max-width: 900px)";

function useNarrow() {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW).matches);
  useEffect(() => {
    const query = window.matchMedia(NARROW);
    const on = () => setNarrow(query.matches);
    query.addEventListener("change", on);
    return () => query.removeEventListener("change", on);
  }, []);
  return narrow;
}

function GraphPage() {
  const { config } = useApp();
  const navigate = useNavigate();
  const ask = (node: NodeContext) => navigate("/", { state: { node, prefill: `What should I know about ${node.name} (${node.type})?`, fresh: Date.now() } });
  return (
    <Suspense fallback={<div className="card"><Skeleton rows={6} label="Loading graph" /></div>}>
      <GraphView defaultAsOf={config.as_of} onAskAssistant={ask} />
    </Suspense>
  );
}

/** App frame: sidebar (rail, full or overlay drawer), a top bar on narrow screens, and the routed main area. */
export default function Shell() {
  const location = useLocation();
  const narrow = useNarrow();
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { return false; } });
  const [drawer, setDrawer] = useState(false);
  const menuBtn = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);

  const toggle = () => setCollapsed((c) => { try { localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1"); } catch { /* private mode */ } return !c; });
  useEffect(() => { setDrawer(false); }, [location.pathname]);
  useEffect(() => { if (!narrow) setDrawer(false); }, [narrow]);
  useEffect(() => {  // overlay drawer: Esc closes, focus returns to the menu button
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setDrawer(false); menuBtn.current?.focus(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawer]);
  useEffect(() => { if (drawer) document.querySelector<HTMLElement>("#sidebar .new-chat")?.focus(); }, [drawer]);

  const chat = location.pathname === "/" || location.pathname.startsWith("/chat");
  const title = chat ? "Chat" : location.pathname.startsWith("/reports") ? "Reports" : "Knowledge graph";
  return (
    <div className={`app${collapsed && !narrow ? " is-rail" : ""}`}>
      <a className="skip-link" href="#main" onClick={(e) => { e.preventDefault(); mainRef.current?.focus(); }}>Skip to content</a>
      {narrow && (
        <header className="topbar no-print">
          <button ref={menuBtn} className="icon ghost" onClick={() => setDrawer(true)} aria-label="Open menu" aria-expanded={drawer} aria-controls="sidebar"><Icon name="menu" size={20} /></button>
          <span className="topbar-brand"><span className="brand-mark" aria-hidden="true"><BrandMark size={16} /></span><strong>Anuvi</strong></span>
        </header>
      )}
      {narrow && drawer && <div className="drawer-scrim no-print" onClick={() => setDrawer(false)} aria-hidden="true" />}
      <Sidebar collapsed={collapsed} onCollapse={toggle} open={drawer} onClose={() => { setDrawer(false); }} isNarrow={narrow} />
      <main id="main" ref={mainRef} tabIndex={-1} className={chat ? "main-chat" : "main-page"}>
        <h1 className="sr-only">{title}</h1>
        <Suspense fallback={<div className="page"><Skeleton rows={5} label="Loading" /></div>}>
        <Routes>
          <Route path="/" element={<ChatWorkspace />} />
          <Route path="/chat/:id" element={<ChatWorkspace />} />
          <Route path="/reports" element={<div className="page"><ReportsPanel /></div>} />
          <Route path="/reports/:reportId" element={<div className="page"><ReportsPanel /></div>} />
          <Route path="/graph" element={<div className="page"><GraphPage /></div>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}
