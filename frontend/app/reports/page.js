"use client";

import { useEffect, useState } from "react";
import AppShell from "../../components/AppShell";
import ThresholdTable from "../../components/ThresholdTable";
import RefreshRatesButton from "../../components/RefreshRatesButton";
import { usePager, Pager } from "../../components/Pagination";
import { api } from "../../lib/api";

export default function ReportsPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [busy, setBusy] = useState(false);
  const [xUniqueId, setXUniqueId] = useState("");
  const [log, setLog] = useState(null);
  const [logBusy, setLogBusy] = useState(false);
  const [logError, setLogError] = useState("");
  const [sendTo, setSendTo] = useState("");
  const [sched, setSched] = useState({ mode: "daily", time: "10:00", date: "" });
  const [schedBusy, setSchedBusy] = useState(false);
  const stagePager = usePager(data?.stage_performance?.rows || [], 25);

  function load() {
    return api.reports()
      .then((d) => {
        setData(d);
        const s = d?.schedule || {};
        setSched({ mode: s.mode || "daily", time: s.time || "10:00", date: s.date || "" });
        setSendTo((d?.recipients || []).join(", "));
        return d;
      })
      .catch((e) => setError(e.message));
  }

  useEffect(() => { load(); }, []);

  function exportCsv() {
    if (!data) return;
    const m = data.metrics || {};
    const rows = [
      ["Sendwise Daily Report", data.date, data.scope],
      [],
      ["Metric", "Value", "Target", "Status"],
      // Targets and statuses come from the API's own thresholds, so the CSV cannot
      // disagree with the screen. Open rate deliberately carries no target: it is a
      // ceiling, not a measurement, and grading it would imply precision it lacks.
      ...(data.thresholds || []).map((t) => [t.label, t.value, t.limit, (t.status || "").toUpperCase()]),
      ["Open rate (upper bound)", `${m.open_rate}%`, "not graded", "INFO"],
      ["Proxy fetches excluded", m.proxy_only_openers ?? 0, "-", "-"],
      [],
      ["Email Campaign Name", "Stage/Sequence", "Date", "Open Rate", "Click Rate", "Target Open Rate", "Target Click Rate", "Achievement of Open rate", "Achievement of Click Rate"],
      ...((data.stage_performance?.rows) || []).map((r) => [
        r.campaign,
        `${r.stage_label} - ${r.focus}`,
        r.date || "",
        r.open_rate == null ? "" : `${r.open_rate}%`,
        r.click_rate == null ? "" : `${r.click_rate}%`,
        r.target_open, r.target_click,
        r.achievement_open == null ? "" : `${r.achievement_open}%`,
        r.achievement_click == null ? "" : `${r.achievement_click}%`,
      ]),
      [],
      ["Recipients", (data.recipients || []).join("; "), "-", "-"],
      ["Sheet", data.emailing?.sheet || "-", "-", "-"],
    ];
    const csv = rows.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sendwise-report-${data.date}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setFlash("Report CSV downloaded");
    setTimeout(() => setFlash(""), 3500);
  }

  async function sendNow() {
    setBusy(true);
    setError("");
    try {
      const to = String(sendTo || "").split(",").map((x) => x.trim()).filter(Boolean);
      const res = await api.sendReport(to);
      if (res.delivered) {
        setFlash(`✓ Email DELIVERED to ${res.to.length} recipient(s): ${res.to.join(", ")} - "${res.subject}"`);
      } else if (res.error) {
        setFlash(`Email failed: ${res.error} - check provider settings`);
      } else {
        setFlash(`Email composed for ${res.to.length} recipient(s) but NOT sent`);
      }
      setBusy(false);
      setTimeout(() => setFlash(""), 8000);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  async function saveSchedule() {
    setSchedBusy(true);
    setError("");
    try {
      const res = await api.setReportSchedule({
        mode: sched.mode,
        time: sched.time,
        date: sched.mode === "once" ? sched.date : "",
      });
      const s = res.schedule || {};
      setSched({ mode: s.mode || sched.mode, time: s.time || sched.time, date: s.date || "" });
      if (s.mode === "off") setFlash("Report emails are now OFF - nothing sends automatically");
      else setFlash(`Schedule saved - next send ${s.next_run || "unknown"} (${s.timezone || ""})`);
      setTimeout(() => setFlash(""), 8000);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setSchedBusy(false);
    }
  }

  async function lookupMessage() {
    const id = xUniqueId.trim();
    if (!id) return;
    setLogBusy(true);
    setLogError("");
    setLog(null);
    try {
      setLog(await api.sendcleanMessages(id));
    } catch (e) {
      setLogError(e.message);
    } finally {
      setLogBusy(false);
    }
  }

  return (
    <AppShell active="/reports">
      <div className="page-head">
        <div>
          <div className="crumb">Intelligence / <b>Reports</b></div>
          <h1>Reports</h1>
        </div>
        <div className="top-actions">
          <RefreshRatesButton updatedAt={data?.rates_updated_at} onDone={load} />
          <button className="btn btn-secondary" onClick={exportCsv} disabled={!data}>Export CSV</button>
          <button className="btn btn-primary" onClick={sendNow} disabled={busy}>{busy ? "Sending…" : "Send Report Now"}</button>
        </div>
      </div>

      {error && <div className="note n-warn">⚠ {error}</div>}
      {flash && <div className="note n-success">✓ {flash}</div>}
      {!data && !error && <div className="loading"><span className="spinner" /> Loading report…</div>}

      {data && (
        <>
          <div className="grid kpis">
            {/* Status comes from the API's thresholds, measured against the configured
                targets. These cards used to hardcode "pass" for everything, so they
                showed a green tick no matter what the numbers said. */}
            {(data.thresholds || []).map((t, i) => {
              const cls = t.status === "pass" ? "ok" : t.status === "fail" ? "bad" : "warn";
              const flag = t.status === "pass" ? "f-pass" : t.status === "fail" ? "f-block" : "f-warn";
              return (
                <div className={`kpi ${cls}`} key={t.label || i}>
                  <div className="k-label">{t.label}</div>
                  <div className="k-value">{t.value}</div>
                  <div className="k-sub">Target {t.limit}</div>
                  <span className={`k-flag ${flag}`}>{(t.status || "?").toUpperCase()}</span>
                </div>
              );
            })}
            {/* Open rate sits apart from the graded cards on purpose. It is a ceiling:
                provider proxies are excluded, but an unfilterable fetch still counts. */}
            <div className="kpi info">
              <div className="k-label">Open rate</div>
              <div className="k-value">{data.metrics.open_rate}%</div>
              <div className="k-sub">
                Upper bound - not graded
                {data.metrics.proxy_only_openers > 0 &&
                  ` · ${data.metrics.proxy_only_openers} proxy fetch(es) excluded`}
              </div>
              <span className="k-flag f-info">CEILING</span>
            </div>
          </div>

          {data.metrics.open_rate_note && (
            <div className="note n-info" style={{ marginTop: 12 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
              <span>{data.metrics.open_rate_note}</span>
            </div>
          )}

          {data.stage_performance && (
            <div className="card mt">
              <div className="card-h">
                <h3>Stage-wise performance</h3>
                <span className="hint">
                  {data.stage_performance.rows.length} row(s) · target from the stage setup
                </span>
              </div>
              {data.stage_performance.rows.length === 0 ? (
                <div className="hint">No stage data yet - it fills in as sends are tracked.</div>
              ) : (
                <>
                  <div className="table-wrap">
                    <table className="t">
                      <thead>
                        <tr>
                          <th>Email Campaign Name</th>
                          <th>Stage / Sequence</th>
                          <th>Date</th>
                          <th className="num-cell">Open Rate</th>
                          <th className="num-cell">Click Rate</th>
                          <th className="num-cell">Target Open Rate</th>
                          <th className="num-cell">Target Click Rate</th>
                          <th className="num-cell">Achievement of Open rate</th>
                          <th className="num-cell">Achievement of Click Rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {stagePager.rows.map((r, i) => {
                          const ach = (v) => {
                            // 100% is the ceiling - meeting or beating the target is a full
                            // pass, so it never renders above 100%.
                            const capped = v === null || v === undefined ? null : Math.min(100, v);
                            return capped === null
                              ? <span style={{ color: "var(--ink-4)" }}>-</span>
                              : (
                                <b style={{
                                  color: capped >= 100 ? "var(--success)" : capped >= 80 ? "var(--warning)" : "var(--danger)",
                                }}>{capped}%</b>
                              );
                          };
                          return (
                            <tr key={`${r.slug}-${r.ref}-${i}`}>
                              <td>
                                <div className="cell-title">
                                  <b>{r.campaign}</b>
                                  <span className="mono">{r.ref}</span>
                                </div>
                              </td>
                              <td>
                                <div style={{ fontSize: 12.5 }}>{r.stage_label}</div>
                                <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{r.focus}</div>
                              </td>
                              <td className="mono" style={{ fontSize: 12 }}>{r.date || "-"}</td>
                              <td className="mono num-cell">{r.open_rate === null ? "-" : `${r.open_rate}%`}</td>
                              <td className="mono num-cell">{r.click_rate === null ? "-" : `${r.click_rate}%`}</td>
                              <td className="mono num-cell" style={{ color: "var(--ink-3)" }}>{r.target_open}</td>
                              <td className="mono num-cell" style={{ color: "var(--ink-3)" }}>{r.target_click}</td>
                              <td className="num-cell">{ach(r.achievement_open)}</td>
                              <td className="num-cell">{ach(r.achievement_click)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <Pager pager={stagePager} label="rows" />
                </>
              )}
            </div>
          )}

          <div className="card mt">
            <div className="card-h">
              <h3>Report delivery</h3>
              <span className="hint">send on demand, or schedule it</span>
            </div>
            {(() => {
              const s = data.schedule || {};
              const on = s.mode === "daily" || s.mode === "once";
              const label =
                s.mode === "daily" ? `ON - every day at ${s.time}`
                : s.mode === "once" ? `ONE TIME - ${s.date} at ${s.time}`
                : "OFF - no automatic send";
              return (
                <div className={`note ${on ? "n-success" : "n-warn"}`} style={{ marginBottom: 14 }}>
                  <b>Automatic report email: {label}</b>
                  {s.next_run ? <span> · next send {s.next_run} ({s.timezone})</span> : null}
                </div>
              );
            })()}
            <div className="grid two-col" style={{ gap: 18 }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 8 }}>Send now</div>
                <div className="field">
                  <label>Recipients (comma separated)</label>
                  <input
                    className="input"
                    value={sendTo}
                    onChange={(e) => setSendTo(e.target.value)}
                    placeholder="boss@ibos.io, cmo@ibos.io"
                  />
                </div>
                <button className="btn btn-primary" style={{ marginTop: 10 }} onClick={sendNow} disabled={busy}>
                  {busy ? "Sending…" : "Send report now"}
                </button>
                <div className="hint" style={{ marginTop: 8 }}>
                  Sends today's report this instant to the addresses above. Leave it as is to use the configured list.
                </div>
              </div>

              <div>
                <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 8 }}>Schedule</div>
                <div className="field">
                  <label>When</label>
                  <select className="input" value={sched.mode} onChange={(e) => setSched({ ...sched, mode: e.target.value })}>
                    <option value="daily">Every day</option>
                    <option value="once">One time (specific date)</option>
                    <option value="off">Off (no automatic send)</option>
                  </select>
                </div>
                <div className="inline" style={{ gap: 10, marginTop: 10 }}>
                  <div className="field" style={{ flex: 1 }}>
                    <label>Time ({data.schedule?.timezone || "Asia/Dhaka"})</label>
                    <input className="input mono" type="time" value={sched.time} onChange={(e) => setSched({ ...sched, time: e.target.value })} />
                  </div>
                  {sched.mode === "once" && (
                    <div className="field" style={{ flex: 1 }}>
                      <label>Date</label>
                      <input className="input mono" type="date" value={sched.date} onChange={(e) => setSched({ ...sched, date: e.target.value })} />
                    </div>
                  )}
                </div>
                <button className="btn btn-primary" style={{ marginTop: 10 }} onClick={saveSchedule} disabled={schedBusy}>
                  {schedBusy ? "Saving…" : "Save schedule"}
                </button>
                <div className="hint" style={{ marginTop: 8 }}>
                  {data.schedule?.mode === "off" ? (
                    <>Automatic sending is <b>OFF</b> - only the manual button sends.</>
                  ) : (
                    <>Next send: <b>{data.schedule?.next_run || "not scheduled"}</b> ({data.schedule?.timezone || "Asia/Dhaka"})</>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="grid two-col">
            <div className="card">
              <div className="card-h"><h3>Daily report snapshot</h3><span className="hint">{data.date} · {data.scope}</span></div>
              <div className="kv">
                <dt>Recipients</dt><dd>{(data.recipients || []).join(", ")}</dd>
                <dt>Daily email</dt><dd>{data.schedule.daily_email} ({data.schedule.timezone}) → the recipients above</dd>
                <dt>Reporting sheet</dt><dd>{data.sheet_settings?.sheet_name || "Campaign Report"} - linked in the email</dd>
              </div>
              <div className="note n-info" style={{ marginTop: 14 }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
                <span>
                  The email goes out automatically once a day at <b>{data.schedule.daily_email} {data.schedule.timezone}</b>.
                  Recipients, subject, body and the time are editable in <b>Settings → Reporting &amp; emailing</b>.
                </span>
              </div>
            </div>
            <div className="card">
              <div className="card-h"><h3>Email preview</h3><span className="hint">subject + body from Settings → Reporting</span></div>
              <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", overflow: "hidden" }}>
                <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)", background: "var(--surface-2)" }}>
                  <div style={{ fontWeight: 700, fontSize: 13.5 }}>{data.emailing?.preview?.subject || "Sendwise Daily Report - 2026-09-01"}</div>
                  <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>To: {(data.recipients || []).join(", ") || "-"} {data.emailing?.smtp_configured ? "· SMTP ready ✓" : "· SMTP not configured"}</div>
                </div>
                <div style={{ padding: "14px 16px", fontSize: 13.5, lineHeight: 1.65, whiteSpace: "pre-wrap", color: "var(--ink-2)" }}>
                  {data.emailing?.preview?.body || "…"}
                </div>
              </div>
              <div className="note n-info" style={{ marginTop: 14 }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
                <span>Sheet: <span className="mono">{data.emailing?.sheet}</span> · updates {data.sheet_settings?.update_frequency} at {data.sheet_settings?.update_time}. Edit all of this in <b>Settings → Reporting & emailing</b>.</span>
              </div>
            </div>
          </div>
          <div className="grid two-col mt">
            <div className="card">
              <div className="card-h"><h3>Emergency thresholds</h3><span className="hint">auto-pause on breach</span></div>
              <ThresholdTable thresholds={data.thresholds} />
            </div>
            <div className="card">
              <div className="card-h"><h3>Message log</h3><span className="hint">lookup by X-Unique-Id</span></div>
              <div className="inline">
                <div className="field" style={{ flex: 1, minWidth: 200 }}>
                  <label>X-Unique-Id</label>
                  <input className="input mono" value={xUniqueId} onChange={(e) => setXUniqueId(e.target.value)} placeholder="your-unique-id" onKeyDown={(e) => { if (e.key === "Enter") lookupMessage(); }} />
                </div>
                <button className="btn btn-primary btn-sm" disabled={logBusy || !xUniqueId.trim()} onClick={lookupMessage}>{logBusy ? "Looking up…" : "Look up"}</button>
              </div>

              {logError && <div className="note n-warn" style={{ marginTop: 12 }}><span>⚠ {logError}</span></div>}

              {log && (
                <div style={{ marginTop: 12 }}>
                  {(log.messages || []).length === 0 ? (
                    <div className="hint">No messages found for <span className="mono">{log.x_unique_id}</span></div>
                  ) : (
                    <table className="t">
                      <thead>
                        <tr><th>Subject</th><th>Recipient</th><th>Status</th><th>Time</th><th className="num-cell">Open</th><th className="num-cell">Click</th></tr>
                      </thead>
                      <tbody>
                        {(log.messages || []).map((m, i) => (
                          <tr key={i}>
                            <td><div className="cell-title"><b>{m.subject || "-"}</b><span className="mono">{m.sender || ""}</span></div></td>
                            <td className="mono">{m.email || m.sendFrom || "-"}</td>
                            <td>{m.status || "-"}</td>
                            <td className="mono">{m.time || "-"}</td>
                            <td className="mono num-cell">{m.open ?? "-"}</td>
                            <td className="mono num-cell">{m.click ?? "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}

              {!log && !logError && (
                <div className="note n-info" style={{ marginTop: 12 }}>
                  <span>SendClean exposes per-message status by X-Unique-Id - there is no aggregate report API.</span>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </AppShell>
  );
}
