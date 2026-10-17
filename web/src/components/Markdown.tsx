import { Suspense, lazy, memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const MermaidBlock = lazy(() => import("./MermaidBlock"));

// GitHub-flavoured Markdown without raw HTML (no rehype-raw): model output cannot inject markup.
const components: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
  // Images are never loaded (alt text only), so an answer cannot beacon data to a remote URL.
  img: ({ alt }) => <span className="muted">[image: {alt || "omitted"}]</span>,
  // ```mermaid fences render as diagrams; every other code block stays a normal <pre><code>.
  code: ({ node: _node, className, children, ...props }) => {
    if (/\blanguage-mermaid\b/.test(className ?? "")) {
      return <Suspense fallback={<span className="muted">Rendering diagram…</span>}><MermaidBlock code={String(children)} /></Suspense>;
    }
    return <code className={className} {...props}>{children}</code>;
  },
  pre: ({ node, children, ...props }) => {
    const child = (node?.children?.[0] ?? null) as { properties?: { className?: string[] } } | null;
    if (child?.properties?.className?.includes("language-mermaid")) return <>{children}</>; // no <pre> frame around a diagram
    return <pre {...props}>{children}</pre>;
  },
  table: ({ node: _node, ...props }) => (
    <div className="md-table" tabIndex={0} role="region" aria-label="Table"><table {...props} /></div>
  ),
};

function Markdown({ text }: { text: string }) {
  return <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown></div>;
}

export default memo(Markdown);
