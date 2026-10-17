import { useEffect, useRef, useState } from "react";
import Icon from "./Icons";
import { TYPE_LABEL, chipState, type ChipState, type GEdge, type GNode, type Universe } from "../graph-utils";

const CHIP: Record<ChipState, { label: string; tone: string }> = {
  active: { label: "active", tone: "ok" }, expiring: { label: "expiring", tone: "warn" }, expired: { label: "expired", tone: "bad" }, future: { label: "future", tone: "info" },
};
const show = (v: unknown) => (v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
const window_ = (e: GEdge) => `${e.valid_from} → ${e.valid_to ?? "open"}`;

/** Right-hand sheet with everything known about one entity: attributes and its relationships grouped by edge type. */
export default function GraphInspector({ node, universe, asOf, onSelect, onFocus, onExpand, onClose, onAsk }: {
  node: GNode; universe: Universe; asOf: string; onSelect: (id: string) => void; onFocus: (id: string) => void; onExpand: (id: string) => void; onClose: () => void;
  onAsk?: (node: { id: string; type: string; name: string }) => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => { closeRef.current?.focus(); }, [node.id]);
  const incoming = universe.edges.filter((e) => e.target === node.id);
  const outgoing = universe.edges.filter((e) => e.source === node.id);
  const group = (edges: GEdge[], end: "source" | "target") => {
    const out = new Map<string, GEdge[]>();
    edges.forEach((e) => out.set(e.type, [...(out.get(e.type) ?? []), e]));
    return [...out.entries()].map(([type, list]) => ({ type, list: list.map((e) => ({ e, other: universe.nodes.get(e[end])! })).filter((x) => x.other).sort((a, b) => a.other.name.localeCompare(b.other.name)) }));
  };
  const attrs = Object.entries(node.attributes ?? {});
  const mapped = ["Rule", "Control", "Policy"].includes(node.type)
    ? [...new Map([...incoming, ...outgoing].map((e) => { const o = universe.nodes.get(e.source === node.id ? e.target : e.source); return [o?.id ?? "", o] as const; })).values()]
        .filter((o): o is GNode => Boolean(o) && ["Rule", "Policy", "Product", "Control"].includes(o!.type) && o!.type !== node.type)
    : [];

  const Section = ({ title, edges, end }: { title: string; edges: GEdge[]; end: "source" | "target" }) => (
    <section aria-label={title}>
      <h4>{title} <span className="mono muted">{edges.length}</span></h4>
      {!edges.length && <p className="muted">None.</p>}
      {group(edges, end).map(({ type, list }) => (
        <div key={type} className="dep-group">
          <div className="dep-type mono">{type}</div>
          <ul>
            {list.map(({ e, other }) => {
              const chip = CHIP[chipState(e, asOf)];
              return (
                <li key={`${e.source}${e.target}${e.valid_from}`}>
                  <button className="link" onClick={() => onSelect(other.id)}>{other.name}</button>
                  <span className="dep-meta"><span className="mono muted">{window_(e)}</span> <span className={`pill ${chip.tone}`}>{chip.label}</span></span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );

  return (
    <aside className="inspector" role="complementary" aria-label={`Inspector: ${node.name}`}
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
      <div className="inspector-head">
        <div>
          <span className={`type-tag t-${node.type}`}>{TYPE_LABEL[node.type] ?? node.type}</span>
          <h3>{node.name}</h3>
          <code className="mono id">{node.id}</code>
        </div>
        <button ref={closeRef} className="icon" onClick={onClose} aria-label="Close inspector">✕</button>
      </div>
      <div className="inspector-actions">
        {onAsk && <button className="primary" onClick={() => onAsk({ id: node.id, type: node.type, name: node.name })}><Icon name="sparkle" size={14} /> Ask assistant about this</button>}
        <button onClick={() => onFocus(node.id)}>Focus on this node</button>
        <button onClick={() => onExpand(node.id)}>Expand connections</button>
        <button className="ghost" onClick={() => { void navigator.clipboard?.writeText(node.id).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }); }}>
          {copied ? "Copied" : "Copy id"}
        </button>
      </div>
      <div className="inspector-body">
        <section aria-label="Attributes">
          <h4>Attributes</h4>
          {attrs.length ? (
            <dl className="attrs">{attrs.map(([k, v]) => <div key={k}><dt>{k.replaceAll("_", " ")}</dt><dd className="mono">{show(v)}</dd></div>)}</dl>
          ) : <p className="muted">No attributes recorded.</p>}
        </section>
        {mapped.length > 0 && (
          <section aria-label="Mapped entities">
            <h4>Mapped {node.type === "Control" ? "rules and products" : "policies, rules and products"}</h4>
            <ul className="mapped">
              {mapped.sort((a, b) => a.type.localeCompare(b.type) || a.name.localeCompare(b.name)).map((o) => (
                <li key={o.id}><span className={`type-tag t-${o.type}`}>{o.type}</span> <button className="link" onClick={() => onSelect(o.id)}>{o.name}</button></li>
              ))}
            </ul>
          </section>
        )}
        <Section title="Upstream (points to this node)" edges={incoming} end="source" />
        <Section title="Downstream (this node points to)" edges={outgoing} end="target" />
      </div>
    </aside>
  );
}
