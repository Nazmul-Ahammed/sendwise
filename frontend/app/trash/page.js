"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "../../components/AppShell";
import { api } from "../../lib/api";

/*
 * Trash & backup.
 *
 * Nothing here is a hard delete unless the operator explicitly purges, and a purge is the
 * one thing a backup restore will never bring back - so that path asks first, twice, and
 * says what it means. Everything else is reversible: restore from Trash, restore from a
 * snapshot, or take a snapshot right now.
 */

function when(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).replace("T", " ").slice(0, 19);
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function bytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(2)} MB`;
}

export default function TrashPage() {
  const [trash, setTrash] = useState(null);
  const [backups, setBackups] = useState(null);
  const [purges, setPurges] = useState(null);
  const [counts, setCounts] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [t, b, p, c] = await Promise.all([
        api.trash(), api.backups(), api.purges(), api.backupCounts(),
      ]);
      setTrash(t);
      setBackups(b);
      setPurges(p);
      setCounts(c);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function act(key, fn) {
    setBusy(key);
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  const t = trash || {};
  const rows = [
    ...(t.campaigns || []).map((x) => ({ kind: "Campaign", name: x.title || x.slug, id: x.slug, at: x.deleted_at, restore: api.restoreCampaign, purge: api.purgeCampaign })),
    ...(t.sequences || []).map((x) => ({ kind: "Sequence", name: x.name || x.slug, id: x.slug, at: x.deleted_at, restore: api.restoreSequence, purge: api.purgeSequence })),
    ...(t.audiences || []).map((x) => ({ kind: "Audience", name: x.name, id: x.id, at: x.deleted_at, restore: api.restoreAudience, purge: api.purgeAudience })),
    ...(t.contacts || []).map((x) => ({ kind: "Contact", name: x.email, id: x.email, at: x.deleted_at, restore: api.restoreContact, purge: api.purgeContact })),
  ].sort((a, b) => String(b.at).localeCompare(String(a.at)));

  const snapshots = (backups && backups.backups) || [];
  const purgeRows = (purges && purges.purges) || [];

  return (
    <AppShell active="/trash">
      <div className="page-head">
        <div>
          <div className="crumb">System / <b>Trash &amp; backup</b></div>
          <h1>Trash &amp; backup</h1>
        </div>
        <div className="top-actions">
          <button className="btn btn-secondary" onClick={load} disabled={!!busy}>Refresh</button>
          <button
            className="btn"
            disabled={busy === "backup"}
            onClick={() => act("backup", () => api.backupNow())}
          >
            {busy === "backup" ? "Taking snapshot…" : "Back up now"}
          </button>
        </div>
      </div>

      {error && <div className="note n-warn"><span>{error}</span></div>}

      {counts && (
        <div className="stat-row">
          <div className="stat">
            <div className="k-label">In trash</div>
            <div className="k-value">{t.total ?? 0}</div>
            <div className="k-sub">reversible</div>
          </div>
          <div className="stat">
            <div className="k-label">Snapshots</div>
            <div className="k-value">{snapshots.length}</div>
            <div className="k-sub">keep {backups?.keep ?? 30}</div>
          </div>
          <div className="stat">
            <div className="k-label">Last backup</div>
            <div className="k-value" style={{ fontSize: 16 }}>{when(backups?.newest)}</div>
            <div className="k-sub">{backups?.next_due ? "a new one is due" : "up to date"}</div>
          </div>
          <div className="stat">
            <div className="k-label">Permanently removed</div>
            <div className="k-value">{purges?.count ?? 0}</div>
            <div className="k-sub">never restored</div>
          </div>
        </div>
      )}

      <div className="card mt">
        <div className="card-h">
          <h3>In trash</h3>
          <div className="inline">
            <span className="hint">{rows.length} item(s)</span>
            {rows.length > 0 && (
              <button
                className="btn btn-ghost"
                disabled={busy === "all"}
                onClick={() => act("all", () => api.restoreAllTrash())}
              >
                {busy === "all" ? "Restoring…" : "Restore all"}
              </button>
            )}
          </div>
        </div>

        {rows.length === 0 ? (
          <div className="empty">
            <b>Trash is empty.</b>
            <span>Deleting a campaign, sequence, audience or contact moves it here. Nothing is erased.</span>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th style={{ width: 100 }}>Kind</th>
                  <th>Name</th>
                  <th style={{ width: 170 }}>Deleted</th>
                  <th className="ta-right" style={{ width: 190 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.kind}-${r.id}`}>
                    <td><span className="badge">{r.kind}</span></td>
                    <td className="mono" style={{ fontSize: 12.5, wordBreak: "break-word" }}>{r.name}</td>
                    <td className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{when(r.at)}</td>
                    <td className="actions-cell">
                      <button
                        className="btn btn-ghost"
                        disabled={busy === `${r.kind}-${r.id}`}
                        onClick={() => act(`${r.kind}-${r.id}`, () => r.restore(r.id))}
                      >
                        Restore
                      </button>
                      <button
                        className="btn btn-ghost"
                        title="Remove for good. A backup restore will not bring it back."
                        onClick={() => {
                          if (!window.confirm(
                            `Permanently remove "${r.name}"?\n\n` +
                            "This is the one action a backup restore will never undo.",
                          )) return;
                          act(`purge-${r.kind}-${r.id}`, () => r.purge(r.id));
                        }}
                      >
                        Remove for good
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card mt">
        <div className="card-h">
          <h3>Automatic backups</h3>
          <span className="hint">
            every {backups?.interval_hours ?? 24}h · written to the database and to disk
          </span>
        </div>

        {snapshots.length === 0 ? (
          <div className="empty">
            <b>No snapshots yet.</b>
            <span>Press <b>Back up now</b> to take the first one.</span>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th style={{ width: 70 }}>#</th>
                  <th style={{ width: 120 }}>Label</th>
                  <th style={{ width: 170 }}>Taken</th>
                  <th>Contents</th>
                  <th className="num-cell" style={{ width: 90 }}>Size</th>
                  <th className="ta-right" style={{ width: 170 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((b) => (
                  <tr key={b.id}>
                    <td className="mono" style={{ fontSize: 12 }}>{b.id}</td>
                    <td><span className="badge">{b.label || "auto"}</span></td>
                    <td className="mono" style={{ fontSize: 11.5 }}>{when(b.created_at)}</td>
                    <td style={{ fontSize: 12, color: "var(--ink-2)" }}>
                      {Object.entries(b.counts || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}
                    </td>
                    <td className="num-cell mono" style={{ fontSize: 12 }}>{bytes(b.size_bytes)}</td>
                    <td className="actions-cell">
                      <button
                        className="btn btn-ghost"
                        disabled={busy === `restore-${b.id}`}
                        title="Merges this snapshot back in. Nothing is ever deleted by a restore."
                        onClick={() => {
                          if (!window.confirm(
                            `Restore snapshot #${b.id}?\n\n` +
                            "This only adds back what is missing and un-trashes what was trashed. " +
                            "It never deletes anything.",
                          )) return;
                          act(`restore-${b.id}`, () => api.restoreBackup(b.id));
                        }}
                      >
                        {busy === `restore-${b.id}` ? "Restoring…" : "Restore"}
                      </button>
                      <button
                        className="btn btn-ghost"
                        title="Drop this snapshot from the history. Live data is untouched."
                        onClick={() => {
                          if (!window.confirm(`Delete snapshot #${b.id} from the history?\n\nLive data is not touched.`)) return;
                          act(`del-${b.id}`, () => api.deleteBackup(b.id));
                        }}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="disclosure" style={{ marginTop: 12 }}>
          A restore is additive by design: it re-adds what is missing and un-trashes what was
          trashed, and never removes anything that exists now. That is what makes it safe to
          press without first checking what is in the snapshot.
        </div>
      </div>

      <div className="card mt">
        <div className="card-h">
          <h3>Permanently removed</h3>
          <span className="hint">the one list a restore will not bring back</span>
        </div>

        {purgeRows.length === 0 ? (
          <div className="empty">
            <b>Nothing has been permanently removed.</b>
            <span>Trashing is always reversible. Only an explicit purge lands here.</span>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th style={{ width: 110 }}>Kind</th>
                  <th>Identifier</th>
                  <th style={{ width: 180 }}>Reason</th>
                  <th style={{ width: 170 }}>When</th>
                  <th className="ta-right" style={{ width: 130 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {purgeRows.map((p) => (
                  <tr key={p.id}>
                    <td><span className="badge">{p.kind}</span></td>
                    <td className="mono" style={{ fontSize: 12.5, wordBreak: "break-word" }}>{p.identifier}</td>
                    <td style={{ fontSize: 12, color: "var(--ink-3)" }}>{p.reason || "—"}</td>
                    <td className="mono" style={{ fontSize: 11.5 }}>{when(p.created_at)}</td>
                    <td className="actions-cell">
                      <button
                        className="btn btn-ghost"
                        title="Allow this identifier to be restored again."
                        onClick={() => {
                          if (!window.confirm(
                            `Release the tombstone for "${p.identifier}"?\n\n` +
                            "After this, a backup restore is allowed to bring it back.",
                          )) return;
                          act(`forget-${p.id}`, () => api.forgetPurge(p.id));
                        }}
                      >
                        Release
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card mt">
        <div className="card-h"><h3>How this works</h3></div>
        <div className="stack" style={{ fontSize: 13, color: "var(--ink-2)" }}>
          <p><b>Delete moves to Trash.</b> A campaign, sequence, audience or contact keeps its tags, list links and send history, and can be restored at any time.</p>
          <p><b>Backups are automatic.</b> One is taken every {backups?.interval_hours ?? 24} hours, on the 15-minute tick and again on startup, and the last {backups?.keep ?? 30} are kept. Each is written to the database and mirrored to disk so a lost database does not take the backups with it.</p>
          <p><b>Restoring is additive.</b> It re-adds what is missing and un-trashes what was trashed — it never deletes. That is deliberate: a restore can only move the data forward.</p>
          <p><b>Purging is final.</b> Removing something for good writes a tombstone, and every future restore skips it. Only <b>Release</b> on that ledger lets it come back.</p>
        </div>
        <div className="note n-info" style={{ marginTop: 14 }}>
          <span>A clean backup cannot recover something that was purged. If you might want an address or campaign back later, move it to Trash instead of removing it for good.</span>
        </div>
      </div>
    </AppShell>
  );
}
