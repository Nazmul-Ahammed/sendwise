"use client";

import { useState, useEffect, Fragment } from "react";
import Link from "next/link";
import { StatusBadge } from "./Badge";
import TagSelect from "./TagSelect";
import { api } from "../lib/api";

/**
 * The Sends list.
 *
 * A one-off send is simply sent. A send built from a sequence is a JOURNEY, so it
 * gets a ▶ Start action right here - the same place every other campaign is run -
 * instead of pushing the operator off to another page.
 */
export default function CampaignTable({ campaigns, onChanged }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [starting, setStarting] = useState("");      // slug of the send being started
  const [audiences, setAudiences] = useState([]);
  const [audienceId, setAudienceId] = useState(0);
  const [tagFilters, setTagFilters] = useState([]);
  const [matchMode, setMatchMode] = useState("any");
  const [preview, setPreview] = useState(null);
  const [enrolling, setEnrolling] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    api.audiences().then((rows) => setAudiences(Array.isArray(rows) ? rows : [])).catch(() => setAudiences([]));
  }, []);

  if (!campaigns || campaigns.length === 0) {
    return <div className="loading">No sends yet - create one from “+ New Send”.</div>;
  }

  async function remove(c) {
    const sent = !!c.provider_message_id;
    const msg = sent
      ? `Move “${c.title}” to Trash?\n\nThis send has already gone out. Nothing is erased - its record, ` +
        `open/click history and sequence progress are all kept, and you can restore it from Trash & backup.\n\n` +
        `Emails already delivered cannot be recalled.`
      : `Move “${c.title}” to Trash?\n\nNothing is erased - the send and its sequence progress are kept, ` +
        `and you can restore it from Trash & backup at any time.`;
    if (!window.confirm(msg)) return;
    setBusy(c.slug);
    setError("");
    try {
      await api.deleteCampaign(c.slug);
      if (onChanged) await onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  function openStart(slug) {
    setStarting(slug);
    setAudienceId(0);
    setTagFilters([]);
    setMatchMode("any");
    setPreview(null);
    setResult(null);
    setError("");
  }

  /** Who would be enrolled - read straight from the audience + tag chosen. */
  useEffect(() => {
    if (!starting || !audienceId) { setPreview(null); return undefined; }
    let live = true;
    api.audienceContacts(audienceId, tagFilters, matchMode)
      .then((d) => { if (live) setPreview(d); })
      .catch(() => { if (live) setPreview(null); });
    return () => { live = false; };
  }, [starting, audienceId, tagFilters, matchMode]);

  async function startJourney(c) {
    if (!audienceId) return;
    const aud = audiences.find((a) => a.id === audienceId);
    const n = preview?.count ?? 0;
    if (!window.confirm(
      `Start “${c.title}” for ${n} recipient(s) from “${aud?.name}”?\n\n`
      + `Email 1 goes out immediately. Every later step follows automatically from each `
      + `person's own opens and clicks - you do not send them yourself.`,
    )) return;
    setEnrolling(true);
    setError("");
    try {
      const res = await api.enrollSequence(c.slug, {
        audience_id: audienceId,
        tag_filter: tagFilters.join(","),
        tag_match: matchMode,
      });
      const counts = {
        queued: res.queued || 0,
        already_enrolled: res.already_enrolled || 0,
        skipped_suppressed: res.skipped_suppressed || 0,
      };
      setResult({ counts, source: res.source, enrolled: res.requested || n });
      if (onChanged) await onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setEnrolling(false);
    }
  }

  const pct = (v) => (v === null || v === undefined ? "-" : `${v}%`);
  const tagOptions = Object.entries(
    (audiences.find((a) => a.id === audienceId) || {}).tags || {},
  ).map(([tag, contacts]) => ({ tag, contacts }));

  return (
    <>
      {error && <div className="note n-warn" style={{ margin: "0 18px 10px" }}>⚠ {error}</div>}
      <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>Send</th><th>Type</th><th>Status</th>
            <th className="num-cell" title="Every image load, the way Mailchimp counts an open. A mail provider preloading the image counts too, so a mailbox can show 1 even if nobody read it. Clicks are the dependable signal.">
              Opens
            </th>
            <th className="num-cell">Clicks</th>
            <th className="num-cell" title="Confirmed by the mail provider. Shown as 'not confirmed' until the provider reports the message arrived; it is never inferred from a successful hand-over.">Delivery</th>
            <th className="ta-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map((c) => {
            const r = c.rates || {};
            const isJourney = (c.emails?.length || 0) > 1;
            const enrollment = c.enrollment || {};
            const hasEnrollment = !!enrollment.exists;
            const running = isJourney && (["live", "sending", "paused"].includes(c.status) || hasEnrollment);
            const canStart = isJourney && !hasEnrollment
              && ["draft", "ready", "approved"].includes(c.status);
            return (
              <Fragment key={c.slug}>
                <tr>
                  <td>
                    <Link href={`/campaigns/${c.slug}`}>
                      <div className="cell-title">
                        <b>{c.title}</b>
                        <span className="mono">
                          {hasEnrollment ? (
                            `${enrollment.enrolled} enrolled · ${enrollment.sends} email${enrollment.sends === 1 ? "" : "s"} sent`
                            + (enrollment.pending_initial ? ` · ${enrollment.pending_initial} queued` : "")
                            + (enrollment.failed ? ` · ${enrollment.failed} failed` : "")
                          ) : (
                            `${c.audience_count ?? "-"} recipients · ${c.delivery?.sent_count ?? 0} tracked`
                            + (c.provider_message_id ? ` · ${c.provider_message_id}` : " · not sent yet")
                          )}
                        </span>
                      </div>
                    </Link>
                  </td>
                  <td>
                    {isJourney ? (
                      <span className="badge b-agent" title={c.sequence_type || ""}>
                        journey · {c.emails.length} emails
                      </span>
                    ) : (
                      <span className="badge b-neutral">one-off</span>
                    )}
                  </td>
                  <td><StatusBadge status={c.status} /></td>
                  <td className="mono num-cell">
                    <b>{pct(r.open_rate)}</b>
                    {r.unique_openers !== null && r.unique_openers !== undefined ? (
                      <div style={{ fontSize: 11, color: "var(--ink-3)", fontWeight: 400 }}>
                        {r.unique_openers} openers
                      </div>
                    ) : null}
                  </td>
                  <td className="mono num-cell">
                    <b>{pct(r.click_rate)}</b>
                    {r.unique_clickers !== null && r.unique_clickers !== undefined ? (
                      <div style={{ fontSize: 11, color: "var(--ink-3)", fontWeight: 400 }}>
                        {r.unique_clickers} clickers
                      </div>
                    ) : null}
                  </td>
                  <td
                    className="mono num-cell"
                    title={
                      r.delivery_rate === null || r.delivery_rate === undefined
                        ? "No delivery confirmation has arrived from the provider. The mail was handed over successfully, but only the provider can confirm it arrived, and its end point is currently not responding."
                        : "Confirmed by the provider."
                    }
                  >
                    {r.delivery_rate === null || r.delivery_rate === undefined
                      ? <span style={{ color: "var(--ink-4)" }}>not confirmed</span>
                      : (
                        <>
                          <b>{pct(r.delivery_rate)}</b>
                          {r.delivered !== null && r.delivered !== undefined ? (
                            <div style={{ fontSize: 11, color: "var(--ink-3)", fontWeight: 400 }}>
                              {r.delivered} delivered
                            </div>
                          ) : null}
                        </>
                      )}
                  </td>
                  <td>
                    <div className="actions-cell">
                    {canStart ? (
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={busy === c.slug}
                        onClick={() => openStart(c.slug)}
                      >
                        ▶ Start journey
                      </button>
                    ) : null}
                    {running ? (
                      <Link className="btn btn-secondary btn-sm"
                        href={`/sequences/${c.sequence_slug || "ibos-9-email-branch-framework"}`}>
                        Activity
                      </Link>
                    ) : null}
                    <Link className="btn btn-secondary btn-sm" href={`/campaigns/${c.slug}?tab=emails`}>
                      Edit
                    </Link>
                    <button
                      className="btn btn-danger btn-sm"
                      disabled={busy === c.slug}
                      onClick={() => remove(c)}
                      title="Move this send to Trash"
                    >
                      {busy === c.slug ? "Moving…" : "Trash"}
                    </button>
                    </div>
                  </td>
                </tr>

                {starting === c.slug ? (
                  <tr className="row-panel">
                    <td colSpan={7}>
                      <div className="rp-title">
                        ▶ Start “{c.title}” - a {c.emails.length}-email journey
                      </div>
                      <div className="rp-sub">
                        Email 1 is queued immediately and sent safely in the background. Every later step is
                        chosen from each recipient&apos;s own opens and clicks - you never send them by hand.
                      </div>

                      {result ? (
                        <div className="note n-success" style={{ margin: 0 }}>
                          <span>
                            <b>Journey queued</b> for {result.enrolled} recipient(s) from {result.source} -{" "}
                            {Object.entries(result.counts).map(([k, v]) => `${k}: ${v}`).join(" · ")}.
                            Watch them under <b>Activity</b>.
                          </span>
                        </div>
                      ) : (
                        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
                          <div style={{ flex: "1 1 240px" }}>
                            <div style={{ fontSize: 11.5, fontWeight: 700, marginBottom: 4 }}>WHO RECEIVES IT</div>
                            <select className="input" value={audienceId}
                              onChange={(e) => { setAudienceId(Number(e.target.value)); setTagFilters([]); }}>
                              <option value={0}>- Choose a saved audience -</option>
                              {audiences.map((a) => (
                                <option key={a.id} value={a.id}>{a.name} · {a.usable} usable</option>
                              ))}
                            </select>
                          </div>
                          {audienceId ? (
                            <div style={{ flex: "1 1 260px" }}>
                              <div style={{ fontSize: 11.5, fontWeight: 700, marginBottom: 4 }}>
                                NARROW BY TAG (OPTIONAL)
                              </div>
                              <TagSelect
                                options={tagOptions}
                                value={tagFilters}
                                onChange={setTagFilters}
                                placeholder="Everyone in this audience"
                                allowCreate={false}
                                matchMode={matchMode}
                                onMatchModeChange={setMatchMode}
                              />
                            </div>
                          ) : null}
                          <div style={{ flex: "0 0 auto", paddingTop: 18 }}>
                            <button className="btn btn-secondary" onClick={() => setStarting("")} disabled={enrolling}>
                              Cancel
                            </button>{" "}
                            <button className="btn btn-primary" onClick={() => startJourney(c)}
                              disabled={enrolling || !audienceId}>
                              {enrolling ? "Starting…" : `▶ Start for ${preview?.count ?? 0}`}
                            </button>
                          </div>
                        </div>
                      )}

                      {preview ? (
                        <div className="hint" style={{ marginTop: 8 }}>
                          {preview.count} will be enrolled
                          {preview.excluded ? ` · ${preview.excluded} excluded (unsubscribed or cannot receive mail)` : ""}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      </div>
    </>
  );
}
