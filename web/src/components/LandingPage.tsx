import type { ReactNode } from "react";
import { BrandMark } from "./Icons";
import ScreenshotCarousel from "./ScreenshotCarousel";

const REPO_URL = "https://github.com/ShivaKumarSanapala/yukdhis";
const DEMO_URL: string = import.meta.env.VITE_DEMO_VIDEO_URL ?? "https://youtu.be/ldPnE9XgWg4";

const STEPS = [
  { n: 1, agent: "Reader", tech: "Document AI", verb: "Read", text: "Parses the circular and extracts grounded rules." },
  { n: 2, agent: "Mapper", tech: "Entity Resolution", verb: "Map", text: "Wires rules into a BIAN-aligned knowledge graph." },
  { n: 3, agent: "GraphRAG", tech: "GQL Traversal", verb: "Trace", text: "Traverses Spanner Graph for the blast radius." },
  { n: 4, agent: "Data Analyst", tech: "Guarded SQL", verb: "Measure", text: "Read-only, parameterised queries over bank data." },
  { n: 5, agent: "Evaluator", tech: "Deterministic Checks", verb: "Decide", text: "Code computes counts and statutory penalties." },
  { n: 6, agent: "Orchestrator", tech: "Approvals", verb: "Act", text: "Drafts the report and proposes actions for human approval." },
];

const FEATURES = [
  { title: "Deterministic numbers", text: "Counts and statutory penalties come from parameterised SQL and code, not from the language model." },
  { title: "No customer PII in the model", text: "Cloud DLP and KMS mask every model call. Agents see opaque references only." },
  { title: "Time-travel compliance", text: "The bitemporal graph answers what was true on any past date, so changes are explainable." },
];

const STACK = ["Gemini 2.5 Pro", "Spanner Graph", "Cloud SQL", "Cloud Run", "Document AI", "Cloud DLP"];

const card = "rounded-xl border border-zinc-200 bg-white shadow-sm";
const container = "mx-auto w-full max-w-6xl px-5 sm:px-8";

function Section({ id, eyebrow, title, children }: { id?: string; eyebrow: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className={`${container} py-16 sm:py-20`}>
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">{eyebrow}</p>
      <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight text-zinc-900 sm:text-4xl" style={{ margin: "12px 0 0" }}>{title}</h2>
      <div className="mt-10">{children}</div>
    </section>
  );
}

