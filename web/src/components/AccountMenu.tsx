import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useApp } from "../store";
import Icon from "./Icons";

/** Technology stack, shown in the status popover (not in the account menu). */
const GOOGLE_STACK = [
  "Gemini 2.5 Pro (Vertex AI)", "Google ADK", "Spanner Graph", "Cloud SQL (Postgres)", "Document AI", "Cloud DLP",
  "Memory Bank (Agent Engine)", "Pub/Sub", "Cloud Run", "Firebase",
] as const;

/** True on phones and narrow tablets (below Tailwind's md breakpoint). */
function useIsPhone(): boolean {
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const onChange = () => setPhone(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return phone;
}

/** Floating panel rendered in a portal and anchored to its trigger. Clamped to the viewport so it never overflows. */
function Floating({ anchor, onClose, label, width, className, children }: {
  anchor: HTMLElement | null; onClose: () => void; label: string; width: number; className: string; children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  useLayoutEffect(() => {
    if (!anchor) return;
    const place = () => {
      const r = anchor.getBoundingClientRect();
      const maxLeft = window.innerWidth - width - 8;
      setPos({ left: Math.max(8, Math.min(r.right + 8, maxLeft)), bottom: Math.max(8, window.innerHeight - r.top + 8) });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [anchor, width]);
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
    <div ref={ref} role="dialog" aria-label={label} className={`fixed z-[60] ${className}`} style={{ left: pos.left, bottom: pos.bottom, width }}>
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

/** Phone: a bottom sheet over a dark backdrop. Portalled to <body>, so the sidebar's overflow and stacking cannot clip it. */
function MobileSheet({ onClose }: { onClose: () => void }) {
  const { identity, signOut } = useApp();
  const titleId = useId();
  const sheetRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    sheetRef.current?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => { document.body.style.overflow = previous; document.removeEventListener("keydown", esc); };
  }, [onClose]);
  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex flex-col justify-end bg-black/60 sm:justify-center"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="w-full rounded-t-3xl bg-zinc-900 px-4 pb-8 pt-4 text-zinc-100 shadow-2xl outline-none sm:mx-auto sm:max-w-sm sm:rounded-3xl"
      >
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wider text-zinc-400">Account</span>
          <button type="button" onClick={onClose} aria-label="Close account menu" style={{ width: 36, height: 36, minWidth: 36, minHeight: 36, padding: 0 }}
            className="flex h-9 w-9 items-center justify-center rounded-full border-0 bg-zinc-800 text-zinc-300 hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-indigo-400">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-4 w-4" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" strokeLinecap="round" /></svg>
          </button>
        </div>

        <div className="mt-2 flex flex-col items-center text-center">
          <Avatar size="lg" />
          <p id={titleId} className="m-0 mt-3 max-w-full truncate text-lg font-semibold text-white">{identity.name}</p>
          {identity.email && <p className="m-0 mt-0.5 max-w-full truncate text-sm text-zinc-400">{identity.email}</p>}
        </div>

        {signOut ? (
          <button type="button" onClick={() => { onClose(); signOut(); }}
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl bg-white py-3.5 text-base font-semibold text-zinc-900 shadow-sm hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-indigo-400">
            <Icon name="signout" size={16} /> Sign out
          </button>
        ) : (
          <p className="mt-6 text-center text-xs text-zinc-500">Signed in locally (no sign-in required on this server).</p>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** Gemini-style account button. Phones get a bottom sheet; wider screens get a popover anchored to the avatar. */
export function AccountMenu({ railed }: { railed: boolean }) {
  const { identity, signOut } = useApp();
  const phone = useIsPhone();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const close = () => setOpen(false);
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
      {open && phone && <MobileSheet onClose={close} />}
      {open && !phone && (
        <Floating anchor={anchor} onClose={close} label="Account" width={320} className="rounded-2xl border border-zinc-200 bg-white p-2 text-zinc-900 shadow-xl">
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
            {signOut ? (
              <>
                <div className="my-1 border-t border-zinc-200" role="separator" />
                <button role="menuitem" type="button" className={`${item} border-0 bg-transparent`} onClick={() => { close(); signOut(); }}>
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
        <Floating anchor={anchor} onClose={() => setOpen(false)} label="System status" width={288} className="w-72 rounded-2xl border border-zinc-200 bg-white p-4 text-zinc-800 shadow-xl">
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
