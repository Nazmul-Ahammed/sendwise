"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import TagSelect from "./TagSelect";
import { verdictOf, isBlocked } from "./VerifyBadge";
import { api } from "../lib/api";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const LIVE_CHECK_LIMIT = 200;

/** Parse a paste. Accepts "email", "Name <email>" and comma/newline separation. */
function parseRows(text) {
  const out = [];
  const seen = new Set();
  let duplicates = 0;
  for (const line of String(text || "").replace(/,/g, "\n").split(/\r?\n/)) {
    const raw = line.trim();
    if (!raw) continue;
    const angled = raw.match(/^(.*)<([^<>]+)>\s*$/);
    const email = (angled ? angled[2] : raw).trim().toLowerCase();
    const name = angled ? angled[1].trim().replace(/^["']|["']$/g, "") : "";
    if (!email) continue;
    if (seen.has(email)) { duplicates += 1; continue; }
    seen.add(email);
    out.push({ email, name, syntaxOk: EMAIL_RE.test(email) });
  }
  return { rows: out, duplicates };
}

export default function AddContacts({ audienceId, tagOptions = [], onAdded, disabled = false }) {
  const [text, setText] = useState("");
  const [tags, setTags] = useState([]);
  const [skipBlocked, setSkipBlocked] = useState(true);
  const [verdicts, setVerdicts] = useState({});   // email -> validation result
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const reqId = useRef(0);

  const parsed = useMemo(() => parseRows(text), [text]);
  const rows = parsed.rows;

  // Addresses worth asking the server about: well-formed and not already known.
  const pending = useMemo(
    () => rows.filter((r) => r.syntaxOk && !verdicts[r.email]).map((r) => r.email).slice(0, LIVE_CHECK_LIMIT),
    [rows, verdicts],
  );
  const pendingKey = pending.join(",");

  // Live check, debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    if (!pendingKey) return undefined;
    const id = ++reqId.current;
    const timer = setTimeout(async () => {
      setChecking(true);
      try {
        const res = await api.validateEmails(pendingKey.split(","));
        if (id !== reqId.current) return; // a newer paste already superseded this
        const map = {};
        (res.results || []).forEach((r) => { map[r.email] = r; });
        setVerdicts((prev) => ({ ...prev, ...map }));
      } catch {
        /* a failed check must never block adding - rows simply stay "not checked" */
      } finally {
        if (id === reqId.current) setChecking(false);
      }
    }, 650);
    return () => clearTimeout(timer);
  }, [pendingKey]);

  function reset() {
    setText("");
    setTags([]);
    setVerdicts({});
    setResult(null);
    setError("");
  }

  function emailOf(line) {
    const raw = String(line || "").trim();
    if (!raw) return "";
    const angled = raw.match(/^(.*)<([^<>]+)>\s*$/);
    return (angled ? angled[2] : raw).trim().toLowerCase();
  }

  /** Drop one address by rewriting the paste, so the textarea stays the single truth. */
  function removeEmail(email) {
    const kept = String(text || "").replace(/,/g, "\n").split(/\r?\n/)
      .filter((line) => line.trim() && emailOf(line) !== email);
    setText(kept.join("\n"));
    setResult(null);
    setError("");
  }

  /** Drop every address matching a verdict - one click for a whole page of bad ones. */
  function removeMatching(predicate) {
    const kept = String(text || "").replace(/,/g, "\n").split(/\r?\n/).filter((line) => {
      if (!line.trim()) return false;
      const row = decorated.find((d) => d.email === emailOf(line));
      return !(row && predicate(row));
    });
    setText(kept.join("\n"));
    setResult(null);
    setError("");
  }

  const decorated = rows.map((r) => {
    const v = verdicts[r.email];
    const status = !r.syntaxOk ? "invalid" : (v ? v.status : "checking");
    return { ...r, status, reason: v?.reason || "", blocked: isBlocked(status) };
  });

  const counts = decorated.reduce((acc, r) => {
    acc[r.status] = (acc[r.status] || 0) + 1;
    return acc;
  }, {});
  const blockedCount = decorated.filter((r) => r.blocked).length;
  const riskyCount = decorated.filter((r) => r.status === "disposable" || r.status === "role").length;
  const willAdd = skipBlocked ? decorated.filter((r) => !r.blocked) : decorated;
  const stillChecking = decorated.some((r) => r.status === "checking");

  async function submit() {
    if (!willAdd.length) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const res = await api.addAudienceContacts(audienceId, {
        emails: willAdd.map((r) => (r.name ? `${r.name} <${r.email}>` : r.email)),
        tags,
      });
      setResult(res);
      setText("");
      setTags([]);
      setVerdicts({});
      if (onAdded) await onAdded(res);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const chip = "badge";

  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
        Paste addresses - one per line
        <span className="hint" style={{ fontWeight: 400, marginLeft: 6 }}>
          “Name &lt;email&gt;” also works · commas are fine
        </span>
      </div>

      <textarea
        rows={5}
        disabled={disabled || busy}
        value={text}
        onChange={(e) => { setText(e.target.value); setResult(null); setError(""); }}
        placeholder={"Rahim Uddin <rahim@travilo.io>\nkarim@ibos.io\nsales@example.com"}
        style={{
          width: "100%", fontFamily: "var(--mono, monospace)", fontSize: 12.5,
          padding: "10px 12px", borderRadius: 8, border: "1px solid var(--border)",
          background: "var(--surface, #fff)", color: "var(--ink-1, #111)", resize: "vertical",
          lineHeight: 1.7,
        }}
      />

      {rows.length > 0 ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
          <span className={chip + " b-neutral"}>{rows.length} address{rows.length !== 1 ? "es" : ""}</span>
          {counts.valid ? <span className={chip + " b-success"}>{counts.valid} ready</span> : null}
          {counts.role ? <span className={chip + " b-warn"}>{counts.role} role inbox</span> : null}
          {counts.disposable ? <span className={chip + " b-warn"}>{counts.disposable} disposable</span> : null}
          {counts.no_mx ? <span className={chip + " b-danger"}>{counts.no_mx} no mail server</span> : null}
          {counts.invalid ? <span className={chip + " b-danger"}>{counts.invalid} invalid</span> : null}
          {counts.unknown ? <span className={chip + " b-neutral"}>{counts.unknown} unverified</span> : null}
          {parsed.duplicates ? <span className={chip + " b-neutral"}>{parsed.duplicates} duplicate skipped</span> : null}
        </div>
      ) : null}

      {/* --- live verification --- */}
      {rows.length > 0 ? (
        <div
          style={{
            marginTop: 12, border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden",
            background: "var(--surface-2, #fafafa)",
          }}
        >
          <div
            style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              gap: 10, padding: "8px 12px", borderBottom: "1px solid var(--border)", flexWrap: "wrap",
            }}
          >
            <span style={{ fontSize: 12, fontWeight: 700 }}>
              Email check
              <span className="hint" style={{ fontWeight: 400, marginLeft: 6 }}>
                syntax · mail server · disposable · role
              </span>
            </span>
            <span style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {checking || stillChecking ? (
                <span style={{ fontSize: 11.5, color: "var(--ink-3)", display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span className="spinner" /> checking mail servers…
                </span>
              ) : (
                <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>checked</span>
              )}
              {blockedCount ? (
                <button
                  type="button"
                  onClick={() => removeMatching((r) => r.blocked)}
                  className="badge b-danger"
                  style={{ border: "none", cursor: "pointer", fontWeight: 700 }}
                  title="Remove every address that cannot receive mail"
                >
                  Remove {blockedCount} blocked
                </button>
              ) : null}
              {riskyCount ? (
                <button
                  type="button"
                  onClick={() => removeMatching((r) => r.status === "disposable" || r.status === "role")}
                  className="badge b-warn"
                  style={{ border: "none", cursor: "pointer", fontWeight: 700 }}
                  title="Remove disposable and role mailboxes"
                >
                  Remove {riskyCount} risky
                </button>
              ) : null}
              {rows.length > 1 ? (
                <button
                  type="button"
                  onClick={() => reset()}
                  className="badge b-neutral"
                  style={{ border: "none", cursor: "pointer", fontWeight: 700 }}
                  title="Clear the whole paste"
                >
                  Clear all
                </button>
              ) : null}
            </span>
          </div>

          <div style={{ maxHeight: 240, overflowY: "auto" }}>
            {decorated.slice(0, 60).map((r, i) => {
              const v = verdictOf(r.status);
              const tone = r.status === "valid" ? "var(--success, #16794C)"
                : isBlocked(r.status) ? "var(--danger, #B42318)"
                : r.status === "checking" ? "var(--ink-3)" : "#B45309";
              return (
                <div
                  key={`${r.email}-${i}`}
                  style={{
                    display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center",
                    padding: "6px 12px", borderBottom: i === Math.min(decorated.length, 60) - 1 ? "none" : "1px solid var(--border)",
                  }}
                >
                  <span className="mono" style={{ fontSize: 11.5, wordBreak: "break-all", flex: 1 }}>
                    {r.name ? <span style={{ fontFamily: "inherit", color: "var(--ink-3)" }}>{r.name} · </span> : null}
                    {r.email}
                  </span>
                  {r.status !== "checking" ? (
                    <span style={{ fontSize: 11, whiteSpace: "nowrap", color: tone, fontWeight: 600 }}>
                      {v.label}{r.reason && r.status !== "valid" ? " - " + r.reason : ""}
                    </span>
                  ) : (
                    <span style={{ fontSize: 11, color: "var(--ink-3)" }}>…</span>
                  )}
                  <button
                    type="button"
                    onClick={() => removeEmail(r.email)}
                    title="Remove this address from the list"
                    style={{
                      flex: "0 0 auto", width: 20, height: 20, lineHeight: "18px", borderRadius: 6,
                      border: "1px solid var(--border)", background: "#fff", cursor: "pointer",
                      fontSize: 12, color: "var(--ink-3)", padding: 0,
                    }}
                  >
                    ×
                  </button>
                </div>
              );
            })}
            {decorated.length > 60 ? (
              <div style={{ padding: "6px 12px", fontSize: 11, color: "var(--ink-3)" }}>
                +{decorated.length - 60} more shown after adding
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {blockedCount ? (
        <label
          style={{
            display: "flex", alignItems: "center", gap: 8, marginTop: 10, fontSize: 12,
            cursor: disabled ? "default" : "pointer",
          }}
        >
          <input type="checkbox" checked={skipBlocked} onChange={(e) => setSkipBlocked(e.target.checked)} disabled={disabled} />
          <span>
            Skip the <b>{blockedCount}</b> address{blockedCount !== 1 ? "es" : ""} that cannot receive mail
            <span className="hint" style={{ marginLeft: 6 }}>
              they would only ever bounce, and a send never uses them anyway
            </span>
          </span>
        </label>
      ) : null}

      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
          Tag these contacts <span className="hint" style={{ fontWeight: 400 }}>(optional, pick any number)</span>
        </div>
        <TagSelect
          options={tagOptions}
          value={tags}
          onChange={setTags}
          placeholder="No tags - or choose from existing…"
          allowCreate
          disabled={disabled || busy}
          hint="Tags let you reuse one list as several segments later."
        />
      </div>

      {error ? <div className="note n-warn" style={{ marginTop: 12 }}><span><b>Could not add:</b> {error}</span></div> : null}

      {result ? (
        <div className="note n-success" style={{ marginTop: 12 }}>
          <span>
            <b>{result.added} contact{result.added !== 1 ? "s" : ""} added</b>
            {result.updated ? ` · ${result.updated} updated` : ""}
            {result.already_members ? ` · ${result.already_members} already in this list` : ""}
            {result.invalid && result.invalid.length ? ` · ${result.invalid.length} skipped as unreadable` : ""}
            {result.verification ? (
              <>
                <br />
                <span style={{ fontSize: 11.5 }}>
                  Deliverability: {result.verification.counts.valid || 0} good
                  {result.verification.counts.role ? `, ${result.verification.counts.role} role inbox` : ""}
                  {result.verification.counts.disposable ? `, ${result.verification.counts.disposable} disposable` : ""}
                  {result.verification.counts.no_mx ? `, ${result.verification.counts.no_mx} with no mail server` : ""}
                  {result.verification.counts.invalid ? `, ${result.verification.counts.invalid} invalid` : ""}
                  {" · "}<b>{result.usable}</b> can receive a send now.
                </span>
              </>
            ) : null}
          </span>
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 12, flexWrap: "wrap" }}>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={disabled || busy || !willAdd.length || stillChecking}
        >
          {busy
            ? "Adding…"
            : stillChecking
              ? "Checking…"
              : `Add ${willAdd.length} contact${willAdd.length !== 1 ? "s" : ""}`}
        </button>
        {rows.length ? (
          <button className="btn btn-secondary" onClick={reset} disabled={busy}>Clear</button>
        ) : null}
        {blockedCount && skipBlocked ? (
          <span className="hint">
            {blockedCount} will not be added - untick the box above to keep them anyway.
          </span>
        ) : null}
      </div>
    </div>
  );
}
