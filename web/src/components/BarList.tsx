// Horizontal bar list on a shared grid (label / bar / value), single hue, sorted descending. Reads like a table column.
export default function BarList({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <p className="muted">No record-level violations.</p>;
  return (
    <div className="bars" role="list">
      <div className="bar-row bar-head" aria-hidden="true"><span>Branch</span><span /><span>Violations</span></div>
      {entries.map(([label, value]) => (
        <div key={label} className="bar-row" role="listitem" aria-label={`${label}: ${value}`}>
          <span className="bar-label">{label}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(value / max) * 100}%` }} /></span>
          <span className="bar-value mono">{value}</span>
        </div>
      ))}
    </div>
  );
}
