import { api, type GraphSnapshot } from "./api";

export interface GNode { id: string; type: string; name: string; attributes?: Record<string, unknown> }
export interface GEdge { source: string; target: string; type: string; valid_from: string; valid_to: string | null }
export interface Universe { nodes: Map<string, GNode>; edges: GEdge[]; requests: number }

export const edgeKey = (e: GEdge) => `${e.source}|${e.type}|${e.target}|${e.valid_from}`;

// ---------------------------------------------------------------- dates (UTC day arithmetic on ISO strings)
export const todayIso = () => new Date().toISOString().slice(0, 10);
const day = (iso: string) => Math.round(Date.parse(`${iso}T00:00:00Z`) / 86_400_000);
export const dayToIso = (n: number) => new Date(n * 86_400_000).toISOString().slice(0, 10);
export const isoToDay = day;
export const addDays = (iso: string, n: number) => dayToIso(day(iso) + n);

// ---------------------------------------------------------------- bitemporal edge state
export const validAt = (e: GEdge, d: string) => e.valid_from <= d && (!e.valid_to || e.valid_to > d);

export type EdgeState = "valid" | "entering" | "expiring" | "invalid";
/** State of an edge at `asOf`. Changes inside the scrubbed interval (between `since` and `asOf`, either direction) are
 *  highlighted: entering = valid_from falls inside it, expiring = valid_to falls inside it. Everything else is valid or
 *  not valid at asOf. */
export function edgeState(e: GEdge, asOf: string, since: string | null): EdgeState {
  if (since && since !== asOf) {
    const lo = since < asOf ? since : asOf, hi = since < asOf ? asOf : since;
    const starts = e.valid_from > lo && e.valid_from <= hi;
    const ends = Boolean(e.valid_to) && e.valid_to! > lo && e.valid_to! <= hi;
    if (starts && ends) return validAt(e, asOf) ? "entering" : "expiring";
    if (starts) return "entering";
    if (ends) return "expiring";
  }
  return validAt(e, asOf) ? "valid" : "invalid";
}

export type ChipState = "active" | "expiring" | "expired" | "future";
/** Status chip for a relationship: expiring means it ends within 90 days after asOf. */
export function chipState(e: GEdge, asOf: string): ChipState {
  if (e.valid_from > asOf) return "future";
  if (e.valid_to && e.valid_to <= asOf) return "expired";
  if (e.valid_to && e.valid_to <= addDays(asOf, 90)) return "expiring";
  return "active";
}

// ---------------------------------------------------------------- data: the edge universe
/** /api/graph only returns edges valid at one date. To see entering and expired edges the client fetches several
 *  dates and unions them: the requested date, a floor, today, a far future date, then every change date found. */
export async function loadUniverse(asOf: string, cache: Map<string, GraphSnapshot>): Promise<Universe> {
  let requests = 0;
  const get = async (d: string) => {
    const hit = cache.get(d);
    if (hit) return hit;
    requests += 1;
    const snap = await api.graph(d);
    cache.set(d, snap);
    return snap;
  };
  const nodes = new Map<string, GNode>();
  const edges = new Map<string, GEdge>();
  const absorb = (s: GraphSnapshot) => {
    for (const n of s.nodes as GNode[]) if (!nodes.has(n.id)) nodes.set(n.id, n);
    for (const e of s.edges) edges.set(edgeKey(e), e);
  };
  const first = [asOf, "2020-01-01", todayIso(), "2099-12-31"];
  (await Promise.all([...new Set(first)].map(get))).forEach(absorb);
  const changes = new Set<string>();
  edges.forEach((e) => { changes.add(e.valid_from); if (e.valid_to) changes.add(e.valid_to); });
  const probes = [...changes].filter((d) => !cache.has(d)).sort().slice(0, 16);
  (await Promise.all(probes.map(get))).forEach(absorb);
  return { nodes, edges: [...edges.values()], requests };
}

// ---------------------------------------------------------------- neighbourhood and layout
export interface Hood { dist: Map<string, number>; edges: GEdge[]; truncated: boolean }
const MAX_NODES = 140;

/** Multi-source BFS from the seeds (anchor plus expanded nodes) over visible layers, up to `hops`. */
export function neighbourhood(u: Universe, seeds: string[], hops: number, layers: Set<string>): Hood {
  const adj = new Map<string, GEdge[]>();
  for (const e of u.edges) {
    for (const id of [e.source, e.target]) { if (!adj.has(id)) adj.set(id, []); adj.get(id)!.push(e); }
  }
  const visible = (id: string) => { const n = u.nodes.get(id); return Boolean(n) && (layers.has(n!.type) || seeds.includes(id)); };
  const dist = new Map<string, number>();
  let frontier = seeds.filter((s) => u.nodes.has(s));
  frontier.forEach((s) => dist.set(s, 0));
  for (let d = 1; d <= hops; d++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const e of adj.get(id) ?? []) {
        const other = e.source === id ? e.target : e.source;
        if (!dist.has(other) && visible(other)) { dist.set(other, d); next.push(other); }
      }
    }
    frontier = next;
  }
  let truncated = false;
  if (dist.size > MAX_NODES) {
    truncated = true;
    const keep = [...dist.entries()].sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0])).slice(0, MAX_NODES);
    dist.clear(); keep.forEach(([id, d]) => dist.set(id, d));
  }
  const edges = u.edges.filter((e) => dist.has(e.source) && dist.has(e.target) && ((dist.get(e.source) ?? 9) < hops || (dist.get(e.target) ?? 9) < hops));
  return { dist, edges, truncated };
}

