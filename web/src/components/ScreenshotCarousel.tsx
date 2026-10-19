import { useCallback, useEffect, useState } from "react";

// Screenshots are 1600 x 847 (about 1.89:1); the frame uses the same ratio so nothing is cropped.
const SLIDES = [
  { src: "/landing/home.jpg", title: "Start from a circular", text: "Pick a sample RBI circular or drop in your own." },
  { src: "/landing/run.jpg", title: "Follow the agent pipeline", text: "Seven stages run on the live circular. Each shows its result, timing and tool calls." },
  { src: "/landing/graph.jpg", title: "Explore the knowledge graph", text: "Search a product, rule or control and see everything it touches." },
  { src: "/landing/report.jpg", title: "Read the impact report", text: "Counts, exposure and penalties computed in code, with the SQL behind each figure." },
  { src: "/landing/assistant.jpg", title: "Ask follow-up questions", text: "The assistant answers from guarded SQL and the graph, not from guesswork." },
  { src: "/landing/privacy.jpg", title: "Check what leaves the bank", text: "Cloud DLP and a Gemma check mask customer identifiers before any text reaches Gemini." },
];
const INTERVAL_MS = 5000;

export default function ScreenshotCarousel() {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const count = SLIDES.length;

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduceMotion(query.matches);
    const onChange = () => setReduceMotion(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (paused || reduceMotion) return;
    const timer = window.setInterval(() => setIndex((i) => (i + 1) % count), INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [paused, reduceMotion, count]);

  const go = useCallback((next: number) => setIndex(((next % count) + count) % count), [count]);

  return (
    <section
      aria-roledescription="carousel"
      aria-label="Product screenshots"
      className="relative"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <div className="relative overflow-hidden rounded-xl bg-zinc-100" style={{ aspectRatio: "1600 / 847" }}>
        {SLIDES.map((slide, k) => (
          <figure
            key={slide.src}
            aria-roledescription="slide"
            aria-label={`${k + 1} of ${count}: ${slide.title}`}
            aria-hidden={k !== index}
            className={`absolute inset-0 m-0 transition-opacity duration-700 ease-in-out ${k === index ? "opacity-100" : "opacity-0"}`}
          >
            <img
              src={slide.src}
              alt={`${slide.title}: ${slide.text}`}
              width={1600}
              height={847}
              loading={k === 0 ? "eager" : "lazy"}
              decoding="async"
              className="h-full w-full object-cover object-top"
            />
          </figure>
        ))}

        <button
          type="button"
          onClick={() => go(index - 1)}
          aria-label="Previous screenshot"
          style={{ width: 36, height: 36, minWidth: 36, minHeight: 36, padding: 0 }}
          className="absolute left-3 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border border-zinc-200 bg-white/90 text-zinc-700 shadow-sm backdrop-blur transition hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4" aria-hidden="true"><path d="M12.5 4.5 7 10l5.5 5.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <button
          type="button"
          onClick={() => go(index + 1)}
          aria-label="Next screenshot"
          style={{ width: 36, height: 36, minWidth: 36, minHeight: 36, padding: 0 }}
          className="absolute right-3 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border border-zinc-200 bg-white/90 text-zinc-700 shadow-sm backdrop-blur transition hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4" aria-hidden="true"><path d="m7.5 4.5 5.5 5.5-5.5 5.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </div>

      <div className="mt-4 flex flex-col gap-3 px-1 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-h-[3.25rem]" aria-live="polite">
          <p className="text-base font-semibold text-zinc-900" style={{ margin: 0 }}>{SLIDES[index].title}</p>
          <p className="mt-0.5 text-sm text-zinc-600" style={{ margin: "2px 0 0" }}>{SLIDES[index].text}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {SLIDES.map((slide, k) => (
            <button
              key={slide.src}
              type="button"
              onClick={() => go(k)}
              aria-label={`Show screenshot ${k + 1}: ${slide.title}`}
              aria-current={k === index}
              style={{ width: k === index ? 24 : 8, height: 8, minHeight: 8, minWidth: 8, padding: 0 }}
              className={`h-2 rounded-full transition-all duration-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 ${k === index ? "w-6 bg-indigo-600" : "w-2 bg-zinc-300 hover:bg-zinc-400"}`}
            />
          ))}
          <span className="ml-1 text-xs text-zinc-500" aria-hidden="true">{paused || reduceMotion ? "Paused" : "Auto"}</span>
        </div>
      </div>
    </section>
  );
}
