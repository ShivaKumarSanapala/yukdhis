import { whenText } from "../format";

/** A timestamp as human text with the exact ISO value in a tooltip and in <time dateTime>. */
export default function When({ value, relative = true }: { value?: string | null; relative?: boolean }) {
  if (!value) return <span className="muted">—</span>;
  const w = whenText(value);
  return (
    <time className="when" dateTime={w.exact} title={w.exact}>
      {relative && w.rel ? w.rel : w.text}
      {relative && w.rel && <span className="when-abs"> · {w.text}</span>}
    </time>
  );
}
