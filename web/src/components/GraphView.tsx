import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import type { GraphSnapshot } from "../api";
import { EmptyState, Skeleton } from "./Motif";
import GraphInspector from "./GraphInspector";
import {
  LAYER_ORDER, SHAPES, TYPE_LABEL, addDays, chipState, dayToIso, edgeState, forceLayout, isoToDay, layerLayout, loadUniverse, neighbourhood, todayIso,
  validAt, wrapLabel, type EdgeState, type GEdge, type Pos, type Universe,
} from "../graph-utils";

const W = 1000, H = 600;
const STATE_TEXT: Record<EdgeState, string> = { valid: "valid", entering: "entering (new in the scrubbed interval)", expiring: "expiring (ended in the scrubbed interval)", invalid: "not valid at this date" };

function Shape({ type }: { type: string }) {
  switch (SHAPES[type] ?? "circle") {
    case "square": return <rect x={-8} y={-8} width={16} height={16} rx={3} />;
    case "diamond": return <path d="M0 -11 L11 0 L0 11 L-11 0 Z" />;
    case "hex": return <path d="M-6 -10 L6 -10 L12 0 L6 10 L-6 10 L-12 0 Z" />;
    case "tri": return <path d="M0 -11 L11 9 L-11 9 Z" />;
    default: return <circle r={9} />;
  }
}

