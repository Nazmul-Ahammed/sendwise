"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";

function ago(iso) {
  if (!iso) return "never";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "never";
  const secs = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/**
 * "Update now" button with the last-updated time attached to it.
 *
 * Default action refreshes every tracked send (`POST /api/delivery/refresh`).
 * Pass `onRefresh` to override for a single scope (a campaign, a report);
 * `onDone` runs after a successful refresh so the page can reload.
 */
export default function RefreshRatesButton({
  updatedAt,
  onRefresh,
  onDone,
  label = "Update now",
  compact = false,
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [, tick] = useState(0);

  // Keep "12s ago" honest without a page reload.
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, []);

  async function run() {
    setBusy(true);
    setErr("");
    try {
      const res = onRefresh ? await onRefresh() : await api.refreshDelivery();
      if (onDone) await onDone(res);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const title = updatedAt
    ? `Last updated ${new Date(updatedAt).toLocaleString()}`
    : "Not updated yet";

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" }}>
      <span
        title={title}
        style={{
          fontSize: 11.5,
          color: err ? "var(--danger, #B91C1C)" : "var(--ink-3)",
          maxWidth: 200,
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {err ? `⚠ ${err}` : `updated ${ago(updatedAt)}`}
      </span>
      <button
        className={`btn btn-secondary${compact ? " btn-sm" : ""}`}
        disabled={busy}
        onClick={run}
        title={title}
      >
        {busy ? "Updating…" : `⟳ ${label}`}
      </button>
    </span>
  );
}
