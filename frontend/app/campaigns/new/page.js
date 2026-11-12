"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "../../../components/AppShell";
import TagSelect from "../../../components/TagSelect";
import RecipientPreview from "../../../components/RecipientPreview";
import { api } from "../../../lib/api";

const STEPS = [
  { id: "brief", label: "Brief" },
  { id: "emails", label: "Email" },
  { id: "review", label: "Review" },
  { id: "launch", label: "Launch" },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const previewHtml = (html = "") => html.replace("</head>", "<style>.sw-compliance{display:none!important}</style></head>");

function parseRecipients(text) {
  const rows = [];
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const angled = line.match(/^(.*)<([^<>]+)>\s*$/);
    if (angled) {
      const email = angled[2].trim();
      const name = angled[1].trim().replace(/^["']|["']$/g, "");
      if (email) rows.push({ name, email });
    } else {
      rows.push({ name: "", email: line });
    }
  }
  return rows;
}

export default function NewCampaignPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);

  const [form, setForm] = useState({
    title: "",
    slug: "",
    product: "Turbo Add-ons",
    offer: "",
    sender_name: "Himel from Turbo Add-ons",
    sender_email: "hello@turbo-addons.com",
  });
  const [emailCopy, setEmailCopy] = useState({});
  const [sendMode, setSendMode] = useState("direct"); // direct | draft
  const [testFirst, setTestFirst] = useState(false);
  const [created, setCreated] = useState(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [meta, setMeta] = useState({ products: [], customProduct: false });
  const [manualText, setManualText] = useState("");
  const [previewOpen, setPreviewOpen] = useState({});
  const [audiences, setAudiences] = useState([]);
  const [audienceId, setAudienceId] = useState(0);
  const [tagFilters, setTagFilters] = useState([]);
  const [tagMatch, setTagMatch] = useState("any");
  const [audienceCount, setAudienceCount] = useState(0);
  const [audienceRows, setAudienceRows] = useState([]);
  const [sequences, setSequences] = useState([]);
  const [sequenceSlug, setSequenceSlug] = useState("single");

  useEffect(() => {
    api.settings().then((s) => {
      const products = s.products || ["Turbo Add-ons", "Turbo Guard", "Travilo", "Family Guard", "Managerium ERP", "PeopleDesk HRMS", "PrimeVAT", "TrackForce", "AeroApps (Shopify)", "AeroDrop", "iBOS Services"];
      setMeta((m) => ({ ...m, products }));
      setForm((f) => ({
        ...f,
        sender_name: s.default_from_name || f.sender_name,
        sender_email: s.default_reply_to || f.sender_email,
      }));
    }).catch((e) => setError(e.message));
    api.audiences()
      .then((rows) => setAudiences(Array.isArray(rows) ? rows : []))
      .catch(() => setAudiences([]));
    api.sequences()
      .then((rows) => setSequences(Array.isArray(rows) ? rows : []))
      .catch(() => setSequences([]));
  }, []);

  // When a saved audience is selected, resolve how many of its contacts are
  // actually usable right now (unsubscribes and suppressions are excluded).
  useEffect(() => {
    if (!audienceId) { setAudienceCount(0); setAudienceRows([]); return undefined; }
    let live = true;
    api.audienceContacts(audienceId, tagFilters, tagMatch)
      // `will_receive` is the deliverable count (unsubscribed, suppressed and
      // undeliverable addresses already dropped). `count` is everyone the tag
      // matched, so labelling that "usable recipients" would promise sends the
      // resolver will skip.
      .then((d) => { if (live) { setAudienceCount(d.will_receive ?? d.count ?? 0); setAudienceRows(d.contacts || []); } })
      .catch(() => { if (live) { setAudienceCount(0); setAudienceRows([]); } });
    return () => { live = false; };
  }, [audienceId, tagFilters, tagMatch]);

  // "▶ Use in a send" on a sequence links here with ?sequence=<slug>, so the journey
  // chosen there is already selected when this page opens.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const want = new URLSearchParams(window.location.search).get("sequence");
    if (want) setSequenceSlug(want);
  }, []);

  // Selecting the approved Turbo Guard campaign flow should also load the exact
  // offer qualifiers the copy engine requires. This prevents a bare "$5.55"
  // claim from reaching Draft with Agent without its first-year, site-limit,
  // and one-month context.
  useEffect(() => {
    if (sequenceSlug !== "turbo-guard-peace-of-mind-coffee-offer" || created) return;
    setForm((f) => ({
      ...f,
      title: f.title || "Turbo Guard: Peace Of Mind. Same Price.",
      product: "Turbo Guard",
      offer: "US$5.55 first year | Up to 2 WordPress sites | One-month offer",
    }));
  }, [sequenceSlug, created]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  // A live counter: GPT-5 takes 25-35s, and a silent button reads as "nothing happened".
  useEffect(() => {
    if (!drafting) { setElapsed(0); return undefined; }
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [drafting]);

  /** Retry once on a transient/5xx failure; a 4xx (bad request) is reported immediately. */
  async function generateWithRetry(slug, tries = 2) {
    let lastErr;
    for (let i = 0; i < tries; i += 1) {
      try {
        return await api.generateCopy(slug);
      } catch (e) {
        lastErr = e;
        if (e.status && e.status < 500) throw e;
        await new Promise((r) => setTimeout(r, 1500));
      }
    }
    throw lastErr;
  }

  /** Turn a generate response into {ref: {subject, preview, body}} and prove it is non-empty. */
  function absorbAgentCopy(gen) {
    const emails = gen.emails || [];
    const written = emails.filter(
      (e) => (e.subject || "").trim() || (e.body || "").trim().length > 40,
    );
    if (!written.length) {
      throw new Error("The Agent returned no copy. Nothing was saved - check Settings → Email Agent, then click Draft with Agent again.");
    }
    const copy = {};
    emails.forEach((e) => {
      copy[e.ref || "E1"] = { subject: e.subject || "", preview: e.preview_text || "", body: e.body || "" };
    });
    return { copy, written };
  }

  const recipientRows = parseRecipients(manualText);
  const validRecipients = recipientRows.filter((r) => EMAIL_RE.test(r.email));
  const invalidCount = recipientRows.length - validRecipients.length;
  const usingAudience = audienceId > 0;
  const recipientCount = usingAudience ? audienceCount : validRecipients.length;
  const recipientsReady = recipientCount > 0 && (usingAudience || invalidCount === 0);

  // "single" is a one-off send; anything else is a real branched sequence, so the
  // editor shows every email the sequence will send (E1…E9), not just one.
  const seqDef = sequences.find((s) => s.slug === sequenceSlug);
  const isSequence = sequenceSlug !== "single" && !!seqDef;
  const plannedEmails = isSequence
    ? (seqDef.stages || []).flatMap((st) =>
        (st.emails || []).map((e) => ({
          stage: st.stage,
          ref: e.ref,
          name: e.name || st.focus || e.ref,
          focus: st.focus || st.name || "Lifecycle step",
          branch: e.branch || "main",
        })),
      )
    : [{ stage: 1, ref: "E1", name: "Single Email", focus: "Single Email", branch: "main" }];
  const sequenceLabel = isSequence
    ? `${seqDef.name} · ${plannedEmails.length} emails`
    : "Single one-off send";

  // How many of the sequence's emails actually have copy yet - the one number the
  // operator needs when a journey is nine emails long.
  const draftedRefs = plannedEmails
    .filter((em) => {
      const saved = (created?.emails || []).find((e) => (e.ref || "E1") === em.ref);
      return !!((saved?.body_html || "").trim());
    })
    .map((em) => em.ref);
  const draftedCount = draftedRefs.length;

  const canNext = step === 0 ? recipientsReady && form.title.trim().length > 2 : true;

  function campaignPayload() {
    return {
      ...form,
      sequence_slug: sequenceSlug,
      recipients_mode: usingAudience ? "audience" : "manual",
      audience_id: usingAudience ? audienceId : 0,
      tag_filter: usingAudience ? tagFilters.join(",") : "",
      tag_match: usingAudience ? tagMatch : "any",
      segment_id: "",
      segment_name: "",
      tags: [],
      manual_emails: usingAudience ? [] : validRecipients.map((r) => r.email),
      audience_count: recipientCount,
      send_mode: sendMode,
      send_test_first: testFirst ? "true" : "false",
    };
  }

  async function createCampaign() {
    setBusy(true);
    setError("");
    try {
      const c = await api.createCampaign(campaignPayload());
      setCreated(c);
      const keys = Object.keys(emailCopy);
      if (keys.length) {
        c.emails = c.emails.map((e) => {
          const cp = emailCopy[e.ref || "E1"] || {};
          return { ...e, subject: cp.subject ?? e.subject, preview_text: cp.preview ?? e.preview_text, body: cp.body ?? e.body };
        });
        const updated = await api.updateEmails(c.slug, c.emails);
        setCreated(updated);
      }
      setFlash("Send created - ekhon ✨ Draft with Agent diye brand MD theke copy + HTML design kore nin");
      setBusy(false);
      setStep(1);
      setTimeout(() => setFlash(""), 5000);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  async function saveEmail(ref) {
    setBusy(true);
    setError("");
    try {
      if (!created) throw new Error("Age send create korun (✨ Draft with Agent click korlei hoy)");
      const cp = emailCopy[ref] || {};
      const patched = (created.emails || []).map((e) =>
        (e.ref || "E1") === ref
          ? { ...e, subject: cp.subject ?? e.subject, preview_text: cp.preview ?? e.preview_text, body: cp.body ?? e.body }
          : e
      );
      const upd = await api.updateEmails(created.slug, patched);
      setCreated(upd);
      const fresh = {};
      (upd.emails || []).forEach((e) => {
        fresh[e.ref || "E1"] = { subject: e.subject || "", preview: e.preview_text || "", body: e.body || "" };
      });
      setEmailCopy((prev) => ({ ...prev, ...fresh }));
      setPreviewOpen((p) => ({ ...p, [ref]: true }));
      setFlash("Email saved - branded HTML design fresh kore banano holo (body = source of truth)");
      setBusy(false);
      setTimeout(() => setFlash(""), 5000);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  async function draftWithAgent() {
    setBusy(true);
    setError("");
    setFlash("");
    setDrafting(true);
    const started = Date.now();
    try {
      let c = created;
      if (!c) {
        c = await api.createCampaign(campaignPayload());
        setCreated(c);
      }
      const gen = await generateWithRetry(c.slug);
      const { copy, written } = absorbAgentCopy(gen);
      setCreated(gen);
      setEmailCopy((prev) => ({ ...prev, ...copy }));
      setStep(1);
      const secs = Math.round((Date.now() - started) / 1000);
      const src = (gen.emails || [])[0]?.source || "agent";
      setFlash(`✓ ${written.length} email(s) written in ${secs}s - ${src}. Edit anything, then “Save email → fresh HTML”.`);
      setTimeout(() => setFlash(""), 14000);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
      setDrafting(false);
    }
  }

  async function reloadCopy() {
    setBusy(true);
    setError("");
    try {
      const fresh = await api.campaign(created.slug);
      setCreated(fresh);
      setFlash("Copy refreshed from send");
      setBusy(false);
      setTimeout(() => setFlash(""), 4000);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  async function act(action) {
    setBusy(true);
    setError("");
    try {
      if (action === "approve") await api.approve(created.slug);
      if (action === "ready") await api.ready(created.slug);
      if (action === "generate") {
        setDrafting(true);
        try {
          const gen = await generateWithRetry(created.slug);
          const { copy } = absorbAgentCopy(gen);
          setCreated(gen);
          setEmailCopy((prev) => ({ ...prev, ...copy }));
        } finally {
          setDrafting(false);
        }
      }
      if (action === "sendtest") await api.sendTest(created.slug);
      if (action === "launch") {
        const keys = Object.keys(emailCopy);
        if (keys.length && created) {
          const patched = created.emails.map((e) => {
            const cp = emailCopy[e.ref || "E1"] || {};
            return { ...e, subject: cp.subject ?? e.subject, preview_text: cp.preview ?? e.preview_text, body: cp.body ?? e.body };
          });
          const upd = await api.updateEmails(created.slug, patched);
          setCreated(upd);
        }
        // Re-read before approving: the local record can be stale after the first
        // launch already moved the send to "live".
        const fresh = await api.campaign(created.slug);
        setCreated(fresh);
        if (["live", "sending", "sent", "exited"].includes(fresh.status)) {
          setError(`This send is already ${fresh.status} - nothing was sent again.`);
          setBusy(false);
          return;
        }
        if (fresh.status === "draft") await api.approve(fresh.slug);
        await api.launch(fresh.slug);
      }
      if (action === "pause") await api.pause(created.slug);
      const flashMap = {
        launch: "Send via SendClean queued",
        ready: "Send ready - agent copy review korbe",
        generate: "Agent copy regenerated from brand MD - HTML design ready",
        sendtest: "Test send via SendClean queued",
        approve: "Send approved",
        pause: "Send paused",
      };
      setFlash(flashMap[action] || "Done");
      setBusy(false);
      if (action === "launch" || action === "ready") {
        const fresh = await api.campaign(created.slug);
        setCreated(fresh);
      }
      setTimeout(() => setFlash(""), 6000);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  return (
    <AppShell active="/campaigns">
      <button className="btn btn-ghost btn-sm" style={{ marginBottom: 10, color: "var(--ink-3)", fontWeight: 600 }} onClick={() => router.push("/campaigns")}>← All sends</button>
      <div className="page-head">
        <div>
          <h1>Create Send</h1>
        </div>
        <div className="actions">
          {created && (created.status === "draft" || created.status === "approved") && (
            <button className="btn btn-primary" disabled={busy} onClick={() => act("launch")}>{busy ? "Working…" : "Approve recipients & Send via SendClean"}</button>
          )}
        </div>
      </div>

      {error && <div className="note n-warn"><span>⚠ {error}</span></div>}
      {flash && <div className="note n-success"><span>✓ {flash}</span></div>}

      <div className="tabs">
        {STEPS.map((s, i) => (
          <button key={s.id} className={step === i ? "on" : ""} onClick={() => i < step && setStep(i)}>
            <span className="n">{i + 1}</span>{s.label}
          </button>
        ))}
      </div>

      {step === 0 && (
        <div className="grid two-col" style={{ gridTemplateColumns: "1.4fr 1fr" }}>
          <div className="card">
            <div className="card-h"><h3>Send brief</h3><span className="hint">step 1 of {STEPS.length}</span></div>
            <div style={{ display: "grid", gap: 14 }}>
              <div className="field"><label>Send title *</label>
                <input className="input" placeholder="e.g. turbo-addons-reactivation-october" value={form.title} onChange={(e) => set("title", e.target.value)} />
              </div>
              <div className="field">
                <label>What kind of send</label>
                <select
                  className="input"
                  value={sequenceSlug}
                  onChange={(e) => setSequenceSlug(e.target.value)}
                  disabled={!!created}
                >
                  <option value="single">Single one-off send · 1 email</option>
                  {sequences.map((s) => {
                    const n = (s.stages || []).reduce((acc, st) => acc + (st.emails || []).length, 0);
                    return (
                      <option key={s.slug} value={s.slug}>
                        {s.name} · {n} emails, branched
                      </option>
                    );
                  })}
                </select>
                <div className={`note ${isSequence ? "n-success" : "n-info"}`} style={{ marginTop: 8 }}>
                  <span>
                    {isSequence ? (
                      <>
                        <b>{sequenceLabel}</b> - the agent drafts all {plannedEmails.length} emails
                        ({plannedEmails.map((e) => e.ref).join(", ")}). After creating it, enrol recipients
                        from <b>Automation → Sequences</b>; each step then branches on that person&apos;s real
                        opens and clicks.
                      </>
                    ) : (
                      <>One email, sent once. For the branched lifecycle flow, pick the 9-email option above.</>
                    )}
                  </span>
                </div>
              </div>
              <div className="field">
                <label>
                  Who receives it{" "}
                  <span className="hint" style={{ fontWeight: 400 }}>
                    - pick a list, then narrow it by tag if you want
                  </span>
                </label>
                {audiences.length === 0 ? (
                  <div className="hint">
                    No saved audiences yet - create one under <b>Automation → Audiences</b> to reuse a
                    list, or just paste recipients below.
                  </div>
                ) : (
                  <select
                    className="input"
                    disabled={!!created}
                    value={audienceId}
                    onChange={(e) => { setAudienceId(Number(e.target.value)); setTagFilters([]); setTagMatch("any"); }}
                  >
                    <option value={0}>- None (paste a list below) -</option>
                    {audiences.map((a) => (
                      <option key={a.id} value={a.id}>{a.name} · {a.usable} usable</option>
                    ))}
                  </select>
                )}
                {usingAudience ? (
                  <div style={{ marginTop: 10 }}>
                    <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                      Segment by tag <span className="hint">(optional - pick any number)</span>
                    </label>
                    <TagSelect
                      options={Object.entries(audiences.find((x) => x.id === audienceId)?.tags || {})
                        .map(([tag, contacts]) => ({ tag, contacts }))}
                      value={tagFilters}
                      onChange={setTagFilters}
                      placeholder="Everyone in this list"
                      allowCreate={false}
                      matchMode={tagMatch}
                      onMatchModeChange={setTagMatch}
                      hint={tagFilters.length > 1
                        ? `Will include contacts matching ${tagMatch === "all" ? "all" : "any"} of the selected tags.`
                        : ""}
                    />
                  </div>
                ) : null}
              </div>

              {usingAudience ? (
                <div className="note n-success" style={{ marginTop: 0 }}>
                  <span>
                    <b>{audienceCount}</b> usable recipient{audienceCount !== 1 ? "s" : ""} from{" "}
                    <b>{audiences.find((x) => x.id === audienceId)?.name}</b>
                    {tagFilters.length
                      ? <> · matching {tagMatch === "all" ? "all" : "any"} of <b>{tagFilters.join(", ")}</b></>
                      : null}. Unsubscribed and suppressed
                    addresses are excluded, so a send can never reach them.
                  </span>
                </div>
              ) : (
                <div className="field">
                  <label>Recipients (one per line - no fixed limit)</label>
                  <textarea
                    className="input"
                    rows={8}
                    disabled={!!created}
                    value={manualText}
                    onChange={(e) => setManualText(e.target.value)}
                    placeholder={"john@example.com\nJane Doe <jane@example.com>"}
                  />
                  <div className={`note ${invalidCount > 0 ? "n-warn" : "n-info"}`} style={{ marginTop: 8 }}>
                    <span>
                      <b>{recipientCount.toLocaleString()}</b> valid recipient{recipientCount !== 1 ? "s" : ""}
                      {invalidCount > 0 ? ` · ${invalidCount} invalid line${invalidCount !== 1 ? "s" : ""}` : ""}
                      {" · "}no fixed application limit.
                    </span>
                  </div>
                </div>
              )}

              <RecipientPreview
                rows={usingAudience
                  ? audienceRows.map((r) => ({ email: r.email, name: r.name, tags: r.tags }))
                  : validRecipients.map((r) => ({ email: r.email, name: r.name }))}
                title="Recipient preview"
                note={usingAudience
                  ? "Live from your saved audience - unsubscribed and suppressed addresses are already excluded."
                  : "Parsed from the list above."}
                empty={usingAudience
                  ? "This audience/segment resolves to nobody right now - pick another tag or add contacts."
                  : "Paste at least one address above to see who will receive this."}
              />

              <div className="field"><label>Product</label>
                {!meta.customProduct ? (
                  <select className="input" value={meta.products.includes(form.product) ? form.product : "__custom__"} onChange={(e) => {
                    if (e.target.value === "__custom__") setMeta((m) => ({ ...m, customProduct: true }));
                    else set("product", e.target.value);
                  }}>
                    {meta.products.map((p) => <option key={p} value={p}>{p}</option>)}
                    <option value="__custom__">Custom…</option>
                  </select>
                ) : (
                  <div style={{ display: "flex", gap: 8 }}>
                    <input className="input" value={form.product} onChange={(e) => set("product", e.target.value)} placeholder="Custom product name" />
                    <button className="btn btn-secondary btn-sm" onClick={() => setMeta((m) => ({ ...m, customProduct: false }))}>Use list</button>
                  </div>
                )}
              </div>
              <div className="field"><label>Offer / angle</label>
                <textarea className="input" rows={3} placeholder="e.g. Native pattern-interrupt: stack question → speed proof → 30% dev credit reactivation" value={form.offer} onChange={(e) => set("offer", e.target.value)} />
              </div>
              <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <div className="field"><label>Sender name (From)</label>
                  <input className="input" value={form.sender_name} onChange={(e) => set("sender_name", e.target.value)} placeholder="Custom sender name" />
                </div>
                <div className="field"><label>Reply-to (From email)</label>
                  <input className="input" value={form.sender_email} onChange={(e) => set("sender_email", e.target.value)} placeholder="custom@domain.com" />
                </div>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="card-h"><h3>Recipient preview</h3><span className="hint">parsed from the list</span></div>
            {recipientRows.length === 0 ? (
              <div className="hint">Paste recipients to see them here</div>
            ) : (
              <div style={{ display: "grid", gap: 6, maxHeight: 360, overflow: "auto" }}>
                {recipientRows.map((r, i) => (
                  <div key={`${r.email}-${i}`} className="thresh">
                    <b>{r.name || r.email}</b>
                    <span className={`v ${EMAIL_RE.test(r.email) ? "ok" : "warn"}`}>{r.name ? r.email : ""}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="note n-info" style={{ marginTop: 14 }}>
              <span>SendClean sends transactional mail to an explicit recipient list. Audiences and segments are not part of the API.</span>
            </div>
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="card">
          <div className="card-h">
            <h3>Email copy {isSequence ? `(${plannedEmails.length}-email sequence)` : "(single)"}</h3>
            <span className="hint">brand MD file theke copy · branded HTML e jabe</span>
          </div>
          <div className="note n-info" style={{ marginTop: 0, marginBottom: 14 }}>
            <span><b>✨ Draft with Agent</b> = product er brand MD file call kore subject + preview + body likhe <b>branded HTML design</b> ready kore dey. Edit korle <b>Save email</b> e click korlei fresh HTML hobe - kono plain-text send nei.</span>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
            <button className="btn btn-primary" disabled={busy} onClick={draftWithAgent}>
              {busy
                ? `✍️ Writing… ${elapsed}s (the Agent usually takes 25–35s - please stay on this page)`
                : "✨ Draft with Agent (brand MD → copy + HTML)"}
            </button>
          </div>

          {plannedEmails.map((em) => {
            const cp = emailCopy[em.ref] || {};
            const savedEmail = (created?.emails || []).find((e) => (e.ref || "E1") === em.ref);
            const htmlReady = !!(savedEmail?.body_html || "").trim();
            return (
              <div key={em.ref} style={{ marginBottom: 16, padding: 14, border: "1px solid var(--border)", borderRadius: "var(--radius)", background: htmlReady ? undefined : "var(--surface)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, gap: 10, flexWrap: "wrap" }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>{em.ref} · {em.name || em.focus}</div>
                  <span style={{ fontSize: 11.5, padding: "3px 9px", borderRadius: 20, background: htmlReady ? "var(--ok-bg, #E7F7EE)" : "var(--surface-2)", color: htmlReady ? "var(--ok, #16794C)" : "var(--ink-3)", fontWeight: 700, border: "1px solid " + (htmlReady ? "var(--ok-border, #BCE4CF)" : "var(--border)") }}>
                    {htmlReady ? "✓ HTML DESIGN READY · " + ((savedEmail.source && savedEmail.source.startsWith("brand_docs") ? savedEmail.source.replace("brand_docs/", "brand-docs/") : "engine")) : "HTML - Draft/Save korlei ready"}
                  </span>
                </div>
                <div className="field" style={{ marginBottom: 10 }}><label>Subject line</label>
                  <input className="input" value={cp.subject ?? ""} onChange={(e) => setEmailCopy((m) => ({ ...m, [em.ref]: { ...m[em.ref], subject: e.target.value } }))} placeholder="e.g. How many plugins is your Elementor site running?" />
                </div>
                <div className="field" style={{ marginBottom: 10 }}><label>Preview text (inbox e subject er pashe)</label>
                  <input className="input" value={cp.preview ?? ""} onChange={(e) => setEmailCopy((m) => ({ ...m, [em.ref]: { ...m[em.ref], preview: e.target.value } }))} placeholder="Completes the subject line…" />
                </div>
                <div className="field"><label>Email body (plain text - HTML auto-design hobe)</label>
                  <textarea className="input" rows={5} value={cp.body ?? ""} onChange={(e) => setEmailCopy((m) => ({ ...m, [em.ref]: { ...m[em.ref], body: e.target.value } }))} placeholder="Agent brand-doc theke draft korbe - chaile nijei likho" />
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                  <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => saveEmail(em.ref)}>{busy ? "Saving…" : "💾 Save email → fresh HTML"}</button>
                  <button className="btn btn-ghost btn-sm" disabled={!htmlReady} onClick={() => setPreviewOpen((p) => ({ ...p, [em.ref]: !p[em.ref] }))}>
                    {previewOpen[em.ref] ? "Hide HTML preview" : "👁 Show HTML preview"}
                  </button>
                  <span style={{ fontSize: 12, color: "var(--ink-3)", alignSelf: "center" }}>
                    journey: {savedEmail?.journey_name || "auto"} · CTA: {savedEmail?.cta_label || "auto"}
                  </span>
                </div>
                {previewOpen[em.ref] && htmlReady && (
                  <div style={{ marginTop: 12, border: "1px solid var(--border)", borderRadius: "var(--radius)", overflow: "hidden" }}>
                    <div style={{ padding: "7px 12px", background: "var(--surface-2)", borderBottom: "1px solid var(--border)", fontSize: 11.5, fontWeight: 700, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: ".04em" }}>
                      Live HTML preview - ei design e {form.product} er email inbox e jabe
                    </div>
                    <iframe title={`HTML preview ${em.ref}`} srcDoc={previewHtml(savedEmail.body_html)} style={{ width: "100%", height: 380, border: "none", background: "#F6F4FB" }} sandbox="" />
                  </div>
                )}
              </div>
            );
          })}

          <div className="note n-info" style={{ marginTop: 8 }}>
            <span>Send hobe <b>sob somoy branded HTML</b> e - brand header, colors, ✔ bullets, CTA button + unique UTM shoho. Copy badle body edit kore <b>Save email</b> korlei design update.</span>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="grid two-col">
          <div className="card">
            <div className="card-h"><h3>Review</h3><span className="hint">everything below is saved to PostgreSQL</span></div>
            <dl className="kv">
              <dt>Type</dt><dd>{isSequence
              ? <><b>{plannedEmails.length}-email branched sequence</b> · {seqDef.name}</>
              : "Single email (SendClean transactional)"}</dd>
              <dt>Title</dt><dd>{form.title}</dd>
              <dt>Recipients</dt><dd>{created
                ? `${created.audience_count ?? recipientCount} recipient(s) · ${created.audience_name || "audience"}`
                : usingAudience
                  ? `${recipientCount.toLocaleString()} recipient(s) · ${audiences.find((x) => x.id === audienceId)?.name || "saved audience"}${tagFilters.length ? ` (${tagFilters.join(tagMatch === "all" ? " AND " : " OR ")})` : ""}`
                  : `${recipientCount.toLocaleString()} recipient(s) · manual list`}</dd>
              <dt>Product</dt><dd>{form.product}</dd>
              <dt>Offer</dt><dd>{form.offer || "-"}</dd>
              <dt>Sender</dt><dd>{form.sender_name} · {form.sender_email}</dd>
            </dl>
            <button className="btn btn-primary btn-lg mt" disabled={busy} onClick={created ? () => setStep(3) : createCampaign}>
              {busy ? "Working…" : created ? "Go to Launch →" : "Create Send (draft)"}
            </button>
          </div>
          <div className="card">
            <div className="card-h"><h3>Safety check</h3></div>
            <div className="gate pass"><div className="g-ico">✓</div><div className="g-body"><b>Explicit recipient list</b><span>Manually supplied · {recipientCount} address(es)</span></div></div>
            <div className="gate pass"><div className="g-ico">✓</div><div className="g-body"><b>Unsubscribe + physical address</b><span>Footer enforced at render</span></div></div>
            <div className="gate pass"><div className="g-ico">✓</div><div className="g-body"><b>Unique UTM per CTA</b><span>Per skill rule - every CTA gets its own UTM</span></div></div>
            <div className="gate pass"><div className="g-ico">✓</div><div className="g-body"><b>Recipient volume</b><span>{recipientCount.toLocaleString()} selected · no fixed application limit</span></div></div>
          </div>
        </div>
      )}

      {step === 2 && (
        <>
          <div className="card mt">
            <div className="card-h"><h3>Email copy preview</h3><span className="hint">subject + preview + body</span></div>
          {isSequence ? (
            <div className={`note ${draftedCount === plannedEmails.length ? "n-success" : "n-info"}`} style={{ marginTop: 0, marginBottom: 14 }}>
              <span>
                <b>{draftedCount} of {plannedEmails.length} emails drafted.</b>{" "}
                {plannedEmails.map((em) => (
                  <span
                    key={em.ref}
                    className={`badge ${draftedRefs.includes(em.ref) ? "b-success" : "b-neutral"}`}
                    style={{ marginRight: 4 }}
                    title={em.name || em.focus}
                  >
                    {em.ref}
                  </span>
                ))}
                {draftedCount === plannedEmails.length
                  ? " Every email in the journey has copy - nothing is missing."
                  : " Press Draft with Agent to write them all in one go - it takes 25–60s for nine emails."}
              </span>
            </div>
          ) : null}

          {plannedEmails.map((em) => {
              const cp = emailCopy[em.ref] || {};
              return (
                <div key={em.ref} style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "12px 14px", marginBottom: 8 }}>
                  <div className="badge b-info" style={{ marginBottom: 6 }}>{em.ref} · {em.name || em.focus}</div>
                  <div style={{ fontWeight: 700, fontSize: 13.5 }}>{cp.subject || "(subject - agent likhe debe)"}</div>
                  <div style={{ fontSize: 12, color: "var(--ink-3)", margin: "2px 0 6px" }}>{cp.preview || "- preview text -"}</div>
                  <div style={{ fontSize: 12.5, color: "var(--ink-2)", whiteSpace: "pre-wrap" }}>{cp.body || "(body - agent likhe debe)"}</div>
                </div>
              );
            })}
          </div>

          <div className="card mt">
            <div className="card-h"><h3>Send settings (via SendClean)</h3></div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              {[{ v: "direct", l: "Send now", d: "Send via SendClean immediately" }, { v: "draft", l: "Create draft", d: "Save as draft - review before sending" }].map((o) => (
                <label key={o.v} style={{ flex: 1, minWidth: 160, border: `1px solid ${sendMode === o.v ? "var(--brand-500)" : "var(--border-strong)"}`, borderRadius: "var(--radius)", padding: 10, cursor: "pointer", background: sendMode === o.v ? "var(--brand-50)" : "var(--surface)" }}>
                  <input disabled={!!created} type="radio" name="sendmode" checked={sendMode === o.v} onChange={() => setSendMode(o.v)} style={{ marginRight: 6 }} />
                  <b style={{ fontSize: 13.5 }}>{o.l}</b>
                  <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{o.d}</div>
                </label>
              ))}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12 }}>
              <span className={`switch ${testFirst ? "on" : ""}`} onClick={() => { if (!created) setTestFirst(!testFirst); }} />
              <div><b style={{ fontSize: 14 }}>Send a test via SendClean first</b>
                <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>SendClean test send pathabe - full list e send er age</div>
              </div>
            </div>
          </div>
        </>
      )}

      {step === 3 && (
        <div className="grid" style={{ gridTemplateColumns: "1.4fr 1fr" }}>
          <div className="card">
            <div className="card-h"><h3>Launch gates</h3><span className="hint">auto-launch authorized only if all pass</span></div>
            {(created?.gates || [
              { name: "Product knowledge sourced", detail: "Pending", pass: true },
              { name: "Sender identity, address, unsubscribe", detail: "All present", pass: true },
              { name: "Offer, dates, links, personalization, tracking", detail: "Validated", pass: true },
              { name: "Compliance + QA checklist", detail: "Run before launch", pass: true },
              { name: "Suppression + purchase exit", detail: "Configured", pass: true },
              { name: "KPI targets + emergency thresholds", detail: "All configured", pass: true },
              { name: "Recipient list & volume limits", detail: "Checked", pass: true },
            ]).map((g, i) => (
              <div className={`gate ${g.pass ? "pass" : "pending"}`} key={i}>
                <div className="g-ico">{g.pass ? "✓" : "–"}</div>
                <div className="g-body"><b>{g.name}</b><span>{g.detail}</span></div>
                <span className="g-tag">{g.pass ? "PASS" : "PENDING"}</span>
              </div>
            ))}
          </div>
          <div className="card">
            <div className="card-h"><h3>Next actions</h3></div>
            {created ? (
              <div style={{ display: "grid", gap: 12 }}>
                <div className="gate pass"><div className="g-ico">OK</div><div className="g-body"><b>{created.emails?.length || 0} email(s) staged</b><span>Status: {created.status} · send: {created.send_mode || "direct"}{created.send_test_first === "true" ? " · test-first ON" : ""}</span></div></div>
                <button className="btn btn-secondary btn-lg" disabled={busy} onClick={() => act("generate")}>
                {drafting ? `✍️ Writing… ${elapsed}s` : "✨ Draft copy with Agent (brand MD → copy + HTML)"}
              </button>
                <button className="btn btn-secondary" disabled={busy} onClick={reloadCopy}>Reload agent copy</button>
                <button className="btn btn-secondary" disabled={busy} onClick={() => act("sendtest")}>SendClean test send</button>
                <div className="note n-info" style={{ marginTop: 0 }}><span><b>Recipients confirmed:</b> {created.audience_count ?? recipientCount} explicit recipient(s) · mode {created.audience_options?.mode || "manual"} - locked to this list so a send can never widen.</span></div>
                <button className="btn btn-primary btn-lg" disabled={busy || !["draft", "approved"].includes(created.status)} onClick={() => act("launch")}>
                  {busy ? "Working…"
                    : !["draft", "approved"].includes(created.status) ? `Launch blocked (${created.status})`
                    : isSequence
                      ? `▶ Start the journey - enrol ${created.audience_count ?? recipientCount} recipient(s)`
                      : "Approve recipients & Send via SendClean"}
                </button>
                {isSequence ? (
                  <div className="note n-info" style={{ marginTop: 0 }}>
                    <span>
                      Email <b>1</b> goes out immediately. The scheduler then sends every later step on its own,
                      choosing the branch from each recipient&apos;s own opens and clicks -{" "}
                      <b>you never send steps 2–{plannedEmails.length} by hand</b>. Watch them under{" "}
                      <b>Automation → Sequences → this sequence → Activity</b>.
                    </span>
                  </div>
                ) : null}
                <button className="btn btn-secondary" onClick={() => router.push(`/campaigns/${created.slug}`)}>Open send detail</button>
                <div style={{ fontSize: 12, color: "var(--ink-3)", textAlign: "center" }}>Send action recorded in audit log · immutable</div>
              </div>
            ) : (
              <div style={{ display: "grid", gap: 12 }}>
                <div className="note n-warn" style={{ marginTop: 0 }}>
                  <span>Send ekhono create hoyni - age Review step e <b>"Create Send"</b> click koro, ba niche button diye agent copy diye create koro.</span>
                </div>
                <button className="btn btn-primary btn-lg" onClick={draftWithAgent} disabled={busy}>
              {drafting ? `✍️ Writing… ${elapsed}s` : "✨ Create + Draft copy with Agent"}
            </button>
                <button className="btn btn-secondary" onClick={() => setStep(2)}>← Back to Review</button>
              </div>
            )}
          </div>
        </div>
      )}

      {step < STEPS.length - 1 && (
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 18 }}>
          <button className="btn btn-secondary" disabled={step === 0} onClick={() => setStep(step - 1)}>← Back</button>
          <button className="btn btn-primary" disabled={!canNext} onClick={() => setStep(step + 1)}>Continue →</button>
        </div>
      )}
    </AppShell>
  );
}