export default function LandingPage({ onSignIn, testLogin }: { onSignIn: () => void; testLogin?: ReactNode }) {
  const primary = "inline-flex items-center justify-center rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600";
  const secondary = "inline-flex items-center justify-center rounded-lg border border-zinc-300 bg-white px-5 py-2.5 text-sm font-medium text-zinc-900 no-underline shadow-sm transition hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600";
  return (
    <div className="min-h-screen overflow-x-hidden bg-zinc-50 text-zinc-600" id="main">
      <header className="sticky top-0 z-20 border-b border-zinc-200 bg-white/90 backdrop-blur">
        <nav className={`${container} flex h-16 items-center justify-between`} aria-label="Primary">
          <a href="#top" className="flex items-center gap-2.5 text-zinc-900 no-underline">
            <span aria-hidden="true" className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white">
              <BrandMark size={16} />
            </span>
            <span className="text-base font-semibold tracking-tight">Anuvi</span>
          </a>
          <button type="button" onClick={onSignIn} className={primary}>Sign in with Google</button>
        </nav>
      </header>

      <main id="top">
        {/* Hero */}
        <section className={`${container} pb-12 pt-14 text-center sm:pt-20`}>
          <p className="mx-auto inline-flex rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700">Google Cloud AI Builder Cup 2026</p>
          <h1 className="mx-auto mt-6 max-w-3xl text-4xl font-semibold leading-[1.1] tracking-tight text-zinc-900 sm:text-6xl" style={{ margin: "24px auto 0" }}>
            Autonomous Compliance Engineer for Banking
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-zinc-600 sm:text-lg">
            Turn regulatory circulars into deterministic, auditable impact reports in minutes. Built on Google Cloud.
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <button type="button" onClick={onSignIn} className={`${primary} w-full sm:w-auto`}>Start analysis</button>
            <a href={DEMO_URL} className={`${secondary} w-full sm:w-auto`}>Watch demo</a>
          </div>

          <div className="mx-auto mt-14 max-w-5xl rounded-2xl border border-zinc-200 bg-white p-3 text-left shadow-xl shadow-indigo-900/5 sm:p-4">
            <ScreenshotCarousel />
          </div>
          {testLogin && <div className="mx-auto mt-10 w-full max-w-sm text-left">{testLogin}</div>}
        </section>

        {/* Problem vs solution */}
        <Section eyebrow="Why Anuvi" title="From weeks of manual cross-checking to one auditable run">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-5">
            <article className={`${card} p-6 sm:p-8 md:col-span-2`}>
              <span className="inline-flex rounded-full border border-red-200 bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-700">The problem</span>
              <h3 className="mt-4 text-xl font-semibold text-zinc-900" style={{ margin: "16px 0 0" }}>The old way</h3>
              <p className="mt-3 leading-relaxed">Manual reading and cross-checking of thousands of records. Hard to audit, with real PII risk.</p>
            </article>
            <article className={`${card} p-6 sm:p-8 md:col-span-3`}>
              <span className="inline-flex rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700">The solution</span>
              <h3 className="mt-4 text-xl font-semibold text-zinc-900" style={{ margin: "16px 0 0" }}>The Anuvi way</h3>
              <p className="mt-3 leading-relaxed">Anuvi reads the circular, wires its rules into a BIAN-aligned knowledge graph and traces the blast radius. Code computes the penalty, and no customer PII reaches the model.</p>
            </article>
          </div>
        </Section>

        {/* Pipeline */}
        <Section eyebrow="How it works" title="A pipeline of specialised ADK agents">
          <ol className="grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-2 lg:grid-cols-6">
            {STEPS.map((s) => (
              <li key={s.n} className={`${card} relative h-full p-5`}>
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-indigo-50 text-sm font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-200">{s.n}</span>
                <p className="mt-4 text-sm font-semibold text-zinc-900" style={{ margin: "16px 0 0" }}>{s.tech}</p>
                <p className="mt-1 text-xs font-medium uppercase tracking-wider text-indigo-600" style={{ margin: "4px 0 0" }}>{s.verb} · {s.agent}</p>
                <p className="mt-2 text-sm leading-relaxed" style={{ margin: "8px 0 0" }}>{s.text}</p>
              </li>
            ))}
          </ol>
        </Section>

        {/* Trust */}
        <Section eyebrow="Why it can be trusted" title="Built so that auditors can check every number">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {FEATURES.map((f) => (
              <article key={f.title} className={`${card} p-6`}>
                <span aria-hidden="true" className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
                  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-4 w-4"><path d="M5 10.5 8.5 14 15 6.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
                <h3 className="mt-4 text-lg font-semibold text-zinc-900" style={{ margin: "16px 0 0" }}>{f.title}</h3>
                <p className="mt-2 leading-relaxed">{f.text}</p>
              </article>
            ))}
          </div>
        </Section>

        {/* Stack */}
        <Section eyebrow="Architecture" title="Built entirely on Google Cloud">
          <ul className="grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-3 lg:grid-cols-6">
            {STACK.map((name) => (
              <li key={name} className={`${card} flex h-16 items-center justify-center px-3 text-center text-sm font-medium text-zinc-800`}>{name}</li>
            ))}
          </ul>
          <div className="mt-6 rounded-xl bg-indigo-600 p-8 text-center shadow-sm">
            <p className="text-xl font-semibold text-white sm:text-2xl" style={{ margin: 0 }}>Processes a complex RBI circular in about four minutes, with no customer PII sent to the model.</p>
          </div>
        </Section>
      </main>

      <footer className="border-t border-zinc-200 bg-white">
        <div className={`${container} flex flex-col items-center justify-between gap-4 py-8 text-sm sm:flex-row`}>
          <p className="text-zinc-500" style={{ margin: 0 }}>Built for the Google Cloud AI Builder Cup.</p>
          <div className="flex gap-6">
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="text-zinc-600 no-underline hover:text-zinc-900">GitHub</a>
            <a href={DEMO_URL} className="text-zinc-600 no-underline hover:text-zinc-900">Demo video</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
