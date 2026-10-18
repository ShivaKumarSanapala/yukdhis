import type { ReactNode } from "react";

const REPO_URL = "https://github.com/ShivaKumarSanapala/yukdhis";
const DEMO_URL: string = import.meta.env.VITE_DEMO_VIDEO_URL ?? "https://youtu.be/ldPnE9XgWg4";

const STEPS = [
  { n: 1, agent: "Reader", tech: "Document AI", verb: "Read", text: "Parses the circular and extracts grounded rules." },
  { n: 2, agent: "Mapper", tech: "Entity Resolution", verb: "Map", text: "Wires rules into a BIAN-aligned knowledge graph." },
  { n: 3, agent: "GraphRAG", tech: "GQL Traversal", verb: "Trace", text: "Traverses Spanner Graph for the blast radius." },
  { n: 4, agent: "Data Analyst", tech: "Guarded SQL", verb: "Measure", text: "Read-only, parameterized queries over bank data." },
  { n: 5, agent: "Evaluator", tech: "Deterministic Checks", verb: "Decide", text: "Code computes counts and statutory penalties." },
  { n: 6, agent: "Orchestrator", tech: "Approvals", verb: "Act", text: "Drafts the report and proposes actions for humans." },
];

const FEATURES = [
  { title: "Deterministic numbers", text: "Counts and statutory penalties come from parameterized SQL code, not LLM hallucinations.", glow: "from-indigo-500/20" },
  { title: "Zero PII exposure", text: "Cloud DLP and KMS mask every model call. Agents only see opaque references.", glow: "from-emerald-500/20" },
  { title: "Time-travel compliance", text: "Bitemporal graph history lets you query the bank's architecture as of any past date.", glow: "from-violet-500/20" },
];

const STACK = ["Gemini 2.5 Pro", "Spanner Graph", "Cloud SQL", "Cloud Run", "Document AI", "Cloud DLP"];

const card = "rounded-2xl border border-zinc-800/50 bg-zinc-900/50 backdrop-blur-md";
const container = "mx-auto w-full max-w-6xl px-5 sm:px-8";

function Section({ id, eyebrow, title, children }: { id?: string; eyebrow: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className={`${container} py-16 sm:py-24`}>
      <p className="text-xs font-medium uppercase tracking-[0.2em] text-indigo-400">{eyebrow}</p>
      <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight text-white sm:text-4xl" style={{ margin: "12px 0 0" }}>{title}</h2>
      <div className="mt-10">{children}</div>
    </section>
  );
}

