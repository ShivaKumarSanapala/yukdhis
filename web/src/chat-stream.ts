import type { Dispatch, SetStateAction } from "react";
import { streamChat } from "./api";
import type { ChatMessage } from "./types";

export interface NodeContext { id: string; type: string; name: string }

/** Context line the assistant receives for a graph node (no backend field needed). */
export const nodeContextLine = (n: NodeContext) =>
  `[UI context: the user is looking at knowledge-graph node ${n.id} (${n.type}: ${n.name}).]`;

/**
 * Sends one question and folds the SSE stream into a user message plus a live assistant draft in `setMessages`.
 * Resolves when the stream ends (also on abort or error: the draft carries the state).
 */
export async function askAssistant(opts: {
  display: string; wire: string; conversationId?: string; threadId?: string; reportId?: string;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>; signal: AbortSignal;
  onStart: (data: { conversation_id: string; title: string; created: boolean }) => void;
}): Promise<void> {
  const { setMessages } = opts;
  const stamp = Date.now();
  const draftId = `draft-${stamp}`;
  setMessages((m) => [...m,
    { message_id: `local-${stamp}`, role: "user", content: opts.display, status: "complete" },
    { message_id: draftId, role: "assistant", content: "", status: "streaming", trace: { thinking: "", tool_calls: [] } }]);
  const update = (fn: (m: ChatMessage) => ChatMessage) => setMessages((all) => all.map((m) => (m.message_id === draftId ? fn(m) : m)));
  const trace = (m: ChatMessage) => m.trace ?? { thinking: "", tool_calls: [] };
  try {
    await streamChat({ text: opts.wire, conversation_id: opts.threadId ? undefined : opts.conversationId, thread_id: opts.threadId, context: opts.reportId ? { report_id: opts.reportId } : undefined }, {
      onStart: opts.onStart,
      onThinking: (delta) => update((m) => ({ ...m, trace: { ...trace(m), thinking: trace(m).thinking + delta } })),
      onText: (delta) => update((m) => ({ ...m, content: m.content + delta })),
      onToolCall: (call) => update((m) => ({ ...m, trace: { ...trace(m), tool_calls: [...trace(m).tool_calls, { ...call, status: "running" }] } })),
      onToolResult: (result) => update((m) => ({
        ...m, trace: { ...trace(m), tool_calls: trace(m).tool_calls.some((c) => c.id === result.id)
          ? trace(m).tool_calls.map((c) => (c.id === result.id ? { ...c, ...result } : c))
          : [...trace(m).tool_calls, { args: {}, ...result }] },
      })),
      onError: (message) => update((m) => ({ ...m, error: message })),
      onDone: (d) => update((m) => ({ ...m, message_id: d.message_id, status: d.status })),
    }, opts.signal);
    // A stream that ends without "done" (e.g. the no-LLM error) is not a complete answer.
    update((m) => (m.status === "streaming" ? { ...m, status: m.error ? "error" : "stopped" } : m));
  } catch (e) {
    const aborted = (e as Error).name === "AbortError";
    update((m) => ({
      ...m, status: aborted ? "stopped" : "error", error: aborted ? m.error : (e as Error).message,
      trace: { ...trace(m), tool_calls: trace(m).tool_calls.map((c) => (c.status === "running" ? { ...c, status: "cancelled" } : c)) },
    }));
  }
}
