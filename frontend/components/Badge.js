export function StatusBadge({ status }) {
  const map = {
    live: { cls: "b-live", label: "LIVE" },
    sending: { cls: "b-agent", label: "SENDING" },
    scheduled: { cls: "b-info", label: "SCHEDULED" },
    exited: { cls: "b-warning", label: "EXITED" },
    sent: { cls: "b-neutral", label: "SENT" },
    paused: { cls: "b-warning", label: "PAUSED" },
    approved: { cls: "b-info", label: "APPROVED" },
    draft: { cls: "b-neutral", label: "DRAFT" },
    pass: { cls: "b-success", label: "PASS" },
    warn: { cls: "b-warning", label: "WATCH" },
    fail: { cls: "b-danger", label: "BLOCK" },
    done: { cls: "b-success", label: "DONE" },
    running: { cls: "b-agent", label: "RUNNING" },
    queued: { cls: "b-neutral", label: "QUEUED" },
    failed: { cls: "b-danger", label: "FAILED" },
    "On target": { cls: "b-success", label: "On target" },
    "Below min": { cls: "b-warning", label: "Below min" },
    "Above max": { cls: "b-warning", label: "Above max" },
  };
  const m = map[status] || { cls: "b-neutral", label: status };
  return <span className={`badge ${m.cls}`}><span className="dot" />{m.label}</span>;
}
