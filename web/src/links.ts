// Which assistant conversation belongs to which run or report. Kept in localStorage: the API has no such field.
const KEY = "yukdhis.threadConversations";

// Conversations belong to a user on the server (another user's id answers "Unknown conversation"), so the run/report
// links are kept per signed-in user. Without this, signing in as someone else in the same browser reuses the
// previous user's conversation id for the same run.
let scope = "";
export const setLinkScope = (email: string) => { scope = email.trim().toLowerCase(); };
const key = () => (scope ? `${KEY}:${scope}` : KEY);

function read(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(key()) ?? "{}") as Record<string, string>; } catch { return {}; }
}
function write(map: Record<string, string>) {
  try { localStorage.setItem(key(), JSON.stringify(map)); } catch { /* private mode */ }
}

/** Thread keys are run ids or report ids. */
export const conversationFor = (threadId: string): string | undefined => read()[threadId];
export const threadFor = (conversationId: string): string | undefined => Object.entries(read()).find(([, c]) => c === conversationId)?.[0];
export function linkConversation(threadId: string, conversationId: string) {
  const map = read();
  map[threadId] = conversationId;
  write(map);
}
/** Conversation ids that are part of a run or report thread (the sidebar lists the thread, not the conversation). */
export const linkedConversations = (): Set<string> => new Set(Object.values(read()));
export function unlinkConversation(conversationId: string) {
  const map = read();
  for (const [k, v] of Object.entries(map)) if (v === conversationId) delete map[k];
  write(map);
}

const SEEN = "yukdhis.watcher.seen";
export const seenWatcher = (): Set<string> => { try { return new Set(JSON.parse(localStorage.getItem(SEEN) ?? "[]") as string[]); } catch { return new Set(); } };
export function markWatcherSeen(runId: string) {
  const seen = seenWatcher();
  if (seen.has(runId)) return;
  seen.add(runId);
  try { localStorage.setItem(SEEN, JSON.stringify([...seen].slice(-200))); } catch { /* private mode */ }
}