export default function GraphView({ defaultAsOf }: { defaultAsOf: string }) {
  const cache = useRef(new Map<string, GraphSnapshot>());
  const [universe, setUniverse] = useState<Universe | null>(null);
  const [error, setError] = useState("");
  const [asOf, setAsOf] = useState(defaultAsOf);        // committed (debounced) business date
  const [scrub, setScrub] = useState(defaultAsOf);      // what the slider/date input shows right now
  const [since, setSince] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [hops, setHops] = useState(1);
  const [mode, setMode] = useState<"explore" | "layers">("explore");
  const [layers, setLayers] = useState<Set<string>>(new Set(LAYER_ORDER));
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [query, setQuery] = useState("");
  const [comboOpen, setComboOpen] = useState(false);
  const [active, setActive] = useState(0);
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const gesture = useRef(false);
  const gestureTimer = useRef<number>(0);
  const asOfRef = useRef(asOf);
  const returnFocus = useRef<Element | null>(null);

  useEffect(() => {
    let live = true;
    loadUniverse(defaultAsOf, cache.current).then((u) => { if (live) setUniverse(u); }).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [defaultAsOf]);

  // Debounced commit of the scrubbed date; the first change of a gesture remembers where it started.
  useEffect(() => {
    if (scrub === asOfRef.current) return;
    const t = window.setTimeout(() => { asOfRef.current = scrub; setAsOf(scrub); }, 140);
    return () => window.clearTimeout(t);
  }, [scrub]);
  const scrubTo = (next: string) => {
    if (!next) return;
    if (!gesture.current) setSince(asOfRef.current);
    gesture.current = true;
    window.clearTimeout(gestureTimer.current);
    gestureTimer.current = window.setTimeout(() => { gesture.current = false; }, 1600);
    setScrub(next);
  };

  const types = useMemo(() => {
    const present = new Set([...(universe?.nodes.values() ?? [])].map((n) => n.type));
    return [...LAYER_ORDER.filter((t) => present.has(t)), ...[...present].filter((t) => !LAYER_ORDER.includes(t)).sort()];
  }, [universe]);
  useEffect(() => { setLayers(new Set(types)); }, [types]);

  const seeds = useMemo(() => (anchor ? [anchor, ...expanded.filter((e) => e !== anchor)] : []), [anchor, expanded]);
  const hood = useMemo(() => (universe && anchor ? neighbourhood(universe, seeds, hops, layers) : null), [universe, anchor, seeds, hops, layers]);
  const positions = useMemo<Map<string, Pos>>(() => {
    if (!universe) return new Map();
    if (mode === "layers") return layerLayout(universe, types, layers);
    return hood ? forceLayout(universe, hood, seeds.length === 1 ? anchor : null) : new Map();
  }, [universe, mode, hood, types, layers, seeds, anchor]);

  const shownEdges: GEdge[] = useMemo(() => {
    if (!universe) return [];
    if (mode === "layers") return universe.edges.filter((e) => positions.has(e.source) && positions.has(e.target));
    return hood?.edges ?? [];
  }, [universe, mode, hood, positions]);

  // timeline range from the edges' own validity dates, padded
  const range = useMemo(() => {
    const all = (universe?.edges ?? []).flatMap((e) => [e.valid_from, e.valid_to].filter(Boolean) as string[]);
    const lo = all.length ? all.reduce((a, b) => (a < b ? a : b)) : addDays(todayIso(), -365);
    const hi = all.filter((d) => d < "2090-01-01").reduce((a, b) => (a > b ? a : b), todayIso());
    return { min: isoToDay(addDays(lo, -30)), max: isoToDay(addDays(hi > todayIso() ? hi : todayIso(), 30)), earliest: lo };
  }, [universe]);
  const changeDates = useMemo(() => [...new Set(shownEdges.flatMap((e) => [e.valid_from, e.valid_to].filter(Boolean) as string[]))].filter((d) => d < "2090-01-01").sort(), [shownEdges]);
  const prevChange = [...changeDates].reverse().find((d) => d < scrub);
  const nextChange = changeDates.find((d) => d > scrub);

  const fit = useCallback(() => {
    if (!positions.size) { setView({ x: W / 2, y: H / 2, k: 1 }); return; }
    const xs = [...positions.values()].map((p) => p.x), ys = [...positions.values()].map((p) => p.y);
    const pad = mode === "layers" ? 40 : 110;
    const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad + (mode === "layers" ? 170 : 0), minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;
    const k = Math.min(W / (maxX - minX), H / (maxY - minY), 1.3);
    setView({ k, x: W / 2 - ((minX + maxX) / 2) * k, y: H / 2 - ((minY + maxY) / 2) * k });
  }, [positions, mode]);
  useEffect(() => { fit(); }, [fit]);

  const zoom = useCallback((factor: number, cx = W / 2, cy = H / 2) => {
    setView((v) => { const k = Math.max(0.15, Math.min(3, v.k * factor)); const r = k / v.k; return { k, x: cx - (cx - v.x) * r, y: cy - (cy - v.y) * r }; });
  }, []);
  const showCanvas = Boolean(universe) && (mode === "layers" || Boolean(anchor));
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const box = el.getBoundingClientRect();
      zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, ((e.clientX - box.left) / box.width) * W, ((e.clientY - box.top) / box.height) * H);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoom, showCanvas]);

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if ((e.target as Element).closest(".gnode")) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current; if (!d) return;
    const scale = W / e.currentTarget.getBoundingClientRect().width;
    setView((v) => ({ ...v, x: d.vx + (e.clientX - d.x) * scale, y: d.vy + (e.clientY - d.y) * scale }));
  };
  const onCanvasKey = (e: ReactKeyboardEvent) => {
    if ((e.target as Element).closest(".gnode")) return;
    const step = 50;
    if (e.key === "ArrowLeft") setView((v) => ({ ...v, x: v.x + step }));
    else if (e.key === "ArrowRight") setView((v) => ({ ...v, x: v.x - step }));
    else if (e.key === "ArrowUp") setView((v) => ({ ...v, y: v.y + step }));
    else if (e.key === "ArrowDown") setView((v) => ({ ...v, y: v.y - step }));
    else if (e.key === "+" || e.key === "=") zoom(1.2);
    else if (e.key === "-") zoom(1 / 1.2);
    else if (e.key === "0") fit();
    else return;
    e.preventDefault();
  };

  const select = (id: string | null, trigger?: Element | null) => {
    if (id && !selected) returnFocus.current = trigger ?? document.activeElement;
    setSelected(id);
  };
  const closeInspector = () => {
    setSelected(null);
    const el = returnFocus.current as HTMLElement | null;
    requestAnimationFrame(() => el?.focus?.());
  };
  const focusNode = (id: string) => { setAnchor(id); setExpanded([]); setMode("explore"); setQuery(""); };
  const expandNode = (id: string) => {
    if (!anchor) { focusNode(id); return; }
    setMode("explore");
    setExpanded((x) => (x.includes(id) || id === anchor ? x : [...x, id]));
  };
  const toggleLayer = (t: string) => setLayers((s) => { const n = new Set(s); if (n.has(t)) n.delete(t); else n.add(t); return n; });

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !universe) return [];
    return [...universe.nodes.values()].filter((n) => n.name.toLowerCase().includes(q) || n.id.toLowerCase().includes(q))
      .sort((a, b) => Number(b.name.toLowerCase().startsWith(q)) - Number(a.name.toLowerCase().startsWith(q)) || a.name.localeCompare(b.name)).slice(0, 8);
  }, [query, universe]);
  const pickMatch = (id: string) => { focusNode(id); setComboOpen(false); setSelected(null); };
  const onSearchKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setComboOpen(true); setActive((a) => Math.min(a + 1, matches.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && matches[active]) { e.preventDefault(); pickMatch(matches[active].id); }
    else if (e.key === "Escape") setComboOpen(false);
  };

  const highlight = hover ?? selected;
  const near = useMemo(() => {
    if (!highlight) return null;
    const s = new Set([highlight]);
    shownEdges.forEach((e) => { if (e.source === highlight) s.add(e.target); if (e.target === highlight) s.add(e.source); });
    return s;
  }, [highlight, shownEdges]);

  const selectedNode = selected && universe ? universe.nodes.get(selected) ?? null : null;
  const presets: [string, string][] = [["Today", todayIso()], ["Start of year", `${todayIso().slice(0, 4)}-01-01`], ["Earliest change", universe ? range.earliest : ""], ["2024-01-01", "2024-01-01"]];
  const suggestions = useMemo(() => ["Rule", "Product", "Control"].map((t) => [...(universe?.nodes.values() ?? [])].find((n) => n.type === t)).filter(Boolean) as { id: string; name: string; type: string }[], [universe]);

  const edgePath = (a: Pos, b: Pos) => (mode === "layers" ? `M${a.x} ${a.y} C${a.x + 110} ${a.y} ${b.x - 110} ${b.y} ${b.x} ${b.y}` : `M${a.x} ${a.y} L${b.x} ${b.y}`);
  const counts = { entering: 0, expiring: 0 };
  shownEdges.forEach((e) => { const s = edgeState(e, asOf, since); if (s === "entering") counts.entering++; if (s === "expiring") counts.expiring++; });

  return (
    <section className="graph-tab">
      <div className="timebar card flush" role="group" aria-label="Time travel (business date)">
        <div className="timebar-row">
          <label className="inline-field">As of
            <input type="date" value={scrub} onChange={(e) => scrubTo(e.target.value)} />
          </label>
          <input className="slider" type="range" aria-label="Scrub the as-of date" min={range.min} max={range.max} step={1}
            value={Math.max(range.min, Math.min(range.max, isoToDay(scrub)))} onChange={(e) => scrubTo(dayToIso(Number(e.target.value)))} />
          <div className="buttons">
            <button disabled={!prevChange} onClick={() => prevChange && scrubTo(prevChange)} aria-label={prevChange ? `Previous change, ${prevChange}` : "No earlier change"}>← Prev change</button>
            <button disabled={!nextChange} onClick={() => nextChange && scrubTo(nextChange)} aria-label={nextChange ? `Next change, ${nextChange}` : "No later change"}>Next change →</button>
          </div>
        </div>
        <div className="timebar-row sub">
          <span className="preset-row">{presets.filter(([, d]) => d).map(([l, d]) => <button key={l} className="chip-btn" onClick={() => scrubTo(d)}>{l}</button>)}</span>
          <label className="inline-field">Highlight changes from
            <input type="date" value={since ?? ""} onChange={(e) => setSince(e.target.value || null)} />
          </label>
          <span className="muted mono" role="status">
            {universe ? `${counts.entering} entering · ${counts.expiring} expiring · ${universe.edges.length} edges known (${universe.requests} date snapshots fetched)` : ""}
          </span>
        </div>
      </div>

      <div className="card graph-card">
        <div className="graph-toolbar">
          <div className="combo">
            <label className="search">
              <span className="sr-only">Search for an entity to anchor the view</span>
              <input type="search" role="combobox" aria-expanded={comboOpen && matches.length > 0} aria-controls="graph-matches" aria-autocomplete="list"
                aria-activedescendant={comboOpen && matches[active] ? `gm-${active}` : undefined} placeholder="Search a rule, product, control…" value={query}
                onChange={(e) => { setQuery(e.target.value); setComboOpen(true); setActive(0); }} onKeyDown={onSearchKey} onFocus={() => setComboOpen(true)}
                onBlur={() => window.setTimeout(() => setComboOpen(false), 120)} />
            </label>
            {comboOpen && matches.length > 0 && (
              <ul id="graph-matches" role="listbox" className="combo-list" aria-label="Matching entities">
                {matches.map((n, i) => (
                  <li key={n.id} id={`gm-${i}`} role="option" aria-selected={i === active} className={i === active ? "active" : ""} onMouseDown={(e) => { e.preventDefault(); pickMatch(n.id); }}>
                    <span className={`type-tag t-${n.type}`}>{n.type}</span> {n.name} <span className="mono muted">{n.id}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="segmented" role="group" aria-label="View mode">
            <button className={mode === "explore" ? "tab active" : "tab"} aria-pressed={mode === "explore"} onClick={() => setMode("explore")}>Explore</button>
            <button className={mode === "layers" ? "tab active" : "tab"} aria-pressed={mode === "layers"} onClick={() => setMode("layers")}>Browse by layer</button>
          </div>
          {mode === "explore" && (
            <div className="segmented" role="group" aria-label="Neighbourhood depth">
              <button className={hops === 1 ? "tab active" : "tab"} aria-pressed={hops === 1} onClick={() => setHops(1)}>1 hop</button>
              <button className={hops === 2 ? "tab active" : "tab"} aria-pressed={hops === 2} onClick={() => setHops(2)}>2 hops</button>
            </div>
          )}
          <div className="buttons zoom-ctl" role="group" aria-label="Canvas controls">
            <button className="icon" onClick={() => zoom(1.25)} aria-label="Zoom in">+</button>
            <button className="icon" onClick={() => zoom(1 / 1.25)} aria-label="Zoom out">−</button>
            <button onClick={fit}>Fit</button>
            <button onClick={() => { setView({ x: W / 2, y: H / 2, k: 1 }); }}>Reset</button>
          </div>
        </div>
        <fieldset className="layer-filter">
          <legend className="sr-only">Visible layers</legend>
          {types.map((t) => (
            <label key={t} className="inline"><input type="checkbox" checked={layers.has(t)} onChange={() => toggleLayer(t)} /><span className={`type-tag t-${t}`}>{t}</span></label>
          ))}
        </fieldset>
        {error && <div className="banner error" role="alert">{error}</div>}
        {!universe && !error && <Skeleton rows={6} label="Loading graph" />}
        {!universe && error && <EmptyState title="Graph not loaded" hint="The knowledge graph could not be fetched." />}
        {universe && mode === "explore" && !anchor && (
          <EmptyState title="Pick an entity to explore" hint="Search above, or start from a rule, product or control. Only its direct connections are drawn, so the graph stays readable.">
            <div className="buttons">{suggestions.map((n) => <button key={n.id} onClick={() => focusNode(n.id)}><span className={`type-tag t-${n.type}`}>{n.type}</span> {n.name}</button>)}</div>
          </EmptyState>
        )}
        {universe && showCanvas && (
          <div className="canvas-wrap">
            <div className="canvas" role="group" aria-label="Knowledge graph canvas. Arrow keys pan, plus and minus zoom, zero fits." tabIndex={0} onKeyDown={onCanvasKey}>
              <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Knowledge graph, ${positions.size} nodes and ${shownEdges.length} relationships as of ${asOf}`}
                onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
                <g style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }} className="viewport">
                  {shownEdges.map((e) => {
                    const a = positions.get(e.source), b = positions.get(e.target);
                    if (!a || !b) return null;
                    const st = edgeState(e, asOf, since);
                    const dim = near ? !(highlight === e.source || highlight === e.target) : false;
                    return (
                      <path key={`${e.source}|${e.type}|${e.target}|${e.valid_from}`} d={edgePath(a, b)} className={`gedge st-${st}${dim ? " dim" : ""}${near && !dim ? " hot" : ""}`}>
                        <title>{`${e.type}: ${e.valid_from} → ${e.valid_to ?? "open"} (${STATE_TEXT[st]})`}</title>
                      </path>
                    );
                  })}
                  {[...positions.entries()].map(([id, p]) => {
                    const n = universe.nodes.get(id);
                    if (!n) return null;
                    const lines = wrapLabel(n.name);
                    const dim = near ? !near.has(id) : false;
                    return (
                      <g key={id} className={`gnode t-${n.type}${selected === id ? " sel" : ""}${id === anchor ? " anchor" : ""}${dim ? " dim" : ""}`}
                        style={{ transform: `translate(${p.x}px, ${p.y}px)` }} tabIndex={0} role="button"
                        aria-label={`${n.name}, ${TYPE_LABEL[n.type] ?? n.type}. Press Enter to inspect.`} aria-pressed={selected === id}
                        onMouseEnter={() => setHover(id)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(id)} onBlur={() => setHover(null)}
                        onClick={(e) => select(selected === id ? null : id, e.currentTarget)} onDoubleClick={() => expandNode(id)}
                        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(selected === id ? null : id, e.currentTarget); } }}>
                        <Shape type={n.type} />
                        <text x={17} y={lines.length > 1 ? -((lines.length - 1) * 6) + 4 : 4}>
                          {lines.map((l, i) => <tspan key={i} x={17} dy={i === 0 ? 0 : 13}>{l}</tspan>)}
                        </text>
                        <title>{`${n.name} (${n.type}) ${n.id}`}</title>
                      </g>
                    );
                  })}
                </g>
              </svg>
            </div>
            {selectedNode && (
              <GraphInspector node={selectedNode} universe={universe} asOf={asOf} onSelect={(id) => setSelected(id)} onFocus={focusNode} onExpand={expandNode} onClose={closeInspector} />
            )}
          </div>
        )}
        <div className="legend" aria-label="Legend">
          <span className="legend-group">
            {types.map((t) => <span key={t} className={`legend-item t-${t}`}><svg width="22" height="22" viewBox="-13 -13 26 26" aria-hidden="true"><g className="gnode legend-shape"><Shape type={t} /></g></svg>{t}</span>)}
          </span>
          <span className="legend-group">
            <span className="legend-item"><svg width="34" height="10" aria-hidden="true"><line x1="0" y1="5" x2="34" y2="5" className="gedge st-valid" /></svg>valid</span>
            <span className="legend-item"><svg width="34" height="10" aria-hidden="true"><line x1="0" y1="5" x2="34" y2="5" className="gedge st-entering" /></svg>entering</span>
            <span className="legend-item"><svg width="34" height="10" aria-hidden="true"><line x1="0" y1="5" x2="34" y2="5" className="gedge st-expiring" /></svg>expiring / ended</span>
            <span className="legend-item"><svg width="34" height="10" aria-hidden="true"><line x1="0" y1="5" x2="34" y2="5" className="gedge st-invalid" /></svg>not valid at date</span>
          </span>
        </div>
        {hood?.truncated && <p className="muted">Showing the {positions.size} closest entities; narrow the layers or use 1 hop to see the rest.</p>}
        {universe && showCanvas && (
          <details className="edge-table">
            <summary>Relationships as a table ({shownEdges.length})</summary>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Relationships in view">
              <table className="grid">
                <thead><tr><th scope="col">Source</th><th scope="col">Type</th><th scope="col">Target</th><th scope="col">Valid from</th><th scope="col">Valid to</th><th scope="col">At {asOf}</th></tr></thead>
                <tbody>
                  {shownEdges.map((e) => (
                    <tr key={`${e.source}|${e.type}|${e.target}|${e.valid_from}`}>
                      <td>{universe.nodes.get(e.source)?.name ?? e.source}</td><td className="mono">{e.type}</td><td>{universe.nodes.get(e.target)?.name ?? e.target}</td>
                      <td className="mono">{e.valid_from}</td><td className="mono">{e.valid_to ?? "open"}</td>
                      <td>{validAt(e, asOf) ? "valid" : "not valid"} · {chipState(e, asOf)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
      </div>
    </section>
  );
}
