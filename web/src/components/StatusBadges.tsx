import type { Summary } from "../types";
import { checkMeta, modalityHint, modalityLabel, overallMeta } from "../report-utils";

/** Overall report status as a pill (lists) or a larger block (report header). Text is always shown, never colour alone. */
export function OverallBadge({ summary, large }: { summary: Summary; large?: boolean }) {
  const m = overallMeta(summary);
  return <span className={large ? `status ${m.tone}` : `pill ${m.tone}`} title={m.hint || undefined}>{m.label}</span>;
}

export function CheckPill({ status }: { status: string }) {
  const m = checkMeta(status);
  return <span className={`pill ${m.tone}`} title={m.hint || undefined}>{m.label}</span>;
}

/** MAY / MUST NOT marker; renders nothing for ordinary MUST rules. */
export function ModalityBadge({ modality }: { modality: string }) {
  if (!modality || modality === "MUST") return null;
  const hint = modalityHint(modality);
  return <span className="pill modality" title={hint} aria-label={`${modalityLabel(modality)}: ${hint}`}>{modalityLabel(modality)}</span>;
}
