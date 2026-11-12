"""The provider's delivery verdict, pulled and stored.

SendClean is the only party that knows whether a message was delivered or bounced, and
its webhook has never delivered a single event to this deployment (measured: zero
`sendclean-webhook` rows in `suppressions`, while a direct message lookup immediately
returned `hard_bounce`). So the verdict is pulled instead of waited for.

One row per sent message in `delivery_status`. That row is the source of truth for a
campaign's Delivered / Bounced figures, and a hard bounce also lands in `suppressions` so
the address is never mailed again.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import email_provider as ep
from . import models
from . import sendclean_client as sc

# A lookup is the provider's slowest call and has been seen to hang; every one is bounded.
LOOKUP_TIMEOUT = 10
WORKERS = 8
# How many messages one run may ask about. A tick runs every 15 minutes, so this is a
# steady drain: large enough to catch up, small enough that a slow provider cannot
# stretch a single pass past the scheduler's patience.
DEFAULT_BUDGET = 200

FINAL = ("delivered", "hard_bounce", "soft_bounce")
BOUNCES = ("hard_bounce", "soft_bounce")
SOFT_BOUNCE_SETTING = "sendclean_soft_bounces"
SOFT_BOUNCE_LIMIT = 3


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalise(status):
    """Map the provider's spelling onto ours. Unknown stays unknown, never 'delivered'."""
    s = str(status or "").strip().lower()
    if s in ("delivered", "delivery"):
        return "delivered"
    if s in ("hard_bounce", "hardbounce", "hard-bounce", "bounced"):
        return "hard_bounce"
    if s in ("soft_bounce", "softbounce", "soft-bounce"):
        return "soft_bounce"
    if s in ("queued", "sent", "accepted"):
        return "queued"
    return s or "unknown"


def _pairs(db, campaign):
    """(x_unique_id, email, ref) for every message this campaign actually sent."""
    out = []
    seen = set()

    for r in (campaign.delivery_json or {}).get("recipients") or []:
        xid = (r.get("x_unique_id") or "").strip()
        if xid and xid not in seen:
            seen.add(xid)
            out.append((xid, (r.get("email") or "").strip().lower(), "E1"))

    rows = (db.query(models.SequenceEnrollment)
            .filter(models.SequenceEnrollment.campaign_slug == campaign.slug).all())
    for e in rows:
        candidates = []
        if (e.last_x_unique_id or "").strip():
            candidates.append(((e.last_x_unique_id or "").strip(), e.current_ref or ""))
        for h in (e.history_json or []):
            if isinstance(h, dict) and (h.get("x_unique_id") or "").strip():
                candidates.append((h["x_unique_id"].strip(), h.get("ref") or ""))
        for xid, ref in candidates:
            if xid not in seen:
                seen.add(xid)
                out.append((xid, (e.email or "").strip().lower(), ref))
    return out


def _already_final(db, xids):
    if not xids:
        return set()
    rows = (db.query(models.DeliveryStatus.x_unique_id)
            .filter(models.DeliveryStatus.x_unique_id.in_(list(xids))).all())
    return {r[0] for r in rows}


def _upsert(db, xid, campaign_slug, ref, email, status, raw):
    row = (db.query(models.DeliveryStatus)
           .filter(models.DeliveryStatus.x_unique_id == xid).first())
    if row is None:
        row = models.DeliveryStatus(x_unique_id=xid, campaign_slug=campaign_slug)
        db.add(row)
    row.campaign_slug = campaign_slug
    row.ref = ref or row.ref or ""
    row.email = email or row.email or ""
    row.status = status
    row.raw_status = str(raw or "")[:120]
    row.checked_at = _now()
    return row


def _suppress_for_bounce(db, email, reason):
    """Never mail this address again, and stop it in every running sequence."""
    if not email:
        return
    row = db.query(models.Suppression).filter(models.Suppression.email == email).first()
    if row is None:
        db.add(models.Suppression(email=email, reason=reason,
                                  created_at=datetime.now(timezone.utc)))
    else:
        # A bounce does not downgrade a deliberate unsubscribe.
        if (row.reason or "") not in ("unsubscribe", "complaint"):
            row.reason = reason
    for enr in (db.query(models.SequenceEnrollment)
                .filter(models.SequenceEnrollment.email == email,
                        models.SequenceEnrollment.status == "active").all()):
        enr.status = "suppressed"


