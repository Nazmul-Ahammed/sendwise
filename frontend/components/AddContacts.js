"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import TagSelect from "./TagSelect";
import { verdictOf, isBlocked } from "./VerifyBadge";
import { api } from "../lib/api";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const LIVE_CHECK_LIMIT = 200;
const STARTING_ROWS = 4;

function cleanEmail(value) {
  return String(value || "").trim().toLowerCase();
}

function parseContactLine(line) {
  const raw = String(line || "").trim();
  if (!raw) return null;
  const angled = raw.match(/^(.*)<([^<>]+)>\s*$/);
  if (angled) {
    return {
      name: angled[1].trim().replace(/^["']|["']$/g, ""),
      email: cleanEmail(angled[2]),
    };
  }
  const cells = raw.includes("\t")
    ? raw.split("\t").map((cell) => cell.trim())
    : raw.split(",").map((cell) => cell.trim());
  if (cells.length > 1) {
    const found = cells.findIndex((cell) => EMAIL_RE.test(cleanEmail(cell)));
    const emailIndex = found >= 0 ? found : 1;
    return {
      name: cells.find((cell, index) => index !== emailIndex && cell) || "",
      email: cleanEmail(cells[emailIndex]),
    };
  }
  return { name: "", email: cleanEmail(raw) };
}

function parseSheetPaste(text) {
  const raw = String(text || "").trim();
  const commaParts = raw.split(",").map((part) => part.trim()).filter(Boolean);
  const commaList = !raw.includes("\t")
    && !raw.includes("\n")
    && commaParts.length > 1
    && commaParts.every((part) => {
      const angled = part.match(/<([^<>]+)>\s*$/);
      return EMAIL_RE.test(cleanEmail(angled ? angled[1] : part));
    });
  const sourceLines = commaList ? commaParts : raw.split(/\r?\n/);
  return sourceLines
    .map(parseContactLine)
    .filter(Boolean)
    .filter((row, index) => {
      if (index !== 0) return true;
      return !(row.name.toLowerCase().includes("name") && row.email.toLowerCase().includes("email"));
    });
}

export default function AddContacts({ audienceId, tagOptions = [], onAdded, disabled = false }) {
  const rowId = useRef(1);
  const requestId = useRef(0);
  function blankRow() {
    return { id: rowId.current++, name: "", email: "" };
  }

  const [sheetRows, setSheetRows] = useState(() =>
    Array.from({ length: STARTING_ROWS }, () => blankRow()),
  );
  const [tags, setTags] = useState([]);
  const [skipBlocked, setSkipBlocked] = useState(true);
  const [verdicts, setVerdicts] = useState({});
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const enteredRows = useMemo(
    () => sheetRows
      .map((row) => ({ ...row, name: row.name.trim(), email: cleanEmail(row.email) }))
      .filter((row) => row.name || row.email),
    [sheetRows],
  );

  const decorated = useMemo(() => {
    const seen = new Set();
    return enteredRows.map((row) => {
      const syntaxOk = EMAIL_RE.test(row.email);
      const duplicate = Boolean(row.email && seen.has(row.email));
      if (row.email) seen.add(row.email);
      const verdict = verdicts[row.email];
      const status = duplicate
        ? "duplicate"
        : !syntaxOk
          ? "invalid"
          : verdict
            ? verdict.status
            : "checking";
      return {
        ...row,
        syntaxOk,
        duplicate,
        status,
        reason: verdict?.reason || "",
        blocked: isBlocked(status),
      };
    });
  }, [enteredRows, verdicts]);

  const pending = useMemo(() =>
    decorated
      .filter((row) => row.syntaxOk && !row.duplicate && !verdicts[row.email])
      .map((row) => row.email)
      .slice(0, LIVE_CHECK_LIMIT),
  [decorated, verdicts]);
  const pendingKey = pending.join(",");

  useEffect(() => {
    if (!pendingKey) return undefined;
    const id = ++requestId.current;
    const timer = setTimeout(async () => {
      setChecking(true);
      try {
        const response = await api.validateEmails(pendingKey.split(","));
        if (id !== requestId.current) return;
        const next = {};
        (response.results || []).forEach((row) => { next[row.email] = row; });
        setVerdicts((previous) => ({ ...previous, ...next }));
      } catch {
        // Verification is advisory. Mark this batch as unknown so an unavailable
        // validator never leaves the sheet permanently stuck on "Checking".
        const unknown = {};
        pendingKey.split(",").forEach((email) => {
          unknown[email] = { email, status: "unknown", reason: "Verification unavailable" };
        });
        setVerdicts((previous) => ({ ...previous, ...unknown }));
      } finally {
        if (id === requestId.current) setChecking(false);
      }
    }, 650);
    return () => clearTimeout(timer);
  }, [pendingKey]);

  const counts = decorated.reduce((summary, row) => {
    summary[row.status] = (summary[row.status] || 0) + 1;
    return summary;
  }, {});
  const duplicateCount = decorated.filter((row) => row.duplicate).length;
  const blockedCount = decorated.filter((row) => row.blocked).length;
  const riskyCount = decorated.filter((row) => row.status === "disposable" || row.status === "role").length;
  const stillChecking = decorated.some((row) => row.status === "checking");
  const willAdd = decorated.filter((row) =>
    row.syntaxOk && !row.duplicate && (!skipBlocked || !row.blocked),
  );

  function reset() {
    setSheetRows(Array.from({ length: STARTING_ROWS }, () => blankRow()));
    setTags([]);
    setVerdicts({});
    setResult(null);
    setError("");
  }

  function updateRow(id, field, value) {
    setSheetRows((rows) => rows.map((row) => row.id === id ? { ...row, [field]: value } : row));
    setResult(null);
    setError("");
  }

  function addRows(count = 1) {
    setSheetRows((rows) => [...rows, ...Array.from({ length: count }, () => blankRow())]);
  }

  function removeRow(id) {
    setSheetRows((rows) => {
      const remaining = rows.filter((row) => row.id !== id);
      return remaining.length ? remaining : [blankRow()];
    });
    setResult(null);
    setError("");
  }

  function removeMatching(predicate) {
    const removeIds = new Set(decorated.filter(predicate).map((row) => row.id));
    setSheetRows((rows) => {
      const remaining = rows.filter((row) => !removeIds.has(row.id));
      return remaining.length ? remaining : [blankRow()];
    });
    setResult(null);
    setError("");
  }

  function pasteIntoSheet(event, startIndex) {
    const clipboard = event.clipboardData.getData("text");
    const parsed = parseSheetPaste(clipboard);
    const gridPaste = clipboard.includes("\t")
      || clipboard.includes("\n")
      || parsed.length > 1
      || /<[^<>]+@[^<>]+>\s*$/.test(clipboard.trim());
    if (!gridPaste || !parsed.length) return;
    event.preventDefault();
    setSheetRows((current) => {
      const next = [...current];
      while (next.length < startIndex + parsed.length) next.push(blankRow());
      parsed.forEach((contact, offset) => {
        next[startIndex + offset] = {
          ...next[startIndex + offset],
          name: contact.name,
          email: contact.email,
        };
      });
      return next;
    });
    setResult(null);
    setError("");
  }

  async function submit() {
    if (!willAdd.length) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await api.addAudienceContacts(audienceId, {
        emails: willAdd.map((row) => row.name ? `${row.name} <${row.email}>` : row.email),
        tags,
      });
      setResult(response);
      setSheetRows(Array.from({ length: STARTING_ROWS }, () => blankRow()));
      setTags([]);
      setVerdicts({});
      if (onAdded) await onAdded(response);
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setBusy(false);
    }
  }

  function statusFor(row) {
    if (!row.name && !row.email) return <span className="hint">Empty row</span>;
    if (row.duplicate) return <span className="badge b-warn">duplicate</span>;
    const verdict = verdictOf(row.status);
    return (
      <span className={`badge ${verdict.cls}`} title={row.reason || verdict.tip}>
        {verdict.label}
      </span>
    );
  }

  return (
    <div>
      <div className="audience-sheet-head">
        <div>
          <h4>Add audience contacts</h4>
          <p>Enter one person per row, or paste Name and Email columns from Excel or Google Sheets.</p>
        </div>
        <button type="button" className="btn btn-secondary" onClick={() => addRows(5)} disabled={disabled || busy}>
          + Add 5 rows
        </button>
      </div>

      <div className="audience-sheet-wrap">
        <table className="audience-sheet">
          <thead>
            <tr>
              <th className="sheet-row-number">#</th>
              <th>Contact name</th>
              <th>Email address <span>required</span></th>
              <th>Status</th>
              <th aria-label="Remove row" />
            </tr>
          </thead>
          <tbody>
            {sheetRows.map((row, index) => {
              const decoratedRow = decorated.find((item) => item.id === row.id);
              return (
                <tr key={row.id} className={decoratedRow?.blocked || decoratedRow?.duplicate ? "sheet-row-error" : ""}>
                  <td className="sheet-row-number">{index + 1}</td>
                  <td>
                    <input
                      value={row.name}
                      onChange={(event) => updateRow(row.id, "name", event.target.value)}
                      onPaste={(event) => pasteIntoSheet(event, index)}
                      placeholder="e.g. Rahim Uddin"
                      disabled={disabled || busy}
                      aria-label={`Contact name row ${index + 1}`}
                    />
                  </td>
                  <td>
                    <input
                      value={row.email}
                      onChange={(event) => updateRow(row.id, "email", event.target.value)}
                      onPaste={(event) => pasteIntoSheet(event, index)}
                      placeholder="name@company.com"
                      inputMode="email"
                      disabled={disabled || busy}
                      aria-label={`Email address row ${index + 1}`}
                    />
                  </td>
                  <td className="sheet-status">{statusFor(decoratedRow || row)}</td>
                  <td className="sheet-remove">
                    <button type="button" onClick={() => removeRow(row.id)} disabled={disabled || busy} title="Remove row">×</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="audience-sheet-toolbar">
        <button type="button" className="btn btn-secondary" onClick={() => addRows()} disabled={disabled || busy}>+ Add row</button>
        <span className="hint">Tip: copy two columns from a spreadsheet, click the first Name cell, then paste.</span>
      </div>

      {enteredRows.length ? (
        <div className="audience-sheet-summary">
          <span className="badge b-neutral">{enteredRows.length} entered</span>
          {counts.valid ? <span className="badge b-success">{counts.valid} ready</span> : null}
          {counts.role ? <span className="badge b-warn">{counts.role} role inbox</span> : null}
          {counts.disposable ? <span className="badge b-warn">{counts.disposable} disposable</span> : null}
          {blockedCount ? <span className="badge b-danger">{blockedCount} blocked</span> : null}
          {duplicateCount ? <span className="badge b-warn">{duplicateCount} duplicate</span> : null}
          {checking || stillChecking ? <span className="sheet-checking"><span className="spinner" /> checking…</span> : null}
          {blockedCount ? <button type="button" className="sheet-summary-action danger" onClick={() => removeMatching((row) => row.blocked)}>Remove blocked</button> : null}
          {duplicateCount ? <button type="button" className="sheet-summary-action" onClick={() => removeMatching((row) => row.duplicate)}>Remove duplicates</button> : null}
          {riskyCount ? <button type="button" className="sheet-summary-action" onClick={() => removeMatching((row) => row.status === "disposable" || row.status === "role")}>Remove risky</button> : null}
        </div>
      ) : null}

      {blockedCount ? (
        <label className="audience-skip-blocked">
          <input type="checkbox" checked={skipBlocked} onChange={(event) => setSkipBlocked(event.target.checked)} disabled={disabled} />
          Skip {blockedCount} address{blockedCount !== 1 ? "es" : ""} that cannot receive mail
        </label>
      ) : null}

      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
          Tag these contacts <span className="hint" style={{ fontWeight: 400 }}>(optional)</span>
        </div>
        <TagSelect
          options={tagOptions}
          value={tags}
          onChange={setTags}
          placeholder="No tags, or choose existing tags…"
          allowCreate
          disabled={disabled || busy}
          hint="Tags let you reuse one audience as several segments later."
        />
      </div>

      {error ? <div className="note n-warn" style={{ marginTop: 12 }}><span><b>Could not add:</b> {error}</span></div> : null}
      {result ? (
        <div className="note n-success" style={{ marginTop: 12 }}>
          <span>
            <b>{result.added} contact{result.added !== 1 ? "s" : ""} added</b>
            {result.updated ? ` · ${result.updated} updated` : ""}
            {result.already_members ? ` · ${result.already_members} already in this audience` : ""}
            {result.invalid?.length ? ` · ${result.invalid.length} unreadable skipped` : ""}
          </span>
        </div>
      ) : null}

      <div className="audience-sheet-actions">
        <button className="btn btn-primary" type="button" onClick={submit} disabled={disabled || busy || !willAdd.length || stillChecking}>
          {busy ? "Adding…" : stillChecking ? "Checking…" : `Add ${willAdd.length} contact${willAdd.length !== 1 ? "s" : ""}`}
        </button>
        {enteredRows.length ? <button className="btn btn-secondary" type="button" onClick={reset} disabled={busy}>Clear sheet</button> : null}
        {duplicateCount ? <span className="hint">Duplicate rows are skipped automatically.</span> : null}
      </div>
    </div>
  );
}
