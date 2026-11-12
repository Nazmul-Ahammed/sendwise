export default function ThresholdTable({ thresholds }) {
  if (!thresholds || thresholds.length === 0) return null;
  return (
    <div>
      {thresholds.map((t, i) => (
        <div className="thresh" key={i}>
          <b>{t.label}</b>
          <span className={`v ${t.status === "pass" ? "ok" : t.status === "warn" ? "warn" : "block"}`}>
            {t.value} {t.limit}
          </span>
        </div>
      ))}
    </div>
  );
}
