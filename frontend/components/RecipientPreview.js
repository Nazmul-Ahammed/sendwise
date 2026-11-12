"use client";

import { useState } from "react";

/**
 * Shows exactly who a send will reach - the resolved address list, not just a count.
 *
 * Suppressed and unsubscribed contacts never appear here because the server has
 * already filtered them out, so what is listed is what will actually be emailed.
 */
export default function RecipientPreview({
  rows = [],
  title = "Recipients",
  note = "",
  empty = "Nobody yet - pick an audience or paste at least one address.",
  tone = "success",
}) {
  const [showAll, setShowAll] = useState(false);
  const [filter, setFilter] = useState("");

  // `will_receive` comes from the server's own recipient resolution. Anything
  // false is listed separately, so the preview never promises a delivery the
  // send would skip.
  const included = rows.filter((r) => r.will_receive !== false);
  const excluded = rows.filter((r) => r.will_receive === false);

  const q = filter.trim().toLowerCase();
  const matched = q
    ? included.filter((r) => (r.email || "").toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q))
    : included;
  const shown = showAll ? matched : matched.slice(0, 8);
  const hidden = matched.length - shown.length;

  const palette = tone === "success"
    ? { bg: "var(--success-bg, #ECFDF3)", border: "var(--success-border, #BBE5CB)", fg: "var(--success, #16794C)" }
    : { bg: "var(--surface-2)", border: "var(--border)", fg: "var(--ink-2)" };

  if (!rows.length) {
    return (
      <div className="note n-info" style={{ marginTop: 0 }}>
        <span>{empty}</span>
      </div>
    );
  }

  return (
    <div style={{ border: `1px solid ${palette.border}`, background: palette.bg, borderRadius: "var(--radius)", padding: "10px 12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, color: palette.fg }}>
          {title} · {included.length} will receive this
          {excluded.length ? <span style={{ fontWeight: 400, color: "var(--ink-3)" }}> · {excluded.length} excluded</span> : null}
        </div>
        {included.length > 8 ? (
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter…"
            style={{ width: 150, fontSize: 12, padding: "3px 8px", borderRadius: 6, border: `1px solid ${palette.border}`, background: "#fff" }}
          />
        ) : null}
      </div>

      {note ? <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 3 }}>{note}</div> : null}

      <div style={{ marginTop: 8, display: "grid", gap: 3 }}>
        {shown.map((r, i) => (
          <div
            key={`${r.email}-${i}`}
            style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline", fontSize: 12 }}
          >
            <span className="mono" style={{ fontSize: 11.5, wordBreak: "break-all" }}>{r.email}</span>
            <span style={{ color: "var(--ink-3)", fontSize: 11, whiteSpace: "nowrap" }}>
              {r.name || (r.tags && r.tags.length ? r.tags.join(", ") : "")}
            </span>
          </div>
        ))}
      </div>

      {matched.length === 0 && included.length > 0 ? (
        <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 6 }}>No address matches “{filter}”.</div>
      ) : null}

      {matched.length > 8 ? (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          style={{ marginTop: 8, background: "none", border: "none", padding: 0, cursor: "pointer", fontSize: 11.5, fontWeight: 700, color: palette.fg }}
        >
          {showAll ? "Show fewer" : `Show all ${matched.length}`}
          {!showAll && hidden > 0 ? ` (${hidden} more)` : ""}
        </button>
      ) : null}

      {excluded.length ? (
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px dashed ${palette.border}` }}>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: "var(--ink-2)", marginBottom: 4 }}>
            Excluded - will not be emailed
          </div>
          {excluded.slice(0, 5).map((r, i) => (
            <div key={`x-${r.email}-${i}`} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 11.5 }}>
              <span className="mono" style={{ color: "var(--ink-3)", wordBreak: "break-all" }}>{r.email}</span>
              <span style={{ color: "var(--ink-3)", whiteSpace: "nowrap" }}>{r.excluded_reason || "excluded"}</span>
            </div>
          ))}
          {excluded.length > 5 ? (
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>+{excluded.length - 5} more</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
