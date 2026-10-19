import type { Dispatch, SetStateAction } from "react";
import { reattachChat, streamChat, type StreamHandlers } from "./api";
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
  setMessages((m) => [...m, { message_id: `local-${stamp}`, role: "user", content: opts.display, status: "complete" }]);
  await foldInto(setMessages, (handlers) =>
    streamChat({ text: opts.wire, conversation_id: opts.threadId ? undefined : opts.conversationId, thread_id: opts.threadId,
      context: opts.reportId ? { report_id: opts.reportId } : undefined }, handlers, opts.signal), opts.onStart);
}

/** Re-attaches to a reply still being written on the server (the user message is already in the saved conversation). */
export async function attachAssistant(opts: { conversationId: string; setMessages: Dispatch<SetStateAction<ChatMessage[]>>; signal: AbortSignal }): Promise<void> {
  await foldInto(opts.setMessages, (handlers) => reattachChat(opts.conversationId, handlers, opts.signal));
}

async function foldInto(setMessages: Dispatch<SetStateAction<ChatMessage[]>>,
  open: (handlers: StreamHandlers) => Promise<void>, onStart?: StreamHandlers["onStart"]): Promise<void> {
  const draftId = `draft-${Date.now()}`;
  setMessages((m) => [...m,
    { message_id: draftId, role: "assistant", content: "", status: "streaming", trace: { thinking: "", tool_calls: [] } }]);
  const update = (fn: (m: ChatMessage) => ChatMessage) => setMessages((all) => all.map((m) => (m.message_id === draftId ? fn(m) : m)));
  const trace = (m: ChatMessage) => m.trace ?? { thinking: "", tool_calls: [] };
  // The answer is held back while the turn runs and shown in one piece when it is complete. Text written before a
  // tool call is a preamble (it can say "I cannot find that" before the tool finds it), so it is dropped at each call.
  let answer = "";
  try {
    await open({
      onStart,
      onThinking: (delta) => update((m) => ({ ...m, trace: { ...trace(m), thinking: trace(m).thinking + delta } })),
      onText: (delta) => { answer += delta; },
      onToolCall: (call) => {
        answer = "";
        update((m) => ({ ...m, trace: { ...trace(m), tool_calls: [...trace(m).tool_calls, { ...call, status: "running" }] } }));
      },
      onToolResult: (result) => update((m) => ({
        ...m, trace: { ...trace(m), tool_calls: trace(m).tool_calls.some((c) => c.id === result.id)
          ? trace(m).tool_calls.map((c) => (c.id === result.id ? { ...c, ...result } : c))
          : [...trace(m).tool_calls, { args: {}, ...result }] },
      })),
      onError: (message) => update((m) => ({ ...m, error: message })),
      onDone: (d) => update((m) => ({ ...m, message_id: d.message_id || m.message_id, status: d.status, content: answer.trim() })),
    });
    // A stream that ends without "done" (e.g. the no-LLM error) is not a complete answer.
    update((m) => (m.status === "streaming" ? { ...m, content: answer.trim(), status: m.error ? "error" : "stopped" } : m));
  } catch (e) {
    const aborted = (e as Error).name === "AbortError";
    update((m) => ({
      ...m, content: answer.trim(), status: aborted ? "stopped" : "error", error: aborted ? m.error : (e as Error).message,
      trace: { ...trace(m), tool_calls: trace(m).tool_calls.map((c) => (c.status === "running" ? { ...c, status: "cancelled" } : c)) },
    }));
  }
}
