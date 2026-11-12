"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "../../components/AppShell";
import JobFeed from "../../components/JobFeed";
import { api } from "../../lib/api";

export default function JobsPage() {
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.jobs().then(setJobs).catch((e) => setError(e.message));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function retry(id) {
    try {
      await api.retryJob(id);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <AppShell active="/jobs">
      <div className="page-head">
        <div>
          <div className="crumb">Intelligence / <b>Jobs & Activity</b></div>
          <h1>Jobs & Activity</h1>
        </div>
        <div className="top-actions">
          <button className="btn btn-secondary" onClick={load}>Refresh</button>
        </div>
      </div>

      {error && <div className="note n-warn">⚠ {error}</div>}
      {!jobs && !error && <div className="loading"><span className="spinner" /> Loading activity…</div>}

      {jobs && (
        <div className="grid two-col">
          <div className="card">
            <div className="card-h"><h3>Agent runs</h3><span className="hint">dispatch · report · QA · launch</span></div>
            <JobFeed jobs={jobs} onRetry={retry} />
          </div>
          <div className="card">
            <div className="card-h"><h3>How it works</h3></div>
            <div style={{ display: "grid", gap: 10, fontSize: 13, color: "var(--ink-2)" }}>
              <p>Every mutation (dispatch, launch, audit) is enqueued as a job and executed by the Sendwise agent through its skills + SendClean / Google MCP servers.</p>
              <p>Jobs are immutable after completion - every launch and pause is recorded for the audit trail.</p>
              <p>The 15-minute scheduler drives queued sends. The 10:00 daily job writes the report to the Google Sheet and emails the recipients.</p>
              <p>Sunday: audit at 10:00, audit email 10:15, controlled optimization at 11:00.</p>
            </div>
            <div className="note n-info" style={{ marginTop: 14 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
              <span>Failed jobs can be retried; the agent never broadens audience or increases offers during optimization.</span>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
