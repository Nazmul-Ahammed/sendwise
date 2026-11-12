"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "../../components/AppShell";
import KpiCard from "../../components/KpiCard";
import BarChart from "../../components/BarChart";
import CampaignTable from "../../components/CampaignTable";
import JobFeed from "../../components/JobFeed";
import ThresholdTable from "../../components/ThresholdTable";
import SendCleanPanel from "../../components/SendCleanPanel";
import RefreshRatesButton from "../../components/RefreshRatesButton";
import { api } from "../../lib/api";

export default function DashboardPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    return api.dashboard().then(setData).catch((e) => setError(e.message));
  }, []);

  // The dashboard is a live view, not a snapshot: poll while visible and refresh on return.
  useEffect(() => {
    load();
    const tick = () => { if (document.visibilityState === "visible") load(); };
    const id = setInterval(tick, 30000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [load]);

  const updatedAt = data?.totals?.updated_at || null;
  const health = (data?.kpis || []).filter((k) =>
    /deliver|bounce|complaint|click|open/i.test(k.label || "")
  );

  return (
    <AppShell active="/dashboard">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="sub">
            Where every send stands right now - delivery, engagement, and anything that
            needs a decision.
          </p>
        </div>
        <div className="actions">
          <span className="hint" title="The dashboard refreshes by itself while the tab is open.">
            ⟳ auto-updating
          </span>
          <RefreshRatesButton updatedAt={updatedAt} onDone={load} />
          <Link className="btn btn-primary" href="/campaigns/new">+ New Send</Link>
        </div>
      </div>

      {error && <div className="note n-warn"><span>{error}</span></div>}
      {!data && !error && (
        <div className="loading"><span className="spinner" /> Loading dashboard…</div>
      )}

      {data && (
        <div className="stack">
          {/* The numbers first: this is what the page is for. */}
          <div className="grid kpis">
            {data.kpis.map((k, i) => <KpiCard key={i} kpi={k} />)}
          </div>

          <div className="grid two-col">
            <div className="card">
              <div className="card-h">
                <h3>Emails sent - last 7 days</h3>
                <span className="hint">recipients actually sent, per day</span>
              </div>
              <BarChart data={data.clicks_by_day} />
            </div>
            <SendCleanPanel />
          </div>

          <div className="grid two-col">
            <div className="card" style={{ padding: 0 }}>
              <div className="card-h" style={{ padding: "22px 22px 0", marginBottom: 14 }}>
                <h3>Recent sends</h3>
                <Link href="/campaigns" style={{ fontSize: 13, color: "var(--brand-600)", fontWeight: 650 }}>
                  View all →
                </Link>
              </div>
              <CampaignTable campaigns={data.recent_campaigns} />
            </div>

            <div className="stack">
              <div className="card">
                <div className="card-h">
                  <h3>Sending health</h3>
                  <span className="hint">delivery · bounce · complaint</span>
                </div>
                <div>
                  {health.map((k, i) => {
                    // Every status the backend can send is mapped, so a "fail" can no
                    // longer render with the reassuring green of "ok".
                    const cls = { pass: "ok", warn: "warn", fail: "block", info: "info", pending: "pending" }[k.status] || "pending";
                    return (
                      <div className="thresh" key={i}>
                        <b>{k.label}</b>
                        <span className={`v ${cls}`}>{k.value}</span>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="card">
                <div className="card-h">
                  <h3>Emergency thresholds</h3>
                  <span className="hint">breach pauses sending</span>
                </div>
                <ThresholdTable thresholds={data.thresholds} />
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <h3>Jobs &amp; activity</h3>
              <span className="hint">everything the agent has run</span>
            </div>
            <JobFeed jobs={data.jobs} />
          </div>

          {/* The caveats matter, but not on every visit. Kept one tap away rather than
              pushing the numbers down the page. */}
          <details className="disclosure">
            <summary>How to read these numbers</summary>
            <div className="d-body">
              <p>
                <b>Opens are indicative, clicks are reliable.</b> An open is recorded when a
                tracking image is loaded. Some clients never load images, so a real reader
                can be missed; mail providers also preload the image, so a fetch is not
                proof anyone read it. A click is generated by the recipient's own browser
                and is never cached or preloaded.
              </p>
              <p>
                <b>Delivery, bounce and complaints lead the decision-making.</b> A breach of
                a threshold below pauses delivery and blocks new auto-launches until it is
                cleared.
              </p>
              <p>
                <b>Engagement is measured by this app.</b> Per-recipient opens and clicks are
                recorded here, so a send can be followed live on its own Engagement tab.
              </p>
            </div>
          </details>
        </div>
      )}
    </AppShell>
  );
}
