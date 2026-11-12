"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

function yes(v) {
  if (v === true) return true;
  if (v === false || v == null) return false;
  const s = String(v).toLowerCase();
  return s === "true" || s === "yes" || s === "valid" || s === "1";
}

function disp(v) {
  if (v === null || v === undefined || v === "") return "-";
  if (typeof v === "object") {
    try {
      return JSON.stringify(v);
    } catch {
      return String(v);
    }
  }
  return v;
}

function DomainBadge({ label, value }) {
  return (
    <span className={`badge ${yes(value) ? "b-success" : "b-warning"}`}>
      <span className="dot" />{label}: {yes(value) ? "valid" : "not valid"}
    </span>
  );
}

export default function SendCleanPanel() {
  const [snap, setSnap] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setBusy(true);
    setError("");
    api
      .sendcleanOverview()
      .then(setSnap)
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const account = snap?.account || {};
  const smtpUsers = Array.isArray(snap?.smtp_users) ? snap.smtp_users : [];
  const sendingDomains = Array.isArray(snap?.sending_domains) ? snap.sending_domains : [];
  const trackingDomains = Array.isArray(snap?.tracking_domains) ? snap.tracking_domains : [];
  const webhooks = Array.isArray(snap?.webhooks) ? snap.webhooks : [];

  return (
    <div className="card">
      <div className="card-h">
        <h3>SendClean - live</h3>
        <span className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span className={`badge ${snap?.live ? "b-success" : "b-warning"}`}>
            <span className="dot" />{snap?.live ? "LIVE" : "NOT LIVE"}
          </span>
          <button className="btn btn-secondary btn-sm" disabled={busy} onClick={load}>{busy ? "Refreshing…" : "Refresh"}</button>
        </span>
      </div>

      {error && (
        <div className="note n-warn">
          <span>⚠ SendClean overview unavailable: {error}</span>
        </div>
      )}

      {!error && !snap && <div className="loading"><span className="spinner" /> Loading SendClean…</div>}

      {snap && !snap.live && (
        <div className="note n-warn" style={{ marginTop: 0 }}>
          <span>{snap.error || "SendClean is not connected. Add the owner id and API token in Settings."}</span>
        </div>
      )}

      {snap && snap.live && (
        <>
          <div className="status-row" style={{ marginBottom: 14 }}>
            <div className="status-card" style={{ background: "var(--brand-50)", border: "1px solid var(--brand-100)" }}>
              <div style={{ fontSize: 12, color: "var(--brand-600)", fontWeight: 600 }}>Account</div>
              <div style={{ fontSize: 17, fontWeight: 800 }}>{account.name || "-"}</div>
              <div style={{ fontSize: 12, color: "var(--ink-3)" }}>{account.email || "-"}</div>
            </div>
            <div className="status-card" style={{ background: "var(--success-bg)", border: "1px solid var(--success-border)" }}>
              <div style={{ fontSize: 12, color: "#065F46", fontWeight: 600 }}>Reputation</div>
              <div style={{ fontSize: 17, fontWeight: 800, color: "var(--success)" }}>{disp(account.reputation)}</div>
              <div style={{ fontSize: 12, color: "var(--ink-3)" }}>allowed speed: {disp(account.allowed_speed)}</div>
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11.5, fontWeight: 750, textTransform: "uppercase", color: "var(--ink-4)", marginBottom: 6 }}>Sending domains</div>
            {sendingDomains.length === 0 ? (
              <span className="hint">No sending domains configured</span>
            ) : (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {sendingDomains.map((d, i) => (
                  <span key={d.domain || i} style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <span className="badge b-neutral"><span className="mono">{d.domain}</span></span>
                    <DomainBadge label="DKIM" value={d.dkim} />
                    <DomainBadge label="SPF" value={d.spf} />
                  </span>
                ))}
              </div>
            )}
          </div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11.5, fontWeight: 750, textTransform: "uppercase", color: "var(--ink-4)", marginBottom: 6 }}>Tracking domains</div>
            {trackingDomains.length === 0 ? (
              <span className="hint">No tracking domains configured</span>
            ) : (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {trackingDomains.map((d, i) => (
                  <span key={d.domain || i} className={`badge ${yes(d.valid_tracking ?? d.cname) ? "b-success" : "b-warning"}`}>
                    <span className="dot" /><span className="mono">{d.domain}</span> · CNAME {yes(d.valid_tracking ?? d.cname) ? "valid" : "not valid"}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11.5, fontWeight: 750, textTransform: "uppercase", color: "var(--ink-4)", marginBottom: 6 }}>SMTP users</div>
            {smtpUsers.length === 0 ? (
              <span className="hint">No SMTP users</span>
            ) : (
              <table className="t">
                <thead>
                  <tr><th>Username</th><th>Status</th><th>Limits</th></tr>
                </thead>
                <tbody>
                  {smtpUsers.map((u, i) => (
                    <tr key={u.smtp_user_name || i}>
                      <td className="mono">{u.smtp_user_name || "-"}</td>
                      <td><span className={`badge ${String(u.status).toLowerCase() === "active" ? "b-success" : "b-neutral"}`}><span className="dot" />{u.status || "-"}</span></td>
                      <td className="mono">{disp(u.limits ?? u.send_limit ?? u.daily_limit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div>
            <div style={{ fontSize: 11.5, fontWeight: 750, textTransform: "uppercase", color: "var(--ink-4)", marginBottom: 6 }}>Webhooks</div>
            {webhooks.length === 0 ? (
              <span className="hint">No webhooks registered</span>
            ) : (
              <table className="t">
                <thead>
                  <tr><th>URL</th><th>Events</th><th>Logging</th></tr>
                </thead>
                <tbody>
                  {webhooks.map((w, i) => (
                    <tr key={w.webhook_id || i}>
                      <td className="mono" style={{ wordBreak: "break-all" }}>{w.url || "-"}</td>
                      <td className="mono">{w.event || "-"}</td>
                      <td>{w.store_log ? "on" : "off"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
