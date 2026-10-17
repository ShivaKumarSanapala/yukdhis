import type { ReactNode } from "react";

const NODES: [number, number, number][] = [
  [16, 40, 3], [48, 14, 2.5], [52, 62, 3], [92, 34, 4], [128, 12, 2.5], [132, 58, 3], [168, 30, 3], [200, 52, 2.5], [196, 10, 2],
];
const EDGES: [number, number][] = [[0, 1], [0, 2], [1, 3], [2, 3], [3, 4], [3, 5], [4, 6], [5, 6], [6, 7], [6, 8], [1, 2]];

/** Faint knowledge-graph motif: nodes and edges in currentColor, decorative only. */
export function Motif({ className = "", width = 216, height = 76 }: { className?: string; width?: number; height?: number }) {
  return (
    <svg className={`motif ${className}`} width={width} height={height} viewBox="0 0 216 76" fill="none" aria-hidden="true" focusable="false" preserveAspectRatio="xMidYMid slice">
      <g stroke="currentColor" strokeWidth="1">
        {EDGES.map(([a, b], i) => <line key={i} x1={NODES[a][0]} y1={NODES[a][1]} x2={NODES[b][0]} y2={NODES[b][1]} />)}
      </g>
      <g fill="currentColor">
        {NODES.map(([x, y, r], i) => <circle key={i} cx={x} cy={y} r={r} />)}
      </g>
    </svg>
  );
}

/** Designed empty state with the node motif. */
export function EmptyState({ title, hint, children }: { title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="empty" role="status">
      <Motif className="empty-motif" />
      <p className="empty-title">{title}</p>
      {hint && <p className="muted empty-hint">{hint}</p>}
      {children}
    </div>
  );
}

/** Shimmering placeholder rows for loading states. */
export function Skeleton({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div className="skeleton" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => <span key={i} className="skeleton-row" style={{ width: `${92 - ((i * 13) % 38)}%` }} />)}
    </div>
  );
}
