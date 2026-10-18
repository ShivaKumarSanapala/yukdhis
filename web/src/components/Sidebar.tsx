import { useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../store";
import { conversationItem, groupByTime, runItem, type HistoryItem } from "../history-utils";
import { seenWatcher } from "../links";
import Icon, { BrandMark } from "./Icons";

const NAV = [
  { to: "/", label: "Chat", icon: "chat", match: (p: string) => p === "/" || p.startsWith("/chat") },
  { to: "/reports", label: "Reports", icon: "reports", match: (p: string) => p.startsWith("/reports") },
  { to: "/graph", label: "Knowledge graph", icon: "graph", match: (p: string) => p.startsWith("/graph") },
] as const;

function HistoryRow({ item, current, editing, onEdit, onSave, onCancelEdit, onDelete }: {
  item: HistoryItem; current: boolean; editing: boolean; onEdit: () => void; onSave: (title: string) => void; onCancelEdit: () => void; onDelete: () => void;
}) {
  const [title, setTitle] = useState(item.title);
  if (editing) {
    return (
      <li className="hist-row editing">
        <input autoFocus aria-label="Conversation title" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} onBlur={() => onSave(title)}
          onKeyDown={(e) => { if (e.key === "Enter") onSave(title); if (e.key === "Escape") { e.stopPropagation(); onCancelEdit(); } }} />
      </li>
    );
  }
  return (
    <li className={`hist-row${current ? " active" : ""}`}>
      <Link to={item.to} className="hist-link" aria-current={current ? "page" : undefined} title={item.title}>
        <span className="hist-lead" aria-hidden="true">
          {item.kind === "run" ? <span className={`dot ${item.tone ?? "neutral"}`} /> : <Icon name="chat" size={13} />}
        </span>
        <span className="hist-title">{item.title}</span>
        {item.kind === "run" && <span className="sr-only"> ({item.statusLabel})</span>}
        {item.unseen && <span className="hist-new" title="From the circular watcher">watcher</span>}
      </Link>
      {item.kind === "conversation" && (
        <span className="hist-actions">
          <button className="icon ghost" onClick={onEdit} aria-label={`Rename ${item.title}`}><Icon name="edit" size={13} /></button>
          <button className="icon ghost" onClick={onDelete} aria-label={`Delete ${item.title}`}><Icon name="trash" size={13} /></button>
        </span>
      )}
    </li>
  );
}

