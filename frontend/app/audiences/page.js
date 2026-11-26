"use client";

import { useEffect, useRef, useState } from "react";
import AppShell from "../../components/AppShell";
import TagSelect from "../../components/TagSelect";
import AddContacts from "../../components/AddContacts";
import VerifyBadge from "../../components/VerifyBadge";
import { Pager } from "../../components/Pagination";
import { api } from "../../lib/api";

function tagList(tags) {
  const out = [];
  for (const t of tags || []) {
    const v = String(t || "").trim().toLowerCase();
    if (v && !out.includes(v)) out.push(v);
  }
  return out;
}

export default function AudiencesPage() {
  const [audiences, setAudiences] = useState([]);
  const [summaries, setSummaries] = useState(null);
  const [listLoading, setListLoading] = useState(true);
  const [summaryError, setSummaryError] = useState("");
  const [selected, setSelected] = useState(null);   // audience id
  const [loadedDetail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const selectedRef = useRef(null);
  const listRequest = useRef(null);
  const detailRequest = useRef(null);
  const detail = loadedDetail?.audience?.id === selected ? loadedDetail : null;
  const [tagFilters, setTagFilters] = useState([]);
  const [matchMode, setMatchMode] = useState("any");
  // Search inside the open list. An audience can hold thousands of addresses, so finding
  // one person by email should not mean paging through the whole list.
  const [memberQuery, setMemberQuery] = useState("");
  const searchTimer = useRef(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [busy, setBusy] = useState(false);

  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const [verifying, setVerifying] = useState(false);
  const [verifySummary, setVerifySummary] = useState(null);
  // Contact-level tag management
  const [picked, setPicked] = useState([]);
  const [tagAdd, setTagAdd] = useState([]);
  const [tagRemove, setTagRemove] = useState([]);
  const [tagBusy, setTagBusy] = useState(false);
  // Renaming the audience
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [contactPage, setContactPage] = useState(1);
  const [contactPageSize, setContactPageSize] = useState(25);

  async function loadAudiences(keepId) {
    const requestedSelection = selectedRef.current;
    listRequest.current?.abort();
    const controller = new AbortController();
    listRequest.current = controller;
    const { signal } = controller;
    setListLoading(true);
    try {
      const rows = await api.audiences({ view: "list", signal });
      if (signal.aborted) return;
      setAudiences(rows);
      setError("");
      setListLoading(false);
      if (selectedRef.current === requestedSelection) {
        const preferred = keepId ?? requestedSelection;
        const want = rows.some((row) => row.id === preferred) ? preferred : rows[0]?.id ?? null;
        if (want === requestedSelection && want != null) {
          loadDetail(want, tagFilters, matchMode, memberQuery, contactPage, contactPageSize);
        } else {
          selectAudience(want);
        }
      }
      api.audiences({ signal }).then((stats) => {
        if (signal.aborted) return;
        setSummaries(Object.fromEntries(stats.map((row) => [row.id, row])));
        setSummaryError("");
      }).catch((e) => { if (!signal.aborted) setSummaryError(e.message); });
    } catch (e) {
      if (!signal.aborted) setError(e.message);
    } finally {
      if (!signal.aborted) setListLoading(false);
    }
  }

  async function loadDetail(id, tags, match, query, page = contactPage, pageSize = contactPageSize) {
    if (!id) return;
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    const { signal } = controller;
    setDetailLoading(true);
    try {
      const q = query === undefined ? memberQuery : query;
      const response = await api.audienceContacts(
        id, tags || [], match || "any", q || "", page, pageSize, { signal, includeTags: false },
      );
      if (signal.aborted || selectedRef.current !== id) return;
      setDetail(response);
      setContactPage(response.page || 1);
      setError("");
    } catch (e) {
      if (!signal.aborted) setError(e.message);
    } finally {
      if (detailRequest.current === controller && !signal.aborted) setDetailLoading(false);
    }
  }

  function selectAudience(id) {
    clearTimeout(searchTimer.current);
    detailRequest.current?.abort();
    selectedRef.current = id;
    setSelected(id);
    setDetail(null);
    setTagFilters([]);
    setMatchMode("any");
    setMemberQuery("");
    setPicked([]);
    setTagAdd([]);
    setTagRemove([]);
    setVerifySummary(null);
    setEditing(false);
    setContactPage(1);
    if (id != null) loadDetail(id, [], "any", "", 1, contactPageSize);
    else setDetailLoading(false);
  }

  useEffect(() => {
    loadAudiences();
    return () => {
      listRequest.current?.abort();
      detailRequest.current?.abort();
      clearTimeout(searchTimer.current);
    };
  }, []);

  function say(msg) {
    setFlash(msg);
    setTimeout(() => setFlash(""), 7000);
  }

  async function createAudience(e) {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy(true);
    setError("");
    try {
      const a = await api.createAudience(newName.trim(), newDesc.trim());
      setNewName("");
      setNewDesc("");
      setShowNew(false);
      say(`Audience “${a.name}” created`);
      await loadAudiences(a.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteAudience(id, name) {
    if (!window.confirm(`Move “${name}” to Trash?\n\nNothing is erased. The list keeps its members, and you can restore it from Trash & backup at any time.`)) return;
    setBusy(true);
    try {
      await api.deleteAudience(id);
      say(`“${name}” moved to Trash - restore it any time`);
      selectAudience(null);
      setDetail(null);
      await loadAudiences(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // ---------------------------------------------------- rename the audience
  function startEdit(aud) {
    setEditName(aud?.name || "");
    setEditDesc(aud?.description || "");
    setEditing(true);
    setError("");
  }

  function cancelEdit() {
    setEditing(false);
    setError("");
  }

  /** The list keeps its id and memberships, so a rename disturbs nothing. */
  async function saveEdit() {
    if (!current) return;
    const name = editName.trim();
    if (!name) {
      setError("Audience name cannot be empty");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await api.updateAudience(current.id, { name, description: editDesc.trim() });
      setEditing(false);
      say(res.renamed_from
        ? `Renamed “${res.renamed_from}” to “${res.name}” - members, tags and sends are unaffected`
        : "Audience updated");
      await loadAudiences(current.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // ---------------------------------------------------- contact tag editing
  function togglePick(email) {
    setPicked((prev) => (prev.includes(email) ? prev.filter((e) => e !== email) : [...prev, email]));
  }

  function pickAll() {
    setPicked((detail?.contacts || []).map((c) => c.email));
  }

  function clearPicks() {
    setPicked([]);
    setTagAdd([]);
    setTagRemove([]);
  }

  /** Add and/or remove tags across every selected contact in one action. */
  async function applyBulkTags() {
    if (!picked.length || (!tagAdd.length && !tagRemove.length)) return;
    setTagBusy(true);
    setError("");
    try {
      const res = await api.bulkTags(selected, picked, tagAdd, tagRemove);
      const bits = [];
      if (res.added.length) bits.push(`+${res.added.join(", ")}`);
      if (res.removed.length) bits.push(`−${res.removed.join(", ")}`);
      say(`${bits.join(" · ")} on ${res.changed} of ${res.contacts} contact(s)`);
      clearPicks();
      await loadAudiences(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setTagBusy(false);
    }
  }

  /** Strip one tag from one contact, straight from its chip. */
  async function removeOneTag(email, tag) {
    setTagBusy(true);
    setError("");
    try {
      await api.bulkTags(selected, [email], [], [tag]);
      say(`“${tag}” removed from ${email}`);
      await loadAudiences(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setTagBusy(false);
    }
  }

  /** Change a tag everywhere - blank deletes it from every contact. */
  async function changeTagEverywhere(oldTag) {
    const next = window.prompt(
      `Rename “${oldTag}” to…\n\nThis renames the tag across every contact in this audience only.`,
      oldTag,
    );
    if (next === null) return;
    if (!String(next).trim()) {
      setError("A new name is required. To delete a tag use Remove - it only works once no contact carries the tag.");
      return;
    }
    setTagBusy(true);
    setError("");
    try {
      const res = await api.renameTag(selected, oldTag, String(next).trim());
      say(`“${oldTag}” renamed to “${res.to}” on ${res.contacts_changed} contact(s)`);
      await loadAudiences(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setTagBusy(false);
    }
  }

  /** Define a new tag for this audience, before anything uses it. */
  async function createTag() {
    const name = window.prompt("New tag name for this audience:");
    if (name === null) return;
    if (!String(name).trim()) return;
    setTagBusy(true);
    setError("");
    try {
      const res = await api.createAudienceTag(selected, String(name).trim());
      say(`Tag “${res.tag}” created in ${res.audience}`);
      await loadAudiences(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setTagBusy(false);
    }
  }

  /** Delete a tag - refused by the server while any contact still carries it. */
  async function deleteTag(tag, count) {
    if (count > 0) {
      setError(
        `“${tag}” is still on ${count} contact${count !== 1 ? "s" : ""} in this audience. `
        + `Remove the tag from them first - then you can remove the tag.`,
      );
      return;
    }
    if (!window.confirm(`Remove the tag “${tag}” from this audience?\n\nNo contact carries it, so nothing else changes.`)) return;
    setTagBusy(true);
    setError("");
    try {
      await api.deleteAudienceTag(selected, tag);
      say(`Tag “${tag}” removed`);
      await loadAudiences(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setTagBusy(false);
    }
  }

  async function removeMember(email) {
    if (!selected) return;
    setBusy(true);
    try {
      await api.removeAudienceContacts(selected, { emails: [email] });
      say(`${email} removed from this audience`);
      await loadAudiences(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteContact(email) {
    if (!window.confirm(`Move ${email} to Trash?\n\nNothing is erased - its tags and list links are kept, and you can restore it from Trash & backup.`)) return;
    setBusy(true);
    try {
      await api.deleteContact(email);
      say(`${email} moved to Trash - restore it any time`);
      await loadAudiences(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  /** Check syntax, MX, disposable and role for every contact in this audience. */
  async function verifyAudience() {
    if (!selected) return;
    setVerifying(true);
    setError("");
    setVerifySummary(null);
    try {
      const res = await api.validateAudience(selected);
      setVerifySummary(res.summary);
      say(
        `Checked ${res.checked} address(es) - ${res.summary.blocked} blocked, `
        + `${res.summary.risky} risky, ${res.summary.counts.valid || 0} clean`,
      );
      await loadAudiences(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setVerifying(false);
    }
  }

  async function applyTagFilters(next, match) {
    clearTimeout(searchTimer.current);
    const tags = next === undefined ? tagFilters : next;
    const mode = match === undefined ? matchMode : match;
    setTagFilters(tags);
    setMatchMode(mode);
    setContactPage(1);
    await loadDetail(selected, tags, mode, memberQuery, 1, contactPageSize);
  }

  // Typing in the member search waits 300ms before asking the API, so a 12-character
  // address is one request rather than twelve.
  function searchMembers(value) {
    setMemberQuery(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    detailRequest.current?.abort();
    setDetailLoading(true);
    searchTimer.current = setTimeout(() => {
      setContactPage(1);
      loadDetail(selected, tagFilters, matchMode, value, 1, contactPageSize);
    }, 300);
  }

  const lists = audiences.map((row) => ({ ...summaries?.[row.id], ...row }));
  const totalMembers = lists.reduce((n, a) => n + (a.members || 0), 0);
  const totalUsable = lists.reduce((n, a) => n + (a.usable || 0), 0);
  const allTags = [...new Set(lists.flatMap((a) => Object.keys(a.tags || {})))].sort();
  const current = lists.find((a) => a.id === selected) || null;
  // Tags for the OPEN audience only - never borrowed from another list.
  const audienceTagOptions = Object.entries(detail?.tags || current?.tags || {})
    .map(([tag, contacts]) => ({ tag, contacts }));
  const contactsPager = {
    rows: detail?.contacts || [],
    total: detail?.count || 0,
    page: detail?.page || contactPage,
    pages: detail?.pages || 1,
    perPage: detail?.page_size || contactPageSize,
    start: ((detail?.page || contactPage) - 1) * (detail?.page_size || contactPageSize),
    end: Math.min(
      (detail?.page || contactPage) * (detail?.page_size || contactPageSize),
      detail?.count || 0,
    ),
    setPage: (page) => {
      setContactPage(page);
      loadDetail(selected, tagFilters, matchMode, memberQuery, page, contactPageSize);
    },
    setPerPage: (size) => {
      setContactPage(1);
      setContactPageSize(size);
      loadDetail(selected, tagFilters, matchMode, memberQuery, 1, size);
    },
  };

  return (
    <AppShell>
      <div className="page-head">
        <div>
          <div className="crumb">Automation / Audiences</div>
          <h1>Audiences</h1>
          <div className="hint">
            Saved recipient lists you can reuse in any send. Segment one list with tags.
            Unsubscribed and suppressed addresses are always excluded.
          </div>
        </div>
        <div className="top-actions">
          <button className="btn btn-secondary" onClick={() => loadAudiences(selected)} disabled={busy}>Refresh</button>
          <button className="btn btn-primary" onClick={() => setShowNew((v) => !v)}>
            {showNew ? "Cancel" : "+ New audience"}
          </button>
        </div>
      </div>

      {error ? <div className="note n-warn" style={{ marginBottom: 14 }}><span><b>Error:</b> {error}</span></div> : null}
      {summaryError ? <div className="note n-warn" style={{ marginBottom: 14 }}><span>Audience totals unavailable. {summaryError}</span></div> : null}
      {flash ? <div className="note n-success" style={{ marginBottom: 14 }}><span>{flash}</span></div> : null}

      <div className="grid kpis" style={{ marginBottom: 16 }}>
        <div className="kpi">
          <div className="k-label">Audiences</div>
          <div className="k-value">{listLoading && !audiences.length ? "…" : audiences.length}</div>
          <div className="k-sub">saved lists</div>
        </div>
        <div className="kpi">
          <div className="k-label">Contacts</div>
          <div className="k-value">{summaries ? totalMembers : "…"}</div>
          <div className="k-sub">across all audiences</div>
        </div>
        <div className="kpi ok">
          <div className="k-label">Usable recipients</div>
          <div className="k-value">{summaries ? totalUsable : "…"}</div>
          <div className="k-sub">subscribed &amp; not suppressed</div>
        </div>
        <div className="kpi">
          <div className="k-label">Tags</div>
          <div className="k-value">{summaries ? allTags.length : "…"}</div>
          <div className="k-sub">{summaries ? allTags.slice(0, 4).join(", ") || "none yet" : "Loading totals"}</div>
        </div>
      </div>

      {showNew ? (
        <form className="card" onSubmit={createAudience} style={{ marginBottom: 16 }}>
          <div className="card-h"><h3>New audience</h3><span className="hint">a named, reusable list</span></div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <input
              style={{ flex: "1 1 240px" }} placeholder="Name - e.g. Travilo Customers"
              value={newName} onChange={(e) => setNewName(e.target.value)} autoFocus
            />
            <input
              style={{ flex: "2 1 320px" }} placeholder="Description (optional)"
              value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
            />
            <button className="btn btn-primary" type="submit" disabled={busy || !newName.trim()}>
              {busy ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
      ) : null}

      {listLoading && !audiences.length ? (
        <div className="card loading" role="status"><span className="spinner" /> Loading audiences…</div>
      ) : error && !audiences.length ? null : audiences.length === 0 ? (
        <div className="card">
          <div className="note n-info" style={{ margin: 0 }}>
            <span>
              No saved audiences yet. Create one, paste your addresses, then pick it when you
              create a send - no more re-pasting the same list every time.
            </span>
          </div>
        </div>
      ) : (
        <div className="audience-layout">
          <div className="card">
            <div className="card-h"><h3>Your lists</h3><span className="hint">{audiences.length}</span></div>
            {lists.map((a) => (
              <button
                key={a.id}
                onClick={() => selectAudience(a.id)}
                style={{
                  display: "block", width: "100%", textAlign: "left", cursor: "pointer",
                  padding: "10px 12px", marginBottom: 8, borderRadius: 8,
                  border: selected === a.id ? "1px solid var(--brand-600)" : "1px solid var(--border)",
                  background: selected === a.id ? "var(--brand-50, rgba(99,102,241,0.06))" : "transparent",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                  <b style={{ fontSize: 13 }}>{a.name}</b>
                  {a.usable != null ? <span className="badge b-neutral">{a.usable} usable</span> : null}
                </div>
                {a.description ? (
                  <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 3 }}>{a.description}</div>
                ) : null}
                <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
                  {a.members != null ? `${a.members} contact(s)` : ""}
                  {Object.keys(a.tags || {}).length
                    ? ` · ${Object.keys(a.tags).map((t) => `${t}(${a.tags[t]})`).join(" ")}`
                    : ""}
                </div>
              </button>
            ))}
          </div>

          <div>
            {!current ? (
              <div className="card"><div className="hint">Select a list on the left.</div></div>
            ) : (
              <>
                  <div className="card">
                    <div className="card-h">
                      {editing ? (
                        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", flex: 1 }}>
                          <input
                            autoFocus
                            value={editName}
                            onChange={(e) => setEditName(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") saveEdit();
                              if (e.key === "Escape") cancelEdit();
                            }}
                            placeholder="Audience name"
                            style={{ fontSize: 15, fontWeight: 700, padding: "5px 9px", borderRadius: 7, border: "1px solid var(--brand-600)", minWidth: 220 }}
                          />
                          <input
                            value={editDesc}
                            onChange={(e) => setEditDesc(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") saveEdit(); if (e.key === "Escape") cancelEdit(); }}
                            placeholder="Description (optional)"
                            style={{ fontSize: 12.5, padding: "5px 9px", borderRadius: 7, border: "1px solid var(--border)", flex: "1 1 240px" }}
                          />
                          <button className="btn btn-primary" onClick={saveEdit} disabled={busy || !editName.trim()}>
                            {busy ? "Saving…" : "Save"}
                          </button>
                          <button className="btn btn-secondary" onClick={cancelEdit} disabled={busy}>
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <>
                          <div>
                            <h3 style={{ marginBottom: 2 }}>{current.name}</h3>
                            {current.description ? (
                              <div style={{ fontSize: 12, color: "var(--ink-3)" }}>{current.description}</div>
                            ) : null}
                          </div>
                          <div className="inline">
                            {current.members != null ? <span className="badge b-neutral">{current.members} contact(s)</span> : null}
                            {current.usable != null ? <span className="badge b-success">{current.usable} usable</span> : null}
                            {current.blocked ? (
                              <span className="badge b-danger" title="Invalid syntax or no mail server - never sent to">
                                {current.blocked} blocked
                              </span>
                            ) : null}
                            {current.risky ? (
                              <span className="badge b-warn" title="Disposable or role mailbox - deliverable but lower value">
                                {current.risky} risky
                              </span>
                            ) : null}
                            <button className="btn btn-secondary" onClick={() => startEdit(current)} disabled={busy || verifying}>
                              ✎ Rename
                            </button>
                            <button className="btn btn-secondary" onClick={verifyAudience} disabled={verifying || busy}>
                              {verifying ? "Checking…" : "✓ Verify emails"}
                            </button>
                            <button className="btn btn-secondary" onClick={() => deleteAudience(current.id, current.name)} disabled={busy}>
                              Move to Trash
                            </button>
                          </div>
                        </>
                      )}
                    </div>

                  {verifySummary ? (
                    <div className="note n-info" style={{ marginTop: 0, marginBottom: 14 }}>
                      <span>
                        Checked syntax, mail server, disposable and role for every contact -{" "}
                        <b>{verifySummary.counts.valid || 0}</b> clean,{" "}
                        <b>{verifySummary.counts.role || 0}</b> role inboxes,{" "}
                        <b>{verifySummary.counts.disposable || 0}</b> disposable,{" "}
                        <b>{verifySummary.counts.no_mx || 0}</b> with no mail server,{" "}
                        <b>{verifySummary.counts.invalid || 0}</b> invalid.
                        {verifySummary.blocked
                          ? ` ${verifySummary.blocked} address(es) are excluded from every send.`
                          : " Nothing is blocked."}
                      </span>
                    </div>
                  ) : null}
                    <AddContacts
                      key={current.id}
                      audienceId={current.id}
                      tagOptions={audienceTagOptions}
                        disabled={busy}
                        onAdded={async () => { await loadAudiences(current.id); }}
                      />
                  </div>

                  {/* Tags belong to this audience alone, and can be deleted once unused. */}
                  <div className="card">
                    <div className="card-h">
                      <h3>Tags in this audience</h3>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span className="hint">{audienceTagOptions.length} defined</span>
                        <button className="btn btn-secondary" onClick={createTag} disabled={tagBusy}>
                          + New tag
                        </button>
                      </div>
                    </div>

                    {audienceTagOptions.length === 0 ? (
                      <div className="hint">
                        No tags yet in this list. Create one - a tag here never appears in any other audience.
                      </div>
                    ) : (
                      <>
                        <table className="t">
                          <thead>
                            <tr><th>Tag</th><th>Contacts</th><th>Status</th><th /></tr>
                          </thead>
                          <tbody>
                            {audienceTagOptions.map(({ tag, contacts }) => {
                              const inUse = contacts > 0;
                              return (
                                <tr key={tag}>
                                  <td><span className="badge b-neutral">{tag}</span></td>
                                  <td>{contacts}</td>
                                  <td>
                                    {inUse ? (
                                      <span className="badge b-warn">in use - cannot remove yet</span>
                                    ) : (
                                      <span className="badge b-success">unused - removable</span>
                                    )}
                                  </td>
                                  <td style={{ whiteSpace: "nowrap" }}>
                                    <button className="btn btn-secondary" disabled={tagBusy}
                                      onClick={() => changeTagEverywhere(tag)}>
                                      ✎ Rename
                                    </button>{" "}
                                    <button
                                      className="btn btn-secondary"
                                      disabled={tagBusy}
                                      style={inUse ? { opacity: 0.5, cursor: "not-allowed" } : { color: "var(--danger, #B42318)" }}
                                      title={inUse
                                        ? `${contacts} contact(s) still carry this tag - remove the tag from them first`
                                        : "Remove this tag from the audience"}
                                      onClick={() => deleteTag(tag, contacts)}
                                    >
                                      ✕ Remove
                                    </button>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                        <div className="hint" style={{ marginTop: 8 }}>
                          A tag can only be removed once no contact carries it. Select the contacts above,
                          use <b>Remove tag</b>, then come back and remove the tag itself.
                        </div>
                      </>
                    )}
                  </div>


                <div className="card">
                  <div className="card-h">
                    <h3>Members</h3>
                    <span className="hint">
                      {detailLoading ? "Updating…" : detail ? `${detail.count} contact(s)` : "-"}
                      {tagFilters.length ? ` · ${tagFilters.join(matchMode === "all" ? " AND " : " OR ")}` : ""}
                    </span>
                  </div>

                  <div style={{ marginBottom: 12, maxWidth: 480 }}>
                    <input
                      type="search"
                      value={memberQuery}
                      onChange={(e) => searchMembers(e.target.value)}
                      placeholder="Search this audience by email or name..."
                      aria-label="Search members by email or name"
                      style={{ width: "100%", padding: "9px 12px", borderRadius: 8,
                               border: "1px solid #d8dbe6", fontSize: 14 }}
                    />
                    {memberQuery ? (
                      <div className="hint" style={{ marginTop: 6 }}>
                        {detail && !detailLoading ? `${detail.count} match(es) for "${memberQuery}"` : "searching..."} -{" "}
                        <a href="#" onClick={(e) => { e.preventDefault(); searchMembers(""); }}>clear</a>
                      </div>
                    ) : null}
                  </div>

                  <div style={{ marginBottom: 12, maxWidth: 480 }}>
                    <TagSelect
                      options={audienceTagOptions}
                      value={tagFilters}
                      onChange={(next) => applyTagFilters(next, matchMode)}
                      placeholder="All members - or filter by tags…"
                      allowCreate={false}
                      matchMode={matchMode}
                      onMatchModeChange={(m) => applyTagFilters(undefined, m)}
                      hint={tagFilters.length > 1
                        ? `Showing members matching ${matchMode === "all" ? "all" : "any"} of the selected tags.`
                        : ""}
                    />
                  </div>

                    {!detail || detail.contacts.length === 0 ? (
                      <div className="hint" role="status">
                        {detailLoading ? "Loading contacts…" : detail ? "No contacts match this view." : "Contacts unavailable."}
                      </div>
                    ) : (
                      <>
                        {/* bulk tag actions on the selected contacts */}
                        <div
                          style={{
                            display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap",
                            padding: "10px 12px", marginBottom: 10, borderRadius: 8,
                            border: picked.length ? "1px solid var(--brand-200, rgba(99,102,241,0.35))" : "1px solid var(--border)",
                            background: picked.length ? "var(--brand-50, rgba(99,102,241,0.05))" : "var(--surface-2, #fafafa)",
                          }}
                        >
                          <div className="inline">
                            <button className="btn btn-secondary" onClick={picked.length ? clearPicks : pickAll} disabled={tagBusy}>
                              {picked.length ? "Clear selection" : "Select all"}
                            </button>
                            <span style={{ fontSize: 12, fontWeight: 700 }}>
                              {picked.length ? `${picked.length} selected` : "Select contacts to tag them in bulk"}
                            </span>
                          </div>

                          {picked.length ? (
                            <div style={{ display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap", flex: "1 1 420px" }}>
                              <div style={{ flex: "1 1 190px" }}>
                                <TagSelect
                                  options={audienceTagOptions}
                                  value={tagAdd}
                                  onChange={setTagAdd}
                                  placeholder="Add tag…"
                                  allowCreate
                                  disabled={tagBusy}
                                />
                              </div>
                              <div style={{ flex: "1 1 190px" }}>
                                <TagSelect
                                  options={audienceTagOptions}
                                  value={tagRemove}
                                  onChange={setTagRemove}
                                  placeholder="Remove tag…"
                                  allowCreate={false}
                                  disabled={tagBusy}
                                />
                              </div>
                              <button
                                className="btn btn-primary"
                                onClick={applyBulkTags}
                                disabled={tagBusy || (!tagAdd.length && !tagRemove.length)}
                              >
                                {tagBusy ? "Applying…" : `Apply to ${picked.length}`}
                              </button>
                            </div>
                          ) : null}
                        </div>

                        <table className="t">
                          <thead>
                            <tr>
                              <th style={{ width: 28 }} />
                              <th>Name</th><th>Email</th><th>Deliverability</th><th>Tags</th><th>Status</th><th />
                            </tr>
                          </thead>
                          <tbody>
                            {contactsPager.rows.map((c) => (
                              <tr key={c.email} style={{ background: picked.includes(c.email) ? "var(--brand-50, rgba(99,102,241,0.05))" : undefined }}>
                                <td>
                                  <input
                                    type="checkbox"
                                    checked={picked.includes(c.email)}
                                    onChange={() => togglePick(c.email)}
                                    aria-label={`Select ${c.email}`}
                                  />
                                </td>
                                <td>{c.name ? <b style={{ fontSize: 12.5 }}>{c.name}</b> : <span className="hint">No name</span>}</td>
                                <td className="mono" style={{ fontSize: 12 }}>{c.email}</td>
                                <td><VerifyBadge status={c.email_status} reason={c.email_reason} /></td>
                                <td>
                                  {tagList(c.tags).length ? (
                                    tagList(c.tags).map((t) => (
                                      <span
                                        key={t}
                                        className="badge b-neutral"
                                        style={{ marginRight: 4, display: "inline-flex", alignItems: "center", gap: 4 }}
                                      >
                                        <span
                                          role="button"
                                          tabIndex={0}
                                          title={`Change “${t}” everywhere`}
                                          style={{ cursor: "pointer" }}
                                          onClick={() => changeTagEverywhere(t)}
                                        >
                                          {t}
                                        </span>
                                        <span
                                          role="button"
                                          tabIndex={0}
                                          title={`Remove “${t}” from this contact`}
                                          style={{ cursor: "pointer", fontWeight: 700, opacity: 0.7 }}
                                          onClick={() => removeOneTag(c.email, t)}
                                        >
                                          ×
                                        </span>
                                      </span>
                                    ))
                                  ) : (
                                    <span className="hint">no tags</span>
                                  )}
                                </td>
                                <td>
                                  <span className={`badge ${c.status === "subscribed" ? "b-success" : c.status === "suppressed" ? "b-danger" : "b-neutral"}`}>
                                    {c.status}
                                  </span>
                                </td>
                                <td style={{ whiteSpace: "nowrap" }}>
                                  <button className="btn btn-secondary" onClick={() => removeMember(c.email)} disabled={busy}>
                                    Remove
                                  </button>{" "}
                                  <button className="btn btn-secondary" onClick={() => deleteContact(c.email)} disabled={busy}>
                                    Trash
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <Pager pager={contactsPager} label="contacts" />
                        <div className="hint" style={{ marginTop: 8 }}>
                          Click a tag name to rename or delete it everywhere · click its × to remove it from that one contact.
                        </div>
                      </>
                    )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}
