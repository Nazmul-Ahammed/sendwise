"use client";

import { StatusBadge } from "./Badge";
import { usePager, Pager } from "./Pagination";

const JOB_STYLE = {
  dispatch: { bg: "#CFFAFE", color: "#0E7490", label: "SEQ" },
  report: { bg: "#ECFDF5", color: "#059669", label: "OK" },
  sync: { bg: "#ECFDF5", color: "#059669", label: "OK" },
  qa: { bg: "#EEF2FF", color: "#4F46E5", label: "QA" },
  launch: { bg: "#EEF2FF", color: "#4F46E5", label: "LAUNCH" },
  audit: { bg: "#EFF6FF", color: "#2563EB", label: "AUDIT" },
  warning: { bg: "#FFFBEB", color: "#D97706", label: "!" },
};

export default function JobFeed({ jobs, onRetry }) {
  // The job history is unbounded; page it so the feed stays readable.
  const pager = usePager(jobs || [], 25);
  if (!jobs || jobs.length === 0) return <div className="loading">No jobs</div>;
  return (
    <div>
      {pager.rows.map((j) => {
        const st = JOB_STYLE[j.job_type] || { bg: "#F1F3F9", color: "#475569", label: "•" };
        return (
          <div className="job" key={j.id}>
            <div className="j-icon" style={{ background: st.bg, color: st.color }}>{st.label}</div>
            <div className="j-body">
              <b>{j.title}</b>
              <span>{j.detail}</span>
            </div>
            {onRetry && j.status === "failed" && (
              <button className="btn btn-secondary btn-sm j-retry" onClick={() => onRetry(j.id)}>Retry</button>
            )}
            <StatusBadge status={j.status} />
            <span className="j-time">{j.created_at}</span>
          </div>
        );
      })}
      <Pager pager={pager} label="jobs" />
    </div>
  );
}