export default function Sidebar({ collapsed, onCollapse, open, onClose, isNarrow }: {
  collapsed: boolean; onCollapse: () => void; open: boolean; onClose: () => void; isNarrow: boolean;
}) {
  const { config, identity, runs, inbox, conversations, setConversations, signOut, historyError } = useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [userMenu, setUserMenu] = useState(false);
  const [error, setError] = useState("");
  const userRef = useRef<HTMLDivElement>(null);
  const railed = collapsed && !isNarrow;

  useEffect(() => {
    if (!userMenu) return;
    const outside = (e: MouseEvent) => { if (!userRef.current?.contains(e.target as Node)) setUserMenu(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setUserMenu(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", esc); };
  }, [userMenu]);

  const seen = seenWatcher();
  const items = useMemo(() => {
    const unseenInbox = new Set(inbox.filter((r) => !seen.has(r.run_id)).map((r) => r.run_id));
    // conversations tied to a run or report are shown under that run, not on their own
    return {
      inbox: inbox.filter((r) => unseenInbox.has(r.run_id)).map((r) => runItem(r, seen)),
      all: [...runs.filter((r) => !unseenInbox.has(r.run_id)).map((r) => runItem(r, seen)),
        ...conversations.filter((c) => !c.thread_id).map(conversationItem)],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs, inbox, conversations, location.pathname]);
  const q = query.trim().toLowerCase();
  const filtered = q ? items.all.filter((i) => i.title.toLowerCase().includes(q)) : items.all;
  const groups = groupByTime(filtered);
  const here = location.pathname;
  const isCurrent = (item: HistoryItem) => here === item.to;

  const save = async (item: HistoryItem, title: string) => {
    setEditing(null);
    const next = title.trim();
    if (!next || next === item.title) return;
    try {
      const updated = await api.renameConversation(item.id, next);
      setConversations((list) => list.map((c) => (c.conversation_id === updated.conversation_id ? { ...c, title: updated.title } : c)));
    } catch (e) { setError((e as Error).message); }
  };
  const remove = async (item: HistoryItem) => {
    if (!window.confirm(`Delete "${item.title}"? This cannot be undone.`)) return;
    try {
      await api.deleteConversation(item.id);
      setConversations((list) => list.filter((c) => c.conversation_id !== item.id));
      if (here === item.to) navigate("/");
    } catch (e) { setError((e as Error).message); }
  };

  return (
    <aside id="sidebar" className={`sidebar no-print${railed ? " mini" : ""}${isNarrow ? " narrow" : ""}${open ? " open" : ""}`} aria-label="Workspace sidebar" {...(isNarrow && !open ? { inert: true } : {})}>
      <div className="sidebar-head">
        <Link to="/" className="brand" aria-label="Yukdhis home" onClick={onClose}>
          <span className="brand-mark" aria-hidden="true"><BrandMark size={18} /></span>
          {!railed && <span className="brand-name"><strong>Yukdhis</strong></span>}
        </Link>
        {isNarrow ? (
          <button className="icon ghost" onClick={onClose} aria-label="Close menu"><Icon name="close" size={18} /></button>
        ) : (
          <button className="icon ghost" onClick={onCollapse} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed} aria-controls="sidebar-body"><Icon name="panel" size={18} /></button>
        )}
      </div>
      <div id="sidebar-body" className="sidebar-body">
        <Link to="/" className="new-chat" onClick={onClose} title="New chat" state={{ fresh: Date.now() }}>
          <Icon name="plus" size={16} />{!railed && <span>New chat</span>}
        </Link>
        <nav aria-label="Sections">
          <ul className="nav-list">
            {NAV.map((n) => {
              const active = n.match(here);
              return (
                <li key={n.to}>
                  <NavLink to={n.to} className={active ? "nav-item active" : "nav-item"} aria-current={active ? "page" : undefined} title={n.label} onClick={onClose}>
                    <Icon name={n.icon} size={16} />{!railed && <span>{n.label}</span>}
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </nav>
        {!railed && (
          <div className="history" aria-label="Chat history" role="region">
            <label className="hist-search">
              <span className="sr-only">Search history</span>
              <Icon name="search" size={14} />
              <input type="search" placeholder="Search chats" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            {(error || historyError) && <div className="banner error" role="alert">{error || historyError}</div>}
            <div className="hist-scroll" onClick={(e) => { if ((e.target as HTMLElement).closest("a")) onClose(); }}>
              {!q && items.inbox.length > 0 && (
                <section aria-labelledby="hist-inbox">
                  <h3 id="hist-inbox" className="hist-label"><Icon name="inbox" size={12} /> Watcher inbox <span className="badge">{items.inbox.length}</span></h3>
                  <ul>{items.inbox.map((i) => <HistoryRow key={i.key} item={i} current={isCurrent(i)} editing={false} onEdit={() => undefined} onSave={() => undefined} onCancelEdit={() => undefined} onDelete={() => undefined} />)}</ul>
                </section>
              )}
              {groups.map((g) => (
                <section key={g.label} aria-label={g.label}>
                  <h3 className="hist-label">{g.label}</h3>
                  <ul>
                    {g.items.map((i) => (
                      <HistoryRow key={i.key} item={i} current={isCurrent(i)} editing={editing === i.key}
                        onEdit={() => setEditing(i.key)} onSave={(t) => void save(i, t)} onCancelEdit={() => setEditing(null)} onDelete={() => void remove(i)} />
                    ))}
                  </ul>
                </section>
              ))}
              {!groups.length && !items.inbox.length && <p className="muted hist-empty">{q ? "No chats match." : "Your analyses and questions will appear here."}</p>}
            </div>
          </div>
        )}
      </div>
      <div className="user-block" ref={userRef}>
        {userMenu && (
          <div className="user-menu" role="menu" aria-label="Account">
            <div className="user-menu-status">
              <span className="pill neutral mono" title="Tenant">{config.tenant_id}</span>
              <span className={config.llm_ready ? "pill ok" : "pill warn"} role="status">{config.llm_ready ? config.reasoning_model : "LLM offline"}</span>
            </div>
            <p className="muted user-menu-note mono">{Object.entries(config.backends).map(([k, v]) => `${k}=${v}`).join(" · ")}</p>
            {signOut ? (
              <button role="menuitem" onClick={() => signOut()}><Icon name="signout" size={14} /> Sign out</button>
            ) : <p className="muted user-menu-note">Signed in locally (no sign-in required on this server).</p>}
          </div>
        )}
        <button className="user-btn" aria-haspopup="menu" aria-expanded={userMenu} onClick={() => setUserMenu(!userMenu)} title={`${identity.name} ${identity.email}`}>
          <span className="avatar" aria-hidden="true">{identity.initials}</span>
          {!railed && (
            <>
              <span className="user-text"><b>{identity.name}</b>{identity.email && <small>{identity.email}</small>}</span>
              <span className={`dot ${config.llm_ready ? "ok" : "warn"}`} role="img" aria-label={config.llm_ready ? "LLM ready" : "LLM offline"} title={config.llm_ready ? "LLM ready" : "LLM offline"} />
              <Icon name="more" size={16} />
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
