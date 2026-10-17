import { useState } from "react";
import { api } from "../api";
import type { ExtractedRule, RunInfo } from "../types";
import { modalityHint } from "../report-utils";

const MODALITIES = [["MUST", "MUST (obligation)"], ["MAY", "MAY (permission)"], ["MUST_NOT", "MUST NOT (prohibition)"]] as const;

/** Review of the rules extracted before analysis: edit title, modality and effective date, drop rules, then approve or reject. */
export default function RuleReview({ run, rules, onDone }: { run: RunInfo; rules: ExtractedRule[]; onDone: () => void }) {
  const [draft, setDraft] = useState<ExtractedRule[]>(() => rules.map((r) => ({ ...r })));
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const kept = draft.filter((r) => !removed.has(r.rule_id));
  const edit = (id: string, patch: Partial<ExtractedRule>) => setDraft((d) => d.map((r) => (r.rule_id === id ? { ...r, ...patch } : r)));
  const toggle = (id: string) => setRemoved((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const changed = (r: ExtractedRule) => {
    const o = rules.find((x) => x.rule_id === r.rule_id);
    return Boolean(o) && (o!.title !== r.title || (o!.modality ?? "MUST") !== (r.modality ?? "MUST") || o!.effective_date !== r.effective_date);
  };
  const invalid = kept.find((r) => !r.title.trim() || !/^\d{4}-\d{2}-\d{2}$/.test(r.effective_date));

  const decide = async (decision: "APPROVE" | "REJECT") => {
    if (decision === "REJECT" && !window.confirm("Reject these rules and stop this analysis?")) return;
    setBusy(decision); setError("");
    try {
      await api.reviewRun(run.run_id, decision, decision === "APPROVE" ? kept.map((r) => ({ ...r, title: r.title.trim() })) : undefined);
      onDone();
    } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };

  return (
    <section className="card review" aria-labelledby="review-title">
      <div className="row between">
        <h2 id="review-title">Review extracted rules</h2>
        <span className="muted" role="status">{kept.length} of {draft.length} kept{draft.some(changed) ? ` · ${draft.filter(changed).length} edited` : ""}</span>
      </div>
      <p className="muted">Checks, scope and citations stay as extracted so every rule remains grounded in the circular. Only the fields below can be changed.</p>
      {!run.is_mine && <div className="banner warn" role="status">Only the person who started this run can approve or reject it.</div>}
      <ol className="review-list">
        {draft.map((r, i) => {
          const off = removed.has(r.rule_id);
          return (
            <li key={r.rule_id} className={off ? "removed" : changed(r) ? "edited" : ""}>
              <div className="rule-head">
                <label className="grow">Rule {i + 1} title
                  <input value={r.title} disabled={off} onChange={(e) => edit(r.rule_id, { title: e.target.value })} aria-invalid={!r.title.trim()} />
                </label>
                <button type="button" className={off ? "" : "ghost"} onClick={() => toggle(r.rule_id)} aria-pressed={off}>
                  {off ? "Restore rule" : "Remove rule"}
                </button>
              </div>
              <div className="row">
                <label>Modality
                  <select value={r.modality ?? "MUST"} disabled={off} onChange={(e) => edit(r.rule_id, { modality: e.target.value })}>
                    {MODALITIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </label>
                <label>Effective date
                  <input type="date" value={r.effective_date} disabled={off} required onChange={(e) => edit(r.rule_id, { effective_date: e.target.value })} />
                </label>
              </div>
              {modalityHint(r.modality ?? "MUST") && <p className="muted">{modalityHint(r.modality ?? "MUST")}.</p>}
              <blockquote>{r.citation?.snippet}</blockquote>
              <p className="muted">Checks: {r.checks.map((c) => `${c.kind.replaceAll("_", " ")}: ${c.description}`).join(" · ") || "none"}</p>
            </li>
          );
        })}
      </ol>
      {error && <div className="banner error" role="alert">{error}</div>}
      <div className="buttons">
        <button className="primary" disabled={!run.is_mine || Boolean(busy) || !kept.length || Boolean(invalid)} onClick={() => decide("APPROVE")}>
          {busy === "APPROVE" ? "Continuing…" : `Approve ${kept.length} rule${kept.length === 1 ? "" : "s"} and continue`}
        </button>
        <button className="ghost" disabled={!run.is_mine || Boolean(busy)} onClick={() => decide("REJECT")}>{busy === "REJECT" ? "Rejecting…" : "Reject run"}</button>
        {!kept.length && <span className="muted">Keep at least one rule, or reject the run.</span>}
        {invalid && <span className="muted">Each kept rule needs a title and a valid date.</span>}
      </div>
    </section>
  );
}