export type Pos = { x: number; y: number };

/** Deterministic force layout: rings by hop distance as the start, then a fixed number of relaxation steps. */
export function forceLayout(u: Universe, hood: Hood, anchor: string | null): Map<string, Pos> {
  const ids = [...hood.dist.keys()].sort((a, b) => (hood.dist.get(a)! - hood.dist.get(b)!) || (u.nodes.get(a)!.type.localeCompare(u.nodes.get(b)!.type)) || u.nodes.get(a)!.name.localeCompare(u.nodes.get(b)!.name));
  const pos = new Map<string, Pos>();
  const byRing = new Map<number, string[]>();
  ids.forEach((id) => { const r = hood.dist.get(id)!; byRing.set(r, [...(byRing.get(r) ?? []), id]); });
  byRing.forEach((members, ring) => {
    const radius = ring === 0 ? (members.length > 1 ? 60 : 0) : 190 * ring;
    members.forEach((id, i) => {
      const a = (2 * Math.PI * i) / members.length + ring * 0.6;
      pos.set(id, { x: Math.cos(a) * radius, y: Math.sin(a) * radius });
    });
  });
  const links = hood.edges.map((e) => [e.source, e.target] as const);
  const REST = 150, REPEL = 26000;
  for (let it = 0; it < 160; it++) {
    const force = new Map<string, Pos>(ids.map((id) => [id, { x: 0, y: 0 }]));
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos.get(ids[i])!, b = pos.get(ids[j])!;
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = (i % 2 ? 1 : -1) * 0.5; dy = 0.5; d2 = 1; }
        const f = REPEL / d2, d = Math.sqrt(d2);
        const fa = force.get(ids[i])!, fb = force.get(ids[j])!;
        fa.x += (dx / d) * f; fa.y += (dy / d) * f; fb.x -= (dx / d) * f; fb.y -= (dy / d) * f;
      }
    }
    for (const [s, t] of links) {
      const a = pos.get(s)!, b = pos.get(t)!;
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.max(1, Math.hypot(dx, dy));
      const f = (d - REST) * 0.04;
      const fa = force.get(s)!, fb = force.get(t)!;
      fa.x += (dx / d) * f; fa.y += (dy / d) * f; fb.x -= (dx / d) * f; fb.y -= (dy / d) * f;
    }
    const cool = 1 - it / 180;
    for (const id of ids) {
      if (id === anchor) continue;
      const p = pos.get(id)!, f = force.get(id)!;
      const gx = -p.x * 0.004, gy = -p.y * 0.004;
      const mx = Math.max(-30, Math.min(30, f.x + gx)) * cool, my = Math.max(-30, Math.min(30, f.y + gy)) * cool;
      p.x += mx; p.y += my;
    }
  }
  return pos;
}

/** Layered columns, one per visible type (the "Browse by layer" mode). */
export function layerLayout(u: Universe, layers: string[], visible: Set<string>): Map<string, Pos> {
  const pos = new Map<string, Pos>();
  const COL = 250, ROW = 58;
  layers.filter((t) => visible.has(t)).forEach((type, c) => {
    [...u.nodes.values()].filter((n) => n.type === type).sort((a, b) => a.name.localeCompare(b.name))
      .forEach((n, i) => pos.set(n.id, { x: c * COL, y: i * ROW }));
  });
  return pos;
}

export const LAYER_ORDER = ["Policy", "Rule", "Product", "Process", "Control", "Team", "CustomerSegment", "Branch"];
export const TYPE_LABEL: Record<string, string> = {
  Policy: "Regulatory policy", Rule: "Obligation (rule)", Product: "Banking product", Process: "BIAN business process",
  Control: "Control", Team: "Owner team", CustomerSegment: "Customer segment", Branch: "Branch",
};
export const SHAPES: Record<string, "circle" | "square" | "diamond" | "hex" | "tri"> = {
  Policy: "square", Rule: "diamond", Product: "circle", Process: "hex", Control: "tri", Team: "square", CustomerSegment: "circle", Branch: "hex",
};

/** Wrap a full label to at most three lines of ~`width` characters (the full text stays in the tooltip and inspector). */
export function wrapLabel(text: string, width = 22): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if (cur && (cur + " " + w).length > width) { lines.push(cur); cur = w; } else cur = cur ? `${cur} ${w}` : w;
  }
  if (cur) lines.push(cur);
  if (lines.length > 3) { const rest = lines.slice(2).join(" "); return [lines[0], lines[1], rest.length > width + 8 ? `${rest.slice(0, width + 7)}…` : rest]; }
  return lines;
}