export default function LandingPage({ onSignIn, testLogin }: { onSignIn: () => void; testLogin?: ReactNode }) {
  const signInButton = "inline-flex items-center justify-center rounded-lg bg-white px-4 py-2 text-sm font-medium text-zinc-950 transition hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400";
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-zinc-950 text-zinc-400" id="main">
      <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-[640px] bg-[radial-gradient(60%_60%_at_50%_0%,rgba(99,102,241,0.22),transparent_70%)]" />

      <header className="sticky top-0 z-20 border-b border-zinc-800/50 bg-zinc-950/70 backdrop-blur-md">
        <nav className={`${container} flex h-14 items-center justify-between`} aria-label="Primary">
          <a href="#top" className="flex items-center gap-2 text-white no-underline">
            <span aria-hidden="true" className="h-5 w-5 rounded-md bg-gradient-to-br from-indigo-400 to-violet-600 shadow-[0_0_16px_rgba(129,140,248,0.6)]" />
            <span className="text-base font-semibold tracking-tight">Anuvi</span>
          </a>
          <button type="button" onClick={onSignIn} className={signInButton}>Sign in with Google</button>
        </nav>
      </header>

      <main id="top">
        {/* Hero */}
        <section className={`${container} relative pb-10 pt-16 text-center sm:pt-24`}>
          <p className="mx-auto inline-flex rounded-full border border-zinc-800/50 bg-zinc-900/50 px-3 py-1 text-xs text-zinc-400 backdrop-blur-md">Google Cloud AI Builder Cup</p>
          <h1 className="mx-auto mt-6 max-w-3xl bg-gradient-to-b from-white to-zinc-400 bg-clip-text text-4xl font-semibold leading-[1.1] tracking-tight text-transparent sm:text-6xl" style={{ margin: "24px auto 0" }}>
            Autonomous Compliance Engineer for Banking
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-zinc-400 sm:text-lg">
            Turn 50-page regulatory circulars into deterministic, auditable impact reports in minutes. Built on Google Cloud.
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <button type="button" onClick={onSignIn} className="inline-flex w-full items-center justify-center rounded-lg bg-indigo-500 px-6 py-3 text-sm font-medium text-white shadow-[0_0_32px_rgba(99,102,241,0.45)] transition hover:bg-indigo-400 sm:w-auto">Start Analysis</button>
            <a href={DEMO_URL} className="inline-flex w-full items-center justify-center rounded-lg border border-zinc-700 px-6 py-3 text-sm font-medium text-white no-underline transition hover:border-zinc-500 hover:bg-zinc-900/60 sm:w-auto">Watch Demo</a>
          </div>
          {testLogin && <div className="mx-auto mt-8 w-full max-w-sm text-left">{testLogin}</div>}

          {/* Hero visual: drop the dashboard screenshots into the two <img> tags */}
          <div className="relative mx-auto mt-14 max-w-5xl">
            <div aria-hidden="true" className="lp-pulse-border absolute -inset-px rounded-2xl bg-gradient-to-r from-indigo-500/60 via-violet-500/40 to-emerald-400/50 blur-[2px]" />
            <div className={`${card} relative overflow-hidden p-2 sm:p-3`}>
              <img src="/placeholder-dashboard.png" alt="Anuvi chat and impact report dashboard on desktop" className="hidden w-full rounded-xl bg-zinc-900 sm:block" style={{ aspectRatio: "16 / 9" }} />
              <img src="/placeholder-dashboard.png" alt="Anuvi chat and impact report dashboard on mobile" className="mx-auto w-3/4 rounded-xl bg-zinc-900 sm:hidden" style={{ aspectRatio: "9 / 16" }} />
            </div>
          </div>
        </section>

        {/* Problem vs solution */}
        <Section eyebrow="Why Anuvi" title="From weeks of manual cross-checking to one auditable run">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-5">
            <article className={`${card} p-6 sm:p-8 md:col-span-2`}>
              <span className="rounded-full border border-red-500/30 bg-red-500/10 px-2.5 py-0.5 text-xs text-red-300">The problem</span>
              <h3 className="mt-4 text-xl font-semibold text-white" style={{ margin: "16px 0 0" }}>The old way</h3>
              <p className="mt-3 leading-relaxed">Manual reading and cross-checking of thousands of records. Hard to audit, with massive PII risks.</p>
            </article>
            <article className={`${card} relative overflow-hidden p-6 sm:p-8 md:col-span-3`}>
              <div aria-hidden="true" className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl" />
              <span className="relative rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-xs text-emerald-300">The solution</span>
              <h3 className="relative mt-4 text-xl font-semibold text-white" style={{ margin: "16px 0 0" }}>The Anuvi way</h3>
              <p className="relative mt-3 leading-relaxed">Anuvi reads the circular, wires rules into a BIAN-aligned knowledge graph, and traces the blast radius. Code computes the penalty. Zero PII reaches the model.</p>
            </article>
          </div>
        </Section>

        {/* Pipeline */}
        <Section eyebrow="How it works" title="A pipeline of specialised ADK agents">
          <ol className="grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-2 lg:grid-cols-6 lg:gap-0">
            {STEPS.map((s, i) => (
              <li key={s.n} className="relative lg:px-2">
                {i < STEPS.length - 1 && <span aria-hidden="true" className="absolute left-[calc(50%+1.25rem)] right-[-50%] top-5 hidden h-px bg-gradient-to-r from-indigo-500/60 to-zinc-800 lg:block" />}
                <div className={`${card} relative h-full p-4 lg:border-0 lg:bg-transparent lg:p-0 lg:backdrop-blur-none`}>
                  <span className="relative flex h-10 w-10 items-center justify-center rounded-full border border-indigo-500/40 bg-zinc-950 text-sm font-semibold text-white shadow-[0_0_20px_rgba(99,102,241,0.35)]">{s.n}</span>
                  <p className="mt-3 text-sm font-semibold text-white" style={{ margin: "12px 0 0" }}>{s.tech}</p>
                  <p className="mt-0.5 text-xs uppercase tracking-wider text-indigo-400" style={{ margin: "2px 0 0" }}>{s.verb} · {s.agent}</p>
                  <p className="mt-2 text-sm leading-relaxed" style={{ margin: "8px 0 0" }}>{s.text}</p>
                </div>
              </li>
            ))}
          </ol>
        </Section>

        {/* USPs */}
        <Section eyebrow="Why it can be trusted" title="Built so that auditors can check every number">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {FEATURES.map((f) => (
              <article key={f.title} className={`${card} relative overflow-hidden p-6`}>
                <div aria-hidden="true" className={`pointer-events-none absolute inset-0 bg-gradient-to-b ${f.glow} to-transparent opacity-60`} />
                <h3 className="relative text-lg font-semibold text-white" style={{ margin: 0 }}>{f.title}</h3>
                <p className="relative mt-3 leading-relaxed">{f.text}</p>
              </article>
            ))}
          </div>
        </Section>

        {/* Stack */}
        <Section eyebrow="Architecture" title="Built entirely on Google Cloud">
          <ul className="grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-3 lg:grid-cols-6">
            {STACK.map((name) => (
              <li key={name} className={`${card} flex h-20 items-center justify-center px-3 text-center text-sm font-medium text-zinc-200`}>{name}</li>
            ))}
          </ul>
          <div className={`${card} relative mt-6 overflow-hidden p-8 text-center`}>
            <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 -top-24 h-48 bg-[radial-gradient(50%_100%_at_50%_0%,rgba(52,211,153,0.18),transparent)]" />
            <p className="relative text-xl font-semibold text-white sm:text-2xl" style={{ margin: 0 }}>Processes complex RBI circulars in ~4 minutes with 100% PII containment.</p>
          </div>
        </Section>
      </main>

      <footer className="border-t border-zinc-800/50">
        <div className={`${container} flex flex-col items-center justify-between gap-4 py-8 text-sm sm:flex-row`}>
          <p style={{ margin: 0 }}>Built for the Google Cloud AI Builder Cup.</p>
          <div className="flex gap-6">
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="text-zinc-400 no-underline hover:text-white">GitHub</a>
            <a href={DEMO_URL} className="text-zinc-400 no-underline hover:text-white">Demo video</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
