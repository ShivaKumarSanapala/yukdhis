import type { Check, ImpactReport, Radius, Rule, Summary, Unexecutable } from "./types";

/** Tone maps to a pill/status colour class; the text label is always shown as well. */
export type Tone = "ok" | "bad" | "warn" | "info" | "neutral";
export interface StatusMeta { label: string; tone: Tone; hint: string }

const OVERALL: Record<string, StatusMeta> = {
  NON_COMPLIANT: { label: "NON COMPLIANT", tone: "bad", hint: "At least one check found a gap." },
  UPCOMING: { label: "UPCOMING", tone: "info", hint: "The rules take effect after the analysis date, so they were not evaluated." },
  INCONCLUSIVE: { label: "Cannot assess", tone: "warn", hint: "Scope or data was not sufficient to assess compliance. This is not a clean result." },
  COMPLIANT: { label: "COMPLIANT", tone: "ok", hint: "All evaluated checks passed." },
};

const CHECK: Record<string, StatusMeta> = {
  GAP: { label: "GAP", tone: "bad", hint: "Violations or missing controls found." },
  COMPLIANT: { label: "COMPLIANT", tone: "ok", hint: "Evaluated and no gap found." },
  NOT_EVALUATED: { label: "NOT EVALUATED", tone: "warn", hint: "This check could not be evaluated. It is not a pass." },
  UPCOMING: { label: "UPCOMING", tone: "info", hint: "The rule takes effect after the analysis date." },
};

const humanise = (value: string) => value.replaceAll("_", " ");

/** Overall status; for old reports without a known value, derive it as before (gaps => non compliant). */
export function overallKey(summary: Summary): string {
  const raw = summary.overall_status;
  if (raw && OVERALL[raw]) return raw;
  if (raw) return raw;
  return (summary.gaps ?? 0) > 0 ? "NON_COMPLIANT" : "COMPLIANT";
}
export function overallMeta(summary: Summary): StatusMeta {
  const key = overallKey(summary);
  return OVERALL[key] ?? { label: humanise(key), tone: "neutral", hint: "" };
}
export function checkMeta(status: string): StatusMeta {
  return CHECK[status] ?? { label: humanise(status || "UNKNOWN"), tone: "neutral", hint: "" };
}
/** Checks that carry a real measurement; others show a dash instead of zeros. */
export const isMeasured = (status: string) => status === "GAP" || status === "COMPLIANT";

export const modalityOf = (rule: Rule) => rule.modality ?? "MUST";
export const modalityLabel = (modality: string) => (modality === "MAY" ? "MAY" : modality === "MUST_NOT" ? "MUST NOT" : modality);
export const modalityHint = (modality: string) =>
  modality === "MAY"
    ? "Permission, not evaluated as an obligation"
    : modality === "MUST_NOT"
      ? "Prohibition, not evaluated as an obligation"
      : "";

export const isUnresolved = (radius: Radius | undefined) => radius?.scope_status === "UNRESOLVED";
export const radiusFor = (report: ImpactReport, ruleId: string) => (report.blast_radius ?? []).find((r) => r.rule_id === ruleId);
export const unresolvedRadii = (report: ImpactReport) => (report.blast_radius ?? []).filter(isUnresolved);
export const ruleTitle = (report: ImpactReport, ruleId: string) => report.rules.find((r) => r.rule_id === ruleId)?.title ?? ruleId.split(":").pop() ?? ruleId;

/** Obligations that cannot be machine-checked, read defensively: old reports lack the field, and items may be partial. */
export function unexecutableOf(report: ImpactReport): Unexecutable[] {
  const raw = (report as { unexecutable?: unknown }).unexecutable;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    .map((item) => ({
      citation: typeof item.citation === "string" ? item.citation : "",
      reason: typeof item.reason === "string" ? item.reason : "",
      missing_data: Array.isArray(item.missing_data) ? item.missing_data.filter((d): d is string => typeof d === "string") : [],
    }))
    .filter((item) => item.citation || item.reason);
}

export const inr = (n: number) => `₹${Math.round(n ?? 0).toLocaleString("en-IN")}`;
export const short = (id: string) => id.split(":").pop() ?? id;

// ---------------------------------------------------------------- CSV export

/** RFC 4180 quoting, plus a leading apostrophe on formula-looking text to stop spreadsheet injection. */
function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let text = String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

const CSV_HEADER = [
  "record_type", "report_id", "policy", "analysis_date", "overall_status",
  "rule_title", "rule_modality", "scope_status", "check_index", "check_kind", "check_description", "check_status",
  "in_scope", "violations", "exposure_inr", "penalty_inr", "evidence",
  "action_workflow", "action_owner", "action_target_count", "action_status",
  "citation", "reason", "missing_data",
];

/** One CSV: a CHECK row per check, an ACTION row per remediation action and an UNEXECUTABLE row per obligation that
 * cannot be machine-checked. Excludes approver identities and notice bodies. */
