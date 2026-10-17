import { useEffect, useId, useState } from "react";

type State = { svg: string } | { error: string } | null;

const prefersDark = () => window.matchMedia?.("(prefers-color-scheme: dark)").matches;

/** Renders a ```mermaid fence as an SVG diagram (loaded on demand). Falls back to the source if it does not parse. */
export default function MermaidBlock({ code }: { code: string }) {
  const [state, setState] = useState<State>(null);
  const id = "mmd-" + useId().replace(/[^a-zA-Z0-9]/g, "");
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { default: mermaid } = await import("mermaid");
        // "strict" sanitises labels and disables click handlers, so model output cannot inject markup or script.
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: prefersDark() ? "dark" : "neutral",
          fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif" });
        const { svg } = await mermaid.render(id, code.trim());
        if (alive) setState({ svg });
      } catch (error) {
        document.getElementById("d" + id)?.remove(); // mermaid leaves its error element in <body>
        if (alive) setState({ error: error instanceof Error ? error.message.split("\n")[0] : "Could not render the diagram" });
      }
    })();
    return () => { alive = false; };
  }, [code, id]);

  if (state && "svg" in state) {
    return <figure className="mermaid" role="img" aria-label="Diagram" dangerouslySetInnerHTML={{ __html: state.svg }} />;
  }
  return (
    <figure className="mermaid mermaid-fallback">
      {state === null ? <span className="muted" role="status">Rendering diagram…</span>
        : <figcaption className="muted">Diagram could not be rendered ({state.error}). Showing the source.</figcaption>}
      {state !== null && <pre><code>{code}</code></pre>}
    </figure>
  );
}
