"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Multi-select tag dropdown.
 *
 * Shows the chosen tags as chips on the closed control, and a searchable
 * checkbox list when open. Existing tags come in via `options`; a tag that
 * does not exist yet can be created inline (when `allowCreate`).
 *
 * Pass `onMatchModeChange` to also offer an AND / OR switch for 2+ tags.
 */
export default function TagSelect({
  options = [],
  value = [],
  onChange,
  placeholder = "Select tags…",
  allowCreate = true,
  disabled = false,
  matchMode = "any",
  onMatchModeChange = null,
  hint = "",
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const boxRef = useRef(null);

  useEffect(() => {
    function onDoc(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    function onEsc(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, []);

  const selected = Array.isArray(value) ? value : [];
  const counts = {};
  for (const o of options) counts[o.tag] = o.contacts;

  const typed = q.trim().toLowerCase();
  const list = options.map((o) => o.tag).filter((t) => (typed ? t.includes(typed) : true));
  const canCreate = allowCreate && typed && counts[typed] === undefined && !selected.includes(typed);

  function toggle(tag) {
    onChange(selected.includes(tag) ? selected.filter((t) => t !== tag) : [...selected, tag]);
  }

  function createTag() {
    if (!canCreate) return;
    onChange([...selected, typed]);
    setQ("");
  }

  const chip = {
    display: "inline-flex", alignItems: "center", gap: 5,
    fontSize: 11.5, fontWeight: 600, padding: "2px 7px", borderRadius: 999,
    background: "var(--brand-50, rgba(99,102,241,0.10))",
    color: "var(--brand-700, #4338ca)",
    border: "1px solid var(--brand-200, rgba(99,102,241,0.28))",
  };

  return (
    <div ref={boxRef} style={{ position: "relative" }}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        style={{
          width: "100%", minHeight: 38, textAlign: "left", cursor: disabled ? "not-allowed" : "pointer",
          display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
          padding: "7px 30px 7px 10px", borderRadius: 8,
          border: open ? "1px solid var(--brand-600)" : "1px solid var(--border)",
          background: "var(--surface, #fff)", color: "var(--ink-1, #111)", fontSize: 13,
          opacity: disabled ? 0.6 : 1,
        }}
      >
        {selected.length === 0 ? (
          <span style={{ color: "var(--ink-3, #888)" }}>{placeholder}</span>
        ) : (
          selected.map((t) => (
            <span key={t} style={chip}>
              {t}
              {counts[t] !== undefined ? <span style={{ opacity: 0.6, fontWeight: 400 }}>{counts[t]}</span> : null}
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => { e.stopPropagation(); toggle(t); }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); toggle(t); } }}
                style={{ cursor: "pointer", opacity: 0.7, fontWeight: 700 }}
                title={`Remove ${t}`}
              >
                ×
              </span>
            </span>
          ))
        )}
        <span style={{ position: "absolute", right: 10, color: "var(--ink-3, #888)", pointerEvents: "none" }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </button>

      {open ? (
        <div
          style={{
            position: "absolute", zIndex: 40, top: "calc(100% + 6px)", left: 0, right: 0,
            background: "#fff", border: "1px solid var(--border)", borderRadius: 10,
            boxShadow: "0 10px 28px rgba(15,23,42,0.14)", overflow: "hidden",
          }}
        >
          <div style={{ padding: 8, borderBottom: "1px solid var(--border)" }}>
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); if (canCreate) createTag(); else if (list.length === 1) toggle(list[0]); } }}
              placeholder={allowCreate ? "Search or type a new tag…" : "Search tags…"}
              style={{ width: "100%", border: "none", outline: "none", fontSize: 13, padding: "4px 2px" }}
            />
          </div>

          {onMatchModeChange && selected.length > 1 ? (
            <div style={{ display: "flex", gap: 6, alignItems: "center", padding: "8px 10px", borderBottom: "1px solid var(--border)" }}>
              <span style={{ fontSize: 11.5, color: "var(--ink-3, #888)" }}>Match</span>
              {[["any", "Any (OR)"], ["all", "All (AND)"]].map(([k, lbl]) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => onMatchModeChange(k)}
                  style={{
                    fontSize: 11.5, fontWeight: 600, padding: "3px 9px", borderRadius: 999, cursor: "pointer",
                    border: matchMode === k ? "1px solid var(--brand-600)" : "1px solid var(--border)",
                    background: matchMode === k ? "var(--brand-50, rgba(99,102,241,0.10))" : "transparent",
                    color: matchMode === k ? "var(--brand-700, #4338ca)" : "var(--ink-2, #555)",
                  }}
                >
                  {lbl}
                </button>
              ))}
            </div>
          ) : null}

          <div style={{ maxHeight: 230, overflowY: "auto", padding: 6 }}>
            {canCreate ? (
              <button
                type="button"
                onClick={createTag}
                style={{
                  display: "flex", width: "100%", alignItems: "center", gap: 8, cursor: "pointer",
                  padding: "8px 10px", borderRadius: 8, border: "none", textAlign: "left",
                  background: "var(--brand-50, rgba(99,102,241,0.08))", color: "var(--brand-700, #4338ca)",
                  fontSize: 12.5, fontWeight: 600, marginBottom: 4,
                }}
              >
                <span style={{ fontSize: 15, lineHeight: 1 }}>+</span> Create tag “{typed}”
              </button>
            ) : null}

            {list.length === 0 && !canCreate ? (
              <div style={{ padding: "10px 12px", fontSize: 12.5, color: "var(--ink-3, #888)" }}>
                {options.length ? "No tag matches that." : "No tags yet - type a name to create the first one."}
              </div>
            ) : (
              list.map((t) => {
                const on = selected.includes(t);
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => toggle(t)}
                    style={{
                      display: "flex", width: "100%", alignItems: "center", gap: 9, cursor: "pointer",
                      padding: "8px 10px", borderRadius: 8, border: "none", textAlign: "left",
                      background: on ? "var(--brand-50, rgba(99,102,241,0.10))" : "transparent",
                      fontSize: 13, color: "var(--ink-1, #111)",
                    }}
                  >
                    <span
                      style={{
                        width: 15, height: 15, borderRadius: 4, flex: "0 0 auto",
                        border: on ? "1px solid var(--brand-600)" : "1px solid var(--border)",
                        background: on ? "var(--brand-600, #4f46e5)" : "#fff",
                        color: "#fff", fontSize: 10, lineHeight: "13px", textAlign: "center",
                      }}
                    >
                      {on ? "✓" : ""}
                    </span>
                    <span style={{ flex: 1, fontWeight: on ? 600 : 400 }}>{t}</span>
                    <span style={{ fontSize: 11, color: "var(--ink-3, #888)" }}>{counts[t]}</span>
                  </button>
                );
              })
            )}
          </div>

          {selected.length ? (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "7px 10px", borderTop: "1px solid var(--border)" }}>
              <span style={{ fontSize: 11.5, color: "var(--ink-3, #888)" }}>
                {selected.length} selected{matchMode === "all" && selected.length > 1 ? " · match all" : ""}
              </span>
              <button
                type="button"
                onClick={() => onChange([])}
                style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11.5, fontWeight: 600, color: "var(--brand-600, #4f46e5)" }}
              >
                Clear all
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {hint ? <div className="hint" style={{ marginTop: 6 }}>{hint}</div> : null}
    </div>
  );
}
