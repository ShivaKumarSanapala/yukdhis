export interface Metric { label: string; value: string | number; sub?: string }

/** One tight grid of metric blocks with shared 1px dividers; values are monospace tabular figures. */
export default function MetricStrip({ metrics, label = "Key metrics" }: { metrics: Metric[]; label?: string }) {
  return (
    <dl className="metrics" aria-label={label}>
      {metrics.map((m) => (
        <div className="metric" key={m.label}>
          <dt>{m.label}</dt>
          <dd>{m.value}</dd>
          {m.sub && <small>{m.sub}</small>}
        </div>
      ))}
    </dl>
  );
}
