import type { Sample } from "../types";
import { Motif } from "./Motif";
import Icon from "./Icons";

/** Bundled circulars that analyse end to end out of the box (see data/evals/cases.json: the sample-* and ecl-07y cases). */
const SUGGESTED = [
  { id: "rbi_2023_08_18_floating_rate_reset_emi_loans.md", title: "Floating-rate reset on EMI loans", sub: "RBI/2023-24/55 · loans missing reset notices and statements" },
  { id: "rbi_2023_09_13_release_of_property_documents.md", title: "Release of property documents", sub: "RBI/2023-24/60 · closed loans with late or missing releases" },
  { id: "rbi_ecl_2026_04_29_iracp_amendment_directions.md", title: "IRACP Amendment Directions, 2026", sub: "RBI/2026-27/45 · new provisioning and resolution-plan rules" },
] as const;

export const QUESTIONS = [
  "How many gold loans are above 75% loan-to-value?",
  "How many active floating-rate loans have not been notified of their rate reset?",
  "What are the penalty terms for late release of property documents?",
];

export function Greeting() {
  return (
    <div className="hero-greeting">
      <Motif className="hero-motif" width={420} height={148} />
      <h2 className="hero-title">Where should we start?</h2>
      <p className="hero-sub">Drop a circular to begin compliance analysis, or ask about your bank.</p>
    </div>
  );
}

export function Suggestions({ samples, onSample, onQuestion, busy }: {
  samples: Sample[]; onSample: (id: string) => void; onQuestion: (q: string) => void; busy: boolean;
}) {
  const available = SUGGESTED.filter((s) => !samples.length || samples.some((x) => x.sample_id === s.id));
  return (
    <div className="hero-suggest">
      {available.length > 0 && (
        <ul className="suggest-cards" aria-label="Sample circulars to analyse">
          {available.map((s) => (
            <li key={s.id}>
              <button className="suggest-card" disabled={busy} onClick={() => onSample(s.id)}>
                <span className="suggest-icon" aria-hidden="true"><Icon name="analyze" size={15} /></span>
                <b>{s.title}</b>
                <small>{s.sub}</small>
                <span className="suggest-go">Analyse this circular</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <ul className="question-chips" aria-label="Questions to ask the assistant">
        {QUESTIONS.map((q) => (
          <li key={q}><button className="q-chip" onClick={() => onQuestion(q)}><Icon name="sparkle" size={13} />{q}</button></li>
        ))}
      </ul>
    </div>
  );
}
