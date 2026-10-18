import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useApp } from "../store";
import Icon from "./Icons";

/** Technology stack, shown in the status popover (not in the account menu). */
const GOOGLE_STACK = [
  "Gemini 2.5 Pro (Vertex AI)", "Google ADK", "Spanner Graph", "Cloud SQL (Postgres)", "Document AI", "Cloud DLP",
  "Memory Bank (Agent Engine)", "Pub/Sub", "Cloud Run", "Firebase",
] as const;

/** Floating panel rendered in a portal and positioned from its anchor, so the sidebar's overflow cannot clip it. */
function Floating({ anchor, onClose, label, className, children }: {
  anchor: HTMLElement | null; onClose: () => void; label: string; className: string; children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  useLayoutEffect(() => {
    if (!anchor) return;
    const place = () => {
      const r = anchor.getBoundingClientRect();
      setPos({ left: Math.min(r.right + 8, window.innerWidth - 8), bottom: Math.max(8, window.innerHeight - r.bottom) });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [anchor]);
  useEffect(() => {
    const outside = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !anchor?.contains(t)) onClose();
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { onClose(); anchor?.focus(); } };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", esc); };
  }, [anchor, onClose]);
  if (!pos) return null;
  return createPortal(
    <div ref={ref} role="dialog" aria-label={label} className={`fixed z-50 ${className}`} style={{ left: pos.left, bottom: pos.bottom, maxWidth: "calc(100vw - 16px)" }}>
      {children}
    </div>,
    document.body,
  );
}

export function Avatar({ size }: { size: "sm" | "lg" }) {
  const { identity } = useApp();
  const [broken, setBroken] = useState(false);
  const dim = size === "lg" ? "h-14 w-14 text-lg" : "h-10 w-10 text-sm";
  if (identity.photoURL && !broken) {
    return <img src={identity.photoURL} alt="" referrerPolicy="no-referrer" onError={() => setBroken(true)} className={`${dim} shrink-0 rounded-full object-cover`} />;
  }
  return <span aria-hidden="true" className={`${dim} flex shrink-0 items-center justify-center rounded-full bg-indigo-600 font-semibold text-white`}>{identity.initials}</span>;
}

const item = "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-zinc-800 no-underline hover:bg-zinc-100 focus-visible:bg-zinc-100 focus-visible:outline-none";

/** Gemini-style account button and popover. */
export function AccountMenu({ railed }: { railed: boolean }) {
  const { identity, signOut } = useApp();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  return (
    <>
      <button ref={setAnchor} type="button" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        title={`${identity.name} ${identity.email}`}
        className={`flex w-full items-center gap-3 rounded-xl border-0 bg-transparent p-1.5 text-left hover:bg-zinc-100 ${railed ? "justify-center" : "justify-start"}`}>
        <Avatar size="sm" />
        {!railed && (
          <span className="flex min-w-0 flex-1 flex-col leading-tight">
            <b className="truncate text-[13px] text-zinc-900">{identity.name}</b>
            {identity.email && <small className="truncate text-[11.5px] font-normal text-zinc-500">{identity.email}</small>}
          </span>
        )}
      </button>
      {open && (
        <Floating anchor={anchor} onClose={() => setOpen(false)} label="Account" className="w-80 rounded-2xl border border-zinc-200 bg-white p-2 text-zinc-900 shadow-xl">
          <div className="flex items-center gap-3 p-3">
            <Avatar size="lg" />
            <div className="min-w-0">
              <p className="m-0 truncate text-base font-semibold text-zinc-900">{identity.name}</p>
              <p className="m-0 flex items-center gap-2 text-xs text-zinc-500">
                <span className="truncate">{identity.email}</span>
                <span className="shrink-0 rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-700">Pro</span>
              </p>
            </div>
          </div>
          <div className="mt-1 grid gap-0.5" role="menu">
            {identity.photoURL && (
              <a role="menuitem" className={item} href="https://myaccount.google.com/" target="_blank" rel="noreferrer">Manage your Google Account</a>
            )}
            {signOut ? (
              <>
                <div className="my-1 border-t border-zinc-200" role="separator" />
                <button role="menuitem" type="button" className={`${item} border-0 bg-transparent`} onClick={() => { setOpen(false); signOut(); }}>
                  <Icon name="signout" size={16} /> Sign out
                </button>
              </>
            ) : <p className="m-0 px-3 py-2 text-xs text-zinc-500">Signed in locally (no sign-in required on this server).</p>}
          </div>
        </Floating>
      )}
    </>
  );
}

/** Tiny, separate status button: tenant, model readiness and the Google Cloud stack. */
export function StatusInfo() {
  const { config } = useApp();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  return (
    <>
      <button ref={setAnchor} type="button" aria-haspopup="dialog" aria-expanded={open} aria-label="System status" title="System status"
        onClick={() => setOpen((v) => !v)} className="mx-auto flex h-6 w-6 items-center justify-center rounded-full border-0 bg-transparent p-0 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700">
        <Icon name="info" size={14} />
      </button>
      {open && (
        <Floating anchor={anchor} onClose={() => setOpen(false)} label="System status" className="w-72 rounded-2xl border border-zinc-200 bg-white p-4 text-zinc-800 shadow-xl">
          <div className="mb-3 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-zinc-100 px-2 py-0.5 font-mono" title="Tenant">{config.tenant_id}</span>
            <span role="status" className={`rounded-full px-2 py-0.5 ${config.llm_ready ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>{config.llm_ready ? config.reasoning_model : "LLM offline"}</span>
          </div>
          <ul className="m-0 grid list-none gap-1 p-0 text-xs text-zinc-600" aria-label="Google Cloud technology stack">
            {GOOGLE_STACK.map((t) => <li key={t}>{t}</li>)}
          </ul>
        </Floating>
      )}
    </>
  );
}