export function reportCsv(report: ImpactReport): string {
  const overall = overallMeta(report.summary).label;
  const base = [report.report_id, report.policy.title, report.as_of, overall];
  const rows: unknown[][] = [];
  for (const c of report.results) {
    const rule = report.rules.find((r) => r.rule_id === c.rule_id);
    const measured = isMeasured(c.status);
    rows.push([
      "CHECK", ...base, rule?.title ?? short(c.rule_id), rule ? modalityOf(rule) : "", radiusFor(report, c.rule_id)?.scope_status ?? "",
      c.check_index, c.kind, c.description, c.status,
      measured ? c.in_scope : "", measured ? c.violations : "", measured ? Math.round(c.exposure_inr) : "", measured ? Math.round(c.penalty_inr) : "", c.evidence,
      "", "", "", "",
      "", "", "",
    ]);
  }
  for (const a of report.actions) {
    rows.push([
      "ACTION", ...base, ruleTitle(report, a.rule_id), "", radiusFor(report, a.rule_id)?.scope_status ?? "",
      "", "", "", "", "", "", "", "", "",
      a.workflow_id, a.owner, a.target_count, a.status,
      "", "", "",
    ]);
  }
  for (const u of unexecutableOf(report)) {
    rows.push([
      "UNEXECUTABLE", ...base, "", "", "",
      "", "", "", "NOT_MACHINE_CHECKABLE", "", "", "", "", "",
      "", "", "", "",
      u.citation, u.reason, u.missing_data.join("; "),
    ]);
  }
  return [CSV_HEADER, ...rows].map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
}

export function downloadCsv(report: ImpactReport) {
  const blob = new Blob(["\uFEFF", reportCsv(report)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `yukdhis-${report.report_id}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Print / Save as PDF; the document title becomes the suggested file name. */
export function printReport(report: ImpactReport) {
  const previous = document.title;
  document.title = `Yukdhis ${report.report_id} ${report.policy.title}`;
  window.addEventListener("afterprint", () => { document.title = previous; }, { once: true });
  window.print();
}

// ------------------------------------------------------------ run comparison

export interface CheckPair { key: string; rule: string; description: string; a?: Check; b?: Check; changed: boolean }
export interface Comparison {
  rulesAdded: string[]; rulesRemoved: string[]; checks: CheckPair[];
}

const norm = (text: string) => text.trim().toLowerCase().replace(/\s+/g, " ");

export function compareReports(a: ImpactReport, b: ImpactReport): Comparison {
  const titles = (r: ImpactReport) => new Map(r.rules.map((x) => [norm(x.title), x.title]));
  const ta = titles(a), tb = titles(b);
  const rulesAdded = [...tb].filter(([k]) => !ta.has(k)).map(([, v]) => v);
  const rulesRemoved = [...ta].filter(([k]) => !tb.has(k)).map(([, v]) => v);

  const index = (r: ImpactReport) => {
    const map = new Map<string, { check: Check; rule: string; description: string }>();
    for (const c of r.results) {
      const rule = ruleTitle(r, c.rule_id);
      let key = `${norm(rule)}||${norm(c.description)}`;
      while (map.has(key)) key += "+"; // identical descriptions within one rule
      map.set(key, { check: c, rule, description: c.description });
    }
    return map;
  };
  const ia = index(a), ib = index(b);
  const keys = [...new Set([...ia.keys(), ...ib.keys()])];
  const checks = keys.map((key) => {
    const ca = ia.get(key), cb = ib.get(key);
    const changed = !ca || !cb || ca.check.status !== cb.check.status || ca.check.violations !== cb.check.violations || ca.check.in_scope !== cb.check.in_scope;
    return { key, rule: (ca ?? cb)!.rule, description: (ca ?? cb)!.description, a: ca?.check, b: cb?.check, changed };
  });
  return { rulesAdded, rulesRemoved, checks };
}

export interface KpiRow { label: string; a: number; b: number; money?: boolean }
export function kpiRows(a: ImpactReport, b: ImpactReport): KpiRow[] {
  const row = (label: string, f: (s: Summary) => number | undefined, money = false): KpiRow => ({ label, a: f(a.summary) ?? 0, b: f(b.summary) ?? 0, money });
  return [
    row("Rules", (s) => s.rules), row("Checks", (s) => s.checks), row("Gaps", (s) => s.gaps),
    row("Products in blast radius", (s) => s.products_in_scope), row("Loans with violations", (s) => s.loans_with_violations),
    row("Customers affected", (s) => s.customers_with_violations), row("Control gaps", (s) => s.control_gaps?.length),
    row("Loans on gapped products", (s) => s.loans_exposed_to_control_gaps), row("Statutory penalty exposure", (s) => s.statutory_penalty_inr, true),
    row("Unresolved-scope rules", (s) => s.unresolved_rules?.length),
  ];
}
