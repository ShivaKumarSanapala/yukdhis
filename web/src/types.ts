export interface Config {
  tenant_id: string;
  user: string;
  default_mode: "agentic" | "deterministic";
  llm_ready: boolean;
  reasoning_model: string;
  as_of: string;
  backends: Record<string, string>;
}
export interface Sample { sample_id: string; title: string }
/** QUEUED | RUNNING | CANCEL_REQUESTED | AWAITING_REVIEW | SUCCEEDED | FAILED | CANCELLED | REJECTED */
export type RunStatus = string;
export type SourceKind = "upload" | "sample" | "text" | "url" | "watcher" | string;
/** Fields after `error` are newer; old runs may omit or null them. */
export interface RunInfo {
  run_id: string; status: RunStatus; mode: string; report_id: string | null; error: string | null;
  created_at?: string; updated_at?: string; document?: Record<string, unknown>;
  created_by?: string | null; source_kind?: SourceKind; filename?: string | null; doc_sha256?: string | null;
  current_stage?: string | null; started_at?: string | null; finished_at?: string | null; queued_position?: number | null;
  as_of?: string | null; review_rules?: boolean; rerun_of?: string | null; rules?: ExtractedRule[];
  previous_run_ids?: string[]; previous_report_id?: string | null; is_mine?: boolean;
}
/** A rule as extracted (full server model); only title, modality and effective date are edited in review. */
export type ExtractedRule = Rule & { scope?: unknown; processes?: string[]; segments?: string[] } & Record<string, unknown>;
export interface StartedRuns { run_id: string; mode: string; run_ids: string[]; runs: { run_id: string; mode: string; filename?: string }[] }
export interface RunEvent { seq: number; ts: string; stage: string; kind: string; payload: Record<string, unknown> }
export interface ReportListItem { report_id: string; title: string; created_at: string; mode: string; summary: Summary }
export interface Summary {
  rules: number; checks: number; gaps: number; products_in_scope: number;
  loans_with_violations: number; customers_with_violations: number; loans_exposed_to_control_gaps: number;
  statutory_penalty_inr: number; control_gaps: string[]; controls_pending_review?: string[]; by_branch: Record<string, number>;
  overall_status: string; privacy_redactions: number;
  /** Newer reports only: rule ids whose scope could not be resolved. */
  unresolved_rules?: string[];
  /** Newer reports only: obligations that cannot be machine-checked. */
  unexecutable_obligations?: number;
}
/** An obligation the engine cannot check with its check kinds or data (newer reports only). */
export interface Unexecutable { citation: string; reason: string; missing_data: string[] }
export interface Check {
  rule_id: string; check_index: number; kind: string; description: string; status: string;
  in_scope: number; violations: number; exposure_inr: number; penalty_inr: number;
  by_branch: Record<string, number>; products_missing_control: string[]; worklist_id: string | null; evidence: string;
}
export interface Rule {
  rule_id: string; title: string; description: string; effective_date: string; penalty_risk: string; status: string;
  confidence: number; citation: { snippet: string; chunk_id?: string; page?: number };
  checks: { kind: string; description: string }[];
  /** Newer reports only; absent means MUST. */
  modality?: "MUST" | "MAY" | "MUST_NOT" | string;
}
export interface Radius {
  rule_id: string; as_of: string; products: { id: string; name: string; owner: string | null }[];
  processes: { id: string; name: string; bian_domain: string; owner: string | null }[];
  controls_required: string[]; control_gaps: Record<string, string[]>; owners: { owner_of: string; team: string }[];
  segments: { id: string; name: string }[]; paths: string[][]; query_language: string; queries: string[];
  /** Newer reports only; absent means the scope was resolved. */
  scope_status?: "RESOLVED" | "UNRESOLVED" | string;
  scope_notes?: string[];
}
export interface Action {
  action_id: string; workflow_id: string; rule_id: string; worklist_id: string | null; target_count: number;
  notice_template: string; owner: string; status: string; decided_by?: string | null;
}
export interface ImpactReport {
  report_id: string; as_of: string; mode: string; generated_at: string; narrative: string;
  policy: { policy_id: string; title: string; issuer: string; reference: string; effective_date: string; source_uri: string };
  rules: Rule[]; results: Check[]; blast_radius: Radius[]; summary: Summary; actions: Action[];
  limitations: string[]; validation_issues: { severity: string; rule_id: string | null; message: string }[];
  extraction_provenance: Record<string, unknown>;
  data_profile: { executed_sql?: string; rows?: Record<string, unknown>[] };
  /** Newer reports only; may be absent or malformed in old ones, so read it through unexecutableOf(). */
  unexecutable?: unknown;
}

// Assistant chat ----------------------------------------------------------------
export interface ToolCallTrace {
  id: string; name: string; args: unknown; output?: unknown;
  status: "running" | "ok" | "error" | "cancelled" | string; duration_ms?: number | null;
}
export interface ChatTrace { thinking: string; tool_calls: ToolCallTrace[] }
export interface ChatMessage {
  message_id: string; role: "user" | "assistant"; content: string;
  /** complete | stopped | error, plus "streaming" while a local draft is live. */
  status: string; created_at?: string; trace?: ChatTrace; error?: string;
}
export interface Conversation { conversation_id: string; title: string; created_at: string; updated_at: string; message_count?: number }
export interface ConversationDetail extends Conversation { messages: ChatMessage[] }
