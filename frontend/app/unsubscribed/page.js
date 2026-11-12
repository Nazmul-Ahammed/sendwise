"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "../../components/AppShell";
import { api } from "../../lib/api";

const REASON_LABEL = {
  unsubscribe: "Unsubscribed",
  hard_bounce: "Hard bounce",
  soft_bounce: "Soft bounce",
  complaint: "Spam complaint",
  manual: "Manually suppressed",
};

function fmt(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export default function UnsubscribedPage() {
  const [data, setData] = useState(null);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState([]);
  const [scope, setScope] = useState(0); // 0 = every audience
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");

  const load = useCallback((term = "") => {
    setError("");
    return api.unsubscribed(term)
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => { load(""); }, [load]);

  const items = data?.items || [];

  // Every audience any of these addresses still sits in, so the scope picker only
  // offers lists that would actually change.
  const audiences = useMemo(() => {
    const map = new Map();
    items.forEach((i) => (i.audiences || []).forEach((a) => map.set(a.id, a.name)));
    return Array.from(map, ([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [items]);

  const withAudience = items.filter((i) => (i.audiences || []).length > 0).length;
  // Only rows that still belong to a list can be removed from one. Everything else is
  // already out, so offering it as selectable is what produced a confusing
  // "0 membership(s) removed" after a second click.
  const removable = items.filter((i) => (i.audiences || []).length > 0);
  const selectable = removable.length;
  const allChecked = selectable > 0 && selected.length === selectable;

  function toggle(email) {
    setSelected((prev) => (prev.includes(email) ? prev.filter((x) => x !== email) : [...prev, email]));
  }

  function toggleAll() {
    setSelected(allChecked ? [] : removable.map((i) => i.email));
  }

  async function removeSelected() {
    if (!selected.length) return;
    const where = scope ? audiences.find((a) => a.id === scope)?.name || "the selected audience" : "ALL audiences";
    if (!window.confirm(
      `Remove ${selected.length} address(es) from ${where}?\n\n` +
      "They stay unsubscribed either way - this only removes the list membership.",
    )) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.removeUnsubscribedFromAudiences({
        emails: selected,
        audience_ids: scope ? [scope] : [],
      });
      setFlash(res.removed
        ? `${res.removed} membership(s) removed for ${res.addresses} address(es). They remain unsubscribed.`
        : `Nothing to remove - those ${res.addresses} address(es) are not in any list. They remain unsubscribed.`);
      setSelected([]);
      await load(q);
      setTimeout(() => setFlash(""), 7000);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell active="/unsubscribed">
      <div className="page-head">
        <div>
          <h1>Unsubscribed</h1>
          <p className="hint" style={{ marginTop: 4 }}>
            Every address that asked not to be emailed again. They are blocked from all future sends.
          </p>
        </div>
        <div className="actions">
          <button className="btn btn-secondary" disabled={busy} onClick={() => load(q)}>Refresh</button>
        </div>
      </div>

      {error && <div className="note n-warn"><span>⚠ {error}</span></div>}
      {flash && <div className="note n-success"><span>✓ {flash}</span></div>}

      <div className="card">
        <div className="card-h">
          <h3>Suppression list</h3>
          <span className="hint">
            {data ? `${data.total} total` : "loading…"}
            {data?.by_reason?.unsubscribe ? ` · ${data.by_reason.unsubscribe} unsubscribed` : ""}
            {withAudience ? ` · ${withAudience} still in a list` : ""}
          </span>
        </div>

        <div className="grid" style={{ gridTemplateColumns: "1fr auto auto", gap: 10, alignItems: "center" }}>
          <input
            className="input"
            placeholder="Search email…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") load(q); }}
          />
          <select className="input" value={scope} onChange={(e) => setScope(Number(e.target.value))}>
            <option value={0}>All audiences</option>
            {audiences.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <button className="btn btn-primary" disabled={busy || !selected.length} onClick={removeSelected}>
            {busy ? "Working…" : selected.length
              ? `Remove ${selected.length} from list${selected.length === 1 ? "" : "s"}`
              : selectable ? "Select rows to remove" : "All already out of lists"}
          </button>
        </div>

        <div className="table-wrap mt">
          <table>
            <thead>
              <tr>
                <th style={{ width: 34 }}>
                  <input
                    type="checkbox"
                    checked={allChecked}
                    disabled={selectable === 0}
                    onChange={toggleAll}
                    aria-label="Select all"
                  />
                </th>
                <th>Email</th>
                <th>Reason</th>
                <th>When</th>
                <th>Unsubscribed from</th>
                <th>Still in audience</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr><td colSpan={6} className="hint">Nothing here yet.</td></tr>
              )}
              {items.map((row) => (
                <tr key={row.email}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.includes(row.email)}
                      disabled={!(row.audiences || []).length}
                      onChange={() => toggle(row.email)}
                      aria-label={`Select ${row.email}`}
                    />
                  </td>
                  <td className="mono" style={{ wordBreak: "break-all" }}>{row.email}</td>
                  <td>
                    <span className={`badge ${row.reason === "unsubscribe" ? "b-neutral" : "b-warn"}`}>
                      {REASON_LABEL[row.reason] || row.reason}
                    </span>
                  </td>
                  <td>{fmt(row.unsubscribed_at)}</td>
                  <td className="mono" style={{ fontSize: 12 }}>
                    {(row.campaigns || []).length ? row.campaigns.join(", ") : "-"}
                  </td>
                  <td>
                    {(row.audiences || []).length
                      ? row.audiences.map((a) => a.name).join(", ")
                      : <span className="hint">not in a list</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="note n-info" style={{ marginTop: 12 }}>
          <span>
            Removing an address from a list does <b>not</b> re-subscribe it. The suppression stays,
            so future sends keep skipping it.
          </span>
        </div>
      </div>
    </AppShell>
  );
}
