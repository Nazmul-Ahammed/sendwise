"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "../../../components/AppShell";
import { api } from "../../../lib/api";

const UNITS = ["minute", "hour", "day", "week"];

function emptyStage(n) {
  return {
    stage: n,
    focus: "",
    delay_value: n === 1 ? 0 : 3,
    delay_unit: "day",
    delay_note: "",
    emails: [],
  };
}

function emptyEmail(index) {
  return {
    ref: `E${index}`,
    name: "",
    branch: "main",
    open_click_next: "",
    open_no_click_next: "",
    not_open_next: "",
  };
}

export default function SequenceEditor() {
  const params = useParams();
  const router = useRouter();
  const slug = Array.isArray(params?.slug) ? params.slug[0] : params?.slug;

  const [seq, setSeq] = useState(null);
  const [stages, setStages] = useState([]);
  const [form, setForm] = useState({ name: "", description: "", trigger: "", slug: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!slug) return;
    api.sequences()
      .then((rows) => {
        const found = (rows || []).find((s) => s.slug === slug);
        if (!found) {
          setError(`No sequence called “${slug}”.`);
          return;
        }
        setSeq(found);
        setForm({
          name: found.name || "",
          description: found.description || "",
          trigger: found.trigger || "",
          slug: found.slug,
        });
        setStages(JSON.parse(JSON.stringify(found.stages || [])));
      })
      .catch((e) => setError(e.message));
  }, [slug]);

  const refs = useMemo(
    () => stages.flatMap((st) => (st.emails || []).map((e) => (e.ref || "").trim().toUpperCase())).filter(Boolean),
    [stages],
  );

  function mark() { setDirty(true); setFlash(""); }

  function patchStage(i, key, value) {
    setStages((prev) => prev.map((st, n) => (n === i ? { ...st, [key]: value } : st)));
    mark();
  }

  function patchEmail(i, j, key, value) {
    setStages((prev) => prev.map((st, n) => {
      if (n !== i) return st;
      const emails = (st.emails || []).map((em, m) => (m === j ? { ...em, [key]: value } : em));
      return { ...st, emails };
    }));
    mark();
  }

  function addStage() {
    setStages((prev) => [...prev, emptyStage(prev.length + 1)]);
    mark();
  }

  function removeStage(i) {
    if (!window.confirm("Remove this stage? Its emails go with it.")) return;
    setStages((prev) => prev.filter((_, n) => n !== i).map((st, n) => ({ ...st, stage: n + 1 })));
    mark();
  }

  function addEmail(i) {
    const nextIndex = stages.reduce((acc, st) => acc + (st.emails || []).length, 0) + 1;
    setStages((prev) => prev.map((st, n) => (
      n === i ? { ...st, emails: [...(st.emails || []), emptyEmail(nextIndex)] } : st
    )));
    mark();
  }

  function removeEmail(i, j) {
    setStages((prev) => prev.map((st, n) => (
      n === i ? { ...st, emails: (st.emails || []).filter((_, m) => m !== j) } : st
    )));
    mark();
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      const updated = await api.updateSequence(slug, {
        name: form.name.trim(),
        description: form.description,
        trigger: form.trigger,
        stages,
        rules: seq?.rules || [],
      });
      setSeq(updated);
      setStages(JSON.parse(JSON.stringify(updated.stages || [])));
      setDirty(false);
      setFlash("Sequence saved. New sends that use it will follow these rules.");
      setTimeout(() => setFlash(""), 9000);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function duplicate() {
    setBusy(true);
    setError("");
    try {
      const copy = await api.duplicateSequence(slug);
      router.push(`/sequences/${copy.slug}`);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Move “${form.name}” to Trash?\n\nAnything can be restored from Trash & backup.`)) return;
    setBusy(true);
    try {
      await api.deleteSequence(slug);
      router.push("/sequences");
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  const branchMap = stages.flatMap((st) => (st.emails || []).map((em) => {
    const parts = [];
    if ((em.open_click_next || "").trim()) parts.push(`clicked → ${em.open_click_next}`);
    if ((em.open_no_click_next || "").trim()) parts.push(`opened → ${em.open_no_click_next}`);
    if ((em.not_open_next || "").trim()) parts.push(`not opened → ${em.not_open_next}`);
    return { ref: em.ref, name: em.name, parts };
  }));

  return (
    <AppShell active="/sequences">
      <div className="page-head">
        <div>
          <h1>{form.name || "Sequence"}</h1>
          <div className="hint">
            The <b>journey</b> a contact travels. A send picks this sequence and supplies the recipients.
          </div>
        </div>
        <div className="actions">
          <button className="btn btn-secondary" onClick={duplicate} disabled={busy}>Duplicate</button>
          <button className="btn btn-secondary" onClick={remove} disabled={busy}>Move to Trash</button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !dirty}>
            {busy ? "Saving…" : dirty ? "Save changes" : "Saved"}
          </button>
        </div>
      </div>

      {error ? <div className="note n-warn" style={{ marginBottom: 14 }}><span><b>Error:</b> {error}</span></div> : null}
      {flash ? <div className="note n-success" style={{ marginBottom: 14 }}><span>{flash}</span></div> : null}
      {dirty ? (
        <div className="note n-info" style={{ marginBottom: 14 }}>
          <span>You have unsaved changes. Nothing changes for running sends until you press <b>Save changes</b>.</span>
        </div>
      ) : null}
      {!seq && !error ? <div className="loading"><span className="spinner" /> Loading sequence…</div> : null}

      {seq ? (
        <>
          <div className="card">
            <div className="card-h"><h3>Details</h3><span className="hint">slug {form.slug} · cannot be changed</span></div>
            <div style={{ display: "grid", gap: 12 }}>
              <div className="field" style={{ marginBottom: 0 }}>
                <label>Name</label>
                <input className="input" value={form.name} onChange={(e) => { setForm((f) => ({ ...f, name: e.target.value })); mark(); }} />
              </div>
              <div className="field" style={{ marginBottom: 0 }}>
                <label>Description</label>
                <input className="input" value={form.description} onChange={(e) => { setForm((f) => ({ ...f, description: e.target.value })); mark(); }} placeholder="What this journey is for" />
              </div>
              <div className="field" style={{ marginBottom: 0 }}>
                <label>Entry trigger</label>
                <input className="input" value={form.trigger} onChange={(e) => { setForm((f) => ({ ...f, trigger: e.target.value })); mark(); }} placeholder="When a contact enters this sequence" />
              </div>
            </div>
          </div>

          {/* The branch map - the whole flow on one screen. */}
          <div className="card">
            <div className="card-h"><h3>Journey map</h3><span className="hint">{refs.length} emails · {stages.length} stages</span></div>
            {branchMap.length === 0 ? (
              <div className="hint">No emails yet - add a stage below.</div>
            ) : (
              <div style={{ display: "grid", gap: 6 }}>
                {branchMap.map((b) => (
                  <div key={b.ref} style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap", fontSize: 12.5 }}>
                    <b className="mono" style={{ minWidth: 34 }}>{b.ref}</b>
                    <span style={{ color: "var(--ink-3)", flex: "1 1 200px" }}>{b.name || "-"}</span>
                    <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-2)" }}>
                      {b.parts.length ? b.parts.join("  ·  ") : "ends the sequence"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {stages.map((st, i) => (
            <div className="card" style={{ marginBottom: 16 }} key={`stage-${i}`}>
              <div className="card-h">
                <h3>Stage {st.stage} {st.focus ? <span className="hint" style={{ fontWeight: 400 }}>· {st.focus}</span> : null}</h3>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span className="badge b-neutral">{(st.emails || []).length} email(s)</span>
                  <button className="btn btn-secondary" onClick={() => removeStage(i)} disabled={busy}>Remove stage</button>
                </div>
              </div>

              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
                <div className="field" style={{ flex: "2 1 260px", marginBottom: 0 }}>
                  <label>Stage focus</label>
                  <input className="input" value={st.focus || ""} onChange={(e) => patchStage(i, "focus", e.target.value)} placeholder="What this stage is trying to do" />
                </div>
                <div className="field" style={{ flex: "0 0 110px", marginBottom: 0 }}>
                  <label>Wait after</label>
                  <input className="input" type="number" min="0" value={st.delay_value ?? 0} onChange={(e) => patchStage(i, "delay_value", Number(e.target.value))} />
                </div>
                <div className="field" style={{ flex: "0 0 120px", marginBottom: 0 }}>
                  <label>&nbsp;</label>
                  <select className="input" value={st.delay_unit || "day"} onChange={(e) => patchStage(i, "delay_unit", e.target.value)}>
                    {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
                  </select>
                </div>
              </div>

              {(st.emails || []).map((em, j) => (
                <div key={`${i}-${j}`} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, marginBottom: 10 }}>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 10 }}>
                    <div className="field" style={{ flex: "0 0 92px", marginBottom: 0 }}>
                      <label>Ref</label>
                      <input className="input mono" value={em.ref || ""} onChange={(e) => patchEmail(i, j, "ref", e.target.value.toUpperCase())} />
                    </div>
                    <div className="field" style={{ flex: "2 1 220px", marginBottom: 0 }}>
                      <label>Email name</label>
                      <input className="input" value={em.name || ""} onChange={(e) => patchEmail(i, j, "name", e.target.value)} placeholder="What this email says" />
                    </div>
                    <button className="btn btn-secondary" onClick={() => removeEmail(i, j)} disabled={busy}>Remove</button>
                  </div>

                  <div style={{ fontSize: 11.5, fontWeight: 700, color: "var(--ink-3)", marginBottom: 6 }}>
                    AFTER THIS EMAIL, GO TO
                  </div>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <div className="field" style={{ flex: "1 1 150px", marginBottom: 0 }}>
                      <label>Clicked the link</label>
                      <select className="input" value={em.open_click_next || ""} onChange={(e) => patchEmail(i, j, "open_click_next", e.target.value)}>
                        <option value="">- end the sequence -</option>
                        {refs.filter((r) => r !== (em.ref || "").toUpperCase()).map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </div>
                    <div className="field" style={{ flex: "1 1 150px", marginBottom: 0 }}>
                      <label>Opened, no click</label>
                      <select className="input" value={em.open_no_click_next || ""} onChange={(e) => patchEmail(i, j, "open_no_click_next", e.target.value)}>
                        <option value="">- end the sequence -</option>
                        {refs.filter((r) => r !== (em.ref || "").toUpperCase()).map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </div>
                    <div className="field" style={{ flex: "1 1 150px", marginBottom: 0 }}>
                      <label>Did not open</label>
                      <select className="input" value={em.not_open_next || ""} onChange={(e) => patchEmail(i, j, "not_open_next", e.target.value)}>
                        <option value="">- end the sequence -</option>
                        {refs.filter((r) => r !== (em.ref || "").toUpperCase()).map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </div>
                  </div>
                </div>
              ))}

              <button className="btn btn-secondary" onClick={() => addEmail(i)} disabled={busy}>+ Add email to this stage</button>
            </div>
          ))}

          <div style={{ display: "flex", gap: 10, marginBottom: 24 }}>
            <button className="btn btn-secondary" onClick={addStage} disabled={busy}>+ Add stage</button>
            <button className="btn btn-primary" onClick={save} disabled={busy || !dirty}>
              {busy ? "Saving…" : dirty ? "Save changes" : "Saved"}
            </button>
          </div>
        </>
      ) : null}
    </AppShell>
  );
}
