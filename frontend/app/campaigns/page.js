"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import AppShell from "../../components/AppShell";
import CampaignTable from "../../components/CampaignTable";
import RefreshRatesButton from "../../components/RefreshRatesButton";
import { api } from "../../lib/api";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "live", label: "Running" },
  { id: "draft", label: "Not sent" },
];
const RUNNING_STATUSES = ["live", "sending", "paused"];
const NOT_SENT_STATUSES = ["draft", "ready", "approved", "scheduled"];

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const hasSending = (campaigns || []).some((c) => c.status === "sending");

  const load = useCallback(() => {
    return api.campaigns()
      .then((rows) => { setCampaigns(rows); setError(""); return rows; })
      .catch((e) => setError(e.message));
  }, []);

  // Opens/Clicks/Delivery must not sit stale on screen: poll while the tab is visible,
  // and refresh the moment the operator returns to it.
  useEffect(() => {
    load();
    const tick = () => { if (document.visibilityState === "visible") load(); };
    const id = setInterval(tick, hasSending ? 5000 : 30000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [load, hasSending]);

  // Newest refresh across every send shown on this page.
  const updatedAt = (campaigns || []).reduce(
    (acc, c) => (c.rates?.computed_at && c.rates.computed_at > acc ? c.rates.computed_at : acc),
    ""
  ) || null;

  const counts = useMemo(() => {
    const list = campaigns || [];
    return {
      all: list.length,
      live: list.filter((c) => RUNNING_STATUSES.includes(c.status)).length,
      draft: list.filter((c) => NOT_SENT_STATUSES.includes(c.status)).length,
    };
  }, [campaigns]);

  const shown = (campaigns || []).filter((c) => {
    if (filter === "live") return RUNNING_STATUSES.includes(c.status);
    if (filter === "draft") return NOT_SENT_STATUSES.includes(c.status);
    return true;
  });

  return (
    <AppShell active="/campaigns">
      <div className="page-head">
        <div>
          <h1>Sends</h1>
          <p className="sub">
            Every campaign and journey, with how each one is performing.
          </p>
        </div>
        <div className="actions">
          <span className="hint" title="This list refreshes by itself while the tab is open.">
            ⟳ auto-updating
          </span>
          <RefreshRatesButton updatedAt={updatedAt} onDone={load} />
          <Link className="btn btn-primary" href="/campaigns/new">+ New Send</Link>
        </div>
      </div>

      {error && <div className="note n-warn"><span>{error}</span></div>}
      {!campaigns && !error && (
        <div className="loading"><span className="spinner" /> Loading sends…</div>
      )}

      {campaigns && campaigns.length === 0 && (
        <div className="card">
          <div className="empty">
            <div className="e-ico">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
                <path d="M3 8l9 6 9-6" />
              </svg>
            </div>
            <b>No sends yet</b>
            <p>Create your first campaign or journey and it will show up here with its results.</p>
            <Link className="btn btn-primary" href="/campaigns/new">+ New Send</Link>
          </div>
        </div>
      )}

      {campaigns && campaigns.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div className="card-h" style={{ padding: "22px 22px 0", marginBottom: 14 }}>
            <div>
              <h3>My sends</h3>
              <span className="hint">{counts.all} in app</span>
            </div>
            {/* Narrowing the list is the first thing anyone wants on a list of sends. */}
            <div className="inline">
              {FILTERS.map((f) => (
                <button
                  key={f.id}
                  className={`btn btn-sm ${filter === f.id ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setFilter(f.id)}
                >
                  {f.label} ({counts[f.id]})
                </button>
              ))}
            </div>
          </div>

          {shown.length === 0 ? (
            <div className="empty">
              <b>Nothing in this filter</b>
              <p>No sends match “{FILTERS.find((f) => f.id === filter)?.label}”.</p>
            </div>
          ) : (
            <CampaignTable campaigns={shown} onChanged={load} />
          )}
        </div>
      )}
    </AppShell>
  );
}
