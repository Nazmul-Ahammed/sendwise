"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
export default function CampaignsPage() {
  const [result, setResult] = useState(null);
  const [rates, setRates] = useState({});
  const [error, setError] = useState("");
  const [metricsError, setMetricsError] = useState("");
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(1);
  const inFlight = useRef(null);
  const hasSending = result?.has_sending || false;

  const load = useCallback(async ({ replace = false } = {}) => {
    if (inFlight.current && !replace) return;
    inFlight.current?.abort();
    const controller = new AbortController();
    const { signal } = controller;
    inFlight.current = controller;
    setLoading(true);
    try {
      const next = await api.sendsPage(page, filter, signal);
      if (signal.aborted) return;
      setResult(next);
      setError("");
      setLoading(false);
      try {
        const metrics = await api.sendsMetrics(next.items.map((row) => row.slug), signal);
        if (!signal.aborted) {
          setRates(metrics);
          setMetricsError("");
        }
      } catch (e) {
        if (!signal.aborted) setMetricsError(e.message);
      }
    } catch (e) {
      if (!signal.aborted) setError(e.message);
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null;
        setLoading(false);
      }
    }
  }, [filter, page]);

  useEffect(() => {
    load({ replace: true });
    return () => inFlight.current?.abort();
  }, [load]);

  useEffect(() => {
    const tick = () => { if (document.visibilityState === "visible") load(); };
    const id = setInterval(tick, hasSending ? 5000 : 30000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [load, hasSending]);

  const shown = (result?.items || []).map((row) => ({ ...row, rates: rates[row.slug] }));
  const counts = result?.counts || { all: 0, live: 0, draft: 0 };
  const updatedAt = shown.reduce(
    (acc, c) => (c.rates?.computed_at && c.rates.computed_at > acc ? c.rates.computed_at : acc),
    ""
  ) || null;

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
          <RefreshRatesButton updatedAt={updatedAt} onDone={() => load({ replace: true })} />
          <Link className="btn btn-primary" href="/campaigns/new">+ New Send</Link>
        </div>
      </div>

      {error && <div className="note n-warn"><span>{error}</span></div>}
      {metricsError && <div className="note n-warn"><span>Metrics unavailable. {metricsError}</span></div>}
      {!result && !error && (
        <div className="loading"><span className="spinner" /> Loading sends…</div>
      )}

      {result && counts.all === 0 && (
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

      {result && counts.all > 0 && (
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
                  onClick={() => { setFilter(f.id); setPage(1); }}
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
            <CampaignTable campaigns={shown} onChanged={() => load({ replace: true })} />
          )}
          {result.pages > 1 && (
            <div className="pager">
              <span className="pager-info" aria-live="polite">
                {loading ? "Updating…" : `Page ${result.page} of ${result.pages}`}
              </span>
              <span className="pager-controls">
                <button type="button" className="btn btn-secondary btn-sm" disabled={loading || result.page <= 1}
                  onClick={() => setPage(result.page - 1)}>Previous</button>
                <button type="button" className="btn btn-secondary btn-sm" disabled={loading || result.page >= result.pages}
                  onClick={() => setPage(result.page + 1)}>Next</button>
              </span>
            </div>
          )}
        </div>
      )}
    </AppShell>
  );
}
