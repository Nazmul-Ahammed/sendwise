"""Sendwise's own delivery ledger - written at send time, never pulled from the provider.

Why this exists
---------------
SendClean's documented API is transactional only: it has no per-recipient event stream
and no aggregate campaign report. Every delivery number Sendwise used to show was
therefore either read live from the provider or inferred from a value it had not
verified, and both of those failed in production:

  * `messages/getMessageInfo` hangs for 30s+ rather than answering, so a report could
    not be produced at all (see `tracking.py`).
  * their webhook registers, returns 200, and then never delivers an event.
  * rotating SECRET_KEY made the stored API token undecryptable, which turned a
    working connection into "not configured" with nothing written down to explain it.

The result was the worst possible one for an operator: a send whose outcome is unknown,
and a report that is blank for reasons that live outside this app.

So the facts Sendwise wrote itself are now stored by Sendwise. One row per
(campaign, step, recipient). The provider is still called to send - that is its job -
but it is no longer the only copy of what happened, and no report has to ask it
anything to be able to answer.

What this is not
----------------
* Not an engagement store. Opens and clicks stay in `engagement_events`, captured by
  our own pixel and click redirect. Copying a derived count here would create a second,
  staler truth; a report joins the two tables instead.
* Not a substitute for a provider acknowledgement. `status="sent"` means SendClean
  accepted the message for delivery, which is exactly as much as SendClean can tell us.
  It does not mean the recipient's server accepted it.
"""
import json

from . import models
from .db import stamp

BACKFILL_FLAG = "send_records_backfill_v1"

# Terminal states, so a late failure cannot overwrite a recorded success.
_TERMINAL = {"sent", "suppressed"}


def _iso_now():
    return stamp()


def _row(db, campaign_slug, ref, email):
    return (db.query(models.SendRecord)
            .filter(models.SendRecord.campaign_slug == campaign_slug,
                    models.SendRecord.ref == ref,
                    models.SendRecord.email == email)
            .first())


def record(db, campaign_slug, ref, email, x_unique_id="", status="sent", batch="",
           reason="", provider_status="", provider_message="", sent_at="", raw=None,
           commit=True):
    """Write (or advance) one ledger row for a single recipient's send.

    Idempotent by (campaign, step, recipient) rather than by X-Unique-Id: a retried send
    mints a new provider id, and that retry is the same fact, not a second email. The
    attempt count is what distinguishes them.

    Never raises. A send must not fail because its bookkeeping could not be written:
    the email has already left, and losing the row is better than losing the response
    the caller needs. Callers that care can pass `commit=False` to join the surrounding
    transaction.
    """
    try:
        email = str(email or "").strip().lower()
        campaign_slug = str(campaign_slug or "").strip()
        ref = str(ref or "").strip().upper()
        now = _iso_now()
        row = _row(db, campaign_slug, ref, email)
        if row is None:
            row = models.SendRecord(
                campaign_slug=campaign_slug, ref=ref, email=email,
                x_unique_id=str(x_unique_id or ""), batch=str(batch or ""),
                status=status, reason=str(reason or "")[:400],
                provider_status=str(provider_status or "")[:80],
                provider_message=str(provider_message or "")[:400],
                attempts=1, first_sent_at=sent_at or now, last_update_at=now,
                raw_json=raw or {},
            )
            db.add(row)
        else:
            row.attempts = int(row.attempts or 0) + 1
            row.last_update_at = now
            if x_unique_id:
                row.x_unique_id = str(x_unique_id)
            if batch:
                row.batch = str(batch)
            if provider_status:
                row.provider_status = str(provider_status)[:80]
            if provider_message:
                row.provider_message = str(provider_message)[:400]
            # Only advance: a recorded success is not downgraded by a later failure of
            # the same step, and a retry that finally succeeds replaces "failed".
            if status and (status in _TERMINAL or row.status not in _TERMINAL):
                row.status = status
                row.reason = str(reason or "")[:400]
            if not row.first_sent_at:
                row.first_sent_at = sent_at or now
            if raw:
                row.raw_json = raw
        if commit:
            db.commit()
        return row
    except Exception:  # noqa: BLE001 - bookkeeping must never break a send
        # Only roll back a transaction this function owns. With commit=False the caller
        # is mid-write (it may already have staged the campaign's own delivery row), and
        # rolling that back would discard real work to save a bookkeeping row.
        if commit:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        return None


def record_failure(db, campaign_slug, ref, email, error, x_unique_id="", batch="",
                   commit=True):
    """A send the provider rejected, or one we refused to attempt (suppression etc.)."""
    return record(db, campaign_slug, ref, email, x_unique_id=x_unique_id, status="failed",
                  batch=batch, reason=str(error or "")[:400], commit=commit)


