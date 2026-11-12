export default function GateList({ gates, compact = false }) {
  if (!gates || gates.length === 0) return null;
  return (
    <div>
      {gates.map((g, i) => (
        <div className={`gate ${g.pass ? "pass" : g.pass === false ? "fail" : "pending"}`} key={i}>
          <div className="g-ico">{g.pass ? "✓" : "–"}</div>
          <div className="g-body">
            <b>{g.name}</b>
            {!compact && <span>{g.detail}</span>}
          </div>
          <span className="g-tag">{g.pass ? "PASS" : "PENDING"}</span>
        </div>
      ))}
    </div>
  );
}
