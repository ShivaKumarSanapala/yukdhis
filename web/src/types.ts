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
export interface RunInfo { run_id: string; status: string; mode: string; report_id: string | null; error: string | null }
export interface RunEvent { seq: number; ts: string; stage: string; kind: string; payload: Record<string, unknown> }
export interface ReportListItem { report_id: string; title: string; created_at: string; mode: string; summary: Summary }
export interface Summary {
  rules: number; checks: number; gaps: number; products_in_scope: number;
  loans_with_violations: number; customers_with_violations: number; loans_exposed_to_control_gaps: number;
  statutory_penalty_inr: number; control_gaps: string[]; by_branch: Record<string, number>;
  overall_status: string; privacy_redactions: number;
}
export interface Check {
  rule_id: string; check_index: number; kind: string; description: string; status: string;
  in_scope: number; violations: number; exposure_inr: number; penalty_inr: number;
  by_branch: Record<string, number>; products_missing_control: string[]; worklist_id: string | null; evidence: string;
}
export interface Rule {
  rule_id: string; title: string; description: string; effective_date: string; penalty_risk: string; status: string;
  confidence: number; citation: { snippet: string; chunk_id?: string; page?: number };
  checks: { kind: string; description: string }[];
}
export interface Radius {
  rule_id: string; as_of: string; products: { id: string; name: string; owner: string | null }[];
  processes: { id: string; name: string; bian_domain: string; owner: string | null }[];
  controls_required: string[]; control_gaps: Record<string, string[]>; owners: { owner_of: string; team: string }[];
  segments: { id: string; name: string }[]; paths: string[][]; query_language: string; queries: string[];
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
}
