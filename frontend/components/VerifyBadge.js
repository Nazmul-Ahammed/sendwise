"use client";

/**
 * A deliverability verdict badge.
 *
 * Only `invalid` and `no_mx` are hard blockers - those addresses can only bounce
 * and are never included in a send. The rest are advisory.
 */
const VERDICTS = {
  valid: { cls: "b-success", label: "valid", tip: "Syntax and mail server both check out." },
  invalid: { cls: "b-danger", label: "invalid", tip: "Not a valid email shape - can never be delivered." },
  no_mx: { cls: "b-danger", label: "no mail server", tip: "The domain publishes no MX or A record - mail cannot arrive." },
  disposable: { cls: "b-warn", label: "disposable", tip: "Throwaway mailbox provider. Deliverable, but likely ignored." },
  role: { cls: "b-warn", label: "role inbox", tip: "Shared mailbox (info@, sales@…). Deliverable, but not a person." },
  unknown: { cls: "b-neutral", label: "unverified", tip: "The lookup did not complete. Not treated as invalid." },
  unverified: { cls: "b-neutral", label: "not checked", tip: "Run “Verify emails” to check this address." },
  checking: { cls: "b-neutral", label: "checking…", tip: "Verifying this address now." },
};

export function verdictOf(status) {
  return VERDICTS[status] || VERDICTS.unverified;
}

/** True when the address can never receive mail, so it must not be sent to. */
export function isBlocked(status) {
  return status === "invalid" || status === "no_mx";
}

export default function VerifyBadge({ status, reason = "", showLabel = true }) {
  const v = verdictOf(status);
  return (
    <span
      className={`badge ${v.cls}`}
      title={reason || v.tip}
      style={{ whiteSpace: "nowrap" }}
    >
      {showLabel ? v.label : ""}
    </span>
  );
}
