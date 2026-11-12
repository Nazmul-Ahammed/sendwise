"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import AppShell from "../../../components/AppShell";
import { StatusBadge } from "../../../components/Badge";
import GateList from "../../../components/GateList";
import ClickMap from "../../../components/ClickMap";
import { usePager, Pager } from "../../../components/Pagination";
import SequenceFlow from "../../../components/SequenceFlow";
import RefreshRatesButton from "../../../components/RefreshRatesButton";
import { api } from "../../../lib/api";

const TABS = [
  { id: "brief", n: 1, label: "Brief" },
  { id: "sequence", n: 2, label: "Sequence" },
  { id: "emails", n: 3, label: "Emails" },
  { id: "qa", n: 4, label: "QA" },
  { id: "launch", n: 5, label: "Launch" },
  { id: "engagement", n: 6, label: "Engagement" },
  { id: "delivery", n: 7, label: "Delivery" },
  { id: "clickmap", n: 8, label: "Click Map" },
];

const previewHtml = (html = "") => html.replace("</head>", "<style>.sw-compliance{display:none!important}</style></head>");

export default function CampaignDetailPage() {
  const { slug } = useParams();
  const router = useRouter();
  const search = useSearchParams();
  const wantedTab = search.get("tab");
  const [campaign, setCampaign] = useState(null);
  const [tab, setTab] = useState(TABS.some((t) => t.id === wantedTab) ? wantedTab : "brief");
  const [sel, setSel] = useState("E1"); // selected email ref for HTML preview
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState("");
  const [delivery, setDelivery] = useState(null);
  const [deliveryErr, setDeliveryErr] = useState("");
  const [seq, setSeq] = useState(null);
  const [seqErr, setSeqErr] = useState("");
  // Live engagement, measured by this app's own pixel rather than the provider.
  const [engage, setEngage] = useState(null);
  const [engageErr, setEngageErr] = useState("");
  const [engageLive, setEngageLive] = useState(true);
  // Click map: the sent HTML with a per-link click count overlaid on the message.
  const [clickmap, setClickmap] = useState(null);
  const [clickmapErr, setClickmapErr] = useState("");
  // Who is where in the journey for THIS send. Recipients belong to a send, not to the
  // sequence template, so this is the page that should show it.
  const [progress, setProgress] = useState(null);
  const [progressErr, setProgressErr] = useState("");
  const [openRow, setOpenRow] = useState("");
  const [seqBusy, setSeqBusy] = useState(false);
  const [seqFlash, setSeqFlash] = useState("");

  const load = useCallback(() => {
    api.campaign(slug).then(setCampaign).catch((e) => setError(e.message));
  }, [slug]);
  // The per-recipient table can hold thousands of rows; page it rather than scroll it.
  const recipientPager = usePager(engage?.rows || [], 25);

  const loadDelivery = useCallback(() => {
    setDeliveryErr("");
    api.campaignDeliveryReport(slug).then(setDelivery).catch((e) => setDeliveryErr(e.message));
  }, [slug]);

  const loadSeq = useCallback(() => {
    setSeqErr("");
    api.sequenceProgress(slug).then(setSeq).catch((e) => setSeqErr(e.message));
  }, [slug]);

  // "3m ago" / "in 2d" - short enough for a dense table, precise enough to act on.
  function fmtWhen(value, future) {
    if (!value) return "-";
    const t = new Date(value);
    if (isNaN(t)) return "-";
    const secs = Math.abs((Date.now() - t.getTime()) / 1000);
    if (secs < 60) return "just now";
    const span = secs < 3600 ? `${Math.floor(secs / 60)}m`
      : secs < 86400 ? `${Math.floor(secs / 3600)}h`
      : `${Math.floor(secs / 86400)}d`;
    return future ? `in ${span}` : `${span} ago`;
  }

  const loadProgress = useCallback(() => {
    setProgressErr("");
    return api.sequenceProgress(slug).then(setProgress).catch((e) => setProgressErr(e.message));
  }, [slug]);

  const loadEngagement = useCallback(() => {
    setEngageErr("");
    return api.campaignEngagement(slug).then(setEngage).catch((e) => setEngageErr(e.message));
  }, [slug]);

  const loadClickmap = useCallback(() => {
    setClickmapErr("");
    return api.campaignClickmap(slug).then(setClickmap).catch((e) => setClickmapErr(e.message));
  }, [slug]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (tab === "delivery" && !delivery && !deliveryErr) loadDelivery();
    if (tab === "delivery" && !seq && !seqErr) loadSeq();
    if (tab === "engagement" && !engage && !engageErr) loadEngagement();
    if (tab === "engagement" && !progress && !progressErr) loadProgress();
    if (tab === "clickmap" && !clickmap && !clickmapErr) loadClickmap();
  }, [tab, delivery, deliveryErr, seq, seqErr, loadDelivery, loadSeq,
      engage, engageErr, loadEngagement, progress, progressErr, loadProgress,
      clickmap, clickmapErr, loadClickmap]);

  // Refresh while this tab is open, so a new open appears without a reload. Only while
  // the tab is visible: a hidden tab has no reason to keep asking.
  useEffect(() => {
    if (tab !== "engagement" || !engageLive) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") { loadEngagement(); loadProgress(); }
    }, 10000);
    return () => clearInterval(id);
  }, [tab, engageLive, loadEngagement, loadProgress]);

  // The campaign's own rates (the Opens/Clicks/Delivery shown in the header and the Brief
  // tab) refresh on their own too, so they never sit stale while the page is open.
  useEffect(() => {
    const tick = () => { if (document.visibilityState === "visible") load(); };
    const id = setInterval(tick, campaign?.status === "sending" ? 5000 : 30000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [load, campaign?.status]);

  async function enrollNow() {
    setSeqBusy(true);
    setSeqErr("");
    setSeqFlash("");
    try {
      const res = await api.enrollSequence(slug);
      setSeqFlash(
        `Queued ${res.queued || 0} recipient(s) for Email 1`
        + (res.already_enrolled ? ` · ${res.already_enrolled} already enrolled` : "")
        + (res.skipped_suppressed ? ` · ${res.skipped_suppressed} suppressed` : ""),
      );
      await load();
      await loadSeq();
      setTimeout(() => setSeqFlash(""), 6000);
    } catch (e) {
      setSeqErr(e.message);
    } finally {
      setSeqBusy(false);
    }
  }

  async function act(action) {
    setBusy(true);
    setError("");
    try {
      if (action === "approve") await api.approve(slug);
      if (action === "launch") {
        // Re-read before acting: a tab opened earlier can still show "draft"
        // after the send already went live, and acting on that stale status is
        // what surfaced "Cannot approve campaign in state live".
        const fresh = await api.campaign(slug);
        setCampaign(fresh);
        if (["live", "sending", "sent", "exited"].includes(fresh.status)) {
          setError(`This send is already ${fresh.status} - nothing was sent again.`);
          setBusy(false);
          return;
        }
        if (fresh.status === "draft") await api.approve(slug);
        await api.launch(slug);
      }
      if (action === "pause") await api.pause(slug);
      setFlash(action === "launch" ? "Send via SendClean - message queued" : action === "pause" ? "Send paused" : "Send approved");
      setBusy(false);
      load();
      setTimeout(() => setFlash(""), 4000);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  async function deleteNow() {
    const sent = !!campaign?.provider_message_id;
    const msg = sent
      ? `Delete "${campaign.title}"?\n\nThis send has already gone out. Deleting removes the local record, its ` +
        `open/click history and any sequence enrollments.\n\nEmails already delivered cannot be recalled.`
      : `Delete "${campaign.title}"?\n\nThis draft has never been sent, so nothing goes out.`;
    if (!window.confirm(msg)) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteCampaign(slug);
      router.push("/campaigns");
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  if (!campaign && !error) {
    return (
      <AppShell active="/campaigns">
        <div className="loading"><span className="spinner" /> Loading campaign…</div>
      </AppShell>
    );
  }

  const gatesPassed = (campaign?.gates || []).filter((g) => g.pass).length;
  const gatesTotal = campaign?.gates?.length || 0;
  const recipients = campaign?.audience_options?.manual_emails || [];
  const hasRecipients = recipients.length > 0;
  const rates = campaign?.rates || { source: "none" };
  const pct = (v) => (v === null || v === undefined ? "-" : `${v}%`);
  const ago = (iso) => {
    if (!iso) return "never";
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return "never";
    const secs = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  return (
    <AppShell active="/campaigns">
      <Link className="btn btn-ghost btn-sm" href="/campaigns" style={{ marginBottom: 14 }}>← All sends</Link>

      {error && <div className="note n-warn"><span>{error}</span></div>}
      {flash && <div className="note n-success"><span>{flash}</span></div>}

      {campaign && (
        <>
          <div className="page-head">
            <div>
              <div className="inline">
                <h1>{campaign.title}</h1>
                <StatusBadge status={campaign.status} />
              </div>
              <div className="inline" style={{ marginTop: 10 }}>
                 <span className="badge b-neutral">
                   {campaign.enrollment?.enrolled || recipients.length || campaign.audience_count || 0} recipients
                 </span>
                 {campaign.enrollment?.exists ? (
                   <span className="badge b-neutral">
                     {campaign.enrollment.sends || 0} sent
                     {campaign.enrollment.pending_initial ? ` · ${campaign.enrollment.pending_initial} queued` : ""}
                     {campaign.enrollment.failed ? ` · ${campaign.enrollment.failed} failed` : ""}
                   </span>
                 ) : null}
                <span className="badge b-neutral">Per-recipient tracking</span>
              </div>
            </div>
            <div className="actions">
              <Link className="btn btn-secondary" href={`/campaigns/${slug}?tab=emails`}>Edit copy</Link>
              {campaign.status !== "paused" && campaign.status !== "exited" && (
                <button className="btn btn-secondary" disabled={busy} onClick={() => act("pause")}>Pause</button>
              )}
              {(campaign.status === "draft" || campaign.status === "approved") && (
                <button className="btn btn-primary" disabled={busy} onClick={() => act("approve")}>Approve</button>
              )}
              {["draft", "approved"].includes(campaign.status) && hasRecipients && (
                <button className="btn btn-primary" disabled={busy || gatesTotal > 0 && gatesPassed < gatesTotal} onClick={() => act("launch")}>
                  {busy ? "Working…" : "Approve & Launch"}
                </button>
              )}
              <button className="btn btn-danger" disabled={busy} onClick={deleteNow}>Delete</button>
            </div>
          </div>

          <div className="tabs">
            {TABS.map((t) => (
              <button key={t.id} className={tab === t.id ? "on" : ""} onClick={() => setTab(t.id)}>
                <span className="n">{t.n}</span>{t.label}
              </button>
            ))}
          </div>

          {tab === "brief" && (
            <div className="grid two-col">
              <div className="card">
                <div className="card-h"><h3>Campaign brief</h3><span className="hint">source: product knowledge + config</span></div>
                <dl className="kv">
                  <dt>Product</dt><dd>{campaign.product}</dd>
                  <dt>Offer / angle</dt><dd>{campaign.offer}</dd>
                  <dt>Recipients</dt><dd>{recipients.length || campaign.audience_count || 0} explicit recipient(s) · mode {campaign.audience_options?.mode || "manual"} · locked to this list</dd>
                  <dt>Sender</dt><dd>{campaign.sender_name} · {campaign.sender_email}</dd>
                  <dt>Sequence type</dt><dd>{campaign.sequence_type}</dd>
                  <dt>Launched</dt><dd>{campaign.launched_at || "-"}</dd>
                  {campaign.provider_message_id && <><dt>Provider message ID</dt><dd className="mono">{campaign.provider_message_id}</dd></>}
                  <dt>Performance</dt>
                  <dd>
                    {rates.source === "none"
                      ? <span style={{ color: "var(--ink-3)" }}>No tracked sends yet - send once, then open the <b>Delivery</b> tab (it will fill in here).</span>
                      : <>Delivery {pct(rates.delivery_rate)} · Open (tracked) {pct(rates.open_rate)} · Click {pct(rates.click_rate)}
                          {rates.sent ? <> · <b>{rates.sent}</b> sent{rates.unique_openers != null ? ` · ${rates.unique_openers} opened` : ""}{rates.unique_clickers != null ? ` · ${rates.unique_clickers} clicked` : ""}</> : null}
                          {rates.source === "sendclean" ? <span className="hint"> · live per-recipient tracking</span> : <span className="hint"> · legacy value</span>}
                        </>}
                  </dd>
                </dl>
              </div>
              <div className="card">
                <div className="card-h"><h3>Launch readiness</h3><span className="hint">overall gate status</span></div>
                <div className="status-row">
                  <div className="status-card" style={{ background: "var(--success-bg)", border: "1px solid var(--success-border)" }}>
                    <div style={{ fontSize: 12, color: "#065F46", fontWeight: 600 }}>Gates passed</div>
                    <div style={{ fontSize: 24, fontWeight: 800, color: "var(--success)" }}>{gatesPassed}/{gatesTotal}</div>
                  </div>
                  <div className="status-card" style={{ background: "var(--warning-bg)", border: "1px solid var(--warning-border)" }}>
                    <div style={{ fontSize: 12, color: "#92400E", fontWeight: 600 }}>Pending</div>
                    <div style={{ fontSize: 24, fontWeight: 800, color: "var(--warning)" }}>{gatesTotal - gatesPassed}</div>
                  </div>
                </div>
                <div className="note n-info" style={{ marginTop: 14 }}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
                  <span>Mode: <b>auto_launch</b> - all gates pass korlei agent auto-launch kore. Emergency threshold breach hoile auto-pause.</span>
                </div>
              </div>
            </div>
          )}

          {tab === "sequence" && (
            <div className="card">
              <div className="card-h"><h3>Lifecycle flow</h3><span className="hint">click an email node → then open Emails tab for copy</span></div>
              <SequenceFlow stages={campaign.sequence} emails={campaign.emails} audience={campaign.audience_name} onSelect={(ref) => { setSel(ref); setTab("emails"); }} selectedRef={sel} />
              <div className="note n-info" style={{ marginTop: 14 }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
                <span>Stages below their open-rate minimum get root-cause analysis in the Sunday audit, then a controlled optimization.</span>
              </div>
            </div>
          )}

          {tab === "emails" && (() => {
            const emails = campaign.emails || [];
            const selEmail = emails.find((e) => e.ref === sel) || emails[0];
            const html = (selEmail?.body_html || "").trim();
            return (
              <div className="grid two-col">
                <div className="card">
                  <div className="card-h"><h3>Stage emails</h3><span className="hint">{emails.length || 0} stage(s) · click korle HTML preview</span></div>
                  {(emails.length ? emails : [{ stage: 1, subject: campaign.title }]).map((e) => (
                    <button key={e.ref || e.stage} onClick={() => setSel(e.ref || e.stage)} style={{ width: "100%", textAlign: "left", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, padding: "9px 11px", borderRadius: "var(--radius-sm)", marginBottom: 6, background: (e.ref || e.stage) === sel ? "var(--brand-50)" : "var(--surface-2)", border: (e.ref || e.stage) === sel ? "1px solid var(--brand-500)" : "1px solid var(--border)", fontSize: 12.5, fontWeight: 600, color: (e.ref || e.stage) === sel ? "var(--brand-600)" : "var(--ink-2)", cursor: "pointer" }}>
                      <span>{e.ref || `S${e.stage}`} · {e.subject || "(no subject yet)"}</span>
                      {(e.body_html || "").trim() ? <span style={{ fontSize: 10.5, fontWeight: 800, color: "var(--ok, #16794C)" }}>HTML ✓</span> : null}
                    </button>
                  ))}
                </div>
                <div className="card">
                  <div className="card-h">
                    <h3>HTML preview - {selEmail?.ref || `Stage ${selEmail?.stage ?? 1}`}</h3>
                    <span className="hint">{selEmail?.source?.startsWith("brand_docs") ? "source: brand-docs/" + selEmail.source.replace("brand_docs/", "") : "branded HTML - body = source of truth"}</span>
                  </div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-3)", marginBottom: 10 }}>
                    {selEmail?.subject ? `Subject: ${selEmail.subject}` : "Subject: (draft korte hobe)"} · {campaign.sender_name} &lt;{campaign.sender_email}&gt;
                  </div>
                  {selEmail?.journey_name && (
                    <div className="note n-info" style={{ marginTop: 0, marginBottom: 10 }}>
                      <span><b>Journey:</b> {selEmail.journey_name} · <b>CTA:</b> {selEmail.cta_label || "auto"}</span>
                    </div>
                  )}
                  {html ? (
                    <iframe title={`HTML preview ${selEmail.ref || selEmail.stage}`} srcDoc={previewHtml(html)} style={{ width: "100%", height: 520, border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "#F6F4FB" }} sandbox="" />
                  ) : (
                    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "16px 18px", fontSize: 13.5, lineHeight: 1.7, color: "var(--ink-2)", whiteSpace: "pre-wrap" }}>
                      {(selEmail?.body || "(body draft hoyni - ✨ Draft with Agent use korun)")}
                    </div>
                  )}
                  <div className="note n-info" style={{ marginTop: 12 }}>
                    <span>Send hobe <b>always branded HTML</b> (email-writing-skill-v4 · Section 0) - brand header, colors, bullets, CTA + unique UTM. {campaign.product}</span>
                  </div>
                </div>
              </div>
            );
          })()}

          {tab === "qa" && (
            <div className="card">
              <div className="card-h"><h3>QA report · {campaign.qa?.length || 0} checks</h3><span className="hint">brand · factual · link · deliverability · compliance</span></div>
              <GateList gates={campaign.qa} compact />
            </div>
          )}

          {tab === "launch" && (
            <div className="grid" style={{ gridTemplateColumns: "1.4fr 1fr" }}>
              <div className="card">
                <div className="card-h"><h3>Launch gates</h3><span className="hint">auto-launch authorized only if all pass</span></div>
                <GateList gates={campaign.gates} />
              </div>
              <div className="card">
                 <div className="card-h"><h3>Final action</h3></div>
                 <div style={{ display: "grid", gap: 12 }}>
                   {!hasRecipients && <div className="note n-danger"><span><b>BLOCKED:</b> no recipients stored for this send. Create a new send with an explicit recipient list.</span></div>}
                   {hasRecipients && <div className="note n-info"><span><b>Recipients confirmed:</b> {recipients.length} explicit recipient(s). Each one is sent with its own tracking ID, so open and click status can be read back per person.</span></div>}
                   <div className="gate pass"><div className="g-ico">✓</div><div className="g-body"><b>{gatesPassed}/{gatesTotal} gates passed</b><span>Ready for dispatch</span></div></div>
                   <button className="btn btn-primary btn-lg" disabled={busy || !hasRecipients || !["draft", "approved"].includes(campaign.status) || (gatesTotal > 0 && gatesPassed < gatesTotal)} onClick={() => act("launch")}> 
                     {busy ? "Working…" : !hasRecipients ? "Launch blocked - no recipients" : !["draft", "approved"].includes(campaign.status) ? `Launch blocked (${campaign.status})` : "Approve recipients & Send via SendClean"}
                   </button>
                  <button className="btn btn-secondary" onClick={() => setTab("brief")}>Back to brief</button>
                  <div style={{ fontSize: 12, color: "var(--ink-3)", textAlign: "center" }}>Launch action recorded in audit log · immutable</div>
                </div>
              </div>
            </div>
          )}
          {tab === "engagement" && (
            <div className="stack">
              {engageErr && <div className="note n-warn"><span>{engageErr}</span></div>}
              {!engage && !engageErr && (
                <div className="loading"><span className="spinner" /> Loading engagement…</div>
              )}
              {engage && (
                <>
                  <div className="inline between">
                    <div>
                      <div className="section-title">Live engagement</div>
                      <div className="section-sub">
                        Measured by this app's own tracking. Times in{" "}
                        {engage.realtime?.timezone || "the account timezone"}.
                      </div>
                    </div>
                    <button
                      className={`btn btn-sm ${engageLive ? "btn-primary" : "btn-secondary"}`}
                      onClick={() => setEngageLive((v) => !v)}
                    >
                      {engageLive ? "● Live" : "Paused"}
                    </button>
                  </div>

                  {/* The Mailchimp metric set, same names and arithmetic. */}
                  {engage.mailchimp && (
                    <div className="card">
                      <div className="card-h">
                        <h3>Campaign report</h3>
                        <span className="hint">
                          rates based on {engage.mailchimp.rate_basis}
                        </span>
                      </div>
                      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(128px,1fr))", gap: 12 }}>
                        {[
                          ["Emails Sent", engage.mailchimp.emails_sent],
                          ["Recipients", engage.mailchimp.recipients],
                          ["Opens", engage.mailchimp.opens],
                          ["Unique Opens", engage.mailchimp.unique_opens],
                          ["Open Rate", `${engage.mailchimp.open_rate}%`],
                          ["Clicks", engage.mailchimp.clicks],
                          ["Unique Clicks", engage.mailchimp.unique_clicks],
                          ["Click Rate", `${engage.mailchimp.click_rate}%`],
                          ["Unsubscribed", engage.mailchimp.unsubscribed],
                          ["Bounced", engage.mailchimp.bounced],
                        ].map(([label, value]) => (
                          <div key={label} className="stat">
                            <span>{label}</span>
                            <b>{value}</b>
                          </div>
                        ))}
                      </div>
                      {(engage.mailchimp.top_links || []).length > 0 && (
                        <div className="mt">
                          <div className="section-title" style={{ fontSize: 14, marginBottom: 8 }}>
                            Top Links Clicked
                          </div>
                          <div className="table-wrap">
                            <table className="t">
                              <thead><tr><th>Link</th><th className="num-cell">Clicks</th></tr></thead>
                              <tbody>
                                {engage.mailchimp.top_links.map((l) => (
                                  <tr key={l.url}>
                                    <td className="mono" style={{ wordBreak: "break-all" }}>{l.url}</td>
                                    <td className="mono num-cell"><b>{l.clicks}</b></td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Which email was opened, and how often. */}
                  {(engage.per_email || []).length > 0 && (
                    <div className="card">
                      <div className="card-h">
                        <h3>Per email</h3>
                        <span className="hint">which email was opened, and how often</span>
                      </div>
                      <div className="table-wrap">
                        <table className="t">
                          <thead>
                            <tr>
                              <th style={{ width: 56 }}>Email</th>
                              <th>Subject</th>
                              <th className="num-cell" style={{ width: 74 }}>Sent to</th>
                              <th className="num-cell" style={{ width: 74 }}>Opens</th>
                              <th className="num-cell" style={{ width: 96 }}>Unique</th>
                              <th className="num-cell" style={{ width: 84 }}>Rate</th>
                            </tr>
                          </thead>
                          <tbody>
                            {engage.per_email.map((e) => (
                              <tr key={e.ref}>
                                <td className="mono"><b>{e.ref}</b></td>
                                <td>
                                  <div style={{ fontSize: 13 }}>{e.name || "-"}</div>
                                  {e.subject && (
                                    <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{e.subject}</div>
                                  )}
                                </td>
                                <td className="mono num-cell">{e.recipients}</td>
                                <td className="mono num-cell">
                                  {e.opens > 0 ? <b>{e.opens}</b> : <span style={{ color: "var(--ink-4)" }}>0</span>}
                                </td>
                                <td className="mono num-cell">{e.unique_opens}</td>
                                <td className="mono num-cell">{e.open_rate}%</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Who is where in the journey. Moved here from the sequence page: a
                      sequence is a template with no recipients, so per-recipient progress
                      only makes sense against a send. */}
                  {progress && (
                    <div className="card">
                      <div className="card-h">
                        <div>
                          <h3>Recipient progress</h3>
                          <span className="hint">
                            where each person has reached in the journey
                          </span>
                        </div>
                        <div className="inline">
                          {[
                            ["Enrolled", progress.counts.enrolled],
                            ["Waiting", progress.counts.active],
                            ["Completed", progress.counts.completed],
                            ["Stopped", progress.counts.unsubscribed + progress.counts.suppressed],
                            ["Failed", progress.counts.failed],
                          ].map(([label, value]) => (
                            <span className="badge b-neutral" key={label}>
                              {label}: <b style={{ marginLeft: 3 }}>{value}</b>
                            </span>
                          ))}
                        </div>
                      </div>
                      {progressErr && <div className="note n-warn"><span>{progressErr}</span></div>}
                      {(progress.enrollments || []).length === 0 ? (
                        <div className="empty">
                          <b>Nobody enrolled</b>
                          <p>Enrol an audience and this shows every recipient's place in the journey.</p>
                        </div>
                      ) : (
                        <div className="table-wrap">
                          <table className="t">
                            <thead>
                              <tr>
                                <th>Recipient</th>
                                <th>Where it stands</th>
                                <th>On email</th>
                                <th>Branch taken</th>
                                <th className="num-cell">Sent</th>
                                <th>Last sent</th>
                                <th>Next due</th>
                              </tr>
                            </thead>
                            <tbody>
                              {progress.enrollments.map((e, i) => (
                                <tr key={`${e.email}-${i}`}>
                                  <td className="mono" style={{ fontSize: 12 }}>{e.email}</td>
                                  <td>
                                    <span className={`badge ${{
                                      active: "b-neutral",
                                      completed: "b-success",
                                      unsubscribed: "b-warn",
                                      suppressed: "b-danger",
                                      failed: "b-danger",
                                      needs_review: "b-warn",
                                    }[e.status] || "b-neutral"}`}>
                                      {e.status === "needs_review" ? "needs review" : e.status}
                                    </span>
                                  </td>
                                  <td><b>{e.current_ref || "-"}</b></td>
                                  <td>{e.branch || "-"}</td>
                                  <td className="num-cell">{e.sends}</td>
                                  <td className="mono" style={{ fontSize: 11 }}>{fmtWhen(e.last_sent_at)}</td>
                                  <td className="mono" style={{ fontSize: 11 }}>
                                    {e.status === "active" ? fmtWhen(e.next_due_at, true) : "-"}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Per recipient, with the timeline underneath. */}
                  {(engage.rows || []).length > 0 && (
                    <div className="card">
                      <div className="card-h">
                        <h3>Per recipient</h3>
                        <span className="hint">
                          click a row to see every view and click
                          {engage.realtime?.last_event_at_local
                            ? ` · last ${engage.realtime.last_event_at_local}` : ""}
                        </span>
                      </div>
                      <div className="table-wrap">
                        <table className="t">
                          <thead>
                            <tr>
                              <th>Recipient</th>
                              {(engage.refs || []).map((r) => <th key={r} className="num-cell">{r}</th>)}
                              <th className="num-cell">Opens</th>
                              <th className="num-cell">Confirmed</th>
                              <th className="num-cell">Clicks</th>
                            </tr>
                          </thead>
                          <tbody>
                            {recipientPager.rows.map((row) => (
                              <Fragment key={row.email}>
                                <tr
                                  className="row-click"
                                  onClick={() => setOpenRow(openRow === row.email ? "" : row.email)}
                                >
                                  <td className="mono">
                                    <span style={{ color: "var(--ink-4)", marginRight: 6 }}>
                                      {openRow === row.email ? "▾" : "▸"}
                                    </span>
                                    {row.email}
                                  </td>
                                  {(engage.refs || []).map((ref) => {
                                    const cell = (row.cells || {})[ref] || {};
                                    const n = cell.pixel_loads || 0;
                                    return (
                                      <td key={ref} className="mono num-cell">
                                        {n > 0 ? <b>{n}</b> : <span style={{ color: "var(--ink-4)" }}>–</span>}
                                      </td>
                                    );
                                  })}
                                  <td className="mono num-cell">{row.pixel_loads || 0}</td>
                                  <td className="mono num-cell">{row.confirmed_opens || 0}</td>
                                  <td className="mono num-cell">
                                    {row.clicks > 0
                                      ? <b style={{ color: "var(--success)" }}>{row.clicks}</b>
                                      : <span style={{ color: "var(--ink-4)" }}>0</span>}
                                  </td>
                                </tr>
                                {openRow === row.email && (
                                  <tr>
                                    <td colSpan={(engage.refs || []).length + 4} style={{ background: "var(--surface-2)" }}>
                                      <b style={{ fontSize: 13 }}>Every view and click - {row.email}</b>
                                      {(engage.refs || []).map((ref) => {
                                        const cell = (row.cells || {})[ref] || {};
                                        const evs = cell.events || [];
                                        if (!evs.length) return null;
                                        return (
                                          <div key={ref} style={{ marginTop: 10 }}>
                                            <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginBottom: 4 }}>
                                              <b style={{ color: "var(--ink)" }}>{ref}</b>{" "}
                                              {cell.pixel_loads} open(s) · {cell.confirmed_opens} confirmed ·{" "}
                                              {cell.clicks} click(s)
                                            </div>
                                            <table className="t" style={{ fontSize: 12 }}>
                                              <thead>
                                                <tr><th style={{ width: 40 }}>#</th><th style={{ width: 170 }}>When</th><th style={{ width: 150 }}>What</th><th>From</th></tr>
                                              </thead>
                                              <tbody>
                                                {evs.map((e, i) => (
                                                  <tr key={i}>
                                                    <td className="mono">{i + 1}</td>
                                                    <td className="mono">{e.at_local || String(e.at).replace("T", " ").slice(0, 19)}</td>
                                                    <td>
                                                      {e.kind === "click" ? (
                                                        <span style={{ color: "var(--success)", fontWeight: 700 }}>Click - a person</span>
                                                      ) : e.machine ? (
                                                        <span style={{ color: "var(--ink-3)" }}>Image load - machine</span>
                                                      ) : (
                                                        <span style={{ color: "var(--success)" }}>Image load - real device</span>
                                                      )}
                                                    </td>
                                                    <td className="mono" style={{ fontSize: 11.5 }}>{e.ip}</td>
                                                  </tr>
                                                ))}
                                              </tbody>
                                            </table>
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
                      <Pager pager={recipientPager} label="recipients" />
                    </div>
                  )}

                  {!engageErr && !engage.rows?.length && (
                    <div className="empty">
                      <div className="e-ico">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9" /><path d="M12 8v4M12 16h.01" /></svg>
                      </div>
                      <b>Nothing tracked yet</b>
                      <p>
                        Opens and clicks are measured by this app's own tracking, so only
                        sends made after it was switched on carry it. Send once and this
                        fills in.
                      </p>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {tab === "delivery" && (
            <div className="grid" style={{ gap: 16 }}>
              <div className="card">
                <div className="card-h">
                  <h3>Delivery &amp; engagement</h3>
                  <RefreshRatesButton
                    compact
                    updatedAt={delivery?.rates?.computed_at}
                    onRefresh={loadDelivery}
                  />
                </div>
                {deliveryErr && (
                  /no per-recipient send data/i.test(deliveryErr)
                    ? <div className="note n-info"><span>Nothing has been sent from this campaign yet. The delivery report appears after the first send.</span></div>
                    : <div className="note n-warn"><span>⚠ {deliveryErr}</span></div>
                )}
                {!delivery && !deliveryErr && <div className="loading"><span className="spinner" /> Loading report…</div>}
                {delivery && (
                  <>
                    <div className="status-row">
                      <div className="status-card">
                        <div style={{ fontSize: 12, fontWeight: 600 }}>Sent</div>
                        <div style={{ fontSize: 24, fontWeight: 800 }}>{delivery.totals.sent}</div>
                      </div>
                      <div className="status-card" style={{ background: "var(--success-bg)", border: "1px solid var(--success-border)" }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: "#065F46" }}>Opened</div>
                        <div style={{ fontSize: 24, fontWeight: 800, color: "var(--success)" }}>{delivery.totals.opened}</div>
                      </div>
                      <div className="status-card">
                        <div style={{ fontSize: 12, fontWeight: 600 }}>Not opened</div>
                        <div style={{ fontSize: 24, fontWeight: 800 }}>{delivery.totals.not_opened}</div>
                      </div>
                      <div className="status-card">
                        <div style={{ fontSize: 12, fontWeight: 600 }}>Clicks</div>
                        <div style={{ fontSize: 24, fontWeight: 800 }}>{delivery.totals.clicks}</div>
                      </div>
                      <div className="status-card">
                        <div style={{ fontSize: 12, fontWeight: 600 }}>Not delivered</div>
                        <div style={{ fontSize: 24, fontWeight: 800 }}>{delivery.totals.not_delivered}</div>
                      </div>
                    </div>
                    <div className="note n-warn" style={{ marginTop: 12 }}><span><b>Open data is indicative.</b> {delivery.caveat}</span></div>
                    <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 10 }}>
                      batch: {delivery.batch} · sent: {delivery.sent_at} · tracking: {delivery.tracking} · open rate (tracked): {delivery.totals.open_rate}%
                      {delivery.rates?.computed_at
                        ? <> · <b>last updated {ago(delivery.rates.computed_at)}</b> ({new Date(delivery.rates.computed_at).toLocaleString()})</>
                        : null}
                    </div>
                  </>
                )}
              </div>

              {delivery && (
                <div className="grid two-col">
                  <div className="card">
                    <div className="card-h"><h3>Opened - {delivery.totals.opened}</h3><span className="hint">tracking pixel fired</span></div>
                    {delivery.opened.length === 0 ? (
                      <div className="hint">No opens tracked yet.</div>
                    ) : (
                      <table className="t">
                        <thead><tr><th>Recipient</th><th className="num-cell">Opens</th><th className="num-cell">Clicks</th><th>Last open IP</th></tr></thead>
                        <tbody>
                          {delivery.opened.map((r) => (
                            <tr key={r.email}>
                              <td className="mono" style={{ fontSize: 12 }}>{r.email}</td>
                              <td className="num-cell">{r.opens}</td>
                              <td className="num-cell">{r.clicks}</td>
                              <td className="mono" style={{ fontSize: 11 }}>{r.last_open_ip || "-"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                  <div className="card">
                    <div className="card-h"><h3>Not opened - {delivery.totals.not_opened}</h3><span className="hint">no pixel hit (may include image-blockers)</span></div>
                    {delivery.not_opened.length === 0 ? (
                      <div className="hint">Nobody in this list.</div>
                    ) : (
                      <table className="t">
                        <thead><tr><th>Recipient</th><th>Status</th></tr></thead>
                        <tbody>
                          {delivery.not_opened.map((r) => (
                            <tr key={r.email}>
                              <td className="mono" style={{ fontSize: 12 }}>{r.email}</td>
                              <td>{r.status}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              )}

              {delivery && delivery.not_delivered.length > 0 && (
                <div className="card">
                  <div className="card-h"><h3>Not delivered - {delivery.totals.not_delivered}</h3></div>
                  <table className="t">
                    <thead><tr><th>Recipient</th><th>Status</th></tr></thead>
                    <tbody>
                      {delivery.not_delivered.map((r) => (
                        <tr key={r.email}>
                          <td className="mono" style={{ fontSize: 12 }}>{r.email}</td>
                          <td>{r.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="card">
                <div className="card-h">
                  <h3>Branched sequence</h3>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    {seq && <span className="hint">next step every {seq.gap_days} days</span>}
                    <button className="btn btn-secondary btn-sm"
                      disabled={seqBusy || campaign?.status === "sending" || (seq?.counts?.enrolled || 0) > 0}
                      title={(seq?.counts?.enrolled || 0) > 0 ? "This journey is already enrolled" : ""}
                      onClick={enrollNow}>
                      {campaign?.status === "sending" ? "Sending…" : (seqBusy ? "Enrolling…" : "Enroll recipients")}
                    </button>
                  </div>
                </div>
                {seqFlash && <div className="note n-success" style={{ marginBottom: 10 }}>✓ {seqFlash}</div>}
                {seqErr && <div className="note n-warn" style={{ marginBottom: 10 }}>⚠ {seqErr}</div>}
                {seq && seq.counts.enrolled === 0 && (
                  <div className="note n-info"><span>No one is enrolled yet. <b>Enroll recipients</b> sends E1 now and starts the branch flow; the scheduler advances everyone every {seq.gap_days} days.</span></div>
                )}
                {seq && seq.counts.enrolled > 0 && (
                  <>
                    <div className="status-row">
                      <div className="status-card"><div style={{ fontSize: 12, fontWeight: 600 }}>Enrolled</div><div style={{ fontSize: 22, fontWeight: 800 }}>{seq.counts.enrolled}</div></div>
                      <div className="status-card"><div style={{ fontSize: 12, fontWeight: 600 }}>Active</div><div style={{ fontSize: 22, fontWeight: 800 }}>{seq.counts.active}</div></div>
                      <div className="status-card"><div style={{ fontSize: 12, fontWeight: 600 }}>Completed</div><div style={{ fontSize: 22, fontWeight: 800 }}>{seq.counts.completed}</div></div>
                      <div className="status-card"><div style={{ fontSize: 12, fontWeight: 600 }}>Unsubscribed</div><div style={{ fontSize: 22, fontWeight: 800 }}>{seq.counts.unsubscribed}</div></div>
                      <div className="status-card"><div style={{ fontSize: 12, fontWeight: 600 }}>Suppressed</div><div style={{ fontSize: 22, fontWeight: 800 }}>{seq.counts.suppressed}</div></div>
                    </div>
                    <div style={{ marginTop: 12 }}>
                      {Object.keys(seq.counts.by_ref || {}).sort().map((r) => (
                        <span className="badge b-neutral" key={r} style={{ marginRight: 6 }}>{r}: <b>{seq.counts.by_ref[r]}</b></span>
                      ))}
                    </div>
                    <table className="t" style={{ marginTop: 14 }}>
                      <thead><tr><th>Recipient</th><th>At</th><th>Branch</th><th className="num-cell">Sent</th><th>Last sent</th><th>Next due</th></tr></thead>
                      <tbody>
                        {seq.enrollments.map((e) => (
                          <tr key={e.email}>
                            <td className="mono" style={{ fontSize: 12 }}>{e.email}</td>
                            <td className="mono"><b>{e.current_ref || "-"}</b></td>
                            <td>{e.branch || "-"}</td>
                            <td className="num-cell">{e.sends}</td>
                            <td className="mono" style={{ fontSize: 11 }}>{e.last_sent_at ? e.last_sent_at.slice(0, 16).replace("T", " ") : "-"}</td>
                            <td className="mono" style={{ fontSize: 11 }}>{e.next_due_at ? e.next_due_at.slice(0, 16).replace("T", " ") : "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 12, lineHeight: 1.9 }}>
                      E1 → opened? <b>E2</b> : <b>E3</b><br />
                      E2 → clicked? <b>E4</b> : opened? <b>E5</b> : <b>E6</b><br />
                      E3 → clicked? <b>E7</b> : opened? <b>E8</b> : <b>E9</b><br />
                      E4–E9 end the sequence
                    </div>
                  </>
                )}
              </div>

              {!hasRecipients && (
                <div className="note n-info"><span>This send has no per-recipient tracking data (it was created before per-recipient tracking, or nothing has been sent yet).</span></div>
              )}
            </div>
          )}

          {tab === "clickmap" && (
            <div className="card">
              <div className="card-h">
                <h3>Click map</h3>
                <span className="hint">rendered email · badges sized by clicks</span>
              </div>
              {clickmapErr && <div className="note n-warn"><span>⚠ {clickmapErr}</span></div>}
              {!clickmap && !clickmapErr && (
                <div className="loading"><span className="spinner" /> Loading click map…</div>
              )}
              {clickmap && (
                <>
                  {clickmap.totals.clicks_unplaced > 0 && (
                    <div className="note n-info" style={{ marginTop: 0 }}>
                      <span>
                        <b>{clickmap.totals.clicks_unplaced}</b> click(s) came from the provider
                        without naming a link, so they are counted but cannot be drawn on the
                        message.
                      </span>
                    </div>
                  )}
                  <ClickMap
                    emails={clickmap.emails}
                    selectedRef={sel}
                    onSelect={setSel}
                    recipients={campaign.audience_count || 0}
                  />
                  <div className="note n-info" style={{ marginTop: 12 }}>
                    <span>{clickmap.note}</span>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </AppShell>
  );
}
