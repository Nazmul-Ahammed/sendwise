"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "../../components/AppShell";
import TagSelect from "../../components/TagSelect";
import { usePager, Pager } from "../../components/Pagination";
import { api } from "../../lib/api";

function ago(iso) {
  if (!iso) return "-";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "-";
  const secs = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function until(iso) {
  if (!iso) return "-";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "-";
  const secs = Math.floor((then.getTime() - Date.now()) / 1000);
  if (secs <= 0) return "due now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `in ${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `in ${hrs}h`;
  return `in ${Math.floor(hrs / 24)}d`;
}

const STATUS_BADGE = {
  active: "b-agent",
  completed: "b-success",
  unsubscribed: "b-neutral",
  suppressed: "b-danger",
  failed: "b-danger",
};

export default function SequencesPage() {
  const router = useRouter();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [busy, setBusy] = useState(false);
  const [dryRun, setDryRun] = useState(null);
  // Which send the Activity table is showing. "all" merges every send, which is rarely
  // what anyone wants, so a specific send is selectable in one click.
  const [enrollCampaign, setEnrollCampaign] = useState("all");
  // "Run a sequence" panel
  const [runTarget, setRunTarget] = useState("");
  const [runAudience, setRunAudience] = useState(0);
  const [runTags, setRunTags] = useState([]);
  const [runMatch, setRunMatch] = useState("any");
  const [audiences, setAudiences] = useState([]);
  const [running, setRunning] = useState(false);
  const [enrollResult, setEnrollResult] = useState(null);
  const [health, setHealth] = useState(null);

  function load() {
    return Promise.all([
      api.sequenceOverview(),
      api.sequenceSelfCheck().catch(() => null),
    ])
      .then(([d, h]) => {
        setData(d);
        setHealth(h);
        const targets = d.run_targets || [];
        setRunTarget((cur) => (cur || (targets.length ? targets[0].slug : "")));
      })
      .catch((e) => setError(e.message));
  }

  /** Re-time any enrollment sitting on a clock its sequence does not define. */
  async function fixClocks() {
    setBusy(true);
    setError("");
    try {
      const res = await api.sequenceSelfCheckFix();
      say(res.fixed
        ? `Re-timed ${res.fixed} of ${res.of} enrollment(s) to their sequence's own delays.`
        : "Nothing needed re-timing.");
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  /** Enrol a saved audience (optionally tag-segmented) into the chosen sequence. */
  async function enrollNow() {
    if (!runTarget || !runAudience) return;
    const target = (data?.run_targets || []).find((r) => r.slug === runTarget);
    const aud = audiences.find((a) => a.id === runAudience);
    if (!window.confirm(
      `Enrol “${aud?.name}”${runTags.length ? ` (${runTags.join(runMatch === "all" ? " AND " : " OR ")})` : ""}\n`
      + `into “${target?.title || runTarget}” and send email 1 now?`,
    )) return;
    setRunning(true);
    setError("");
    setFlash("");
    setEnrollResult(null);
    try {
      const res = await api.enrollSequence(runTarget, {
        audience_id: runAudience,
        tag_filter: runTags.join(","),
        tag_match: runMatch,
      });
      const counts = {};
      (res.results || []).forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
      setEnrollResult({ campaign: res.campaign, source: res.source, counts, results: res.results || [] });
      await load();
      setFlash(`Enrolled from ${res.source} - ${Object.entries(counts).map(([k, v]) => `${k}: ${v}`).join(", ")}`);
      setTimeout(() => setFlash(""), 12000);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  useEffect(() => {
    load();
    api.audiences().then((rows) => setAudiences(Array.isArray(rows) ? rows : [])).catch(() => setAudiences([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Start a new sequence - one stage with one email, ready to edit. */
  async function createNew() {
    const name = window.prompt("Name for the new sequence?\n\nA new one starts with one stage and one email, then you build it out.");
    if (name === null) return;
    const clean = String(name).trim();
    if (!clean) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createSequence({ name: clean, description: "" });
      router.push(`/sequences/${created.slug}`);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  async function dispatch(isDry) {
    setBusy(true);
    setError("");
    setFlash("");
    setDryRun(null);
    try {
      const res = await api.dispatchSequences(isDry);
      if (isDry) {
        setDryRun(res);
      } else {
        await load();
        // A dispatch that found the lock held returns no counters, only a reason. Read
        // every bucket defensively: an absent array used to throw "cannot read
        // properties of undefined (reading 'length')" and hid the very message that
        // explained what had happened.
        if (res?.skipped) {
          setFlash(`Nothing dispatched - ${res.skipped}`);
        } else {
          const n = (v) => (Array.isArray(v) ? v.length : 0);
          const parts = [`${n(res?.sent)} sent`, `${n(res?.completed)} completed`];
          if (n(res?.blocked_suppressed)) parts.push(`${n(res.blocked_suppressed)} suppressed`);
          if (n(res?.paused)) parts.push(`${n(res.paused)} paused (send is in Trash)`);
          if (n(res?.failed)) parts.push(`${n(res.failed)} failed`);
          if (n(res?.parked)) parts.push(`${n(res.parked)} needs review`);
          setFlash(`Dispatch ran - ${parts.join(" · ")}`);
        }
        setTimeout(() => setFlash(""), 8000);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const t = data?.totals;
  const targets = data?.run_targets || [];
  // The sends that actually have enrollments, so the selector only ever lists something real.
  const campaignOptions = Array.from(
    new Set((data?.enrollments || []).map((e) => e.campaign))
  ).sort();
  const shownEnrollments = (data?.enrollments || []).filter(
    (e) => enrollCampaign === "all" || e.campaign === enrollCampaign
  );
  const enrollPager = usePager(shownEnrollments, 25);
  // Keep the selector valid if the chosen send disappears after a refresh.
  const campaignKey = campaignOptions.join(",");
  useEffect(() => {
    if (enrollCampaign !== "all" && data && !campaignOptions.includes(enrollCampaign)) {
      setEnrollCampaign("all");
    }
  }, [data, enrollCampaign, campaignKey]);
  const target = targets.find((r) => r.slug === runTarget) || null;

  return (
    <AppShell active="/sequences">
      <div className="page-head">
        <div>
          <div className="crumb">Automation / <b>Sequences</b></div>
          <h1>Sequences</h1>
        </div>
        <div className="top-actions">
          <button className="btn btn-secondary" disabled={busy} onClick={createNew}>
            + New sequence
          </button>
          <button className="btn btn-secondary" disabled={busy} onClick={() => dispatch(true)}>
            {busy ? "Working…" : "Preview next step"}
          </button>
          <button className="btn btn-primary" disabled={busy} onClick={() => dispatch(false)}>
            {busy ? "Working…" : "Run dispatch now"}
          </button>
        </div>
      </div>

      <div className="note n-info" style={{ marginBottom: 14 }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
        <span>
          Each recipient is sent with its own tracking id, so branching uses <b>real open and click data</b>.
          Each sequence carries its <b>own delays</b> - the wait shown in its editor is the wait that is used.
          The scheduler checks every <b>15 minutes</b>, so a step due between ticks goes out on the next one
          (a 5-minute step in a test sequence can therefore take up to ~15 minutes).
          Hard bounces, spam complaints, and {data?.soft_bounce_limit ?? 3}+ soft bounces are suppressed automatically.
        </span>
      </div>

      {error && <div className="note n-warn">⚠ {error}</div>}
      {flash && <div className="note n-success">✓ {flash}</div>}

      {/* Self-check: is any journey waiting on a clock its sequence does not define? */}
      {health && !health.ok ? (
        <div className="note n-warn" style={{ marginBottom: 14 }}>
          <span>
            <b>{health.problems.length} enrollment(s) are waiting far longer than their sequence says.</b>{" "}
            This is invisible from the outside - nothing errors, the journey just sleeps.{" "}
            {health.problems.slice(0, 3).map((p) => (
              <span key={`${p.campaign}-${p.email}`} className="mono" style={{ fontSize: 11.5, marginRight: 8 }}>
                {p.email} @ {p.campaign} ({p.ref}): waiting {p.actual}, sequence says {p.expected}
              </span>
            ))}
            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 6 }}
              onClick={fixClocks} disabled={busy}>
              {busy ? "Fixing…" : "Fix the clocks"}
            </button>
          </span>
        </div>
      ) : null}

      {/* ── How this works ───────────────────────────────────────────── */}
      {/* Read once, then noise. Kept one tap away so the work starts at the top. */}
      <details className="disclosure">
        <summary>New here? How a sequence runs</summary>
        <div className="d-body">
          <div className="howto">
            <div className="step">
              <span className="badge b-agent" style={{ flex: "0 0 auto" }}>1 · SEQUENCE</span>
              <p>
                The <b>journey</b> - stages, emails, delays and the branch rules. A template,
                with no recipients in it. Edit one under <b>Sequence templates</b> below.
              </p>
            </div>
            <div className="step">
              <span className="badge b-neutral" style={{ flex: "0 0 auto" }}>2 · SEND</span>
              <p>
                A send <b>uses</b> a sequence. Create it in <b>Sends → New send</b> and pick
                “9 emails, branched”. The send copies the journey and holds the recipients.
              </p>
            </div>
            <div className="step">
              <span className="badge b-success" style={{ flex: "0 0 auto" }}>3 · RUN</span>
              <p>
                <b>Enrol</b> a saved audience into that send, just below. Email 1 goes out
                immediately; every later step is chosen from that person&apos;s own opens and
                clicks, automatically.
              </p>
            </div>
          </div>
          <p style={{ marginTop: 14 }}>
            You never send step 2, 3 or 4 by hand - the scheduler does it. Your job is only:
            pick a journey, pick who receives it, then watch the results.
          </p>
        </div>
      </details>

      {/* ── Run a sequence ───────────────────────────────────────────── */}
      <div className="card">
        <div className="card-h">
          <h3>Run a sequence</h3>
          <span className="hint">pick a sequence send → pick who receives it → enrol</span>
        </div>

        {targets.length === 0 ? (
          <div className="note n-info" style={{ margin: 0 }}>
            <span>
              No sequence send exists yet. Create one in <b>Sends → New send</b> and choose the
              <b> 9-email branched flow</b>, then come back here to enrol recipients.
            </span>
          </div>
        ) : (
          <div className="stack">
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Sequence send</label>
              <select
                className="input"
                value={runTarget}
                onChange={(e) => setRunTarget(e.target.value)}
                disabled={running}
              >
                <option value="">- Choose a sequence -</option>
                {targets.map((r) => (
                  <option key={r.slug} value={r.slug}>
                    {r.title || r.slug} · {r.emails} emails
                    {r.ready ? "" : ` · ${r.missing_copy.length} missing copy`}
                  </option>
                ))}
              </select>
              {target ? (
                <div className="hint" style={{ marginTop: 6 }}>
                  refs {target.refs.join(" → ")} · <b>{target.enrolled}</b> enrolled ({target.active} active)
                  {target.ready
                    ? ""
                    : ` · ⚠ ${target.missing_copy.join(", ")} still has no copy - open the send and use “Draft with Agent” first`}
                </div>
              ) : null}
            </div>

            <div className="field" style={{ marginBottom: 0 }}>
              <label>Who receives it</label>
              {audiences.length === 0 ? (
                <div className="hint">
                  No saved audiences yet - create one under <b>Automation → Audiences</b>.
                </div>
              ) : (
                <select
                  className="input"
                  value={runAudience}
                  onChange={(e) => { setRunAudience(Number(e.target.value)); setRunTags([]); }}
                  disabled={running}
                >
                  <option value={0}>- Choose a saved audience -</option>
                  {audiences.map((a) => (
                    <option key={a.id} value={a.id}>{a.name} · {a.usable} usable</option>
                  ))}
                </select>
              )}
              {runAudience ? (() => {
                const a = audiences.find((x) => x.id === runAudience);
                const opts = Object.entries(a?.tags || {}).map(([tag, contacts]) => ({ tag, contacts }));
                return (
                  <div style={{ marginTop: 10 }}>
                    <TagSelect
                      options={opts}
                      value={runTags}
                      onChange={setRunTags}
                      placeholder="Everyone in this audience"
                      allowCreate={false}
                      matchMode={runMatch}
                      onMatchModeChange={setRunMatch}
                      hint={opts.length ? "Optional - narrow this audience to a tag segment." : ""}
                    />
                  </div>
                );
              })() : null}
            </div>

            <div className="inline">
              <button
                className="btn btn-primary"
                onClick={enrollNow}
                disabled={running || !runTarget || !runAudience}
              >
                {running ? "Enrolling…" : "▶ Enrol &amp; send email 1 now"}
              </button>
              <span className="hint" style={{ maxWidth: 460 }}>
                Sends the first email immediately. Later steps run on the {data?.gap_days ?? 3}-day schedule
                and branch on real opens and clicks - you do not need to do anything else.
              </span>
            </div>

            {enrollResult ? (
              <div className="note n-success" style={{ margin: 0 }}>
                <span>
                  Enrolled from <b>{enrollResult.source}</b> -{" "}
                  {Object.entries(enrollResult.counts).map(([k, v]) => `${k}: ${v}`).join(" · ")}
                  {enrollResult.counts.skipped_suppressed
                    ? " (suppressed addresses were blocked, as they always are)"
                    : ""}.
                </span>
              </div>
            ) : null}
          </div>
        )}
      </div>
      {!data && !error && <div className="loading"><span className="spinner" /> Loading sequences…</div>}

      {data && (
        <>
          <div className="grid kpis" style={{ marginBottom: 16 }}>
            {[
              { l: "Enrolled", v: t.enrolled, s: "total recipients" },
              { l: "Active", v: t.active, s: "still moving" },
              { l: "Completed", v: t.completed, s: "finished the flow" },
              { l: "Unsubscribed", v: t.unsubscribed, s: "opted out" },
              { l: "Suppressed", v: t.suppressed_addresses, s: "bounce / complaint / manual" },
            ].map((k, i) => (
              <div className="kpi ok" key={i}>
                <div className="k-label">{k.l}</div>
                <div className="k-value">{k.v}</div>
                <div className="k-sub">{k.s}</div>
              </div>
            ))}
          </div>

          <div className="card">
            <div className="card-h">
              <h3>Sequence templates</h3>
              <Link href="/sequences/ibos-9-email-branch-framework" className="btn btn-secondary" style={{ textDecoration: "none" }}>
                ✎ Edit a sequence
              </Link>
            </div>
            {data.templates.length === 0 ? (
              <div className="hint">No sequence templates found.</div>
            ) : (
              data.templates.map((tpl) => (
                <div key={tpl.slug} style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
                  <div className="inline">
                    <Link href={`/sequences/${tpl.slug}`} style={{ fontWeight: 700, color: "var(--brand-600)" }}>
                      {tpl.name}
                    </Link>
                    <span className="badge b-neutral">{tpl.stages} stages</span>
                    <span className="badge b-neutral">{tpl.emails.length} emails</span>
                    <Link
                      href={`/campaigns/new?sequence=${encodeURIComponent(tpl.slug)}`}
                      className="btn btn-primary btn-sm"
                      style={{ textDecoration: "none" }}
                    >
                      ▶ Use in a send
                    </Link>
                    <Link href={`/sequences/${tpl.slug}`} className="btn btn-secondary btn-sm" style={{ textDecoration: "none" }}>
                      ✎ Edit journey
                    </Link>
                  </div>
                  <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 5 }}>
                    {tpl.slug} · refs: {tpl.emails.join(", ")}
                    {tpl.gap_summary ? <> · waits: {tpl.gap_summary}</> : null}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 3 }}>{tpl.trigger}</div>
                </div>
              ))
            )}
            <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 14, lineHeight: 1.9 }}>
              E1 → opened? <b>E2</b> : <b>E3</b><br />
              E2 → clicked? <b>E4</b> : opened? <b>E5</b> : <b>E6</b><br />
              E3 → clicked? <b>E7</b> : opened? <b>E8</b> : <b>E9</b><br />
              E4–E9 end the sequence
            </div>
            <div className="hint" style={{ marginTop: 10 }}>
              <b>▶ Use in a send</b> starts a normal send that follows this journey - same place you build any
              other campaign, in <b>Sends → New send</b>. <b>✎ Edit journey</b> changes the stages, delays and
              branch rules themselves.
            </div>
          </div>

          {/* One table for the whole journey. Previously the enrolled-recipient list
              mixed every campaign together and sat in a column of separate tables, so
              answering "what is happening in this send" meant reading three of them. */}
          <div className="card">
            <div className="card-h">
              <div>
                <h3>Activity</h3>
                <span className="hint">
                  {shownEnrollments.length} recipient(s)
                  {enrollCampaign !== "all" ? ` in ${enrollCampaign}` : " across all sends"}
                </span>
              </div>
              <div className="inline">
                <select
                  className="input"
                  style={{ minWidth: 260 }}
                  value={enrollCampaign}
                  onChange={(e) => setEnrollCampaign(e.target.value)}
                >
                  <option value="all">All sends ({data.enrollments.length})</option>
                  {campaignOptions.map((c) => (
                    <option key={c} value={c}>
                      {c} ({data.enrollments.filter((e) => e.campaign === c).length})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* The preview result lands here rather than in a table of its own. */}
            {dryRun && (
              <div className="note n-info" style={{ marginTop: 0, marginBottom: 14 }}>
                <span>
                  <b>Preview - nothing was sent.</b>{" "}
                  {(dryRun.plan || []).length === 0
                    ? "No enrollment is due right now."
                    : <>{(dryRun.plan || []).length} would move next:{" "}
                        {Array.from(new Set((dryRun.plan || []).map((p) => `${p.from_ref}→${p.next_ref}`)))
                          .join(", ")}</>}
                </span>
              </div>
            )}

            {data.enrollments.length === 0 ? (
              <div className="empty">
                <b>Nobody is enrolled yet</b>
                <p>
                  Open a send that uses a sequence, then enrol an audience from its
                  <b> Engagement</b> tab. Email 1 goes out immediately.
                </p>
              </div>
            ) : shownEnrollments.length === 0 ? (
              <div className="empty">
                <b>Nobody enrolled in this send</b>
                <p>Choose another send above, or “All sends”.</p>
              </div>
            ) : (
              <>
              <div className="table-wrap">
                <table className="t">
                  <thead>
                    <tr>
                      <th>Recipient</th>
                      {enrollCampaign === "all" && <th>Send</th>}
                      <th>Status</th>
                      <th>On email</th>
                      <th>Branch taken</th>
                      <th className="num-cell">Sent</th>
                      <th>Last sent</th>
                      <th>Next due</th>
                    </tr>
                  </thead>
                  <tbody>
                    {enrollPager.rows.map((e, i) => (
                      <tr key={`${e.campaign}-${e.email}-${i}`}>
                        <td className="mono" style={{ fontSize: 12 }}>{e.email}</td>
                        {enrollCampaign === "all" && (
                          <td>
                            <Link href={`/campaigns/${e.campaign}?tab=engagement`} style={{ color: "var(--brand-600)", fontWeight: 600 }}>
                              {e.campaign}
                            </Link>
                          </td>
                        )}
                        <td><span className={`badge ${STATUS_BADGE[e.status] || "b-neutral"}`}>{e.status}</span></td>
                        <td><b>{e.current_ref || "-"}</b></td>
                        <td>{e.branch || "-"}</td>
                        <td className="num-cell">{e.sends}</td>
                        <td className="mono" style={{ fontSize: 11 }}>{ago(e.last_sent_at)}</td>
                        <td className="mono" style={{ fontSize: 11 }}>{until(e.next_due_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager pager={enrollPager} label="recipients" />
              </>
            )}
          </div>

          <div className="card">
            <div className="card-h">
              <h3>Suppression list</h3>
              <span className="hint">{data.suppressions.length} address(es) - never emailed again</span>
            </div>
            {data.suppressions.length === 0 ? (
              <div className="hint">No suppressed addresses. Hard bounces, spam complaints and 3+ soft bounces land here automatically.</div>
            ) : (
              <table className="t">
                <thead><tr><th>Email</th><th>Reason</th><th>Source</th><th>Added</th></tr></thead>
                <tbody>
                  {data.suppressions.map((s, i) => (
                    <tr key={`${s.email}-${i}`}>
                      <td className="mono" style={{ fontSize: 12 }}>{s.email}</td>
                      <td>{s.reason}</td>
                      <td>{s.source || "-"}</td>
                      <td className="mono" style={{ fontSize: 11 }}>{ago(s.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </AppShell>
  );
}
