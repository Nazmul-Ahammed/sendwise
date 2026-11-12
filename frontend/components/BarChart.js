export default function BarChart({ data }) {
  if (!data || data.length === 0) return null;
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="bars">
      {data.map((d) => (
        <div className="bar-col" key={d.day}>
          <div className="bar" style={{ height: `${Math.round((d.value / max) * 100)}%` }}>
            <span className="tip">{d.value} clicks</span>
          </div>
          <span className="bar-day">{d.day}</span>
        </div>
      ))}
    </div>
  );
}