def sync(db, campaign=None, budget=DEFAULT_BUDGET, refresh=False, dry_run=False):
    """Pull the provider's verdict for recent sends and store it.

    `refresh=False` skips messages that already have a final verdict, so a repeated run
    costs nothing. `budget` caps the lookups in one pass.
    """
    creds = ep.credentials(db)
    if not (creds.get("owner_id") and creds.get("token")):
        return {"ok": False, "error": "SendClean credentials are not configured"}

    campaigns = [campaign] if campaign else (
        db.query(models.Campaign)
        .filter(models.Campaign.status.in_(("live", "sending", "paused")))
        .order_by(models.Campaign.id.desc()).all())

    targets = []
    for c in campaigns:
        pairs = _pairs(db, c)
        if not refresh:
            done = _already_final(db, [p[0] for p in pairs])
            pairs = [p for p in pairs if p[0] not in done]
        for xid, email, ref in pairs:
            targets.append((c.slug, xid, email, ref))
    untracked = len(targets)
    targets = targets[:max(1, int(budget))]

    def ask(item):
        slug, xid, email, ref = item
        try:
            info = sc.get_message_info(creds["owner_id"], creds["token"], xid,
                                       timeout=LOOKUP_TIMEOUT)
            rows = info.get("message_data") or []
            row = rows[0] if rows else {}
            return item, (row.get("status"), row)
        except Exception as exc:  # noqa: BLE001 - one slow message must not stop the pass
            return item, (None, {"__error": str(exc)[:120]})

    counts = {"checked": 0, "delivered": 0, "hard_bounce": 0, "soft_bounce": 0,
              "queued": 0, "unknown": 0, "errors": 0, "suppressed": 0}
    soft_map = {}
    setting = (db.query(models.Setting)
               .filter(models.Setting.key == SOFT_BOUNCE_SETTING).first())
    if setting and setting.value:
        try:
            soft_map = json.loads(setting.value)
        except ValueError:
            soft_map = {}

    if targets:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for item, (raw_status, _row) in pool.map(ask, targets):
                slug, xid, email, ref = item
                if raw_status is None:
                    counts["errors"] += 1
                    continue
                status = _normalise(raw_status)
                counts["checked"] += 1
                counts[status] = counts.get(status, 0) + 1
                if dry_run:
                    continue
                _upsert(db, xid, slug, ref, email, status, raw_status)
                if status == "hard_bounce":
                    _suppress_for_bounce(db, email, "hard_bounce")
                    counts["suppressed"] += 1
                elif status == "soft_bounce":
                    soft_map[email] = soft_map.get(email, 0) + 1
                    if soft_map[email] >= SOFT_BOUNCE_LIMIT:
                        _suppress_for_bounce(db, email, "soft_bounce")
                        counts["suppressed"] += 1
        if not dry_run:
            if setting is None:
                db.add(models.Setting(key=SOFT_BOUNCE_SETTING, value=json.dumps(soft_map)))
            else:
                setting.value = json.dumps(soft_map)
            db.commit()

    counts.update({"ok": True, "campaigns": len(campaigns), "untracked": untracked,
                   "budget": budget, "measured_at": _now()})
    return counts


def summary(db, campaign_slug):
    """Delivered / bounced as the provider reported them for one campaign."""
    rows = (db.query(models.DeliveryStatus)
            .filter(models.DeliveryStatus.campaign_slug == campaign_slug).all())
    out = {"delivered": 0, "hard_bounce": 0, "soft_bounce": 0, "queued": 0,
           "unknown": 0, "measured": len(rows), "measured_at": ""}
    latest = ""
    for r in rows:
        key = r.status if r.status in out else "unknown"
        out[key] = out.get(key, 0) + 1
        if (r.checked_at or "") > latest:
            latest = r.checked_at
    out["bounced"] = out["hard_bounce"] + out["soft_bounce"]
    out["measured_at"] = latest
    return out
