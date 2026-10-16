// Horizontal bar list: single hue, value labels on every bar, sorted descending.
export default function BarList({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <p className="muted">No record-level violations.</p>;
  return (
    <div className="bars" role="list">
      {entries.map(([label, value]) => (
        <div key={label} className="bar-row" role="listitem" aria-label={`${label}: ${value}`}>
          <span className="bar-label">{label}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(value / max) * 100}%` }} /></span>
          <span className="bar-value">{value}</span>
        </div>
      ))}
    </div>
  );
}
