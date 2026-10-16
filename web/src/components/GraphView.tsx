import { useEffect, useMemo, useState } from "react";
import { api, type GraphSnapshot } from "../api";

const COLUMNS = ["Policy", "Rule", "CustomerSegment", "Product", "Process", "Control", "Team"];
const W = 1180, COL = W / COLUMNS.length, ROW = 34;

export default function GraphView({ defaultAsOf }: { defaultAsOf: string }) {
  const [asOf, setAsOf] = useState(defaultAsOf);
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.graph(asOf).then(setGraph).catch((e) => setError(e.message)); }, [asOf]);

  const layout = useMemo(() => {
    const pos = new Map<string, { x: number; y: number }>();
    if (!graph) return { pos, height: 200 };
    let height = 0;
    COLUMNS.forEach((type, c) => {
      graph.nodes.filter((n) => n.type === type).forEach((n, i) => {
        pos.set(n.id, { x: c * COL + 8, y: 40 + i * ROW });
        height = Math.max(height, 80 + i * ROW);
      });
    });
    return { pos, height };
  }, [graph]);

  const linked = useMemo(() => {
    if (!focus || !graph) return null;
    const set = new Set([focus]);
    graph.edges.forEach((e) => { if (e.source === focus) set.add(e.target); if (e.target === focus) set.add(e.source); });
    return set;
  }, [focus, graph]);

  return (
    <section className="card">
      <div className="row">
        <h2>BIAN knowledge graph</h2>
        <label>Time-travel (business date)
          <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
        </label>
        <span className="muted">{graph ? `${graph.nodes.length} nodes · ${graph.edges.length} edges valid on ${graph.as_of}` : ""}</span>
      </div>
      {error && <div className="banner error">{error}</div>}
      <p className="muted">Click a node to highlight its relationships. Try 2024-01-01 vs today: DriveNow's fixed-rate option edge expired on 2024-07-01.</p>
      <div className="graph-scroll">
        <svg width={W} height={layout.height} role="img" aria-label="Knowledge graph">
          {COLUMNS.map((c, i) => <text key={c} x={i * COL + 8} y={18} className="col-title">{c}</text>)}
          {graph?.edges.map((e, i) => {
            const a = layout.pos.get(e.source), b = layout.pos.get(e.target);
            if (!a || !b) return null;
            const on = !linked || (linked.has(e.source) && linked.has(e.target) && (e.source === focus || e.target === focus));
            return <line key={i} x1={a.x + COL - 24} y1={a.y + 10} x2={b.x} y2={b.y + 10} className={`edge ${e.type} ${on ? "" : "dim"}`}><title>{e.type} (valid from {e.valid_from})</title></line>;
          })}
          {graph?.nodes.map((n) => {
            const p = layout.pos.get(n.id);
            if (!p) return null;
            const on = !linked || linked.has(n.id);
            return (
              <g key={n.id} transform={`translate(${p.x},${p.y})`} className={`node ${n.type} ${on ? "" : "dim"}`} onClick={() => setFocus(focus === n.id ? null : n.id)}>
                <rect width={COL - 24} height={22} rx={5} />
                <text x={6} y={15}>{n.name.length > 24 ? n.name.slice(0, 23) + "…" : n.name}</text>
                <title>{n.id}</title>
              </g>
            );
          })}
        </svg>
      </div>
    </section>
  );
}
