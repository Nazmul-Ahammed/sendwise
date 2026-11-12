export default function KpiCard({ kpi }) {
  // Only three outcomes are a grade: pass / warn / fail. Everything else is a state,
  // not a verdict - an "info" ceiling and a "pending" no-data card must not borrow the
  // red of a failure. Mapping every non-pass to BLOCK (the old behaviour) is why the
  // open-rate ceiling showed up as BLOCK.
  const FLAG = {
    pass: { cls: "f-pass", label: "PASS", tone: "ok" },
    warn: { cls: "f-warn", label: "WATCH", tone: "warn" },
    fail: { cls: "f-block", label: "BLOCK", tone: "bad" },
    info: { cls: "f-info", label: "INFO", tone: "info" },
    pending: { cls: "f-pending", label: "PENDING", tone: "pending" },
  };
  const f = FLAG[kpi.status] || FLAG.pending;
  return (
    <div className={`kpi ${f.tone}`}>
      <div className="k-label">{kpi.label}</div>
      <div className="k-value">{kpi.value}</div>
      <div className="k-sub">{kpi.sub}</div>
      <span className={`k-flag ${f.cls}`}>{f.label}</span>
    </div>
  );
}
