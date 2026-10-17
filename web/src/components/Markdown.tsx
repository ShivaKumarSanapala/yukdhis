import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// GitHub-flavoured Markdown without raw HTML (no rehype-raw): model output cannot inject markup.
const components: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
  // Images are never loaded (alt text only), so an answer cannot beacon data to a remote URL.
  img: ({ alt }) => <span className="muted">[image: {alt || "omitted"}]</span>,
  table: ({ node: _node, ...props }) => (
    <div className="md-table" tabIndex={0} role="region" aria-label="Table"><table {...props} /></div>
  ),
};

function Markdown({ text }: { text: string }) {
  return <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown></div>;
}

export default memo(Markdown);
