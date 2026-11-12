import { StatusBadge } from "./Badge";
import { delayLabel, ROUTE_FIELDS, EXIT_LABEL } from "../lib/flow";

export default function StageRates({ stages }) {
  if (!stages || stages.length === 0) return <div className="loading">No stage data</div>;
  return (
    <div>
      {stages.map((s) => (
        <div className="stage" key={s.stage}>
          <div className="num">S{s.stage}</div>
          <div className="info">
            <b>{s.focus}</b>
            <span>{delayLabel(s)}{s.open_min != null ? ` · Open ${s.open_min}–${s.open_max}% · Click ${s.click_min}–${s.click_max}%` : ""}</span>
            {(s.emails || []).length > 0 && (
              <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap", alignItems: "center" }}>
                {(s.emails || []).map((e) => (
                  <span key={e.ref} className="badge b-info" style={{ fontFamily: "var(--mono)" }} title={e.name}>
                    {e.ref}
                    {ROUTE_FIELDS.some((r) => e[r.key] != null) && (
                      <span style={{ marginLeft: 6, display: "inline-flex", gap: 4 }}>
                        {ROUTE_FIELDS.map((r) => (
                          <span key={r.key} className={`badge ${r.badge}`} style={{ fontFamily: "var(--mono)", fontSize: 10.5 }}>
                            {r.label} → {e[r.key] || EXIT_LABEL}
                          </span>
                        ))}
                      </span>
                    )}
                  </span>
                ))}
              </div>
            )}
          </div>
          <span className={`rate ${s.actual && s.actual >= s.open_min ? "up" : "down"}`}>
            {s.actual != null ? `${s.actual}%` : "-"}
          </span>
          <StatusBadge status={s.status} />
        </div>
      ))}
    </div>
  );
}
