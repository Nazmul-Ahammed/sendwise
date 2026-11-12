"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import AppShell from "../../components/AppShell";
import { usePager, Pager } from "../../components/Pagination";
import { api } from "../../lib/api";

// The provider pushes opens and clicks to our webhook the moment they happen, so the
// data behind this table is already live. The screen re-reads it on a short timer so a
// new event appears on its own, without anyone pressing refresh.
const POLL_MS = 5000;

export default function EngagementPage() {
  const [campaigns, setCampaigns] = useState([]);
  const [slug, setSlug] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [live, setLive] = useState(true);
  const [changed, setChanged] = useState({});
  const [pulse, setPulse] = useState(0);
  // Which recipient's timeline is open. Every fetch is stored separately, so opening
  // twice is two rows here - a running total on its own cannot show that.
  const [openRow, setOpenRow] = useState("");

  // What we last showed, so a cell that just moved can be highlighted rather than
  // silently changing under the reader.
  const seen = useRef({});

  useEffect(() => {
    api
      .campaigns()
      .then((r) => {
        // /api/campaigns returns a bare array, not an object with a `campaigns` key.
        const list = (Array.isArray(r) ? r : r.campaigns || []).filter((c) => !c.deleted_at);
        setCampaigns(list);
        // Default to the most recently launched send - that is the one being watched.
        const recent = [...list].sort((a, b) =>
          String(b.launched_at || "").localeCompare(String(a.launched_at || ""))
        )[0];
        setSlug(recent ? recent.slug : list[0]?.slug || "");
      })
      .catch((e) => setError(e.message));
  }, []);

  const load = useCallback(
    (target) => {
      const s = target || slug;
      if (!s) return;
      return api
        .campaignEngagement(s)
        .then((d) => {
          const now = {};
          const moved = {};
          (d.rows || []).forEach((row) => {
            now[row.email] = { opens: row.opens, clicks: row.clicks };
            const before = seen.current[row.email];
            if (before && (before.opens !== row.opens || before.clicks !== row.clicks)) {
              moved[row.email] = true;
            }
          });
          seen.current = now;
          if (Object.keys(moved).length) {
            setChanged(moved);
            setPulse((n) => n + 1);
            setTimeout(() => setChanged({}), 2500);
          }
          setData(d);
          setError("");
        })
        .catch((e) => setError(e.message));
    },
    [slug]
  );

  useEffect(() => {
    if (!slug) return;
    load(slug);
  }, [slug, load]);

  // Poll only while the tab is visible: a hidden tab does not need to keep asking.
  useEffect(() => {
    if (!slug || !live) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") load(slug);
    }, POLL_MS);
    return () => clearInterval(id);
  }, [slug, live, load]);

  async function syncHistory() {
    if (!slug) return;
    setSyncing(true);
    setError("");
    try {
      const r = await api.syncCampaignEngagement(slug);
      setFlash(
        `Imported ${r.imported} stored event(s)` +
          (r.skipped ? `, ${r.skipped} already held` : "") +
          (r.reason ? ` - ${r.reason}` : "")
      );
      await load(slug);
    } catch (e) {
      setError(e.message);
    } finally {
      setSyncing(false);
      setTimeout(() => setFlash(""), 6000);
    }
  }

  const refs = data?.refs || [];
  const rows = data?.rows || [];
  const rowsPager = usePager(rows, 25);
  const totals = data?.totals || {};

  function cellFor(row, ref) {
    return (row.cells || {})[ref] || { opens: 0, clicks: 0, provider_fetches: 0 };
  }

  return (
    <AppShell active="engagement">
      <div className="page-head">
        <div>
          <h1>Engagement - who opened, who clicked</h1>
          <p className="sub">
            Updates on its own every {POLL_MS / 1000}s. Opens and clicks are pushed here by
            the provider the moment they happen, so a new one shows up without a refresh.
          </p>
        </div>
        <div className="inline">
          <button
            className={`btn ${live ? "btn-primary" : ""}`}
            onClick={() => setLive((v) => !v)}
            title="Pause or resume the automatic refresh"
          >
            {live ? "● Live" : "Paused"}
          </button>
          <button className="btn" onClick={syncHistory} disabled={syncing || !slug}>
            {syncing ? "Importing…" : "Import stored history"}
          </button>
        </div>
      </div>

      {error && <div className="note n-warn">{error}</div>}
      {flash && <div className="note n-success">{flash}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-h">
          <h3>Which send</h3>
          <span className="hint">one campaign or sequence at a time</span>
        </div>
        <select
          value={slug}
          onChange={(e) => {
            seen.current = {};
            setData(null);
            setSlug(e.target.value);
          }}
          className="input"
        >
          {campaigns.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.title || c.slug} - {c.status}
              {c.sequence_slug ? " (sequence)" : ""}
            </option>
          ))}
        </select>
      </div>

      {data && (
        <>
          {/* The Mailchimp report, same names and same arithmetic, so the numbers read
              the same way to anyone who has seen a Mailchimp campaign report. */}
          {data.mailchimp && (
            <div className="card" style={{ marginBottom: 16 }}>
              <div className="card-h">
                <h3>Campaign report</h3>
                <span className="hint">
                  same figures Mailchimp shows · rates based on {data.mailchimp.rate_basis}
                </span>
              </div>

              <div className="stat-row">
                {[
                  ["Emails Sent", data.mailchimp.emails_sent],
                  ["Recipients", data.mailchimp.recipients],
                  ["Opens", data.mailchimp.opens],
                  ["Unique Opens", data.mailchimp.unique_opens],
                  ["Open Rate", `${data.mailchimp.open_rate}%`],
                  ["Clicks", data.mailchimp.clicks],
                  ["Unique Clicks", data.mailchimp.unique_clicks],
                  ["Click Rate", `${data.mailchimp.click_rate}%`],
                  ["Unsubscribed", data.mailchimp.unsubscribed],
                  ["Bounced", data.mailchimp.bounced],
                ].map(([label, value]) => (
                  <div className="stat" key={label}>
                    <span>{label}</span>
                    <b>{value}</b>
                  </div>
                ))}
              </div>

              <div style={{ marginTop: 16 }}>
                <b style={{ fontSize: 13 }}>Top Links Clicked</b>
                {(data.mailchimp.top_links || []).length === 0 ? (
                  <div className="note n-info" style={{ marginTop: 8 }}>
                    No link clicks recorded yet. Mailchimp resolves link URLs from its own
                    click redirect; ours stores the destination with each click, so this
                    fills in as soon as someone clicks.
                  </div>
                ) : (
                  <table className="t" style={{ marginTop: 8, fontSize: 12.5 }}>
                    <thead>
                      <tr><th>Link</th><th className="num-cell" style={{ width: 90 }}>Clicks</th></tr>
                    </thead>
                    <tbody>
                      {data.mailchimp.top_links.map((l) => (
                        <tr key={l.url}>
                          <td className="mono" style={{ wordBreak: "break-all" }}>{l.url}</td>
                          <td className="mono num-cell"><b>{l.clicks}</b></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          <div className="grid kpis">
            <div className="kpi info">
              <div className="k-label">Recipients</div>
              <div className="k-value">{totals.recipients ?? 0}</div>
              <div className="k-sub">people this send reached</div>
            </div>
            <div className="kpi ok">
              <div className="k-label">Opens</div>
              <div className="k-value">{totals.provider_fetches ?? 0}</div>
              <div className="k-sub">every image load - how Mailchimp counts</div>
            </div>
            <div className="kpi ok">
              <div className="k-label">Confirmed opens</div>
              <div className="k-value">{totals.opens ?? 0}</div>
              <div className="k-sub">from a real device, not a proxy</div>
            </div>
            <div className="kpi ok">
              <div className="k-label">Clicks</div>
              <div className="k-value">{totals.clicks ?? 0}</div>
              <div className="k-sub">the dependable signal</div>
            </div>
          </div>

          {/* One row per email. "Which email worked" is a different question from
              "who engaged", and only shows when the counts are gathered by email. */}
          {(data.per_email || []).length > 0 && (
            <div className="card mt">
              <div className="card-h">
                <h3>Per email - which one was opened</h3>
                <span className="hint">
                  counts for each email in the journey, across everyone it went to
                </span>
              </div>
              <div className="table-wrap">
                <table className="t">
                  <thead>
                    <tr>
                      <th style={{ width: 60 }}>Email</th>
                      <th>Subject</th>
                      <th className="num-cell" style={{ width: 80 }}>Sent to</th>
                      <th className="num-cell" style={{ width: 80 }} title="Every image load for this email">
                        Opens
                      </th>
                      <th className="num-cell" style={{ width: 110 }} title="Distinct people who opened it">
                        Unique opens
                      </th>
                      <th className="num-cell" style={{ width: 90 }}>Open rate</th>
                      <th className="num-cell" style={{ width: 80 }}>Clicks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.per_email || []).map((e) => (
                      <tr key={e.ref}>
                        <td className="mono"><b>{e.ref}</b></td>
                        <td>
                          <div style={{ fontSize: 12.5 }}>{e.name || "-"}</div>
                          {e.subject && (
                            <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                              {e.subject}
                            </div>
                          )}
                        </td>
                        <td className="mono num-cell">{e.recipients}</td>
                        <td className="mono num-cell">
                          {e.opens > 0 ? (
                            <b>{e.opens}</b>
                          ) : (
                            <span style={{ color: "var(--ink-4)" }}>0</span>
                          )}
                        </td>
                        <td className="mono num-cell">{e.unique_opens}</td>
                        <td className="mono num-cell">{e.open_rate}%</td>
                        <td className="mono num-cell">
                          {e.clicks > 0 ? (
                            <b style={{ color: "var(--success)" }}>{e.clicks}</b>
                          ) : (
                            <span style={{ color: "var(--ink-4)" }}>0</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="card mt">
            <div className="card-h">
              <h3>Per recipient, per email</h3>
              <span className="hint">
                {data.realtime?.events_stored || 0} event(s) held
                {data.realtime?.last_event_at
                  ? ` · last ${data.realtime.last_event_at_local || String(data.realtime.last_event_at).replace("T", " ").slice(0, 19)}`
                  : ""}
              </span>
            </div>

            {rows.length === 0 ? (
              <div className="note n-info" style={{ marginTop: 10 }}>
                Nothing tracked for this send yet. Opens and clicks are measured by this
                app's own pixel, so only sends made after local tracking started carry it -
                press <b>Import stored history</b> to pull anything the provider still holds.
              </div>
            ) : (
              <>
              <div className="table-wrap">
                <table className="t">
                  <thead>
                    <tr>
                      <th>Recipient</th>
                      {refs.length === 0 && <th>Sent</th>}
                      {refs.map((r) => (
                        <th key={r} className="num-cell" title="opens / clicks for this email">
                          {r}
                        </th>
                      ))}
                      <th className="num-cell" title="Every time the tracking image was loaded, counted the way Mailchimp counts an open. Includes a mail provider preloading the image, so a mailbox can show 1 here even if nobody read it.">
                        Opens
                      </th>
                      <th title="Loads that came from a real device rather than a mail provider's proxy. Always equal to or lower than Opens, and the honest floor rather than the ceiling." className="num-cell">
                        Confirmed
                      </th>
                      <th className="num-cell">Clicks</th>
                      <th title={`Times are shown in ${data.realtime?.timezone || "the account timezone"}`}>
                        Last activity{data.realtime?.timezone ? ` (${data.realtime.timezone})` : ""}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                      {rowsPager.rows.map((row) => (
                      <Fragment key={row.email}>
                        <tr
                          className={`${changed[row.email] ? "row-live" : ""} row-click`}
                          data-pulse={changed[row.email] ? pulse : undefined}
                          onClick={() => setOpenRow(openRow === row.email ? "" : row.email)}
                          title="Click to see every view and click, with its time"
                        >
                        <td className="mono">
                          <span style={{ color: "var(--ink-4)", marginRight: 6 }}>
                            {openRow === row.email ? "▾" : "▸"}
                          </span>
                          {row.email}
                        </td>
                        {refs.length === 0 && (
                          <td className="mono">
                            {cellFor(row, "").opens || cellFor(row, "").clicks ? "engaged" : "sent"}
                          </td>
                        )}
                        {refs.map((ref) => {
                          const cell = cellFor(row, ref);
                          const active = cell.opens > 0 || cell.clicks > 0;
                          return (
                            <td
                              key={ref}
                              className="mono num-cell"
                              title={
                                `${cell.opens} human open(s), ${cell.clicks} click(s)` +
                                (cell.provider_fetches
                                  ? ` - ${cell.provider_fetches} provider fetch(es) excluded`
                                  : "")
                              }
                            >
                              {active ? (
                                <span>
                                  {cell.opens}
                                  {cell.clicks > 0 && (
                                    <span style={{ color: "var(--success)", fontWeight: 700 }}>
                                      {" "}
                                      · {cell.clicks}↗
                                    </span>
                                  )}
                                </span>
                              ) : (
                                <span style={{ color: "var(--ink-4)" }}>–</span>
                              )}
                            </td>
                          );
                        })}
                        <td className="mono num-cell">
                          {row.pixel_loads > 0 ? (
                            <b>{row.pixel_loads}</b>
                          ) : (
                            <span style={{ color: "var(--ink-4)" }}>0</span>
                          )}
                        </td>
                        <td
                          className="mono num-cell"
                          title="Loads from a real device. Lower than Opens when a provider proxy did some of the fetching."
                        >
                          {row.confirmed_opens > 0 ? (
                            <span style={{ color: "var(--success)" }}>
                              {row.confirmed_opens}
                            </span>
                          ) : (
                            <span style={{ color: "var(--ink-4)" }}>0</span>
                          )}
                        </td>
                        <td className="mono num-cell">
                          {row.clicks > 0 ? (
                            <b style={{ color: "var(--success)" }}>{row.clicks}</b>
                          ) : (
                            <span style={{ color: "var(--ink-4)" }}>0</span>
                          )}
                        </td>
                        <td className="mono" style={{ fontSize: 12 }}>
                          {row.last_at_local || (row.last_at ? String(row.last_at).replace("T", " ").slice(0, 19) : "–")}
                        </td>
                      </tr>

                      {openRow === row.email && (
                        <tr>
                          <td colSpan={refs.length + 5} style={{ background: "var(--surface-2)", padding: "14px 16px" }}>
                            <b style={{ fontSize: 13 }}>Every view and click - {row.email}</b>
                            {(refs.length ? refs : [""]).map((ref) => {
                              const cell = cellFor(row, ref);
                              const evs = cell.events || [];
                              return (
                                <div key={ref} style={{ marginTop: 10 }}>
                                  <div style={{ fontSize: 12.5, marginBottom: 4 }}>
                                    <b>{ref || "this send"}</b>{" "}
                                    <span style={{ color: "var(--ink-3)" }}>
                                      {cell.pixel_loads ?? cell.provider_fetches} open(s) ·{" "}
                                      {cell.confirmed_opens ?? cell.opens} confirmed ·{" "}
                                      {cell.clicks} click(s)
                                    </span>
                                  </div>
                                  {evs.length === 0 ? (
                                    <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
                                      No image load recorded. This mailbox does not fetch the
                                      tracking image, so no open can be observed for it.
                                    </div>
                                  ) : (
                                    <table className="t" style={{ fontSize: 12 }}>
                                      <thead>
                                        <tr>
                                          <th style={{ width: 40 }}>#</th>
                                          <th style={{ width: 175 }}>When</th>
                                          <th style={{ width: 130 }}>What</th>
                                          <th>From</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {evs.map((e, i) => (
                                          <tr key={i}>
                                            <td className="mono">{i + 1}</td>
                                            <td className="mono num-cell" title={e.at}>
                                              {e.at_local || String(e.at).replace("T", " ").slice(0, 19)}
                                            </td>
                                            <td>
                                              {e.kind === "click" ? (
                                                <span style={{ color: "var(--success)", fontWeight: 700 }}>
                                                  Click - a person
                                                </span>
                                              ) : e.machine ? (
                                                <span style={{ color: "var(--ink-3)" }}>
                                                  Image load - machine
                                                </span>
                                              ) : (
                                                <span style={{ color: "var(--success)" }}>
                                                  Image load - real device
                                                </span>
                                              )}
                                            </td>
                                            <td className="mono num-cell" style={{ fontSize: 11.5 }}>
                                              {e.ip}
                                              {e.reason ? (
                                                <span style={{ color: "var(--ink-4)" }}> · {e.reason}</span>
                                              ) : null}
                                            </td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  )}
                                </div>
                              );
                            })}
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager pager={rowsPager} label="recipients" />
              </>
            )}
          </div>

          <div className="note n-info" style={{ marginTop: 14 }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
            <span>{data.note}</span>
          </div>
        </>
      )}

      {!data && !error && <div className="loading"><span className="spinner" /> Loading engagement…</div>}
    </AppShell>
  );
}
