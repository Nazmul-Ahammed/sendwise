export default function FlowLegend() {
  return (
    <div className="card" style={{ padding: "14px 16px", marginBottom: 16, background: "linear-gradient(135deg,#EEF2FF,#F5F3FF)" }}>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center", fontSize: 13 }}>
        <span style={{ fontWeight: 800, color: "var(--brand-600)" }}>Flow logic</span>
        <span className="badge b-agent">S = Stage (flow step)</span>
        <span style={{ color: "var(--ink-3)" }}>·</span>
        <span className="badge b-info">E = Email (message)</span>
        <span style={{ color: "var(--ink-2)" }}>
          Prottek email er <b>3 ta outcome</b> routing:
          <span className="badge b-success" style={{ margin: "0 4px" }}>Open+Click →</span>
          <span className="badge b-info">Open+Not Click →</span>
          <span className="badge b-warning" style={{ margin: "0 4px" }}>Not Open →</span>
          next email ba Exit. E1: Open+Click → E2 · Open+Not Click → E3 · Not Open → E4.
        </span>
      </div>
    </div>
  );
}