def backfill(db, limit=2000):
    """Import recipients already recorded in `campaigns.delivery_json`.

    Sends made before this ledger existed carry their per-recipient rows in a JSON blob
    on the campaign. Copying them in once means history is queryable like everything
    else, instead of only being visible on the campaign that happens to still have it.

    Runs at most once, guarded by a settings row, and never overwrites an existing row.
    """
    existed = (db.query(models.Setting)
               .filter(models.Setting.key == BACKFILL_FLAG).first())
    if existed and (existed.value or "").strip() == "done":
        return {"skipped": True, "imported": 0}

    imported = 0
    campaigns = (db.query(models.Campaign)
                 .execution_options(include_trashed=True)
                 .filter(models.Campaign.delivery_json.isnot(None))
                 .limit(limit).all())
    for c in campaigns:
        delivery = c.delivery_json or {}
        if not isinstance(delivery, dict):
            continue
        batch = str(delivery.get("batch") or "")
        sent_at = str(delivery.get("sent_at") or "")
        for entry in delivery.get("recipients") or []:
            if not isinstance(entry, dict) or not entry.get("email"):
                continue
            if _row(db, c.slug, "E1", entry["email"]):
                continue
            record(db, c.slug, "E1", entry["email"],
                   x_unique_id=entry.get("x_unique_id", ""), status="sent", batch=batch,
                   provider_status=entry.get("provider_status", ""),
                   provider_message=entry.get("provider_message", ""),
                   sent_at=sent_at, commit=False)
            imported += 1
        for entry in delivery.get("failures") or []:
            if not isinstance(entry, dict) or not entry.get("email"):
                continue
            if _row(db, c.slug, "E1", entry["email"]):
                continue
            record(db, c.slug, "E1", entry["email"],
                   x_unique_id=entry.get("x_unique_id", ""), status="failed", batch=batch,
                   reason=entry.get("error", ""), sent_at=sent_at, commit=False)
            imported += 1
    row = existed or models.Setting(key=BACKFILL_FLAG, value="done")
    row.value = "done"
    if not existed:
        db.add(row)
    db.commit()
    return {"skipped": False, "imported": imported}


def summary(db, campaign_slug=None, batch=None, limit_campaigns=50):
    """Local-only delivery counts. Touches no provider endpoint.

    Every figure here comes from rows this app wrote, so it is available even when
    SendClean is unreachable, has rotated its token, or is being rate limited - which
    is precisely when an operator needs to know what was actually sent.
    """
    q = db.query(models.SendRecord)
    if campaign_slug:
        q = q.filter(models.SendRecord.campaign_slug == campaign_slug)
    if batch:
        q = q.filter(models.SendRecord.batch == batch)
    rows = q.all()

    by_status, by_campaign, by_ref = {}, {}, {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        bucket = by_campaign.setdefault(
            r.campaign_slug or "(none)", {"total": 0, "sent": 0, "failed": 0})
        bucket["total"] += 1
        if r.status in ("sent", "suppressed"):
            bucket["sent"] += 1
        elif r.status == "failed":
            bucket["failed"] += 1
        by_ref[r.ref or "E1"] = by_ref.get(r.ref or "E1", 0) + 1

    campaigns = sorted(by_campaign.items(), key=lambda kv: kv[1]["total"], reverse=True)
    return {
        "source": "sendwise_local_ledger",
        "campaign_slug": campaign_slug or "",
        "batch": batch or "",
        "total_rows": len(rows),
        "by_status": by_status,
        "by_ref": by_ref,
        "campaigns": [{"campaign_slug": k, **v} for k, v in campaigns[:limit_campaigns]],
    }


def recent(db, campaign_slug=None, limit=100):
    """The newest ledger rows, for the "was this address actually sent?" question."""
    q = db.query(models.SendRecord)
    if campaign_slug:
        q = q.filter(models.SendRecord.campaign_slug == campaign_slug)
    rows = q.order_by(models.SendRecord.id.desc()).limit(max(1, min(limit, 500))).all()
    return [{
        "campaign_slug": r.campaign_slug,
        "ref": r.ref,
        "email": r.email,
        "x_unique_id": r.x_unique_id,
        "status": r.status,
        "reason": r.reason,
        "provider_status": r.provider_status,
        "attempts": r.attempts,
        "first_sent_at": r.first_sent_at,
        "last_update_at": r.last_update_at,
    } for r in rows]


def counts_for(db, campaign_slug, ref=None):
    """How many rows exist for a step - used to prove a batch was not half-recorded."""
    q = db.query(models.SendRecord).filter(
        models.SendRecord.campaign_slug == campaign_slug)
    if ref:
        q = q.filter(models.SendRecord.ref == str(ref).upper())
    return q.count()
