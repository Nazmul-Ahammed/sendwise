"use client";

import { useEffect, useMemo, useRef, useState } from "react";

/**
 * Click map - the sent email rendered as it was sent, with a click count drawn on
 * every link, the way Mailchimp's Click Performance report does it.
 *
 * It is a *link-level* map, not a pixel heatmap: an email cannot run JavaScript, so no
 * tool (Mailchimp included) can see the exact tap coordinate. A click only ever lands on
 * a link, so the honest, reliable picture is "which link, how many times" - drawn back
 * onto the link's own position in the message.
 *
 * The message renders in an iframe so its own <style> cannot leak into the app.
 * `sandbox="allow-same-origin"` and NOT `allow-scripts` is deliberate: scripts inside a
 * message must never run (XSS), but the parent still has to read the link positions.
 */

// The compliance footer is hidden in the in-app preview too (see the Emails tab).
const hideCompliance = (html = "") =>
  html.replace("</head>", "<style>.sw-compliance{display:none!important}</style></head>");

const fmt = (n) => (n ?? 0).toLocaleString();

export default function ClickMap({ emails = [], selectedRef, onSelect, recipients = 0 }) {
  const list = Array.isArray(emails) ? emails : [];
  const email = list.find((e) => e.ref === selectedRef) || list[0] || null;

  const iframeRef = useRef(null);
  const [marks, setMarks] = useState([]);
  // Mailchimp shows a map and a table, and lets you switch the metric.
  const [view, setView] = useState("map"); // map | table
  const [metric, setMetric] = useState("total"); // total | unique

  // The number drawn on a link depends on the chosen metric.
  const valueOf = (l) => (metric === "unique" ? l.unique_clickers || 0 : l.clicks || 0);

  const links = useMemo(() => {
    const rows = [...(email?.links || [])].filter((l) => l.clickable !== false);
    rows.sort((a, b) => valueOf(b) - valueOf(a) || (a.url < b.url ? -1 : 1));
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email, metric]);

  const clicked = links.filter((l) => valueOf(l) > 0);
  const max = clicked.reduce((m, l) => Math.max(m, valueOf(l)), 0);
  const totalClicks = email?.clicks || 0;
  const uniqueClicks = email?.unique_clickers || 0;
  const clickRate = recipients > 0 ? ((uniqueClicks / recipients) * 100).toFixed(2) : null;

  // Look each clicked link up by its href, tolerating `&amp;` vs `&`.
  const byUrl = useMemo(() => {
    const m = {};
    for (const l of clicked) {
      if (!l?.url) continue;
      m[l.url] = l;
      m[l.url.replace(/&/g, "&amp;")] = l;
      m[l.url.replace(/&amp;/g, "&")] = l;
    }
    return m;
  }, [clicked]);

  useEffect(() => {
    setMarks([]);
    if (view !== "map") return undefined;
    const iframe = iframeRef.current;
    if (!iframe || !email) return undefined;

    let cancelled = false;
    let observer = null;
    let started = false;

    function layout() {
      if (cancelled) return;
      const doc = iframe.contentDocument;
      const body = doc && doc.body;
      if (!body) return;

      // Expand to the full content height so link positions below the fold are real.
      const height = Math.max(body.scrollHeight, doc.documentElement?.scrollHeight || 0);
      if (height) iframe.style.height = `${height}px`;

      const frame = iframe.getBoundingClientRect();
      const next = [];
      doc.querySelectorAll("a[href]").forEach((a) => {
        const href = (a.getAttribute("href") || "").trim();
        const link = byUrl[href] || byUrl[href.replace(/&amp;/g, "&")];
        if (!link) return;
        const r = a.getBoundingClientRect();
        next.push({
          id: `${href}-${next.length}`,
          url: link.url,
          clicks: link.clicks || 0,
          unique: link.unique_clickers || 0,
          top: r.top - frame.top,
          left: r.left - frame.left,
          width: r.width,
          height: r.height,
        });
      });
      setMarks(next);
    }

    function onLoad() {
      if (started) return;
      started = true;
      layout();
      try {
        observer = new ResizeObserver(layout);
        observer.observe(iframe.contentDocument.documentElement);
      } catch {
        /* ResizeObserver is optional */
      }
      setTimeout(layout, 250);
      setTimeout(layout, 1000);
    }

    iframe.addEventListener("load", onLoad);
    if (iframe.contentDocument?.readyState === "complete") onLoad();
    window.addEventListener("resize", layout);
    return () => {
      cancelled = true;
      iframe.removeEventListener("load", onLoad);
      window.removeEventListener("resize", layout);
      if (observer) {
        try {
          observer.disconnect();
        } catch {
          /* ignore */
        }
      }
    };
  }, [email, byUrl, view]);

  if (!email) {
    return (
      <div className="empty">
        <b>No emails to map</b>
        <p>This send has no staged email copy yet.</p>
      </div>
    );
  }

  // Heat colour: stronger with the click share, on a calm red ramp.
  const heat = (v) => {
    const t = max > 0 ? Math.min(1, v / max) : 0;
    return {
      overlay: `rgba(239, 68, 68, ${(0.10 + 0.55 * t).toFixed(2)})`,
      badge: `rgba(185, 28, 28, ${(0.55 + 0.45 * t).toFixed(2)})`,
      border: `rgba(185, 28, 28, ${(0.35 + 0.55 * t).toFixed(2)})`,
    };
  };

  const Btn = ({ on, children, onClick }) => (
    <button
      type="button"
      onClick={onClick}
      className={`btn btn-sm ${on ? "btn-primary" : "btn-secondary"}`}
    >
      {children}
    </button>
  );

  return (
    <div className="stack">
      {/* Email picker (a sequence has one email per stage; a Mailchimp campaign has one) */}
      <div className="inline" style={{ flexWrap: "wrap", gap: 6 }}>
        {list.map((e) => (
          <button
            key={e.ref}
            type="button"
            onClick={() => onSelect?.(e.ref)}
            className={`btn btn-sm ${e.ref === email.ref ? "btn-primary" : "btn-secondary"}`}
          >
            {e.ref} · {e.clicks} click{e.clicks === 1 ? "" : "s"}
          </button>
        ))}
      </div>

      {/* Mailchimp-style summary */}
      <div className="status-row" style={{ marginBottom: 4 }}>
        <div className="status-card">
          <div style={{ fontSize: 12, fontWeight: 600 }}>Total clicks</div>
          <div style={{ fontSize: 24, fontWeight: 800 }}>{fmt(totalClicks)}</div>
        </div>
        <div className="status-card">
          <div style={{ fontSize: 12, fontWeight: 600 }}>Unique clicks</div>
          <div style={{ fontSize: 24, fontWeight: 800 }}>{fmt(uniqueClicks)}</div>
        </div>
        {clickRate !== null && (
          <div className="status-card">
            <div style={{ fontSize: 12, fontWeight: 600 }}>Click rate</div>
            <div style={{ fontSize: 24, fontWeight: 800 }}>{clickRate}%</div>
          </div>
        )}
        <div className="status-card">
          <div style={{ fontSize: 12, fontWeight: 600 }}>Links clicked</div>
          <div style={{ fontSize: 24, fontWeight: 800 }}>
            {clicked.length}
            <span style={{ fontSize: 13, color: "var(--ink-3)", fontWeight: 600 }}> / {links.length}</span>
          </div>
        </div>
      </div>

      {/* Controls: Map / Table, Total / Unique */}
      <div className="inline between" style={{ flexWrap: "wrap", gap: 8 }}>
        <div className="inline" style={{ gap: 6 }}>
          <Btn on={view === "map"} onClick={() => setView("map")}>Click map</Btn>
          <Btn on={view === "table"} onClick={() => setView("table")}>Table</Btn>
        </div>
        <div className="inline" style={{ gap: 6 }}>
          <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>Show</span>
          <Btn on={metric === "total"} onClick={() => setMetric("total")}>Total clicks</Btn>
          <Btn on={metric === "unique"} onClick={() => setMetric("unique")}>Unique clicks</Btn>
        </div>
      </div>

      {view === "map" && (
        <>
          {totalClicks === 0 && (
            <div className="note n-info" style={{ marginTop: 0 }}>
              <span>
                No clicks recorded for this send yet. The map fills in as recipients click -
                Mailchimp's map works the same way, from the links it rewrote.
              </span>
            </div>
          )}
          <div
            style={{
              position: "relative",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              overflow: "hidden",
              background: "#F6F4FB",
            }}
          >
            <iframe
              ref={iframeRef}
              title={`Click map ${email.ref}`}
              srcDoc={hideCompliance(email.html)}
              sandbox="allow-same-origin"
              style={{ width: "100%", border: 0, display: "block", background: "#F6F4FB" }}
            />
            <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
              {marks.map((m) => {
                const v = metric === "unique" ? m.unique : m.clicks;
                if (v <= 0) return null;
                const c = heat(v);
                return (
                  <div key={m.id}>
                    {/* heat region over the link */}
                    <div
                      style={{
                        position: "absolute",
                        top: m.top,
                        left: m.left,
                        width: m.width,
                        height: m.height,
                        background: c.overlay,
                        // The region is a hint, the badge is the fact - so it stays clickable-through.
                        borderRadius: 3,
                      }}
                    />
                    {/* the count badge, centred on the link */}
                    <div
                      title={`${m.clicks} click${m.clicks === 1 ? "" : "s"} · ${m.unique} unique · ${m.url}`}
                      style={{
                        position: "absolute",
                        top: Math.max(0, m.top + m.height / 2 - 13),
                        left: Math.max(0, m.left + m.width / 2 - 13),
                        width: 26,
                        height: 26,
                        minWidth: 26,
                        padding: "0 4px",
                        borderRadius: 999,
                        background: c.badge,
                        border: `2px solid #fff`,
                        boxShadow: `0 1px 5px rgba(15,23,42,0.35), 0 0 0 1px ${c.border}`,
                        color: "#fff",
                        fontSize: 11.5,
                        fontWeight: 800,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        pointerEvents: "auto",
                        cursor: "default",
                      }}
                    >
                      {v}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {clicked.length > 0 && (
            <div className="inline" style={{ gap: 10, fontSize: 11.5, color: "var(--ink-3)" }}>
              <span>
                {metric === "unique" ? "Unique clicks" : "Total clicks"} per link · hover a badge for the URL
              </span>
              <span className="inline" style={{ gap: 4 }}>
                fewer
                <span style={{ width: 46, height: 8, borderRadius: 999, background: "linear-gradient(90deg, rgba(239,68,68,0.18), rgba(185,28,28,0.95))" }} />
                more
              </span>
            </div>
          )}
        </>
      )}

      {view === "table" && (
        <div className="card" style={{ margin: 0 }}>
          <div className="card-h">
            <h3>Clicks by link - {email.ref}</h3>
            <span className="hint">
              {metric === "unique" ? "unique clicks" : "total clicks"} · {clicked.length} of {links.length} links clicked
            </span>
          </div>
          {links.length === 0 ? (
            <div className="hint">This email has no trackable links.</div>
          ) : (
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th style={{ width: 40 }} className="num-cell">#</th>
                    <th>Link</th>
                    <th className="num-cell" style={{ width: 90 }}>Total</th>
                    <th className="num-cell" style={{ width: 90 }}>Unique</th>
                    <th className="num-cell" style={{ width: 80 }}>Share</th>
                  </tr>
                </thead>
                <tbody>
                  {links.map((l, i) => {
                    const v = valueOf(l);
                    const share = totalClicks > 0 ? ((l.clicks / totalClicks) * 100).toFixed(1) : "0.0";
                    return (
                      <tr key={`${l.url}-${i}`} style={v > 0 ? undefined : { opacity: 0.55 }}>
                        <td className="num-cell mono" style={{ color: "var(--ink-3)" }}>{i + 1}</td>
                        <td>
                          <div style={{ fontSize: 12.5 }}>
                            {l.text || <span style={{ color: "var(--ink-3)" }}>(no label)</span>}
                          </div>
                          <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", wordBreak: "break-all" }}>
                            {l.url}
                          </div>
                          {l.clickable === false && (
                            <span className="badge b-neutral" style={{ marginTop: 3 }}>not tracked</span>
                          )}
                          {l.orphan && (
                            <span className="badge b-neutral" style={{ marginTop: 3 }}>sent earlier · copy changed</span>
                          )}
                        </td>
                        <td className="num-cell mono"><b>{fmt(l.clicks)}</b></td>
                        <td className="num-cell mono">{fmt(l.unique_clickers)}</td>
                        <td className="num-cell mono" style={{ color: "var(--ink-3)" }}>{share}%</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
