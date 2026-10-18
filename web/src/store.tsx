import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Config, Conversation, RunInfo, Sample, StartedRuns } from "./types";
import { isActive, runStatusMeta, runTitle } from "./run-utils";

export interface Identity { name: string; email: string; initials: string }

export interface StartInput { sampleIds?: string[]; files?: File[]; text?: string; url?: string }

interface AppState {
  config: Config;
  identity: Identity;
  samples: Sample[];
  runs: RunInfo[];
  inbox: RunInfo[];
  conversations: Conversation[];
  setConversations: (update: (list: Conversation[]) => Conversation[]) => void;
  refreshHistory: () => Promise<void>;
  /** Starts one run per sample, file, text or URL with the default mode and as-of date. */
  startAnalysis: (input: StartInput) => Promise<StartedRuns>;
  signOut?: () => void;
  historyError: string;
}

const Ctx = createContext<AppState | null>(null);
export function useApp(): AppState {
  const value = useContext(Ctx);
  if (!value) throw new Error("useApp outside AppProvider");
  return value;
}

const initialsOf = (name: string) => {
  const words = name.replace(/@.*$/, "").split(/[\s._-]+/).filter(Boolean);
  return ((words[0]?.[0] ?? "?") + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
};
export const identityOf = (name: string | null | undefined, email: string | null | undefined): Identity => {
  const mail = email ?? "";
  const display = name?.trim() || (mail ? mail.replace(/@.*$/, "").replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "Local user");
  return { name: display, email: mail, initials: initialsOf(name?.trim() || mail || "Local user") };
};

function notify(run: RunInfo, status: string) {
  if (!run.is_mine || !document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(`Analysis ${runStatusMeta(status).label.toLowerCase()}`, { body: runTitle(run), tag: run.run_id }); } catch { /* unsupported */ }
}

/** Shell-level data: bootstrap config, the merged history (runs and conversations) and run starting. */
export function AppProvider({ config, identity, signOut, children }: { config: Config; identity: Identity; signOut?: () => void; children: ReactNode }) {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [inbox, setInbox] = useState<RunInfo[]>([]);
  const [conversations, setConversationsState] = useState<Conversation[]>([]);
  const [historyError, setHistoryError] = useState("");
  const statuses = useRef(new Map<string, string>());

  const refreshHistory = useCallback(async () => {
    try {
      const [list, watcher, convs] = await Promise.all([api.runs({ limit: 60 }), api.runs({ sourceKind: "watcher", limit: 8 }), api.conversations()]);
      for (const run of list) {
        const before = statuses.current.get(run.run_id);
        if (before && before !== run.status && !isActive(run) && run.status !== "AWAITING_REVIEW") notify(run, run.status);
        statuses.current.set(run.run_id, run.status);
      }
      setRuns(list);
      setInbox(watcher.filter((r) => r.created_by === "watcher" && !r.rerun_of));
      setConversationsState(convs);
      setHistoryError("");
    } catch (e) { setHistoryError((e as Error).message); }
  }, []);

  useEffect(() => { api.samples().then(setSamples).catch(() => undefined); }, []);
  useEffect(() => { void refreshHistory(); }, [refreshHistory]);
  const anyActive = runs.some(isActive) || inbox.some(isActive);
  useEffect(() => {  // quick while something runs, slow otherwise; also when the tab becomes visible again
    const timer = setInterval(() => { if (!document.hidden || anyActive) void refreshHistory(); }, anyActive ? 3000 : 20000);
    const onVisible = () => { if (!document.hidden) void refreshHistory(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [anyActive, refreshHistory]);

  const startAnalysis = useCallback(async (input: StartInput) => {
    if ("Notification" in window && Notification.permission === "default") void Notification.requestPermission().catch(() => undefined);
    // Defaults: agentic when the LLM is up, deterministic otherwise; analysis date is today (server config).
    const started = await api.startRuns({ ...input, mode: config.llm_ready ? "agentic" : "deterministic", asOf: config.as_of });
    void refreshHistory();
    return started;
  }, [config, refreshHistory]);

  const value = useMemo<AppState>(() => ({
    config, identity, samples, runs, inbox, conversations, historyError, signOut, refreshHistory, startAnalysis,
    setConversations: (update) => setConversationsState(update),
  }), [config, identity, samples, runs, inbox, conversations, historyError, signOut, refreshHistory, startAnalysis]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
