import type { Conversation, RunInfo } from "./types";
import { runTitle } from "./run-utils";

export type DotTone = "info live" | "neutral" | "ok" | "bad" | "warn";
export interface HistoryItem {
  key: string; kind: "run" | "conversation"; id: string; title: string; at: number; to: string;
  tone?: DotTone; statusLabel?: string; watcher?: boolean; unseen?: boolean;
}
export type HistoryGroup = { label: string; items: HistoryItem[] };

const time = (value?: string | null) => { const t = value ? Date.parse(value) : NaN; return Number.isNaN(t) ? 0 : t; };

const RUN_DOT: Record<string, { tone: DotTone; label: string }> = {
  QUEUED: { tone: "neutral", label: "Queued" }, RUNNING: { tone: "info live", label: "Running" }, CANCEL_REQUESTED: { tone: "warn", label: "Cancelling" },
  AWAITING_REVIEW: { tone: "warn", label: "Needs review" }, SUCCEEDED: { tone: "ok", label: "Done" }, FAILED: { tone: "bad", label: "Failed" },
  CANCELLED: { tone: "neutral", label: "Cancelled" }, REJECTED: { tone: "neutral", label: "Rejected" },
};

export function runItem(run: RunInfo, seen: Set<string>): HistoryItem {
  const dot = RUN_DOT[run.status] ?? { tone: "neutral" as const, label: run.status };
  const watcher = run.created_by === "watcher";
  return {
    key: run.run_id, kind: "run", id: run.run_id, title: runTitle(run), at: time(run.updated_at) || time(run.created_at),
    to: `/chat/${run.run_id}`, tone: dot.tone, statusLabel: dot.label, watcher, unseen: watcher && !seen.has(run.run_id),
  };
}
export const conversationItem = (c: Conversation): HistoryItem => ({
  key: c.conversation_id, kind: "conversation", id: c.conversation_id, title: c.title || "Untitled", at: time(c.updated_at) || time(c.created_at), to: `/chat/${c.conversation_id}`,
});

/** Today / Yesterday / Previous 7 days / Older, newest first inside each group. */
export function groupByTime(items: HistoryItem[], now: number = Date.now()): HistoryGroup[] {
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const day = 86_400_000;
  const buckets: [string, (t: number) => boolean][] = [
    ["Today", (t) => t >= startOfToday.getTime()],
    ["Yesterday", (t) => t >= startOfToday.getTime() - day],
    ["Previous 7 days", (t) => t >= startOfToday.getTime() - 7 * day],
    ["Older", () => true],
  ];
  const groups: HistoryGroup[] = buckets.map(([label]) => ({ label, items: [] }));
  for (const item of [...items].sort((a, b) => b.at - a.at)) groups[buckets.findIndex(([, test]) => test(item.at))].items.push(item);
  return groups.filter((g) => g.items.length);
}
