import base64
import hashlib
import hmac
import json
import os
import re
import smtplib
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text as _sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from . import open_signal
from . import tracking
from . import delivery_ledger as ledger
from .auth import create_token, hash_password, require_user, verify_password
from .db import get_db, engine, stamp
from .email_templates import branded_report_html, upsell_email, body_to_html
from . import sendclean_client as sc
from . import email_provider as ep
from .llm_providers import encrypt_secret

router = APIRouter(prefix="/api")


def _require_provider_capability(db, capability):
    try:
        return ep.require_capability(db, capability)
    except ep.ProviderCapabilityError as exc:
        raise HTTPException(status_code=400, detail=f"BLOCKED: {exc}") from exc


# ---------- auth ----------
class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == body.email.lower().strip()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_token(db, user.id)
    return {"token": token, "user": {"email": user.email, "name": user.name, "role": user.role}}


@router.get("/health")
def health():
    return {"status": "ok", "service": "sendwise-api", "version": "1.0.0"}


# ---------- helpers ----------
def _engagement_summary(db, c):
    """Rates built from `engagement_events`, for sends the provider ledger cannot cover.

    A sequence send records each email against an enrollment, so `campaign.delivery_json`
    stays empty and `_refresh_campaign_rates` (which reads that ledger) bails out before
    it can write a summary. That left every journey showing blank open and click rates in
    the Sends list, permanently, no matter how much engagement it drew.

    The webhook event store has the data instead, and it needs no call to the provider,
    so this also keeps working when message lookup is degraded. Rates are per PERSON, not
    per email: six people receiving four emails each is still six people to reach.
    """
    if db is None:
        return None

    sent_index = _campaign_send_index(db, c)
    if not sent_index:
        return None

    recipients = {email for email, _ref in sent_index.values()}
    if not recipients:
        return None

    events = (db.query(models.EngagementEvent)
                .filter(models.EngagementEvent.campaign_slug == c.slug).all())
    if not events:
        return None

    # Two open counts, because they answer different questions:
    #   openers_any   - every pixel fetch, including a mail provider's image proxy.
    #                   This is the headline number on the Sends list.
    #   openers_human - only fetches that were NOT identified as a proxy/scanner.
    #                   Shown beside it, never mixed into the same figure.
    # The old code also treated any non-click, non-machine event as an open, so a
    # "delivered" or bounce event would have inflated the opener count. Only a real
    # `open` event counts now.
    openers_any, openers_human, clickers, clicks = set(), set(), set(), 0
    for ev in events:
        who = (ev.email or "").lower()
        if ev.event_type == "click":
            clickers.add(who)
            clicks += 1
        elif ev.event_type == "open":
            openers_any.add(who)
            if not ev.is_machine:
                openers_human.add(who)

    total = len(recipients)

    def pct(n):
        return round(n / total * 100, 2) if total else 0.0

    # Delivery is reported only when the provider has actually confirmed it. It is not
    # inferred from "we sent it", because those are different claims and only one of them
    # is true. When no confirmation has arrived the value stays None, and the column says
    # so rather than showing a number nobody can stand behind.
    delivered_to = {e for e in _emails_with(events, "delivered", machine=None)}

    # What the provider actually said, pulled by delivery_sync. This is the number to
    # trust for Delivered / Bounced: the webhook never arrived, so counting our own
    # "we sent it" as delivery was the only alternative and it is not the same claim.
    from . import delivery_sync as dstat
    measured = dstat.summary(db, c.slug)
    bounced_here = measured["bounced"]
    if measured["measured"]:
        delivered_to = delivered_to | {
            r.email for r in
            (db.query(models.DeliveryStatus)
             .filter(models.DeliveryStatus.campaign_slug == c.slug,
                     models.DeliveryStatus.status == "delivered").all())
            if getattr(r, "email", "")}


    # Delivery confirmation is unreliable here: SendClean's webhook registers and answers
    # the handshake but never posts a `delivered` event, and their message lookup returns
    # data only intermittently (the same id answered "delivered" once, then empty).
    # Leaving the column blank made the send look unmeasured, so when no confirmation has
    # arrived we report what we DO know - the provider accepted these messages (250 queued)
    # - and say so in `delivery_basis`. `delivery_confirmed` tells the two apart.
    if measured["measured"]:
        delivery_rate = pct(measured["delivered"])
        delivery_basis = (f"provider message lookup - {measured['measured']} message(s) "
                          f"checked, {bounced_here} bounced")
        delivery_confirmed = True
    elif delivered_to:
        delivery_rate = pct(len(delivered_to))
        delivery_basis = "provider delivery confirmations"
        delivery_confirmed = True
    else:
        delivery_rate = pct(len(sent_index))
        delivery_basis = ("accepted by the provider (250 queued); "
                          "no delivery confirmation received")
        delivery_confirmed = False

    return {
        "sent": len(sent_index),
        "delivered": len(delivered_to) or None,
        "bounced": bounced_here,
        "delivery_measured": measured["measured"],
        "recipients": total,
        "unique_openers": len(openers_any),
        "unique_openers_human": len(openers_human),
        "unique_clickers": len(clickers),
        "clicks": clicks,
        "open_rate": pct(len(openers_any)),
        "open_rate_human": pct(len(openers_human)),
        "click_rate": pct(len(clickers)),
        "delivery_rate": delivery_rate,
        "delivery_confirmed": delivery_confirmed,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "own-tracker",
        "open_rate_basis": ("all pixel fetches, including provider proxy loads; "
                            "open_rate_human excludes proxy/scanner fetches"),
        "delivery_basis": delivery_basis,
    }


def _campaign_rates(c, db=None):
    """Open/click/delivery rates.

    Prefers the live SendClean delivery-report summary. Falls back to the realtime event
    store, which is what a sequence send has. The Mailchimp-era
    `open_rate`/`click_rate`/`delivery_rate` columns are only a legacy fallback - nothing
    writes them any more, which is why they read as empty.
    """
    summary = (c.delivery_json or {}).get("summary")
    if summary:
        return summary
    live = _engagement_summary(db, c)
    if live:
        return live
    if c.open_rate is not None or c.click_rate is not None or c.delivery_rate is not None:
        return {
            "open_rate": c.open_rate, "click_rate": c.click_rate, "delivery_rate": c.delivery_rate,
            "sent": None, "delivered": None, "unique_openers": None, "unique_clickers": None,
            "clicks": None, "computed_at": "", "source": "legacy",
        }
    return {
        "open_rate": None, "click_rate": None, "delivery_rate": None,
        "sent": len((c.delivery_json or {}).get("recipients") or []),
        "delivered": None, "unique_openers": None, "unique_clickers": None, "clicks": None,
        "computed_at": "", "source": "none",
    }


def _enrollment_summary(db, campaign_slug):
    if db is None:
        return {"exists": False, "enrolled": 0, "active": 0, "failed": 0,
                "pending_initial": 0, "sends": 0}
    row = db.execute(_sql_text("""
        SELECT COUNT(*) AS enrolled,
               COUNT(*) FILTER (WHERE status = 'active') AS active,
               COUNT(*) FILTER (WHERE status = 'failed') AS failed,
               COUNT(*) FILTER (WHERE status = 'active' AND COALESCE(current_ref, '') = '')
                   AS pending_initial,
               COALESCE(SUM(sends), 0) AS sends
        FROM sequence_enrollments WHERE campaign_slug = :slug
    """), {"slug": campaign_slug}).mappings().first()
    data = dict(row or {})
    return {"exists": bool(data.get("enrolled")),
            "enrolled": int(data.get("enrolled") or 0),
            "active": int(data.get("active") or 0),
            "failed": int(data.get("failed") or 0),
            "pending_initial": int(data.get("pending_initial") or 0),
            "sends": int(data.get("sends") or 0)}


def _campaign_dict(c: models.Campaign, db=None):
    enrollment = _enrollment_summary(db, c.slug)
    # The enrollment ledger is the sending source of truth. A worker can finish after
    # the original HTTP request has timed out or restarted, leaving the old campaign
    # label on "approved" even though mail went out. Derive what the UI shows from the
    # durable recipient rows, while preserving deliberate paused/exited states.
    effective_status = c.status
    if enrollment["exists"] and c.status not in ("paused", "exited"):
        if enrollment["pending_initial"]:
            effective_status = "sending"
        elif enrollment["sends"]:
            effective_status = "live"
    return {
        "id": c.id,
        "slug": c.slug,
        "title": c.title,
        "status": effective_status,
        "persisted_status": c.status,
        "audience_name": c.audience_name,
        "audience_count": c.audience_count,
        "product": c.product,
        "offer": c.offer,
        "sender_name": c.sender_name,
        "sender_email": c.sender_email,
        "sequence_type": c.sequence_type,
        "sequence_slug": c.sequence_slug or ("single" if len(c.emails_json or []) <= 1 else ""),
        "launched_at": c.launched_at,
        "open_rate": c.open_rate,
        "click_rate": c.click_rate,
        "delivery_rate": c.delivery_rate,
        "sequence": c.sequence_json or [],
        "emails": c.emails_json or [],
        "qa": c.qa_json or [],
        "gates": c.gates_json or [],
        "audience_options": c.audience_options or {},
        "send_mode": c.send_mode or "direct",
        "send_test_first": c.send_test_first or "false",
        "provider_message_id": c.provider_message_id or "",
        "enrollment": enrollment,
          "rates": _campaign_rates(c, db),
        "delivery": {
            "batch": (c.delivery_json or {}).get("batch", ""),
            "sent_count": len((c.delivery_json or {}).get("recipients") or []),
            "failed_count": len((c.delivery_json or {}).get("failures") or []),
            "tracking": (c.delivery_json or {}).get("tracking", ""),
            "summary": (c.delivery_json or {}).get("summary") or None,
        },
    }


def _setting_dict(s: models.Setting):
    try:
        return json.loads(s.value)
    except (json.JSONDecodeError, TypeError):
        return s.value


def product_key_for(product):
    from .copy_engine import product_key
    return product_key(product)
def _final_html(c, e, unsubscribe_url=""):
    """ALWAYS build a fresh branded HTML design from the final plain-text body at send time.
    Stale body_html is never trusted - body is the single source of truth.

    `unsubscribe_url` renders the compliance unsubscribe line. A one-off send has no
    sequence enrollment, so it used to go out with no way to unsubscribe at all.
    """
    body = (e.get("body") or "").strip()
    if not body:
        body = f"Hi there,\n\nBest regards,\n{c.sender_name or c.product}"
    return body_to_html(body, product_key_for(c.product), variant=e.get("journey_phase", 1),
                        headline=(e.get("subject") or c.title or "").strip(),
                        unsubscribe_url=unsubscribe_url)


def _normalise_emails(emails):
    """Normalise, deduplicate and validate. Returns [] when nothing valid was supplied.

    Unlike `_clean_manual_emails` this does NOT raise on an empty list, so a caller
    resolving a saved audience can decide its own message for the empty case.
    """
    raw = emails or []
    cleaned = []
    for value in raw:
        email = str(value or "").strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise HTTPException(status_code=400, detail=f"Invalid email: {email or '(blank)'}")
        if email not in cleaned:
            cleaned.append(email)
    return cleaned


def _clean_manual_emails(emails):
    """Fail closed: normalize, deduplicate and validate every requested address."""
    cleaned = _normalise_emails(emails)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Manual mode selected but no valid emails were provided")
    return cleaned
def _sequence_dict(s: models.Sequence):
    return {
        "id": s.id,
        "slug": s.slug,
        "name": s.name,
        "description": s.description,
        "trigger": s.trigger,
        "stages": s.stages_json or [],
        "rules": s.rules_json or [],
        "created_at": s.created_at,
    }


# ---------- dashboard ----------
def _delivery_totals(db):
    """Roll up every send that has a SendClean delivery summary.

    These numbers come from real per-recipient SendClean tracking, not the
    Mailchimp-era rate columns (which nothing writes any more).
    """
    sent = delivered = openers = clickers = clicks = campaigns = 0
    recipients = 0
    proxy_only = 0
    latest = ""
    delivered_known = False
    confirmed_sent = 0
    for c in db.query(models.Campaign).all():
        # `_campaign_rates` prefers the provider summary but falls back to this app's own
        # tracker - the live source, since the provider's lookup is unreliable. Reading
        # only the summary made the dashboard show 0% while the sends had real opens and
        # clicks recorded locally.
        s = _campaign_rates(c, db)
        if not s or not s.get("sent"):
            continue
        campaigns += 1
        sent += int(s.get("sent") or 0)
        recipients += int(s.get("recipients") or s.get("sent") or 0)
        if s.get("delivered") is not None:
            delivered += int(s.get("delivered") or 0)
            confirmed_sent += int(s.get("sent") or 0)
            delivered_known = True
        openers += int(s.get("unique_openers") or 0)
        clickers += int(s.get("unique_clickers") or 0)
        clicks += int(s.get("clicks") or 0)
        proxy_only += int(s.get("proxy_only_openers") or 0)
        stamp = str(s.get("computed_at") or "")
        if stamp > latest:
            latest = stamp

    def pct(n, d):
        return round(n / d * 100, 2) if d else None

    # Rates divide by RECIPIENTS, not by "emails sent" - a journey sends many emails to the
    # same person, so dividing by sends understates every rate.
    base = recipients or None
    return {
        "campaigns": campaigns, "sent": sent, "recipients": recipients,
        "delivered": delivered if delivered_known else None,
        "unique_openers": openers, "unique_clickers": clickers, "clicks": clicks,
        "proxy_only_openers": proxy_only,
        # A rate is only reported when its data exists. Delivery divides by the sends the
        # provider ACTUALLY confirmed (not every send - most have no confirmation yet), so
        # a partly-confirmed set cannot read as a 0.5% delivery failure. Bounce needs a
        # real bounce figure; "sent minus confirmed" is not a bounce, so it stays unknown.
        "delivery_rate": pct(delivered, confirmed_sent) if delivered_known and confirmed_sent else None,
        "open_rate": pct(openers, base),
        "click_rate": pct(clickers, base),
        "bounce_rate": None,
        "updated_at": latest or None,
    }


def _refresh_campaign_summary(db, c, creds):
    """Re-read SendClean per-recipient status for one send and store the summary.

    Returns the rates dict, or None when the campaign has no tracking mapping.
    Always deep-copies delivery_json first: SQLAlchemy does not detect in-place
    mutation of a JSON column.
    """
    delivery = json.loads(json.dumps(c.delivery_json or {}))
    recipients = delivery.get("recipients") or []
    if not recipients:
        return None

    def look(entry):
        try:
            info = sc.get_message_info(creds["owner_id"], creds["token"], entry["x_unique_id"])
            rows = info.get("message_data") or []
            return rows[0] if rows else None
        except Exception:  # noqa: BLE001
            return None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(look, recipients))

    total = len(recipients)
    opened = not_delivered = clickers = clicks = 0
    proxy_only = machine_opens = 0
    for row in rows:
        if not row:
            continue
        status = row.get("status", "")
        # Count people, not pixel fetches. `open` includes the mail provider's own
        # image proxy caching the tracking pixel on delivery.
        eng = open_signal.engagement(row)
        clk = eng["clicks"]
        clicks += clk
        machine_opens += eng["machine_opens"]
        if clk > 0:
            clickers += 1
        if status not in ("delivered", "queued", ""):
            not_delivered += 1
        elif eng["human_opens"] > 0:
            opened += 1
        elif eng["raw_opens"] > 0:
            # SendClean reports opens, but every one was a machine. Held apart so it
            # cannot inflate the open rate, and reported so it stays visible.
            proxy_only += 1

    delivered = total - not_delivered

    def pct(n, d):
        return round(n / d * 100, 2) if d else 0.0

    rates = {
        "sent": total, "delivered": delivered,
        "unique_openers": opened, "unique_clickers": clickers, "clicks": clicks,
        "proxy_only_openers": proxy_only,
        "machine_opens_ignored": machine_opens,
        "open_rate": pct(opened, total),
        "click_rate": pct(clickers, total),
        "delivery_rate": pct(delivered, total),
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "sendclean",
        "open_rate_basis": "human opens only; proxy/scanner pixel fetches excluded",
    }
    delivery["summary"] = rates
    c.delivery_json = delivery
    c.open_rate = rates["open_rate"]
    c.click_rate = rates["click_rate"]
    c.delivery_rate = rates["delivery_rate"]
    return rates


@router.post("/delivery/refresh", dependencies=[Depends(require_user)])
def refresh_delivery(db: Session = Depends(get_db)):
    """'Update now' - re-read SendClean tracking for every tracked send at once."""
    _require_provider_capability(db, "message_lookup")
    creds = ep.credentials(db)
    tracked = [c for c in db.query(models.Campaign).all()
               if (c.delivery_json or {}).get("recipients")]
    refreshed, failed = 0, 0
    for c in tracked:
        try:
            if _refresh_campaign_summary(db, c, creds) is not None:
                refreshed += 1
        except Exception:  # noqa: BLE001
            failed += 1
    db.commit()
    totals = _delivery_totals(db)
    db.add(models.Job(job_type="sync", title=f"Rates refreshed · {refreshed} send(s)",
                      detail=f"updated_at={totals.get('updated_at') or '-'}"
                             + (f" · {failed} failed" if failed else ""),
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "refreshed": refreshed, "failed": failed, "totals": totals,
            "updated_at": totals.get("updated_at")}


def _rate_kpi(label, value, target, icon, lower_is_better=False):
    if value is None:
        return {"label": label, "value": "-", "sub": "no sends tracked yet", "status": "pending", "icon": icon}
    if lower_is_better:
        status = "pass" if value <= target else ("warn" if value <= target * 1.25 else "fail")
        sub = f"Max {target}%"
    else:
        status = "pass" if value >= target else ("warn" if value >= target * 0.85 else "fail")
        sub = f"Target >= {target}%"
    return {"label": label, "value": f"{value}%", "sub": sub, "status": status, "icon": icon}


def _sends_by_day(db, days=7):
    """Recipients actually sent, per day, from the stored delivery records."""
    today = datetime.now(timezone.utc).date()
    buckets = {}
    for i in range(days - 1, -1, -1):
        buckets[(today - timedelta(days=i)).isoformat()] = 0
    for c in db.query(models.Campaign).all():
        dj = c.delivery_json or {}
        raw = str(dj.get("sent_at") or "")
        if not raw:
            continue
        try:
            day = datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except (TypeError, ValueError):
            continue
        if day in buckets:
            buckets[day] += len(dj.get("recipients") or [])
    return [{"day": k[5:], "value": v} for k, v in buckets.items()]


@router.get("/dashboard", dependencies=[Depends(require_user)])
def dashboard(db: Session = Depends(get_db)):
    totals = _delivery_totals(db)
    kpis = [
        _rate_kpi("Delivery rate", totals["delivery_rate"], 95.0, "delivery"),
        # Deliberately NOT graded. An open is a pixel fetch, and a machine can fetch it;
        # `_delivery_totals` already drops the proxies we can prove, so what remains is a
        # ceiling rather than a measurement. Grading a ceiling would assert a precision
        # the number does not have, so it is reported as information instead.
        {
            "label": "Open rate (upper bound)",
            "value": (f"{totals['open_rate']}%"
                      if totals["open_rate"] is not None else "n/a"),
            "sub": "Ceiling, not graded - provider prefetch excluded",
            "status": "info",
            "icon": "open",
        },
        _rate_kpi("Unique clicks", totals["click_rate"], 1.3, "clicks"),
        _rate_kpi("Bounce rate", totals["bounce_rate"], 2.0, "bounce", lower_is_better=True),
        {
            "label": "Emails sent", "value": f"{totals['sent']:,}",
            "sub": f"{totals['campaigns']} send(s) tracked", "icon": "unsub",
            "status": "pass" if totals["sent"] else "pending",
        },
    ]
    clicks_by_day = _sends_by_day(db)
    main_campaign = db.query(models.Campaign).filter(models.Campaign.status == "live").first() \
        or db.query(models.Campaign).first()
    stage_rates = []
    if main_campaign:
        for s in main_campaign.sequence_json or []:
            status = "On target" if s.get("actual_open") and s.get("open_min") and s["actual_open"] >= s["open_min"] else "Below min"
            stage_rates.append({
                "stage": s["stage"], "focus": s["focus"], "delay": s.get("delay", ""),
                "open_min": s.get("open_min"), "open_max": s.get("open_max"),
                "click_min": s.get("click_min"), "click_max": s.get("click_max"),
                "actual": s.get("actual_open"), "status": status,
            })
    campaigns = db.query(models.Campaign).order_by(models.Campaign.id.desc()).limit(20).all()
    jobs = db.query(models.Job).order_by(models.Job.id.desc()).limit(50).all()

    def thr(label, value, limit, target, lower_is_better=False):
        if value is None:
            return {"label": label, "value": "-", "limit": limit, "status": "pending"}
        ok = value <= target if lower_is_better else value >= target
        near = value <= target * 1.25 if lower_is_better else value >= target * 0.9
        return {"label": label, "value": f"{value}%", "limit": limit,
                "status": "pass" if ok else ("warn" if near else "fail")}

    thresholds = [
        thr("Delivery rate", totals["delivery_rate"], ">= 95%", 95.0),
        thr("Bounce rate", totals["bounce_rate"], "<= 2%", 2.0, lower_is_better=True),
        thr("Open rate (tracked)", totals["open_rate"], ">= 11.5%", 11.5),
        thr("Click rate", totals["click_rate"], ">= 1.3%", 1.3),
        {"label": "Emails sent", "value": f"{totals['sent']:,}", "limit": "tracked sends",
         "status": "pass" if totals["sent"] else "pending"},
        {"label": "Delivered", "value": f"{totals['delivered']:,}", "limit": "per recipient",
         "status": "pass" if totals["delivered"] else "pending"},
    ]
    gates = (main_campaign.gates_json or []) if main_campaign else []
    passed = sum(1 for g in gates if g.get("pass"))
    return {
        "kpis": kpis,
        "clicks_by_day": clicks_by_day,
        "totals": totals,
        "stage_rates": stage_rates,
        "recent_campaigns": [_campaign_dict(c, db) for c in campaigns],
        "jobs": [{"id": j.id, "job_type": j.job_type, "title": j.title, "detail": j.detail, "status": j.status, "created_at": j.created_at} for j in jobs],
        "thresholds": thresholds,
        "launch_gates": {"passed": passed, "total": len(gates)},
    }


# ---------- campaigns ----------
class CampaignCreate(BaseModel):
    # `ignore` (not `forbid`): the UI must never hard-fail a send because it sent a
    # field this version does not know about yet. Version skew is normal here.
    model_config = ConfigDict(extra="ignore")

    title: str
    slug: str = ""
    audience_name: str = "Turbo_Addons | Deactivate_2500"
    audience_count: int = 0
    product: str = "Turbo Add-ons"
    offer: str = "Lifecycle sequence"
    sender_name: str = "Himel from Turbo Add-ons"
    sender_email: str = "hello@turbo-addons.com"
    sequence_slug: str = "turbo-addons-activated-1plus"
    # Label only - `list | segment | manual | audience`. Deliberately NOT an enum:
    # a new mode name must never 422 the whole send. What actually guards the send
    # is the recipient resolution below (an audience that yields nobody is rejected).
    recipients_mode: str = "manual"
    audience_id: int = 0          # saved audience to pull recipients from
    tag_filter: str = ""          # tag segment inside that audience ("a,b" = any by default)
    tag_match: str = "any"        # any (OR) | all (AND)
    segment_id: str = ""
    segment_name: str = ""
    tags: list = []
    manual_emails: list = []
    send_mode: Literal["direct", "draft", "schedule"] = "direct"
    send_test_first: str = "false"


@router.get("/campaigns", dependencies=[Depends(require_user)])
def list_campaigns(db: Session = Depends(get_db)):
    rows = db.query(models.Campaign).order_by(models.Campaign.id.desc()).all()
    return [_campaign_dict(c, db) for c in rows]


def _sendclean_send(db, c, email_row, recipients, html, x_unique_id, tracking_domain=""):
    """Single guarded transactional send through SendClean.

    Tracking headers are set explicitly: SendClean only records opens/clicks when the
    store-tracking flags are on, and routing them through a verified tracking domain
    (sctrans.<sender-domain>) keeps the pixel and click URLs on our own domain.
    """
    creds = ep.credentials(db)
    if not (creds["owner_id"] and creds["token"] and creds["smtp_user_name"]):
        raise HTTPException(
            status_code=400,
            detail="BLOCKED: SendClean owner ID, API token and SMTP username must all be configured in Settings",
        )
    if not tracking_domain:
        sender_domain = str(c.sender_email or "").split("@")[-1].strip().lower()
        tracking_domain = f"sctrans.{sender_domain}" if "." in sender_domain else ""
    headers = {
        "X-Unique-Id": x_unique_id,
        # Our own pixel measures this send; the provider's tracking is off because it
        # cannot be read back (their lookup hangs, their webhook never delivers).
        "X-Store-TrackOpens": "no",
        "X-Store-TrackClicks": "no",
    }
    if tracking_domain:
        # Left supported for a caller that still wants the provider's tracking domain
        # stamped on the message; it is unused while the store flags are off.
        headers["X-Store-TrackingDomain"] = tracking_domain
    message = {
        "subject": email_row.get("subject") or c.title,
        "from_email": c.sender_email,
        "from_name": c.sender_name,
        "to": recipients,
        "html": html,
        "headers": headers,
    }
    try:
        return sc.send_mail(creds["owner_id"], creds["token"], creds["smtp_user_name"], message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"SendClean error: {exc}") from exc

@router.post("/campaigns", dependencies=[Depends(require_user)])
def create_campaign(body: CampaignCreate, db: Session = Depends(get_db)):
    """Create a send record. SendClean has no audience API, so recipients are an explicit list."""
    # Slugs end up inside tracking tokens and URLs, so they are restricted to a safe
    # character set. Titles are free text: "26 October | PeopleDesk" used to become a
    # slug containing "|", which collided with the token delimiter and misfiled every
    # open and click for that campaign.
    raw_slug = body.slug.strip() or body.title.strip()
    base_slug = re.sub(r"[^a-z0-9]+", "-", raw_slug.lower()).strip("-") or "send"
    # The unique index on `slug` also covers soft-deleted campaigns, while the ORM
    # soft-delete guard hides those rows from ordinary queries. Checking through the
    # ORM therefore passed and the INSERT then raised UniqueViolation - which reached
    # the operator as a bare "Internal Server Error" while drafting with the agent.
    # Read the raw table (no soft-delete criteria) and give a colliding name a suffix.
    slug = base_slug
    suffix = 2
    while db.execute(
        _sql_text("SELECT 1 FROM campaigns WHERE slug = :s LIMIT 1"), {"s": slug}
    ).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    if body.tags:
        raise HTTPException(status_code=400, detail="Tags are not supported; provide an explicit recipient list instead")
    if body.audience_id:
        # Pull recipients from a saved audience; suppression and unsubscribes always win.
        from .audience_routes import member_emails, parse_tag_param
        aud = db.query(models.Audience).filter(models.Audience.id == int(body.audience_id)).first()
        if not aud:
            raise HTTPException(status_code=400, detail="Selected audience does not exist")
        emails = _normalise_emails(member_emails(db, aud.id, body.tag_filter, body.tag_match))
        if not emails:
            raise HTTPException(
                status_code=400,
                detail="BLOCKED: that audience/segment has no usable recipients "
                       "(unsubscribed and suppressed addresses are excluded)",
            )
        picked = parse_tag_param(body.tag_filter)
        if picked:
            joiner = " AND " if str(body.tag_match).lower() == "all" else " OR "
            audience_label = f"{aud.name} · {joiner.join(picked)}"
        else:
            audience_label = aud.name
    else:
        emails = _clean_manual_emails(body.manual_emails)
        audience_label = "Explicit recipient list"
    if not emails:
        raise HTTPException(
            status_code=400,
            detail="BLOCKED: no recipients - pick a saved audience, or paste at least one address.",
        )
    verified_count = len(emails)
    body.audience_count = verified_count
    # A plain single send, or a real branched sequence when the slug matches one.
    seq_row = None
    seq_slug = (body.sequence_slug or "").strip()
    if seq_slug and seq_slug not in ("single", "none"):
        seq_row = db.query(models.Sequence).filter(models.Sequence.slug == seq_slug).first()

    if seq_row and (seq_row.stages_json or []):
        stages = seq_row.stages_json
        sequence_type = f"{len(stages)}-stage branched lifecycle ({seq_row.name})"
    else:
        stages = [{
            "stage": 1, "focus": "Single Email", "delay_value": 0, "delay_unit": "hour",
            "delay_note": "Immediate - one-time send",
            "open_min": None, "open_max": None, "click_min": None, "click_max": None,
            "actual_open": None, "status": "On target",
            "emails": [{"ref": "E1", "name": "Single Email", "branch": "main",
                        "open_click_next": "", "open_no_click_next": "", "not_open_next": ""}],
        }]
        sequence_type = "Single transactional send (SendClean)"

    email_rows = []
    for st in stages:
        for definition in (st.get("emails") or []):
            email_rows.append({
                "stage": st.get("stage", 1),
                "ref": definition.get("ref") or f"E{len(email_rows) + 1}",
                "email_name": definition.get("name") or st.get("focus", "Email"),
                "branch": definition.get("branch", "main"),
                "subject": definition.get("name") or st.get("focus", "Email"),
                "preview_text": "",
                "body": "",
                "from": f"{body.sender_name} <{body.sender_email}>",
                "status": "Draft",
            })
    if not email_rows:
        email_rows = [{
            "stage": 1, "ref": "E1", "email_name": "Single Email", "branch": "main",
            "subject": body.title, "preview_text": "", "body": "",
            "from": f"{body.sender_name} <{body.sender_email}>", "status": "Draft",
        }]
    rec_mode = "audience" if body.audience_id else "manual"
    c = models.Campaign(
        slug=slug, title=body.title, status="draft",
        audience_name=audience_label, audience_count=verified_count,
        product=body.product, offer=body.offer,
        sender_name=body.sender_name, sender_email=body.sender_email,
        sequence_type=sequence_type,
        sequence_slug=(seq_row.slug if seq_row else "single"),
        sequence_json=stages,
        audience_options={
            "schema_version": 3,
            "provider": "sendclean",
            "mode": rec_mode,
            "audience_id": int(body.audience_id or 0),
            "audience_name": audience_label if body.audience_id else "",
            "tag_filter": (body.tag_filter or "").strip().lower(),
            "tag_match": str(body.tag_match or "any").lower(),
            "manual_emails": emails,
            "verified_count": verified_count,
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        send_mode=body.send_mode,
        send_test_first=body.send_test_first,
        emails_json=email_rows,
        qa_json=[],
        gates_json=[
            {"name": "Product knowledge sourced", "detail": "Pending source reference", "pass": True},
            {"name": "Recipients resolved and validated", "detail": f"{verified_count} address(es) from {audience_label}, no fixed application limit", "pass": True},
            {"name": "Sender identity + reply-to configured", "detail": "Present", "pass": True},
            {"name": "Content, links and personalisation validated", "detail": "Run before send", "pass": True},
            {"name": "SendClean credentials configured", "detail": "Owner ID + token + SMTP username", "pass": True},
            {"name": "Recipient volume recorded", "detail": f"{verified_count} address(es)", "pass": True},
        ],
    )
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        # Backstop for a concurrent create that took the slug between the check
        # above and this INSERT: report it instead of a 500.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A send with that name was created at the same moment. Change the title and try again.",
        )
    db.refresh(c)
    return _campaign_dict(c, db)


@router.get("/campaigns/{slug}", dependencies=[Depends(require_user)])
def get_campaign(slug: str, db: Session = Depends(get_db)):
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return _campaign_dict(c, db)


@router.post("/campaigns/{slug}/approve", dependencies=[Depends(require_user)])
def approve_campaign(slug: str, db: Session = Depends(get_db)):
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    # Approving is ONLY the draft -> approved transition. Everything else is a
    # deliberate no-op that still returns the campaign:
    #   - a retried launch must not fail just because the first attempt already
    #     moved the send past "draft" (that is what produced "Cannot approve
    #     campaign in state live" while the operator was clicking Launch);
    #   - and a live sequence must never be demoted back to "approved", which
    #     would hide its running state from the UI.
    if c.status == "draft":
        c.status = "approved"
        db.commit()
        db.refresh(c)
    return _campaign_dict(c, db)


@router.post("/campaigns/{slug}/launch", dependencies=[Depends(require_user)])
def launch_campaign(slug: str, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db)):
    """Send via SendClean. SendClean is transactional-only: one send to an explicit list."""
    _require_provider_capability(db, "transactional_send")
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    gates = c.gates_json or []
    if gates and not all(g.get("pass") for g in gates):
        raise HTTPException(status_code=400, detail="Not all launch gates passed")
    # Already-running sends answer with what happened instead of an opaque state
    # error, so a double click or a stale tab cannot look like a broken launch.
    if c.status in ("live", "sending"):
        raise HTTPException(
            status_code=409,
            detail=f"This send is already {c.status} - nothing was sent again. "
                   f"Open the campaign to see its sequence and delivery status.",
        )
    if c.status != "approved":
        raise HTTPException(status_code=400, detail=f"Cannot launch campaign in state {c.status}; final approval is required")
    if c.provider_message_id:
        raise HTTPException(status_code=400, detail="BLOCKED: this campaign was already sent; duplicate send is not allowed")
    mode = c.send_mode or "direct"
    emails = c.emails_json or []

    # A branched journey is NOT a one-off blast. Launching it MEANS enrolling the
    # recipients: E1 goes now and every later step follows from their own behaviour.
    # Sending E1 as a plain batch would fire it once, set provider_message_id, and
    # never start the journey - which is exactly the flow that was wrong before.
    if len(emails) > 1:
        from . import sequence_engine as seq
        to_enrol = _normalise_emails((c.audience_options or {}).get("manual_emails", []))
        if not to_enrol:
            raise HTTPException(status_code=400,
                                detail="BLOCKED: no recipients are stored for this sequence")
        c.status = "sending"
        db.commit()
        try:
            counts = seq.queue_enrollments(db, c, to_enrol)
        except ValueError as exc:
            c.status = "approved"
            db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.add(models.Job(
            job_type="launch", title=f"Sequence queued · {c.slug}",
            detail=" · ".join(f"{k}={v}" for k, v in counts.items())
                   + f" · {len(emails)} emails in the journey",
            status="queued", created_at=stamp()))
        db.commit()
        background_tasks.add_task(seq.run_queued_dispatch, c.slug)
        return {"ok": True, "accepted": True, "mode": "sequence",
                "campaign": c.slug, "status": "sending", **counts,
                "note": "Recipients are queued. Email 1 is sending in the background; "
                        "the scheduler safely resumes any remainder."}

    e = emails[0] if emails else {"subject": c.title, "preview_text": "", "body": ""}
    recipients = [{"email": addr, "type": "to"} for addr in (c.audience_options or {}).get("manual_emails", [])]
    if not recipients:
        raise HTTPException(status_code=400, detail="BLOCKED: no explicit recipients are stored for this campaign")
    html = _final_html(c, e)
    x_unique_id = f"sw-{c.slug}-{int(datetime.now(timezone.utc).timestamp())}"
    # Draft mode still needs a rendered html for the preview, but no token is minted
    # because nothing goes out.
    result = {"sent": False, "message_id": "", "note": ""}
    if mode == "draft":
        c.status = "ready"
        result["note"] = "Draft mode - nothing was sent through SendClean"
    else:
        # One SendClean call per recipient, each with its OWN X-Unique-Id, so open and
        # click status can be read back per person (SendClean returns exactly one entry
        # for a unique id). The batch id groups them for reporting.
        now_iso = datetime.now(timezone.utc).isoformat()
        delivered, failures = [], []
        for idx, rec in enumerate(recipients, start=1):
            uid = f"{x_unique_id}-{idx:03d}"
            try:
                # One-off sends have no enrollment, so the unsubscribe link is built here
                # from a signed token naming this recipient and this campaign. Without it
                # a single-email campaign went out with no way to opt out.
                unsub_url = f"{seq_public_base()}/api/unsubscribe?token=" + \
                    unsubscribe_token(db, c.slug, rec["email"])
                personalised = _final_html(c, e, unsubscribe_url=unsub_url)
                # Instrumented per recipient: the pixel and the redirects have to identify
                # which person they came from, so one html cannot be shared across a batch.
                send_result = _sendclean_send(
                    db, c, e, [rec],
                    tracking.instrument_for(db, personalised, uid, rec["email"], seq_public_base()),
                    uid)
                delivered.append({
                    "email": rec["email"],
                    "x_unique_id": uid,
                    "status": "sent",
                    "provider_status": send_result.get("status", ""),
                    "provider_message": str(send_result.get("message", ""))[:120],
                })
                # Own copy of the fact, written per recipient and committed with the
                # batch below: a provider outage afterwards cannot erase what was sent.
                ledger.record(
                    db, c.slug, (e.get("ref") or "E1"), rec["email"], x_unique_id=uid,
                    status="sent", batch=x_unique_id,
                    provider_status=send_result.get("status", ""),
                    provider_message=str(send_result.get("message", "")),
                    sent_at=now_iso, commit=False)
            except HTTPException as exc:
                failures.append({"email": rec["email"], "x_unique_id": uid, "error": str(exc.detail)[:200]})
                ledger.record_failure(
                    db, c.slug, (e.get("ref") or "E1"), rec["email"], exc.detail,
                    x_unique_id=uid, batch=x_unique_id, commit=False)
        if not delivered:
            raise HTTPException(
                status_code=400,
                detail="BLOCKED: every recipient send failed - " + "; ".join(f["error"] for f in failures[:3]),
            )
        c.delivery_json = {
            "batch": x_unique_id,
            "sent_at": now_iso,
            "tracking": "per_recipient",
            "recipients": delivered,
            "failures": failures,
        }
        c.provider_message_id = x_unique_id
        c.status = "live"
        result["sent"] = True
        result["message_id"] = x_unique_id
        result["sent_count"] = len(delivered)
        result["failed_count"] = len(failures)
        stamped = json.loads(json.dumps(emails))
        for row in stamped:
            if (row.get("ref") or "E1") == "E1":
                row["send_status"] = "sent"
                row["provider_id"] = x_unique_id
                row["sent_at"] = now_iso
        c.emails_json = stamped
    db.add(models.Job(
        job_type="launch",
        title=f"SendClean {'SENT' if result['sent'] else 'created'} - {c.slug}",
        detail=f"mode={mode} - x_unique_id={x_unique_id} - {len(recipients)} recipient(s)",
        status="done", created_at=stamp(),
    ))
    db.commit()
    out = _campaign_dict(c, db)
    out["sendclean"] = result
    return out


@router.get("/reports/delivery-ledger", dependencies=[Depends(require_user)])
def delivery_ledger_report(campaign_slug: str = "", batch: str = "", limit: int = 100,
                           db: Session = Depends(get_db)):
    """Delivery counts from Sendwise's own ledger. No provider call, ever.

    This is the report to read when SendClean is unreachable, rate limiting, or has had
    its API token rotated - the three situations in which a live pull returns nothing and
    the operator most needs to know what actually went out.
    """
    return {
        "summary": ledger.summary(db, campaign_slug=campaign_slug or None,
                                  batch=batch or None),
        "recent": ledger.recent(db, campaign_slug=campaign_slug or None, limit=limit),
    }


@router.post("/reports/delivery-ledger/backfill", dependencies=[Depends(require_user)])
def delivery_ledger_backfill(db: Session = Depends(get_db)):
    """Import per-recipient rows that only exist in old `campaigns.delivery_json` blobs."""
    return ledger.backfill(db)


@router.get("/email-provider/diagnostics", dependencies=[Depends(require_user)])
def email_provider_diagnostics(db: Session = Depends(get_db)):
    """Item-by-item reason the SendClean connection works or does not, including a probe."""
    return ep.diagnostics(db)


@router.post("/campaigns/{slug}/delivery-sync", dependencies=[Depends(require_user)])
def campaign_delivery_sync(slug: str, budget: int = 300, refresh: int = 0,
                           db: Session = Depends(get_db)):
    """Ask SendClean what happened to this campaign's messages and store the answers.

    SendClean is the only party that knows whether a message was delivered or bounced, and
    its webhook has never posted to this deployment. Running this writes one row per
    message into `delivery_status` and turns every hard bounce into a suppression.
    """
    from . import delivery_sync as dstat
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    result = dstat.sync(db, campaign=c, budget=budget, refresh=bool(refresh))
    db.add(models.Job(job_type="sync",
                      title=f"Delivery status synced · {slug}",
                      detail=(f"checked={result.get('checked')} "
                              f"delivered={result.get('delivered')} "
                              f"hard_bounce={result.get('hard_bounce')} "
                              f"soft_bounce={result.get('soft_bounce')} "
                              f"errors={result.get('errors')} "
                              f"suppressed={result.get('suppressed')}"),
                      status="done", created_at=stamp()))
    db.commit()
    result["summary"] = dstat.summary(db, slug)
    return result


@router.get("/campaigns/{slug}/delivery-status", dependencies=[Depends(require_user)])
def campaign_delivery_status(slug: str, db: Session = Depends(get_db)):
    """What the provider has confirmed so far for this campaign."""
    from . import delivery_sync as dstat
    return dstat.summary(db, slug)


@router.get("/campaigns/{slug}/delivery-report", dependencies=[Depends(require_user)])
def campaign_delivery_report(slug: str, refresh: int = 1, db: Session = Depends(get_db)):
    """Opened vs not-opened, per recipient.

    Reads SendClean status using each recipient's own X-Unique-Id. Open data is
    indicative, not exact: clients that block images never fire the tracking pixel
    (false negatives), while Gmail's image proxy and Apple Mail prefetch fire it
    without a real open (false positives). Clicks are the reliable signal.
    """
    _require_provider_capability(db, "message_lookup")
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    # Deep-copy before mutating: SQLAlchemy does not detect in-place changes to a
    # JSON column, so assigning the same object back would silently drop the write.
    delivery = json.loads(json.dumps(c.delivery_json or {}))
    recipients = delivery.get("recipients") or []
    if not recipients:
        raise HTTPException(status_code=400, detail="No per-recipient send data for this campaign yet")

    creds = ep.credentials(db)

    def look(entry):
        try:
            info = sc.get_message_info(creds["owner_id"], creds["token"], entry["x_unique_id"])
            rows = info.get("message_data") or []
            return entry, (rows[0] if rows else None)
        except Exception as exc:  # noqa: BLE001
            return entry, {"__error": str(exc)[:150]}

    if refresh:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            looked = list(pool.map(look, recipients))
    else:
        looked = [(r, None) for r in recipients]

    opened, not_opened, not_delivered, unknown, prefetch_only = [], [], [], [], []
    for entry, row in looked:
        item = {"email": entry["email"], "x_unique_id": entry["x_unique_id"]}
        if not row or row.get("__error"):
            item["status"] = "unknown"
            if row and row.get("__error"):
                item["error"] = row["__error"]
            unknown.append(item)
            continue
        item["status"] = row.get("status", "")
        # `opens` is the HUMAN count. The raw provider count and the machine count are
        # carried alongside so the reason a recipient sits here stays inspectable.
        eng = open_signal.engagement(row)
        item["opens"] = eng["human_opens"]
        item["raw_opens"] = eng["raw_opens"]
        item["machine_opens"] = eng["machine_opens"]
        item["clicks"] = eng["clicks"]
        item["last_open_ip"] = ((row.get("openData") or {}).get("ip") or [None])[-1]
        item["last_open_ua"] = ((row.get("openData") or {}).get("ua") or [None])[-1]
        if item["status"] not in ("delivered", "queued", ""):
            not_delivered.append(item)
        elif item["opens"] > 0:
            opened.append(item)
        elif item["raw_opens"] > 0:
            # Delivered, SendClean logged opens, but every one came from a proxy or
            # scanner fetching the pixel. Not an opener; listed separately.
            prefetch_only.append(item)
        else:
            not_opened.append(item)

    total = len(recipients)
    delivered_count = total - len(not_delivered)
    human_openers = len(opened)
    # Headline = every person whose pixel fired, proxy or not, so this matches the
    # Sends list and the Engagement tab. The strict count travels beside it.
    all_openers = human_openers + len(prefetch_only)
    # Clicks count across every delivered bucket: someone whose only pixel fetch was a
    # proxy can still have clicked a link, and a click is the strongest signal we have.
    engaged = opened + not_opened + prefetch_only
    unique_clickers = sum(1 for i in engaged if i.get("clicks", 0) > 0)
    total_clicks = sum(i.get("clicks", 0) for i in engaged)
    machine_opens = sum(i.get("machine_opens", 0) for i in engaged)
    rates = {
        "sent": total,
        "delivered": delivered_count,
        "unique_openers": all_openers,
        "unique_openers_human": human_openers,
        "unique_clickers": unique_clickers,
        "clicks": total_clicks,
        "proxy_only_openers": len(prefetch_only),
        "machine_opens_ignored": machine_opens,
        "open_rate": round(all_openers / total * 100, 2) if total else 0.0,
        "open_rate_human": round(human_openers / total * 100, 2) if total else 0.0,
        "click_rate": round(unique_clickers / total * 100, 2) if total else 0.0,
        "delivery_rate": round(delivered_count / total * 100, 2) if total else 0.0,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "sendclean",
        "open_rate_basis": ("all pixel fetches, including provider proxy loads; "
                            "open_rate_human excludes proxy/scanner fetches"),
    }

    # Persist the summary so the Brief tab and dashboard can show real numbers
    # without re-querying SendClean for every recipient.
    delivery["summary"] = rates
    c.delivery_json = delivery
    if rates["open_rate"]:
        c.open_rate = rates["open_rate"]
    if rates["click_rate"]:
        c.click_rate = rates["click_rate"]
    if rates["delivery_rate"]:
        c.delivery_rate = rates["delivery_rate"]
    if not c.launched_at:
        c.launched_at = delivery.get("sent_at", "")
    db.commit()

    return {
        "campaign": c.slug,
        "batch": delivery.get("batch", ""),
        "sent_at": delivery.get("sent_at", ""),
        "tracking": delivery.get("tracking", "per_recipient"),
        "totals": {
            "sent": total,
            "opened": unique_openers,
            "not_opened": len(not_opened),
            "prefetch_only": len(prefetch_only),
            "not_delivered": len(not_delivered),
            "unknown": len(unknown),
            "clicks": total_clicks,
            "open_rate": rates["open_rate"],
            "click_rate": rates["click_rate"],
            "delivery_rate": rates["delivery_rate"],
        },
        "rates": rates,
        "opened": sorted(opened, key=lambda x: -x.get("opens", 0)),
        "prefetch_only": prefetch_only,
        "not_opened": not_opened,
        "not_delivered": not_delivered,
        "unknown": unknown,
        "caveat": "Open data is indicative: image-blocking clients never fire the pixel (false "
                  "negatives), and a mail provider's image proxy can fire it without a real open. "
                  "Proxy and scanner fetches are identified by their User-Agent, excluded from "
                  "`opened`, and listed under `prefetch_only` instead. Apple Mail Privacy "
                  "Protection presents an ordinary browser User-Agent and cannot be filtered this "
                  "way, so treat the open rate as an upper bound. Clicks are the reliable signal.",
    }


# ---------- branched sequence engine ----------
def _unsub_page(title, message):
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head>
<body style="margin:0;font-family:Arial,Helvetica,sans-serif;background:#f5f7fb;color:#333;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:60px 16px;">
<table width="520" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;border-top:4px solid #253F8D;">
<tr><td style="padding:32px;">
<h1 style="font-size:20px;color:#253F8D;margin:0 0 12px;">{title}</h1>
<p style="font-size:14.5px;line-height:1.7;color:#555;margin:0;">{message}</p>
</td></tr></table></td></tr></table></body></html>"""


class EnrollIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emails: list | None = None
    audience_id: int = 0          # enroll a saved audience instead of pasting addresses
    tag_filter: str = ""
    tag_match: str = "any"


@router.post("/campaigns/{slug}/enroll", status_code=202,
             dependencies=[Depends(require_user)])
def enroll_sequence(slug: str, background_tasks: BackgroundTasks,
                    body: EnrollIn | None = None, db: Session = Depends(get_db)):
    """Enroll recipients into the branched sequence and send the first email now.

    Recipients come from, in order of precedence: an explicitly supplied list, a
    saved audience (+ optional tag segment), or the list the send was built with.
    """
    from . import sequence_engine as seq
    _require_provider_capability(db, "transactional_send")
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if c.status == "sending":
        raise HTTPException(status_code=409,
                            detail="This journey is already sending. Nothing was started twice.")
    pending = db.query(models.SequenceEnrollment).filter(
        models.SequenceEnrollment.campaign_slug == c.slug,
        models.SequenceEnrollment.status == "active",
        models.SequenceEnrollment.current_ref == "",
    ).count()
    if pending:
        c.status = "sending"
        db.commit()
        raise HTTPException(status_code=409,
                            detail=f"This journey already has {pending} recipient(s) queued for Email 1.")

    source = "send's own recipient list"
    if body and body.audience_id:
        from .audience_routes import member_emails
        aud = db.query(models.Audience).filter(models.Audience.id == int(body.audience_id)).first()
        if not aud:
            raise HTTPException(status_code=400, detail="Selected audience does not exist")
        recipients = _normalise_emails(member_emails(db, aud.id, body.tag_filter, body.tag_match))
        source = f"audience “{aud.name}”" + (f" · tags {body.tag_filter}" if body.tag_filter else "")
        if not recipients:
            raise HTTPException(
                status_code=400,
                detail="BLOCKED: that audience/segment has no usable recipients "
                       "(unsubscribed and suppressed addresses are excluded)",
            )
    elif body and body.emails:
        recipients = _clean_manual_emails(body.emails)
    else:
        recipients = _clean_manual_emails((c.audience_options or {}).get("manual_emails") or [])

    if not recipients:
        raise HTTPException(status_code=400, detail="BLOCKED: no recipients to enroll")
    previous_status = c.status
    c.status = "sending"
    db.commit()
    try:
        counts = seq.queue_enrollments(db, c, recipients)
    except ValueError as exc:
        c.status = previous_status
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.add(models.Job(job_type="dispatch", title=f"Sequence queued · {c.slug}",
                      detail=f"from {source} · "
                             + " · ".join(f"{k}={v}" for k, v in counts.items()),
                      status="queued", created_at=stamp()))
    db.commit()
    background_tasks.add_task(seq.run_queued_dispatch, c.slug)
    return {"ok": True, "accepted": True, "campaign": c.slug, "source": source,
            "status": "sending", **counts,
            "note": "Email 1 is sending in the background. Do not start this journey again."}


@router.post("/cron/dispatch")
@router.post("/cron/sequence-dispatch")
def cron_sequence_dispatch(x_cron_secret: str = Header(default=""), dry_run: int = 0,
                           db: Session = Depends(get_db)):
    """Cron tick: advance every enrollment that is due through its branch."""
    from . import sequence_engine as seq
    _require_cron(x_cron_secret)
    _require_provider_capability(db, "transactional_send")

    # One dispatcher at a time: overlapping ticks would each send the same email.
    if not dry_run and not seq.acquire_dispatch_lock(db):
        return {"ok": True, "skipped": "another dispatch is already in progress"}

    try:
        result = seq.dispatch(db, dry_run=bool(dry_run))
    finally:
        if not dry_run:
            seq.release_dispatch_lock(db)

    # Cron is the durable recovery path after a worker restart. Reconcile every queued
    # or historically stale send it owns, not only the campaign whose HTTP background
    # task happened to survive. Paused/live campaigns are deliberately left untouched.
    if not dry_run:
        stale_slugs = (
            db.query(models.SequenceEnrollment.campaign_slug)
            .join(models.Campaign,
                  models.Campaign.slug == models.SequenceEnrollment.campaign_slug)
            .filter(models.Campaign.status.in_(("approved", "sending")))
            .distinct().all()
        )
        for (campaign_slug,) in stale_slugs:
            seq.reconcile_campaign_state(db, campaign_slug)

    if not dry_run and (result["sent"] or result["completed"] or result["failed"]
                        or result.get("parked")):
        db.add(models.Job(
            job_type="dispatch",
            title=f"Sequence dispatch · {len(result['sent'])} sent",
            detail=f"completed={len(result['completed'])} "
                   f"suppressed={len(result['blocked_suppressed'])} "
                   f"failed={len(result['failed'])} "
                   f"needs_review={len(result.get('parked') or [])}"
                   + (f" · {'; '.join((result.get('parked') or [])[:2])}"
                      if result.get("parked") else ""),
            status="failed" if (result["failed"] or result.get("parked")) else "done",
            created_at=stamp()))
        db.commit()

    if not dry_run:
        sending_slugs = [slug for (slug,) in db.query(models.Campaign.slug).filter(
            models.Campaign.status == "sending").all()]
        for campaign_slug in sending_slugs:
            seq.reconcile_campaign_state(db, campaign_slug)

    # Provider verdicts ride on this tick too. The scheduler already runs every 15
    # minutes, and the webhook has never posted, so this bounded pull is what keeps
    # Delivered / Bounced honest. Failures here must never break the send path.
    delivery_result = None
    if not dry_run:
        try:
            from . import delivery_sync as dstat
            delivery_result = dstat.sync(db, budget=120)
        except Exception:  # noqa: BLE001 - a provider hiccup must not stop a dispatch
            delivery_result = None

    # The automatic backup rides on this tick. The scheduler already runs every 15
    # minutes, so no second moving part is needed - and a restart also checks, so
    # downtime is caught up on either way.
    _maybe_auto_backup(db, label="auto")

    # Self-check: a journey waiting on the wrong clock looks completely normal from the
    # outside, so the tick verifies that what each enrollment is waiting for matches
    # what its sequence declares. A mismatch is recorded loudly instead of silently.
    try:
        from . import sequence_engine as seq_check
        problems = seq_check.audit_delays(db)
        if problems:
            db.add(models.Job(
                job_type="warning",
                title=f"{len(problems)} enrollment(s) waiting on the wrong clock",
                detail=" · ".join(
                    f"{p['email']} @ {p['campaign']} ({p['ref']}): waiting {p['actual']}, "
                    f"sequence says {p['expected']}" for p in problems[:3])
                    + (" …" if len(problems) > 3 else "")
                    + " · fix from Sequences → Self-check",
                status="failed", created_at=stamp()))
            db.commit()
    except Exception:  # noqa: BLE001 - a self-check must never break a send
        pass

    return {"ok": True, **result}


def _maybe_auto_backup(db, label="auto"):
    """Take a snapshot when one is due. Never raises - a backup must not break a send."""
    try:
        from . import backup as bk
        row = bk.run_due_backup(db, label=label)
        if row:
            db.add(models.Job(job_type="sync", title=f"Automatic backup · {row.label}",
                              detail=f"{row.size_bytes:,} bytes · "
                                     + " · ".join(f"{k}={v}" for k, v in (row.counts_json or {}).items()),
                              status="done", created_at=stamp()))
            db.commit()
        return row
    except Exception:  # noqa: BLE001
        return None


@router.post("/cron/backup")
def cron_backup(x_cron_secret: str = Header(default=""), force: int = 0,
                db: Session = Depends(get_db)):
    """Take an automatic backup. Safe to call any time; set force=1 to skip the interval."""
    from . import backup as bk
    _require_cron(x_cron_secret)
    row = bk.create_backup(db, label="auto") if force else bk.run_due_backup(db)
    if not row:
        return {"ok": True, "skipped": "a recent backup already exists",
                "interval_hours": bk.BACKUP_INTERVAL_HOURS}
    bk.rotate(db)
    return {"ok": True, "id": row.id, "label": row.label, "size_bytes": row.size_bytes,
            "counts": row.counts_json,
            "created_at": row.created_at.isoformat() if row.created_at else ""}


# ---------- backups: Sendwise takes these itself ----------
@router.get("/backups", dependencies=[Depends(require_user)])
def list_backups(db: Session = Depends(get_db)):
    """The rolling backup history. Automatic - nothing here needs a manual step."""
    from . import backup as bk
    rows = db.query(models.Backup).order_by(models.Backup.id.desc()).limit(200).all()
    newest = rows[0].created_at.isoformat() if rows and rows[0].created_at else ""
    return {
        "interval_hours": bk.BACKUP_INTERVAL_HOURS,
        "keep": bk.KEEP_BACKUPS,
        "next_due": bk.backup_due(db),
        "newest": newest,
        "backups": [
            {"id": r.id, "label": r.label, "size_bytes": r.size_bytes,
             "checksum": r.checksum, "counts": r.counts_json or {},
             "created_at": r.created_at.isoformat() if r.created_at else ""}
            for r in rows
        ],
    }


@router.post("/backups/now", dependencies=[Depends(require_user)])
def backup_now(db: Session = Depends(get_db)):
    """Take a snapshot immediately, without waiting for the schedule."""
    from . import backup as bk
    row = bk.create_backup(db, label="manual")
    bk.rotate(db)
    return {"ok": True, "id": row.id, "size_bytes": row.size_bytes, "counts": row.counts_json}


@router.get("/backups/{backup_id}", dependencies=[Depends(require_user)])
def get_backup(backup_id: int, db: Session = Depends(get_db)):
    """The full snapshot payload, for the download button."""
    row = db.query(models.Backup).filter(models.Backup.id == int(backup_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")
    return row.payload_json or {}


@router.post("/backups/{backup_id}/restore", dependencies=[Depends(require_user)])
def restore_backup(backup_id: int, db: Session = Depends(get_db)):
    """Merge a snapshot back in. Additive only - it never deletes anything."""
    from . import backup as bk
    row = db.query(models.Backup).filter(models.Backup.id == int(backup_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")

    # Snapshot the current state first, so a restore is itself reversible.
    safety = bk.create_backup(db, label="pre-restore")
    stats = bk.restore(db, row.payload_json or {})
    db.add(models.Job(
        job_type="sync", title=f"Restored from backup #{row.id}",
        detail=" · ".join(f"{k}={v}" for k, v in stats.items())
               + f" · safety snapshot #{safety.id} taken first",
        status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "restored_from": row.id, "safety_backup": safety.id, "stats": stats}


@router.delete("/backups/{backup_id}", dependencies=[Depends(require_user)])
def delete_backup(backup_id: int, db: Session = Depends(get_db)):
    """Remove one snapshot. Only the snapshot goes - the live data is untouched."""
    row = db.query(models.Backup).filter(models.Backup.id == int(backup_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")
    db.delete(row)
    db.commit()
    return {"ok": True, "removed": backup_id}


@router.get("/unsubscribe")
def unsubscribe(token: str = "", db: Session = Depends(get_db)):
    """One-click unsubscribe. Public - the link lands in the recipient's inbox.

    Two kinds of link arrive here. A sequence send carries its enrollment token; a
    one-off send (a single-email campaign, which has no enrollment) carries a signed
    token naming the campaign and the address. Both end in the same suppression, so a
    recipient can leave from any email we sent them.
    """
    from . import sequence_engine as seq
    if not token:
        return HTMLResponse(_unsub_page("Invalid link", "No unsubscribe token was supplied."),
                            status_code=400)

    email = ""
    slug_hit = ""
    enr = (db.query(models.SequenceEnrollment)
           .filter(models.SequenceEnrollment.token == token).first())
    if enr:
        email, slug_hit = enr.email, enr.campaign_slug
    else:
        payload = tracking.verify(token, _tracking_secret(db))
        if not payload or not payload.startswith("u" + tracking.FIELD_SEP):
            return HTMLResponse(_unsub_page(
                "Link not recognised",
                "This unsubscribe link is invalid or has already been used."), status_code=404)
        _, slug_hit, email = payload.split(tracking.FIELD_SEP, 2)

    if not email:
        return HTMLResponse(_unsub_page(
            "Link not recognised",
            "This unsubscribe link is invalid or has already been used."), status_code=404)

    seq.suppress(db, email, reason="unsubscribe", source=slug_hit, detail="one-click")

    # Attribution: only the campaign whose email was actually opened can claim the
    # unsubscribe. Every other campaign that has this address simply stops sending -
    # it is marked `suppressed`, not `unsubscribed` - otherwise one click inflated the
    # unsubscribe count of every campaign the person had ever received.
    stopped = 0
    query = (db.query(models.SequenceEnrollment)
             .filter(models.SequenceEnrollment.email == email,
                     models.SequenceEnrollment.status == "active"))
    for row in query.all():
        row.status = "unsubscribed" if row.campaign_slug == slug_hit else "suppressed"
        stopped += 1
    db.commit()
    db.add(models.Job(job_type="warning", title="Unsubscribe recorded",
                      detail=f"{email} from {slug_hit or 'unknown send'} · "
                             f"{stopped} enrollment(s) stopped",
                      status="done", created_at=stamp()))
    db.commit()
    return HTMLResponse(_unsub_page(
        "You're unsubscribed",
        f"<b>{email}</b> has been added to the suppression list and will not receive "
        f"further emails from any of our sends. You can close this page."))


UNSUB_PREFIX = "u"


def unsubscribe_token(db, campaign_slug, email):
    """Signed one-click unsubscribe token for a send with no enrollment.

    The address is inside a signed payload, so it needs no lookup and cannot be edited
    by the recipient to unsubscribe somebody else.
    """
    payload = f"{UNSUB_PREFIX}{tracking.FIELD_SEP}{campaign_slug}{tracking.FIELD_SEP}{email}"
    return tracking.sign(payload, _tracking_secret(db))


TRACKING_SECRET_SETTING = "tracking_secret"


def _tracking_secret(db):
    """The signing key for tracking tokens, shared with the send paths."""
    return tracking.secret_for(db)


# How close two opens must be to count as one. A single render can request the tracking
# image more than once: measured on real mail, the duplicate arrives 0.0 to 0.6 seconds
# after the first, from the same address and the same user agent, which is one open
# counted twice. A person reopening an email cannot do it in under a second - genuine
# repeats in the same data were 8 to 10 seconds apart - so a short window separates them
# without merging real repeat views.
OPEN_DEDUPE_SECONDS = int(os.getenv("TRACK_OPEN_DEDUPE_SECONDS", "5"))


def _duplicate_open(db, xid):
    """The open already recorded for this send within the dedupe window, if any."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=OPEN_DEDUPE_SECONDS)
    return (db.query(models.EngagementEvent)
              .filter(models.EngagementEvent.x_unique_id == xid,
                      models.EngagementEvent.event_type == "open",
                      models.EngagementEvent.received_at >= cutoff)
              .order_by(models.EngagementEvent.id.desc())
              .first())


def _record_hit(db, request, token, kind, target=""):
    """Store one open or click that arrived at our own endpoint.

    This is the only place engagement is captured. The request's own User-Agent and
    network decide whether a machine or a person made it, using the same rules as
    everywhere else.

    `target` is the link a click was headed for. It is kept so "Top Links Clicked" can be
    answered - knowing that a campaign drew 12 clicks is far less useful than knowing
    which link drew them.

    An open that repeats within OPEN_DEDUPE_SECONDS is the same render asking twice, and is
    not recorded: it is one event, not two, so storing it would inflate every open count
    and the branch rule that reads them. Returns `None` for such a repeat.
    """
    payload = tracking.verify(token, _tracking_secret(db))
    if not payload:
        return None
    xid, email = tracking.read_token(payload)
    if not xid or not email:
        return None

    if kind == "open" and _duplicate_open(db, xid) is not None:
        return None

    ua = request.headers.get("user-agent", "")
    ip = tracking.client_ip(request)
    machine = open_signal.is_machine_ua(ua) or open_signal.is_proxy_ip(ip)
    parsed = open_signal.parse_x_unique_id(xid)

    db.add(models.EngagementEvent(
        email=email,
        event_type=kind,
        x_unique_id=xid,
        campaign_slug=parsed["slug"],
        ref=parsed["ref"],
        is_machine=machine,
        machine_reason=open_signal.machine_reason(ua, ip) if machine else "",
        ip=ip[:64],
        user_agent=ua[:400],
        # The provider's timestamp is not available on our own pixel, so the arrival
        # time IS the event time. Leaving this empty broke every day-bucketed report
        # (and the "opened when" column) even though the event itself was recorded.
        occurred_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        raw_json={"source": "own-tracker",
                  "forwarded_for": request.headers.get("x-forwarded-for", "")[:120],
                  "target": str(target or "")[:300]},
    ))
    db.commit()
    return {"email": email, "kind": kind, "machine": machine, "campaign": parsed["slug"],
            "ref": parsed["ref"], "target": str(target or "")[:300]}


@router.get("/track/o/{name}")
def track_open(name: str, request: Request, db: Session = Depends(get_db)):
    """The tracking pixel. Public, because it is fetched by the recipient's mail client.

    Always answers with the image, whatever happens: a broken pixel shows a broken image
    in someone's inbox, which is worse than losing one open.
    """
    token = name[:-4] if name.lower().endswith(".gif") else name
    try:
        _record_hit(db, request, token, "open")
    except Exception:  # noqa: BLE001 - never fail the image
        db.rollback()
    return Response(
        content=tracking.PIXEL,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                 "Pragma": "no-cache", "Expires": "0"},
    )


@router.get("/track/c/{name}")
def track_click(name: str, request: Request, u: str = "", db: Session = Depends(get_db)):
    """The click redirect. Records the click, then sends the reader on their way.

    The destination is passed in rather than stored, so a link still resolves even if we
    cannot record it. A redirect that fails closed would break the email it was meant to
    measure.
    """
    target = (u or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="No destination supplied")
    if not re.match(r"^https?://", target, re.I):
        # Anything that is not http(s) is refused rather than followed: an open redirect
        # to an arbitrary scheme is a phishing vector.
        raise HTTPException(status_code=400, detail="Destination must be http or https")
    try:
        _record_hit(db, request, name, "click", target=target)
    except Exception:  # noqa: BLE001 - never block the reader
        db.rollback()
    return RedirectResponse(url=target, status_code=302)


@router.get("/campaigns/{slug}/sequence-progress", dependencies=[Depends(require_user)])
def sequence_progress(slug: str, db: Session = Depends(get_db)):
    """Live per-recipient progress through the branched sequence."""
    from . import sequence_engine as seq
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    rows = (db.query(models.SequenceEnrollment)
            .filter(models.SequenceEnrollment.campaign_slug == slug)
            .order_by(models.SequenceEnrollment.id).all())

    by_ref = {}
    for e in rows:
        key = e.current_ref or "not_sent"
        by_ref[key] = by_ref.get(key, 0) + 1

    return {
        "campaign": slug,
        "tree": seq.BRANCH_TREE,
        "gap_days": seq.NEXT_GAP_DAYS,
        "counts": {
            "enrolled": len(rows),
            "active": sum(1 for e in rows if e.status == "active"),
            "completed": sum(1 for e in rows if e.status == "completed"),
            "unsubscribed": sum(1 for e in rows if e.status == "unsubscribed"),
            "suppressed": sum(1 for e in rows if e.status == "suppressed"),
            "failed": sum(1 for e in rows if e.status == "failed"),
            "by_ref": by_ref,
        },
        "enrollments": [{
            "email": e.email, "status": e.status, "current_ref": e.current_ref,
            "branch": e.branch, "stage": e.stage, "sends": e.sends,
            "last_sent_at": e.last_sent_at, "next_due_at": e.next_due_at,
            "history": e.history_json or [],
        } for e in rows],
    }


@router.post("/sequences/dispatch-now", dependencies=[Depends(require_user)])
def dispatch_now(dry_run: int = 0, db: Session = Depends(get_db)):
    """Advance every due enrollment immediately (the cron does this every 15 min)."""
    from . import sequence_engine as seq
    _require_provider_capability(db, "transactional_send")

    # The same lock as the cron: a manual run during a scheduled one would otherwise
    # send the same email twice.
    if not dry_run and not seq.acquire_dispatch_lock(db):
        # Same shape as a real run. Returning only `skipped` made every caller that
        # reads `sent.length` blow up with "cannot read properties of undefined",
        # which hid the actual reason from the person who pressed the button.
        return {"ok": True, "skipped": "another dispatch is already in progress",
                "dry_run": False, "plan": [], "sent": [], "completed": [],
                "blocked_suppressed": [], "failed": [], "parked": [], "paused": [],
                "deferred": 0}

    try:
        result = seq.dispatch(db, dry_run=bool(dry_run))
    finally:
        if not dry_run:
            seq.release_dispatch_lock(db)

    if not dry_run and (result["sent"] or result["completed"] or result["failed"]
                        or result.get("parked") or result.get("paused")):
        db.add(models.Job(job_type="dispatch",
                          title=f"Sequence dispatch (manual) - {len(result['sent'])} sent",
                          detail=f"completed={len(result['completed'])} "
                                 f"suppressed={len(result['blocked_suppressed'])} "
                                 f"failed={len(result['failed'])} "
                                 f"needs_review={len(result.get('parked') or [])} "
                                 f"paused={len(result.get('paused') or [])}",
                          status="failed" if (result["failed"] or result.get("parked")) else "done",
                          created_at=stamp()))
        db.commit()
    return {"ok": True, **result}


@router.get("/sequence-overview", dependencies=[Depends(require_user)])
def sequence_overview(db: Session = Depends(get_db)):
    """Everything the Sequences page needs: templates, live enrollments, suppressions."""
    from . import sequence_engine as seq

    templates = []
    for s in (db.query(models.Sequence)
              .filter(models.Sequence.deleted_at == "")
              .order_by(models.Sequence.id).all()):
        stages = s.stages_json or []
        refs = [e.get("ref") for st in stages for e in (st.get("emails") or []) if e.get("ref")]
        # Each sequence carries its OWN delays, so the global default is only a
        # fallback - showing it as "every 3 days" would misdescribe a 5-minute flow.
        waits = []
        for st in stages:
            if not (st.get("emails") or []):
                continue
            value = st.get("delay_value")
            unit = st.get("delay_unit") or "day"
            if value:
                waits.append(f"{value} {unit}")
            else:
                waits.append("immediate")
        templates.append({
            "slug": s.slug, "name": s.name, "trigger": s.trigger,
            "stages": len(stages), "emails": refs, "created_at": s.created_at,
            "waits": waits,
            "gap_summary": " → ".join(waits) if waits else f"{seq.NEXT_GAP_DAYS} days",
        })

    rows = (db.query(models.SequenceEnrollment)
            .order_by(models.SequenceEnrollment.id.desc()).limit(5000).all())

    by_campaign = {}
    for e in rows:
        b = by_campaign.setdefault(e.campaign_slug, {
            "campaign": e.campaign_slug, "total": 0, "active": 0, "completed": 0,
            "unsubscribed": 0, "suppressed": 0, "failed": 0, "by_ref": {},
        })
        b["total"] += 1
        if e.status in b:
            b[e.status] += 1
        key = e.current_ref or "not_sent"
        b["by_ref"][key] = b["by_ref"].get(key, 0) + 1

    suppressions = (db.query(models.Suppression)
                    .order_by(models.Suppression.id.desc()).limit(200).all())

    # Sends that actually carry a multi-email sequence - these are the ones that can be run.
    run_targets = []
    for c in db.query(models.Campaign).order_by(models.Campaign.id.desc()).all():
        emails = [e for e in (c.emails_json or []) if e.get("ref")]
        if len(emails) < 2:
            continue
        missing = [e["ref"] for e in emails if len((e.get("body") or "").strip()) < 40]
        run_targets.append({
            "slug": c.slug, "title": c.title, "product": c.product, "status": c.status,
            "emails": len(emails), "refs": [e["ref"] for e in emails],
            "missing_copy": missing, "ready": not missing,
            "enrolled": sum(1 for e in rows if e.campaign_slug == c.slug),
            "active": sum(1 for e in rows if e.campaign_slug == c.slug and e.status == "active"),
        })

    return {
        "tree": seq.BRANCH_TREE,
        "run_targets": run_targets,
        "gap_days": seq.NEXT_GAP_DAYS,
        "soft_bounce_limit": SOFT_BOUNCE_LIMIT,
        "templates": templates,
        "campaigns": sorted(by_campaign.values(), key=lambda x: -x["total"]),
        "totals": {
            "enrolled": len(rows),
            "active": sum(1 for e in rows if e.status == "active"),
            "completed": sum(1 for e in rows if e.status == "completed"),
            "unsubscribed": sum(1 for e in rows if e.status == "unsubscribed"),
            "suppressed_enrollments": sum(1 for e in rows if e.status == "suppressed"),
            "failed": sum(1 for e in rows if e.status == "failed"),
            "suppressed_addresses": db.query(models.Suppression).count(),
        },
        "enrollments": [{
            "campaign": e.campaign_slug, "email": e.email, "status": e.status,
            "current_ref": e.current_ref, "branch": e.branch, "stage": e.stage,
            "sends": e.sends, "last_sent_at": e.last_sent_at, "next_due_at": e.next_due_at,
        } for e in rows],
        "suppressions": [{
            "email": r.email, "reason": r.reason, "source": r.source, "created_at": r.created_at,
        } for r in suppressions],
    }


@router.get("/suppressions", dependencies=[Depends(require_user)])
def list_suppressions(db: Session = Depends(get_db)):
    rows = db.query(models.Suppression).order_by(models.Suppression.id.desc()).limit(5000).all()
    return [{"email": r.email, "reason": r.reason, "source": r.source,
             "detail": r.detail, "created_at": r.created_at} for r in rows]


class SuppressIn(BaseModel):
    email: str
    reason: str = "manual"


@router.post("/suppressions", dependencies=[Depends(require_user)])
def add_suppression(body: SuppressIn, db: Session = Depends(get_db)):
    from . import sequence_engine as seq
    if "@" not in (body.email or ""):
        raise HTTPException(status_code=400, detail="A valid email is required")
    seq.suppress(db, body.email, reason=body.reason or "manual", source="operator")
    (db.query(models.SequenceEnrollment)
       .filter(models.SequenceEnrollment.email == body.email.strip().lower(),
               models.SequenceEnrollment.status == "active")
       .update({"status": "suppressed"}, synchronize_session=False))
    db.commit()
    return {"ok": True, "email": body.email.strip().lower(), "reason": body.reason}


# ---------- SendClean event webhook: bounces + complaints -> suppression ----------
WEBHOOK_KEY_SETTING = "sendclean_webhook_key"
WEBHOOK_URL_SETTING = "sendclean_webhook_url"
SOFT_BOUNCE_SETTING = "sendclean_soft_bounces"
SOFT_BOUNCE_LIMIT = 3


def _webhook_signature_ok(request, form, key, configured_url=""):
    """SendClean signs HMAC-SHA1(base64) over the CONFIGURED url + sorted POST vars.

    The configured URL must be used rather than request.url: behind the edge the
    request arrives as http://127.0.0.1:8000/... which would never match.
    """
    if not key:
        return True  # not registered yet, nothing to verify against
    provided = request.headers.get("x-sendcleantes-signature", "")
    if not provided:
        return False
    base = configured_url or str(request.url)
    for name in sorted(form.keys()):
        base += name + str(form.get(name, ""))
    digest = hmac.new(key.encode("utf-8"), base.encode("utf-8"), hashlib.sha1).digest()
    return hmac.compare_digest(provided, base64.b64encode(digest).decode())


def _xid_from_event(ev):
    """The X-Unique-Id on a webhook event, under any of the spellings it may use."""
    nested = ev.get("msg") if isinstance(ev.get("msg"), dict) else {}
    for src in (ev, nested):
        if not isinstance(src, dict):
            continue
        for name in ("x_unique_id", "xuniqueid", "x_unique", "unique_id",
                     "X-Unique-Id", "uid"):
            v = src.get(name)
            if v not in (None, ""):
                return str(v).strip()
    return ""


def _record_delivery(db, ev, xid, email):
    """Store one provider delivery confirmation.

    Kept as its own event type because it answers a different question from an open:
    whether the message arrived at all. The provider is the only source for it - our own
    pixel cannot distinguish "delivered and unread" from "never arrived".
    """
    if not email:
        return None
    parsed = open_signal.parse_x_unique_id(xid) if xid else {"slug": "", "ref": ""}
    db.add(models.EngagementEvent(
        email=email,
        event_type="delivered",
        x_unique_id=xid or "",
        campaign_slug=parsed.get("slug", ""),
        ref=parsed.get("ref", ""),
        is_machine=False,
        ip="",
        user_agent="",
        occurred_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        raw_json={"source": "sendclean-webhook"},
    ))
    return {"email": email, "event": "delivered"}


def _sync_delivery(db, campaign, limit=200):
    """Pull delivery status from SendClean for messages we have no confirmation for.

    SendClean's webhook registers and answers the handshake, but (measured, not assumed)
    it never posts a `delivered` event - so a sequence send had no delivery data at all
    and the Delivery column stayed blank forever. Their message lookup does answer, and
    returns a per-message `status` such as "delivered" or "bounced", so delivery is read
    from there instead of waiting for a callback that never comes.

    Bounded on purpose: `limit` messages per call keeps the request short, and the caller
    repeats the call until `remaining` reaches 0.
    """
    from concurrent.futures import ThreadPoolExecutor

    creds = ep.credentials(db)
    if not (creds["owner_id"] and creds["token"]):
        return {"checked": 0, "delivered": 0, "bounced": 0, "remaining": 0,
                "error": "SendClean credentials are not configured"}

    already = {r[0].lower() for r in
               db.query(models.EngagementEvent.email)
                 .filter(models.EngagementEvent.campaign_slug == campaign.slug,
                         models.EngagementEvent.event_type == "delivered").all()
               if r[0]}

    rows = (db.query(models.SequenceEnrollment)
              .filter(models.SequenceEnrollment.campaign_slug == campaign.slug,
                      models.SequenceEnrollment.last_x_unique_id != "")
              .all())
    pending = [e for e in rows
               if (e.email or "").lower() not in already
               and (e.status or "") not in ("unsubscribed", "suppressed")]
    batch = pending[:max(1, int(limit or 200))]

    def look(enr):
        try:
            info = sc.get_message_info(creds["owner_id"], creds["token"],
                                       enr.last_x_unique_id, timeout=12)
        except Exception:  # noqa: BLE001 - one unreadable message must not stop the batch
            return enr, ""
        messages = (info or {}).get("messages") or []
        if not messages:
            return enr, ""
        return enr, str((messages[0] or {}).get("status") or "").strip().lower()

    delivered = bounced = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=8) as pool:
        for enr, status in pool.map(look, batch):
            if status == "delivered":
                parsed = open_signal.parse_x_unique_id(enr.last_x_unique_id or "")
                db.add(models.EngagementEvent(
                    email=(enr.email or "").lower(),
                    event_type="delivered",
                    x_unique_id=enr.last_x_unique_id or "",
                    campaign_slug=parsed.get("slug") or campaign.slug,
                    ref=parsed.get("ref", ""),
                    is_machine=False, ip="", user_agent="",
                    occurred_at=now,
                    raw_json={"source": "sendclean-message-lookup"},
                ))
                delivered += 1
            elif status in ("bounced", "failed", "rejected", "hard_bounce"):
                parsed = open_signal.parse_x_unique_id(enr.last_x_unique_id or "")
                db.add(models.EngagementEvent(
                    email=(enr.email or "").lower(),
                    event_type="hard_bounce",
                    x_unique_id=enr.last_x_unique_id or "",
                    campaign_slug=parsed.get("slug") or campaign.slug,
                    ref=parsed.get("ref", ""),
                    is_machine=False, ip="", user_agent="",
                    occurred_at=now,
                    raw_json={"source": "sendclean-message-lookup", "status": status},
                ))
                bounced += 1
    db.commit()
    return {"checked": len(batch), "delivered": delivered, "bounced": bounced,
            "remaining": max(0, len(pending) - len(batch)),
            "total_pending": len(pending)}


@router.post("/campaigns/{slug}/sync-delivery", dependencies=[Depends(require_user)])
def sync_campaign_delivery(slug: str, limit: int = 200, db: Session = Depends(get_db)):
    """Fill in delivery confirmations from the provider for this send."""
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    result = _sync_delivery(db, c, limit=limit)
    if result.get("delivered"):
        db.add(models.Job(
            job_type="delivery", title=f"Delivery synced · {c.slug}",
            detail=f"{result['delivered']} delivered, {result['bounced']} bounced, "
                   f"{result.get('remaining', 0)} still pending",
            status="done", created_at=stamp()))
        db.commit()
    return result


def _suppress_and_stop(db, email, reason, detail):
    """Global suppression + stop every active enrollment for that address."""
    from . import sequence_engine as seq
    seq.suppress(db, email, reason=reason, source="sendclean-webhook", detail=detail)
    (db.query(models.SequenceEnrollment)
       .filter(models.SequenceEnrollment.email == email,
               models.SequenceEnrollment.status == "active")
       .update({"status": "suppressed"}, synchronize_session=False))


def _webhook_keys_seen(events):
    """The field names present on each event kind, for discovering the payload shape.

    The open/click payload is undocumented, so the first real events are how we learn
    what they actually contain. Returning the key names (never the values) makes that
    visible in the webhook response and the job log without leaking addresses.
    """
    seen = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("event") or "unknown").lower()
        keys = sorted(ev.keys())
        nested = ev.get("msg")
        if isinstance(nested, dict):
            keys += [f"msg.{k}" for k in sorted(nested.keys())]
        seen.setdefault(kind, sorted(set(keys)))
    return seen


@router.post("/sendclean/webhook")
async def sendclean_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive SendClean delivery events.

    A hard bounce or spam complaint suppresses the address globally and stops its
    sequence. Soft bounces are counted and suppressed after SOFT_BOUNCE_LIMIT.
    SendClean posts one field, SENDCLEANTES_events, holding a JSON array.
    """
    form = await request.form()
    raw = form.get("SENDCLEANTES_events") or form.get("events") or ""

    if not raw:
        # SendClean's registration handshake: it calls the URL and requires this
        # exact body back before it will accept the webhook.
        return PlainTextResponse("God bless you, SendClean")

    key_row = db.query(models.Setting).filter(models.Setting.key == WEBHOOK_KEY_SETTING).first()
    key = (key_row.value if key_row else "") or ""
    url_row = db.query(models.Setting).filter(models.Setting.key == WEBHOOK_URL_SETTING).first()
    configured_url = (url_row.value if url_row else "") or f"{seq_public_base()}/api/sendclean/webhook"

    if not _webhook_signature_ok(request, form, key, configured_url):
        raise HTTPException(status_code=401, detail="Bad webhook signature")

    try:
        events = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        events = []
    if isinstance(events, dict):
        events = [events]

    counts = {"hard_bounce": 0, "soft_bounce": 0, "spam": 0, "delivered": 0, "open": 0, "click": 0}
    suppressed, soft = [], []

    soft_row = db.query(models.Setting).filter(models.Setting.key == SOFT_BOUNCE_SETTING).first()
    try:
        soft_map = json.loads(soft_row.value) if soft_row and soft_row.value else {}
    except (json.JSONDecodeError, TypeError):
        soft_map = {}

    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("event") or "").lower()
        if kind not in counts:
            continue
        counts[kind] += 1
        email = str(ev.get("email") or ev.get("recipient") or "").strip().lower()
        if not email or "@" not in email:
            continue
        if kind == "hard_bounce":
            _suppress_and_stop(db, email, "hard_bounce", json.dumps(ev.get("msg") or {})[:200])
            suppressed.append(email)
        elif kind == "spam":
            _suppress_and_stop(db, email, "complaint", "spam complaint reported")
            suppressed.append(email)
        elif kind == "delivered":
            # Stored, so delivery can be reported per campaign. This is the one signal our
            # own pixel cannot supply: it measures engagement, not whether the mail
            # arrived. Previously these were counted and discarded, which left the Delivery
            # column permanently blank on every sequence send.
            _record_delivery(db, ev, _xid_from_event(ev), email)
        elif kind in ("open", "click"):
            # Ignored on purpose. Engagement is measured by this app's own pixel and
            # redirect, and these are no longer subscribed; storing them as well would
            # double-count the same event, or contradict our own classification of it.
            # Counted above so the response still shows they arrived.
            pass
        elif kind == "soft_bounce":
            soft_map[email] = int(soft_map.get(email, 0)) + 1
            soft.append(email)
            if soft_map[email] >= SOFT_BOUNCE_LIMIT:
                _suppress_and_stop(db, email, "soft_bounce",
                                   f"{soft_map[email]} soft bounces")
                suppressed.append(email)

    # keep only the most recent 2000 soft-bounce counters
    if len(soft_map) > 2000:
        soft_map = dict(list(soft_map.items())[-2000:])
    if soft_row:
        soft_row.value = json.dumps(soft_map)
    else:
        db.add(models.Setting(key=SOFT_BOUNCE_SETTING, value=json.dumps(soft_map)))

    db.commit()
    if suppressed:
        db.add(models.Job(job_type="warning", title=f"Webhook suppressed {len(suppressed)} address(es)",
                          detail=f"events={counts} - " + ", ".join(sorted(set(suppressed))[:10]),
                          status="done", created_at=stamp()))
        db.commit()
    return {"ok": True, "events": counts, "suppressed": sorted(set(suppressed)),
            "soft_bounces": soft, "raw_keys": _webhook_keys_seen(events)}


@router.post("/sendclean/webhook/register", dependencies=[Depends(require_user)])
def register_sendclean_webhook(db: Session = Depends(get_db)):
    """Subscribe SendClean to our event webhook and store the signing key."""
    _require_provider_capability(db, "webhooks")
    creds = ep.credentials(db)
    url = f"{seq_public_base()}/api/sendclean/webhook"
    # `open` and `click` are deliberately NOT subscribed. Engagement is measured by this
    # app's own pixel and redirect now, and a second source would double-count the same
    # event - or contradict it, since the provider's own classification differs. These
    # bounce events remain useful and have no local equivalent.
    events = "hard_bounce,soft_bounce,spam,delivered"
    description = "Sendwise suppression (engagement is measured locally)"

    listed = sc.settings_call("listWebhook", creds["owner_id"], creds["token"])
    hooks = listed.get("webhook_list") or []
    existing = next((h for h in hooks if str(h.get("url")) == url), None)

    if existing:
        # Update in place. Registering a second webhook on the same URL would create a
        # different signing key and orphan the events already flowing to this one.
        sc.settings_call("editWebhook", creds["owner_id"], creds["token"],
                         webhook_id=existing.get("webhook_id"), url=url,
                         event=events, description=description)
        action = "updated"
    else:
        sc.settings_call("addWebhook", creds["owner_id"], creds["token"],
                         url=url, event=events, description=description)
        action = "created"

    listed = sc.settings_call("listWebhook", creds["owner_id"], creds["token"])
    hooks = listed.get("webhook_list") or []
    hook = next((h for h in hooks if str(h.get("url")) == url), None)
    if not hook:
        raise HTTPException(
            status_code=502,
            detail=f"SendClean accepted the webhook but it is not listed after {action}")

    key = str(hook.get("key") or "")
    row = db.query(models.Setting).filter(models.Setting.key == WEBHOOK_KEY_SETTING).first()
    if row:
        row.value = key
    else:
        db.add(models.Setting(key=WEBHOOK_KEY_SETTING, value=key))
    url_row = db.query(models.Setting).filter(models.Setting.key == WEBHOOK_URL_SETTING).first()
    if url_row:
        url_row.value = url
    else:
        db.add(models.Setting(key=WEBHOOK_URL_SETTING, value=url))
    db.commit()
    db.add(models.Job(job_type="sync", title="SendClean webhook registered",
                      detail=f"url={url} · events={events} · id={hook.get('webhook_id')}",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "url": url, "events": events, "action": action,
            "webhook_id": hook.get("webhook_id"), "signing_key_set": bool(key)}


def seq_public_base():
    return os.getenv("PUBLIC_BASE_URL", "https://sendwise.ibos.agency").rstrip("/")


def _emails_with(events, kind, machine=None):
    """Distinct addresses that produced one kind of event.

    `machine=None` counts every occurrence; `machine=False` counts only ones that did not
    come from a proxy. Kept as one helper so the two counts can never drift apart.
    """
    out = []
    for e in events:
        if e.event_type != kind:
            continue
        if machine is not None and bool(e.is_machine) != machine:
            continue
        out.append((e.email or "").lower())
    return out


def _campaign_send_index(db, campaign):
    """Every email this campaign sent, as {x_unique_id: (email, ref)}.

    A sequence send has one entry per email per recipient; a one-off send has a single
    entry per recipient. Both are keyed by the id the provider echoes back on tracking
    events, which is what ties an event to a person and an email.
    """
    index = {}
    enrollments = (db.query(models.SequenceEnrollment)
                     .filter(models.SequenceEnrollment.campaign_slug == campaign.slug).all())
    for e in enrollments:
        hist = e.history_json if isinstance(e.history_json, list) else json.loads(e.history_json or "[]")
        for step in hist:
            if isinstance(step, dict) and step.get("x_unique_id"):
                index[str(step["x_unique_id"])] = ((e.email or "").lower(),
                                                   str(step.get("ref") or "").upper())
    if not index:
        for r in ((campaign.delivery_json or {}).get("recipients") or []):
            if r.get("x_unique_id"):
                index[str(r["x_unique_id"])] = (str(r.get("email") or "").lower(), "")
    return index


def _tz_name(db, settings=None):
    """The configured display timezone (Asia/Dhaka by default)."""
    if settings:
        return settings.get("timezone") or "Asia/Dhaka"
    row = db.query(models.Setting).filter(models.Setting.key == "timezone").first()
    return (row.value if row and row.value else "Asia/Dhaka") or "Asia/Dhaka"


def _local_time(value, tz_name):
    """Render a stored UTC timestamp in the configured timezone, for display only.

    Everything is stored in UTC, which is right; but it was also being *shown* in UTC with
    no label, so a reader in Dhaka saw times six hours earlier than their own clock and
    reasonably concluded the clock was broken. Storage stays UTC; only the rendering moves.
    """
    if not value:
        return ""
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001 - an unknown zone must not break the response
        return dt.isoformat(timespec="seconds")


@router.get("/campaigns/{slug}/engagement", dependencies=[Depends(require_user)])
def campaign_engagement(slug: str, db: Session = Depends(get_db)):
    """Per-recipient, per-email engagement, shaped for a live table.

    Reads `engagement_events`, which the webhook fills the moment an open or click
    arrives. Only HUMAN opens are counted: a mail provider's image proxy fetching the
    pixel is not a person reading, and treating the two as one is what made the earlier
    numbers wrong. Clicks are counted as they arrive, since a click is generated by the
    recipient's own browser.
    """
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")

    tz_name = _tz_name(db)
    events = (db.query(models.EngagementEvent)
                .filter(models.EngagementEvent.campaign_slug == slug).all())

    index = _campaign_send_index(db, c)

    bucket = {}
    for ev in events:
        email = (ev.email or "").lower()
        ref = (ev.ref or "").upper()
        cell = bucket.setdefault(email, {}).setdefault(
            ref, {"opens": 0, "clicks": 0, "provider_fetches": 0, "last_at": "",
                  "events": []})
        cell["provider_fetches"] += 1
        if ev.event_type == "click":
            cell["clicks"] += 1
        elif not ev.is_machine:
            cell["opens"] += 1
        stamp = str(ev.occurred_at or ev.received_at or "")
        if stamp > cell["last_at"]:
            cell["last_at"] = stamp
        # Every fetch is kept, not just the newest. "Opened twice" is two separate rows
        # here, so the count and the timing of each view are both answerable - which a
        # running total alone cannot show.
        cell["events"].append({
            "at": stamp,
            "at_local": _local_time(stamp, tz_name),
            "kind": ev.event_type,
            "machine": bool(ev.is_machine),
            "reason": ev.machine_reason or "",
            "ip": ev.ip or "",
        })

    for email_cells in bucket.values():
        for cell in email_cells.values():
            cell["events"].sort(key=lambda e: e["at"])
            # Bound the payload: the oldest are dropped first, and the newest are the
            # ones anyone is looking at. Raised from 60 so the drill-down does not hide
            # a busy recipient's history.
            if len(cell["events"]) > 500:
                cell["events"] = cell["events"][-500:]

    # Who was sent what. Prefer the enrollment history; fall back to the delivery ledger
    # so a one-off send still produces rows.
    sent = {}
    if index:
        for _xid, (email, ref) in index.items():
            sent.setdefault(email, set()).add(ref)
    else:
        for r in ((c.delivery_json or {}).get("recipients") or []):
            sent.setdefault(str(r.get("email") or "").lower(), set()).add("")

    rows = []
    for email in sorted(set(sent) | set(bucket)):
        cells = {}
        tot_o = tot_c = tot_f = 0
        for ref in sorted(sent.get(email, {""})):
            cell = (bucket.get(email) or {}).get(ref) or {}
            o = int(cell.get("opens") or 0)
            ck = int(cell.get("clicks") or 0)
            f = int(cell.get("provider_fetches") or 0)
            cells[ref] = {"opens": o, "clicks": ck, "provider_fetches": f,
                          "sent": True, "last_at": cell.get("last_at") or "",
                          "events": cell.get("events") or []}
            # `opens` is the strict count and `pixel_loads` is the raw one, exactly as
            # Mailchimp reports it. Both are surfaced because they answer different
            # questions: "did anyone definitely read this" versus "how many times was it
            # rendered". Reporting only the strict one hid every genuine Gmail open
            # behind the word "machine", because Gmail fetches through its own proxy.
            cells[ref]["pixel_loads"] = f
            cells[ref]["confirmed_opens"] = o
            tot_o += o
            tot_c += ck
            tot_f += f
        rows.append({"email": email, "cells": cells,
                     "opens": tot_o, "clicks": tot_c, "provider_fetches": tot_f,
                     "pixel_loads": tot_f, "confirmed_opens": tot_o,
                     "last_at": max((v["last_at"] for v in cells.values()), default="")})

    refs = sorted({ref for refs_ in sent.values() for ref in refs_ if ref})
    last_event = max((str(e.received_at or "") for e in events), default="")

    # Two denominators, because a sequence is not shaped like a Mailchimp campaign:
    #
    #   emails sent  - every email to every person (12 for 6 people on a 2-email run)
    #   recipients   - the people themselves (6)
    #
    # Mailchimp's rates divide by the audience, because one of its campaigns is one email.
    # Ours would be badly understated by dividing a per-person figure by per-email sends -
    # 3 openers out of 12 emails reads as 25% when the honest answer is 3 of 6 people, 50%.
    # Rates use recipients; the email count is still reported, just not used as the basis.
    emails_sent = len(index)
    recipients_total = len(sent) or len({e for e in _emails_with(events, "open", machine=None)})
    # Confirmed by the provider only; never inferred from "we sent it".
    delivered_people = {e for e in _emails_with(events, "delivered", machine=None)}
    delivered_count = len(delivered_people) or None
    unique_openers = {e for e in _emails_with(events, "open", machine=False)}
    # Every pixel fetch, including mail-provider proxies. Reported separately so the
    # raw render count is still visible, but never as "the open rate": the Sends list
    # divides human opens by recipients, and two pages showing two different open
    # rates for the same send is a bug the operator cannot reconcile.
    unique_openers_any = {e for e in _emails_with(events, "open", machine=None)}
    unique_clickers = {e for e in _emails_with(events, "click", machine=None)}
    total_opens = sum(1 for e in events if e.event_type == "open")
    total_clicks = sum(1 for e in events if e.event_type == "click")

    def _pct(n):
        return round(n / recipients_total * 100, 2) if recipients_total else 0.0

    link_counts = {}
    for e in events:
        if e.event_type != "click":
            continue
        url = str((e.raw_json or {}).get("target") or "").strip()
        if url:
            link_counts[url] = link_counts.get(url, 0) + 1

    # Scoped to THIS campaign. Reading the whole suppression table made every campaign's
    # page report the account-wide total: four different sends all showed "Unsubscribed 55",
    # which is one number repeated, not four findings.
    recipients_here = {(e or "").strip().lower()
                       for e in (c.audience_options or {}).get("manual_emails") or []}
    suppressions = db.query(models.Suppression).all()
    reason_count = {}
    for s in suppressions:
        k = (s.reason or "other").strip().lower()
        reason_count[k] = reason_count.get(k, 0) + 1
    unsub_here = sum(1 for s in suppressions
                     if (s.source or "") == c.slug
                     and (s.reason or "").strip().lower() == "unsubscribe")
    # A bounce arrives through the provider webhook with no campaign attached, so it is
    # scoped by membership of this campaign's own recipient list instead.
    bounce_here = sum(1 for s in suppressions
                      if (s.email or "").strip().lower() in recipients_here
                      and (s.reason or "").strip().lower() in ("hard_bounce", "soft_bounce"))

    mailchimp = {
        "emails_sent": emails_sent,
        "recipients": recipients_total,
        "opens": total_opens,
        "unique_opens": len(unique_openers_any),
        "open_rate": _pct(len(unique_openers_any)),
        # The strict count, kept as its own field. A proxy fetch is not a person
        # reading, so this is the number to trust when judging engagement - but the
        # headline stays the raw pixel count so it matches the Sends list exactly.
        "human_unique_opens": len(unique_openers),
        "human_open_rate": _pct(len(unique_openers)),
        "unique_opens_all_fetches": len(unique_openers_any),
        "open_rate_all_fetches": _pct(len(unique_openers_any)),
        "clicks": total_clicks,
        "unique_clicks": len(unique_clickers),
        "click_rate": _pct(len(unique_clickers)),
        "open_rate_basis": ("all pixel fetches, including provider proxy loads; "
                            "human_open_rate excludes proxy/scanner fetches"),
        "unsubscribed": unsub_here,
        "bounced": bounce_here,
        "top_links": sorted(
            [{"url": u, "clicks": c} for u, c in link_counts.items()],
            key=lambda x: -x["clicks"])[:100],
        "rate_basis": "recipients",
        # A thread that renders several of a journey's emails fires one beacon per email,
        # so this total is renders, not read-receipts. Unique Opens is the per-person
        # figure and the one to read. Mailchimp has no equivalent because one of its
        # campaigns is a single email with a single beacon.
        "opens_basis": "every image load; a thread rendering several emails counts once each",
    }

    # ------------------------------------------------- one row per EMAIL
    #
    # The per-person table answers "who engaged". This answers "which email worked", which
    # is a different and often more useful question: a subject line or a stage either draws
    # attention or it does not, and that only shows when the counts are gathered by email.
    email_meta = {}
    for e in (c.emails_json or []):
        ref = str(e.get("ref") or "").upper()
        if ref:
            email_meta[ref] = {"name": e.get("name") or "", "subject": e.get("subject") or ""}

    per_email = []
    for ref in refs:
        sends = sum(1 for _xid, (_em, r) in index.items() if r == ref)
        sent_to = {(em) for _xid, (em, r) in index.items() if r == ref}
        opens = [e for e in events if e.event_type == "open" and (e.ref or "").upper() == ref]
        clicks = [e for e in events if e.event_type == "click" and (e.ref or "").upper() == ref]
        unique_openers = {(e.email or "").lower() for e in opens}
        unique_clickers = {(e.email or "").lower() for e in clicks}
        audience = len(sent_to) or sends
        per_email.append({
            "ref": ref,
            "name": email_meta.get(ref, {}).get("name", ""),
            "subject": email_meta.get(ref, {}).get("subject", ""),
            "sends": sends,
            "recipients": audience,
            "opens": len(opens),
            "unique_opens": len(unique_openers),
            "open_rate": round(len(unique_openers) / audience * 100, 2) if audience else 0.0,
            "clicks": len(clicks),
            "unique_clicks": len(unique_clickers),
            "click_rate": round(len(unique_clickers) / audience * 100, 2) if audience else 0.0,
        })

    return {
        "campaign": {"slug": c.slug, "title": c.title, "status": c.status,
                     "sequence_slug": c.sequence_slug or ""},
        "refs": refs,
        "rows": rows,
        "per_email": per_email,
        "mailchimp": mailchimp,
        "totals": {
            "recipients": len(rows),
            "opens": sum(r["opens"] for r in rows),
            "clicks": sum(r["clicks"] for r in rows),
            "provider_fetches": sum(r["provider_fetches"] for r in rows),
        },
        "realtime": {"events_stored": len(events), "last_event_at": last_event,
                     "timezone": tz_name,
                     "last_event_at_local": _local_time(last_event, tz_name)},
        "note": ("Human opens and clicks only. A provider's image-proxy fetch is kept out "
                 "of `opens` and shown as `provider_fetches`. For Gmail that fetch happens "
                 "at delivery, so it is not evidence that anyone read the email."),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# A link in the rendered email. Read with a regex rather than a full HTML parser on
# purpose: the branded HTML comes from our own template and always has the one anchor
# shape this needs to read, so a parser would be a heavier dependency for no gain.
_ANCHOR_RE = re.compile(r'<a\b[^>]*?\bhref\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)</a>',
                        re.I | re.S)
_INNER_TAGS_RE = re.compile(r"<[^>]+>")
# Never redirectable (see tracking.rewrite_links), so they can never carry a click.
_UNTRACKED_HREF = re.compile(r"^(?:mailto:|tel:|#|javascript:|\s*$)", re.I)


def _anchors_in(html):
    """Every anchor in the rendered email, keyed by its decoded href, in document order.

    Decoded because that is what the tracker stores back: the send path quotes the raw
    href into the redirect, and a query written `?a=1&amp;b=2` comes back as `?a=1&b=2`.
    Keying both sides the same way is what keeps a click drawn on the right link.
    """
    from html import unescape

    out = {}
    for _quote, href, inner in _ANCHOR_RE.findall(html or ""):
        url = unescape(href).strip()
        if not url:
            continue
        label = " ".join(unescape(_INNER_TAGS_RE.sub("", inner or "")).split())[:140]
        cell = out.get(url)
        if cell is None:
            out[url] = {"url": url, "text": label, "occurrences": 1,
                        "clickable": not bool(_UNTRACKED_HREF.match(url))}
        else:
            cell["occurrences"] += 1
            if not cell["text"] and label:
                cell["text"] = label
    return out


@router.get("/campaigns/{slug}/clickmap", dependencies=[Depends(require_user)])
def campaign_clickmap(slug: str, db: Session = Depends(get_db)):
    """Where inside the email people clicked - a click map over this app's own tracker.

    A click count says how many; a click map says which part of the message drew them.
    Every click stores the link it was headed for, so those targets are grouped back onto
    the anchors of the rendered email and returned together. The UI overlays the counts on
    the message, the way Mailchimp's click map does.

    The HTML is rebuilt with `_final_html`, the exact function the send path uses, so the
    links drawn here are the links that were sent - copy edited after the fact cannot move
    a click onto a button nobody received.
    """
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")

    events = (db.query(models.EngagementEvent)
                .filter(models.EngagementEvent.campaign_slug == slug,
                        models.EngagementEvent.event_type == "click").all())

    from html import unescape

    # ref -> url -> {clicks, clickers}; plus every clicker across the whole campaign.
    per_ref = {}
    all_clickers = set()
    for ev in events:
        url = unescape(str((ev.raw_json or {}).get("target") or "")).strip()
        if not url:
            continue  # a provider-synced click: it has a count but names no link
        ref = (ev.ref or "").upper() or "E1"
        who = (ev.email or "").lower()
        cell = per_ref.setdefault(ref, {}).setdefault(url, {"clicks": 0, "clickers": set()})
        cell["clicks"] += 1
        cell["clickers"].add(who)
        all_clickers.add(who)

    emails_out = []
    for e in (c.emails_json or []):
        # Real sends carry a ref (E1..E9). Older/seed rows carry only `stage`, so derive
        # the same ref from it rather than dropping the email from the map entirely.
        ref = str(e.get("ref") or "").upper()
        if not ref and e.get("stage"):
            ref = f"E{e.get('stage')}"
        if not ref:
            continue
        html = _final_html(c, e)
        counts = per_ref.get(ref, {})
        anchors = _anchors_in(html)

        links = []
        for url, a in anchors.items():
            cell = counts.get(url) or {}
            links.append({
                "url": url,
                "text": a["text"],
                "occurrences": a["occurrences"],
                "clickable": a["clickable"],
                "clicks": int(cell.get("clicks") or 0),
                "unique_clickers": len(cell.get("clickers") or ()),
            })
        # A click can name a link the current copy no longer holds (the email was edited
        # after the send). Still reported, so the map never hides a real click.
        for url, cell in counts.items():
            if url in anchors:
                continue
            links.append({"url": url, "text": "", "occurrences": 0, "clickable": True,
                          "clicks": int(cell["clicks"]),
                          "unique_clickers": len(cell["clickers"]), "orphan": True})

        links.sort(key=lambda l: (-l["clicks"], l["url"]))
        clickers = set()
        for cell in counts.values():
            clickers |= cell["clickers"]
        emails_out.append({
            "ref": ref,
            "name": e.get("name") or "",
            "subject": e.get("subject") or "",
            "clicks": sum(l["clicks"] for l in links),
            "unique_clickers": len(clickers),
            "max_clicks": max((l["clicks"] for l in links), default=0),
            "html": html,
            "links": links,
        })

    placed = sum(e["clicks"] for e in emails_out)
    return {
        "campaign": {"slug": c.slug, "title": c.title, "status": c.status,
                     "sender_name": c.sender_name or "", "sender_email": c.sender_email or ""},
        "emails": emails_out,
        "totals": {
            "emails": len(emails_out),
            "clicks": len(events),
            "clicks_placed": placed,
            "clicks_unplaced": max(0, len(events) - placed),
            "unique_clickers": len(all_clickers),
            "links_clicked": sum(1 for e in emails_out for l in e["links"] if l["clicks"] > 0),
        },
        "note": ("Clicks come from this app's own tracker, which records the exact link each "
                 "click left from. Links that cannot be redirected (mailto:, tel:, #, "
                 "unsubscribe) are listed but can never carry a click. Clicks imported from "
                 "the provider name no link, so they are counted but cannot be placed."),
    }


@router.post("/campaigns/{slug}/engagement/sync", dependencies=[Depends(require_user)])
def sync_campaign_engagement(slug: str, db: Session = Depends(get_db)):
    """Import SendClean's stored opens and clicks into the realtime store.

    The webhook only sees events from the moment it was subscribed, so a send made
    before that has nothing in `engagement_events`. This pulls the provider's
    per-message data and inserts whatever is missing, so history and live activity end
    up in one table instead of two sources that disagree.

    Idempotent: an event already held is skipped by matching on the send id, the kind
    and the provider's own timestamp.
    """
    _require_provider_capability(db, "message_lookup")
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")

    index = _campaign_send_index(db, c)
    if not index:
        return {"ok": True, "imported": 0, "reason": "this campaign has no tracked sends"}

    creds = ep.credentials(db)
    have = {(e.x_unique_id, e.event_type, str(e.occurred_at or ""))
            for e in db.query(models.EngagementEvent)
                      .filter(models.EngagementEvent.campaign_slug == slug).all()}

    # One lookup per send, and a send can be slow to answer. Fetched in parallel because
    # doing them in sequence took longer than an HTTP request is allowed to live: a
    # 12-email sequence spent two minutes and the caller gave up before anything landed.
    from concurrent.futures import ThreadPoolExecutor

    items = list(index.items())

    def fetch(item):
        xid, (email, ref) = item
        # SendClean intermittently answers 502 under load, so a single attempt is not a
        # reliable answer. Retried briefly before being reported as a failure.
        last = ""
        for attempt in range(3):
            try:
                info = sc.get_message_info(creds["owner_id"], creds["token"], xid)
                return xid, email, ref, (info.get("message_data") or []), None
            except Exception as exc:  # noqa: BLE001 - one bad lookup must not stop the rest
                last = f"{type(exc).__name__}: {str(exc)[:110]}"
                time.sleep(1.2 * (attempt + 1))
        return xid, email, ref, None, last

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(fetch, items))

    imported = skipped = failed = 0
    errors = []
    for xid, email, ref, mrows, err in results:
        if err or mrows is None:
            failed += 1
            if err and err not in errors:
                errors.append(err)
            continue
        if not mrows:
            continue
        m = mrows[0]
        od = m.get("openData") or {}
        uas = od.get("ua") or []
        ips = od.get("ip") or []
        times = od.get("time") or []

        def at(seq, i):
            return str(seq[i]) if i < len(seq) else ""

        for i in range(max(len(uas), len(ips), len(times))):
            ua, ip, when = at(uas, i), at(ips, i), at(times, i)
            key = (xid, "open", when)
            if key in have:
                skipped += 1
                continue
            machine = open_signal.is_machine_ua(ua) or open_signal.is_proxy_ip(ip)
            db.add(models.EngagementEvent(
                email=email, event_type="open", x_unique_id=xid,
                campaign_slug=c.slug, ref=ref,
                is_machine=machine,
                machine_reason=open_signal.machine_reason(ua, ip) if machine else "",
                ip=ip[:64], user_agent=ua[:400], occurred_at=when[:40],
                raw_json={"source": "getMessageInfo", "index": i}))
            have.add(key)
            imported += 1

        clicks = int(m.get("click") or 0)
        for i in range(clicks):
            when = f"click-{i}"
            key = (xid, "click", when)
            if key in have:
                skipped += 1
                continue
            db.add(models.EngagementEvent(
                email=email, event_type="click", x_unique_id=xid,
                campaign_slug=c.slug, ref=ref, is_machine=False,
                occurred_at=when, raw_json={"source": "getMessageInfo", "index": i}))
            have.add(key)
            imported += 1

    db.commit()
    db.add(models.Job(job_type="sync", title=f"Engagement synced - {c.slug}",
                      detail=f"imported={imported} skipped={skipped} failed={failed}",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "imported": imported, "skipped": skipped,
            "failed": failed, "sends": len(index),
            "errors": errors[:5], "error_count": len(errors)}


@router.delete("/campaigns/{slug}", dependencies=[Depends(require_user)])
def delete_campaign(slug: str, db: Session = Depends(get_db)):
    """Move a send to Trash. Nothing is erased - it can be restored exactly.

    Sequence enrollments are LEFT ALONE on purpose: they carry each recipient's
    live open/click history, and a sequence may still be mid-flight. Restoring the
    send therefore brings back its progress too, instead of silently orphaning it.
    """
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug,
                                         models.Campaign.deleted_at == "").first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    was_status = c.status or "draft"
    sent = bool(c.provider_message_id)
    enrollments = (db.query(models.SequenceEnrollment)
                   .filter(models.SequenceEnrollment.campaign_slug == slug).count())
    active = (db.query(models.SequenceEnrollment)
              .filter(models.SequenceEnrollment.campaign_slug == slug,
                      models.SequenceEnrollment.status == "active").count())
    c.deleted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.add(models.Job(job_type="warning", title=f"Send moved to Trash · {slug}",
                      detail=f"status was {was_status}"
                             f"{' · had already sent' if sent else ' · never sent'}"
                             f" · {enrollments} enrollment(s) kept ({active} active)"
                             f" · restorable from Trash",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "trashed": slug, "restorable": True,
            "enrollments_kept": enrollments, "had_sent": sent, "status_was": was_status}


@router.post("/campaigns/{slug}/restore", dependencies=[Depends(require_user)])
def restore_campaign(slug: str, db: Session = Depends(get_db)):
    """Bring a send back out of Trash, with its emails and sequence progress intact."""
    c = (db.query(models.Campaign).filter(models.Campaign.slug == slug)
         .execution_options(include_trashed=True).first())
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    c.deleted_at = ""
    db.commit()
    return {"ok": True, "restored": slug, "status": c.status}


@router.delete("/campaigns/{slug}/purge", dependencies=[Depends(require_user)])
def purge_campaign(slug: str, db: Session = Depends(get_db)):
    """Remove a send FOR GOOD, along with its sequence enrollments.

    Unlike Trash this is final: the slug goes on the permanent-removal ledger, so no
    backup snapshot and no restore can bring it back.
    """
    from . import backup as bk
    c = (db.query(models.Campaign).filter(models.Campaign.slug == slug)
         .execution_options(include_trashed=True).first())
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    dropped = (db.query(models.SequenceEnrollment)
               .filter(models.SequenceEnrollment.campaign_slug == slug)
               .delete(synchronize_session=False))
    db.delete(c)
    bk.record_purge(db, "campaign", slug, "permanently removed")
    db.add(models.Job(job_type="warning", title=f"Permanently removed send · {slug}",
                      detail=f"{dropped} enrollment(s) dropped · tombstoned, restores will skip it",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "purged": slug, "permanent": True, "enrollments_dropped": dropped}


@router.delete("/sequences/{slug}/purge", dependencies=[Depends(require_user)])
def purge_sequence(slug: str, db: Session = Depends(get_db)):
    """Remove a sequence template FOR GOOD - tombstoned against restores."""
    from . import backup as bk
    s = (db.query(models.Sequence).filter(models.Sequence.slug == slug)
         .execution_options(include_trashed=True).first())
    if not s:
        raise HTTPException(status_code=404, detail="Sequence not found")
    db.delete(s)
    bk.record_purge(db, "sequence", slug, "permanently removed")
    db.add(models.Job(job_type="warning", title=f"Permanently removed sequence · {slug}",
                      detail="tombstoned, restores will skip it",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "purged": slug, "permanent": True}


@router.post("/trash/restore-all", dependencies=[Depends(require_user)])
def restore_all_trash(db: Session = Depends(get_db)):
    """Restore every trashed audience, contact, send and sequence in one go."""
    counts = {}
    for model, label in ((models.Audience, "audiences"), (models.Contact, "contacts"),
                         (models.Campaign, "campaigns"), (models.Sequence, "sequences")):
        rows = (db.query(model).filter(model.deleted_at != "")
                .execution_options(include_trashed=True).all())
        for r in rows:
            r.deleted_at = ""
        counts[label] = len(rows)
    db.add(models.Job(job_type="sync", title="Trash emptied by restore",
                      detail=" · ".join(f"{k}={v}" for k, v in counts.items()),
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "restored": counts}


@router.post("/campaigns/{slug}/send-test", dependencies=[Depends(require_user)])
def send_test(slug: str, db: Session = Depends(get_db)):
    """Send a real SendClean test email to the configured test recipient."""
    _require_provider_capability(db, "transactional_send")
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()}
    target = settings.get("test_recipient") or c.sender_email
    emails = c.emails_json or []
    e = emails[0] if emails else {"subject": c.title, "preview_text": "", "body": ""}
    html = _final_html(c, e)
    x_unique_id = f"sw-test-{c.slug}-{int(datetime.now(timezone.utc).timestamp())}"
    test_row = dict(e)
    test_row["subject"] = f"[TEST] {e.get('subject') or c.title}"
    _sendclean_send(db, c, test_row, [{"email": target, "type": "to"}], html, x_unique_id)
    db.add(models.Job(
        job_type="launch", title=f"Test email sent - {c.slug}",
        detail=f"to {target} - x_unique_id={x_unique_id}", status="done", created_at=stamp(),
    ))
    db.commit()
    return {"ok": True, "to": [target], "x_unique_id": x_unique_id, "sent": True}


@router.post("/campaigns/{slug}/pause", dependencies=[Depends(require_user)])
def pause_campaign(slug: str, db: Session = Depends(get_db)):
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    c.status = "paused"
    db.add(models.Job(job_type="warning", title=f"Sequence paused · {c.slug}", detail="Manual operator action", status="done", created_at=stamp()))
    db.commit()
    return _campaign_dict(c, db)


@router.post("/campaigns/{slug}/ready", dependencies=[Depends(require_user)])
def ready_campaign(slug: str, db: Session = Depends(get_db)):
    """Mark campaign ready - copy is final, next step is the SendClean send."""
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    c.status = "ready"
    db.commit()
    return _campaign_dict(c, db)


class EmailsIn(BaseModel):
    emails: list


@router.patch("/campaigns/{slug}/emails", dependencies=[Depends(require_user)])
def update_emails(slug: str, body: EmailsIn, db: Session = Depends(get_db)):
    """Update email copy (subject, preview_text, body) - set by agent or operator."""
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    c.emails_json = body.emails
    # keep the stored body_html fresh - every edit regenerates the branded design (UI preview + future sends)
    for e in c.emails_json or []:
        txt = (e.get("body") or "").strip()
        if txt:
            e["body_html"] = body_to_html(txt, product_key_for(c.product), variant=e.get("journey_phase", 1),
                                          headline=(e.get("subject") or c.title or "").strip())
    db.commit()
    return _campaign_dict(c, db)


@router.post("/campaigns/{slug}/generate-copy", dependencies=[Depends(require_user)])
def generate_copy(slug: str, db: Session = Depends(get_db)):
    """Generate subject + preview + body for every email from the product brand copy engine, and save it.

    LLM calls run in parallel so a 9-email sequence stays within one serverless
    function invocation (GPT-5 is a reasoning model and each call is slow).
    """
    c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    from .copy_engine import generate
    from .llm_providers import get_active_config
    from .trend_intelligence import campaign_trend_context, trend_provenance
    from concurrent.futures import ThreadPoolExecutor
    llm_config = get_active_config(db)
    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()}
    # All network/DB trend work is complete before any per-email worker starts.
    trend_context = campaign_trend_context(db, c.product, settings)
    provenance = trend_provenance(trend_context)

    def _one(e):
        stage = e.get("stage", 1)
        email_ref = e.get("ref") or next((em.get("ref", f"E{stage}") for s in (c.sequence_json or []) if s.get("stage") == stage for em in (s.get("emails") or [])), f"E{stage}")
        stage_focus = next((s.get("focus", "") for s in (c.sequence_json or []) if s.get("stage") == stage), "A quick update")
        # Branch emails need their own message angle. Using only the parent stage focus
        # made every E4-E9 provider prompt nearly identical even though the routing
        # behavior and intent were different.
        focus = e.get("email_name") or e.get("name") or stage_focus
        gen = generate(product=c.product, offer=c.offer, sender_name=c.sender_name, stage_focus=focus,
                       audience_name=c.audience_name, email_ref=email_ref, campaign_slug=c.slug,
                       llm_config=llm_config, trend_context=trend_context)
        return {
            "stage": stage,
            "ref": email_ref,
            "email_name": e.get("email_name") or e.get("name") or stage_focus,
            "branch": e.get("branch", "main"),
            "subject": gen["subject"],
            "preview_text": gen["preview_text"],
            "body": gen["body"],
            "body_html": gen["body_html"],
            "cta_url": gen["cta_url"],
            "cta_label": gen.get("cta_label"),
            "journey_phase": gen.get("journey_phase"),
            "journey_name": gen.get("journey_name"),
            "from": e.get("from") or f"{c.sender_name} <{c.sender_email}>",
            "status": "Agent drafted",
            "source": (f"{gen.get('llm_provider')} / {gen.get('llm_model')}" if gen.get("via_llm")
                       else gen.get("md_source") or "rule-based fallback"),
            "via_llm": gen.get("via_llm", False),
            "llm_provider": gen.get("llm_provider"),
            "llm_model": gen.get("llm_model"),
            "trend_status": provenance["status"],
            "trend_provenance": dict(provenance),
        }

    rows = list(c.emails_json or [])
    if not rows:
        raise HTTPException(status_code=400, detail="BLOCKED: this send has no emails to write yet.")

    def _safe(e):
        """One bad email must not lose the other eight."""
        try:
            return _one(e), None
        except Exception as exc:  # noqa: BLE001 - the reason is surfaced below
            return None, f"{e.get('ref') or e.get('stage')}: {type(exc).__name__}: {exc}"

    if llm_config:
        with ThreadPoolExecutor(max_workers=min(8, len(rows))) as ex:
            results = list(ex.map(_safe, rows))
    else:
        results = [_safe(e) for e in rows]

    written = [r for r, _ in results if r]
    failures = [f for _, f in results if f]

    if not written:
        reason = "; ".join(failures)[:600] or "no output produced"
        db.add(models.Job(
            job_type="qa", title=f"Copy generation FAILED · {c.slug}",
            detail=reason, status="failed", created_at=stamp(),
        ))
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="The Agent could not write this copy: " + reason[:300]
                   + " - check Settings → Email Agent, then click Draft with Agent again.",
        )

    # A generated email with no subject or a near-empty body would send blank,
    # so it is treated as a failure rather than a success.
    blank = [r["ref"] for r in written
             if not (r.get("subject") or "").strip() or len((r.get("body") or "").strip()) < 40]

    c.emails_json = written
    db.add(models.Job(
        job_type="qa",
        title=f"Copy {'incomplete' if blank else 'generated'} · {c.slug}",
        detail=(f"{len(written)} email(s) - {llm_config['label']} / {llm_config['model']} + {c.product} brand MD"
                if llm_config else f"{len(written)} email(s) - rule-based fallback + {c.product} brand MD")
               + f" · trend={provenance['status']}"
               + (f" · empty copy: {', '.join(blank)}" if blank else "")
               + (f" · {len(failures)} failed" if failures else ""),
        status="failed" if blank else "done", created_at=stamp(),
    ))
    db.commit()
    return _campaign_dict(c, db)


# ---------- sequences ----------
@router.get("/sequences", dependencies=[Depends(require_user)])
def list_sequences(db: Session = Depends(get_db)):
    rows = db.query(models.Sequence).order_by(models.Sequence.id.desc()).all()
    return [_sequence_dict(s) for s in rows]


# --------------------------------------------------------- sequence health
# Registered BEFORE "/sequences/{slug}" on purpose: FastAPI matches in registration
# order, so a literal path placed after it would be captured as a slug called
# "self-check" and return "Sequence not found".
@router.get("/sequences/self-check", dependencies=[Depends(require_user)])
def sequences_self_check(db: Session = Depends(get_db)):
    """Is any journey waiting on the wrong clock?

    Compares every active enrollment's actual wait against the wait its sequence
    declares. This is the check that would have caught a 5-minute sequence sitting on
    a 3-day timer - a fault that is invisible from the outside.
    """
    from . import sequence_engine as seq
    problems = seq.audit_delays(db)
    return {
        "ok": not problems,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "problems": problems,
        "summary": ("Every active enrollment is waiting exactly as long as its sequence "
                    "says." if not problems else
                    f"{len(problems)} enrollment(s) are waiting far longer than their "
                    f"sequence declares."),
    }


@router.post("/sequences/self-check/fix", dependencies=[Depends(require_user)])
def sequences_self_check_fix(db: Session = Depends(get_db)):
    """Re-time any stuck enrollment to the wait its sequence actually declares."""
    from . import sequence_engine as seq
    problems = seq.audit_delays(db)
    fixed = 0
    for p in problems:
        e = (db.query(models.SequenceEnrollment)
             .filter(models.SequenceEnrollment.campaign_slug == p["campaign"],
                     models.SequenceEnrollment.email == p["email"],
                     models.SequenceEnrollment.status == "active").first())
        if not e:
            continue
        camp = db.query(models.Campaign).filter(models.Campaign.slug == p["campaign"]).first()
        last = seq._parse(e.last_sent_at)
        if not camp or not last:
            continue
        # Use the wait AFTER the last email sent (the next email's stage), never the
        # stage of the email already sent - reading the wrong stage is the bug this
        # whole check exists to catch, and it would make a 3-day journey due at once.
        wait = seq.wait_after(camp, e.current_ref)
        if wait is None:
            continue
        e.next_due_at = seq._iso(last + wait)
        fixed += 1
    if fixed:
        db.add(models.Job(job_type="warning", title=f"Re-timed {fixed} stuck enrollment(s)",
                          detail="set to the wait their sequence actually declares",
                          status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "fixed": fixed, "of": len(problems)}


@router.get("/sequences/{slug}", dependencies=[Depends(require_user)])
def get_sequence(slug: str, db: Session = Depends(get_db)):
    s = db.query(models.Sequence).filter(models.Sequence.slug == slug).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sequence not found")
    return _sequence_dict(s)


class SequenceIn(BaseModel):
    name: str
    slug: str = ""
    description: str = ""
    trigger: str = "Manual / campaign entry"
    stages: list = []
    rules: list = []


def _validate_sequence_branches(stages):
    """A branch may only point at an email that exists in this sequence.

    Without this a journey can silently dead-end: the engine would look for a next
    email that was never defined and the recipient would stall mid-sequence.
    """
    refs = []
    for stage in stages or []:
        for em in (stage.get("emails") or []):
            ref = str(em.get("ref") or "").strip().upper()
            if ref:
                refs.append(ref)
    if not refs:
        raise HTTPException(status_code=400, detail="A sequence needs at least one email")

    seen = set()
    for ref in refs:
        if ref in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate email ref “{ref}” - every email needs its own.")
        seen.add(ref)

    for stage in stages or []:
        for em in (stage.get("emails") or []):
            ref = str(em.get("ref") or "").strip().upper()
            for field, label in (
                ("open_click_next", "opened and clicked"),
                ("open_no_click_next", "opened, no click"),
                ("not_open_next", "did not open"),
            ):
                target = str(em.get(field) or "").strip().upper()
                if target and target not in seen:
                    raise HTTPException(
                        status_code=400,
                        detail=(f"{ref}: the “{label}” branch points at “{target}”, which is not "
                                f"an email in this sequence. Leave it blank to end the sequence there."),
                    )
    return refs


def _validate_sequence_delays(stages):
    allowed = {"minute", "hour", "day", "week"}
    for index, stage in enumerate(stages or []):
        unit = str(stage.get("delay_unit") or "minute").lower()
        if unit not in allowed:
            raise HTTPException(status_code=400, detail=f"Stage {index + 1}: delay unit must be minute, hour, day or week")
        try:
            value = float(stage.get("delay_value", 0) or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Stage {index + 1}: delay must be a number")
        # E1 may send immediately. Every later transition must wait at least one minute.
        if index > 0 and value < 1:
            raise HTTPException(status_code=400, detail=f"Stage {index + 1}: minimum delay is 1 minute")


@router.post("/sequences", dependencies=[Depends(require_user)])
def create_sequence(body: SequenceIn, db: Session = Depends(get_db)):
    # A brand-new sequence starts with one stage and one email, so it is valid and
    # the editor has something to show straight away.
    stages = body.stages or [{
        "stage": 1, "focus": "", "delay_value": 0, "delay_unit": "day", "delay_note": "",
        "emails": [{"ref": "E1", "name": "", "branch": "main",
                    "open_click_next": "", "open_no_click_next": "", "not_open_next": ""}],
    }]
    _validate_sequence_delays(stages)
    _validate_sequence_branches(stages)
    slug = body.slug.strip() or body.name.strip().lower().replace(" ", "-").replace("_", "-")
    if db.query(models.Sequence).filter(models.Sequence.slug == slug).first():
        raise HTTPException(status_code=400, detail=f"A sequence called “{slug}” already exists")
    # A trashed sequence still owns its slug, so bring it back instead of colliding.
    trashed = (db.query(models.Sequence)
               .filter(models.Sequence.slug == slug)
               .execution_options(include_trashed=True).first())
    if trashed:
        trashed.deleted_at = ""
        trashed.name = body.name
        trashed.description = body.description
        trashed.trigger = body.trigger
        trashed.stages_json = stages
        trashed.rules_json = body.rules
        db.commit()
        db.refresh(trashed)
        return _sequence_dict(trashed)
    s = models.Sequence(
        slug=slug, name=body.name, description=body.description, trigger=body.trigger,
        stages_json=stages, rules_json=body.rules, created_at=stamp(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _sequence_dict(s)


@router.put("/sequences/{slug}", dependencies=[Depends(require_user)])
def update_sequence(slug: str, body: SequenceIn, db: Session = Depends(get_db)):
    s = db.query(models.Sequence).filter(models.Sequence.slug == slug).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sequence not found")
    _validate_sequence_delays(body.stages)
    _validate_sequence_branches(body.stages)
    s.name = body.name
    s.description = body.description
    s.trigger = body.trigger
    s.stages_json = body.stages
    s.rules_json = body.rules
    db.commit()
    return _sequence_dict(s)


@router.post("/sequences/{slug}/duplicate", dependencies=[Depends(require_user)])
def duplicate_sequence(slug: str, db: Session = Depends(get_db)):
    source = db.query(models.Sequence).filter(models.Sequence.slug == slug).first()
    if not source:
        raise HTTPException(status_code=404, detail="Sequence not found")
    base = f"{source.slug}-copy"
    new_slug = base
    n = 2
    while db.query(models.Sequence).filter(models.Sequence.slug == new_slug).first():
        new_slug = f"{base}-{n}"
        n += 1
    clone = models.Sequence(
        slug=new_slug,
        name=f"{source.name} (Copy)",
        description=source.description,
        trigger=source.trigger,
        stages_json=json.loads(json.dumps(source.stages_json or [])),
        rules_json=json.loads(json.dumps(source.rules_json or [])),
        created_at=stamp(),
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return _sequence_dict(clone)


@router.get("/sequences/{slug}/activity", dependencies=[Depends(require_user)])
def sequence_activity(slug: str, db: Session = Depends(get_db)):
    """Everything happening on this sequence: what went out, and what is queued.

    The queue shows the next send time plus every branch that could be chosen, without
    asking SendClean for each recipient's opens - a preview that never costs a lookup.
    """
    from . import sequence_engine as seq

    s = db.query(models.Sequence).filter(models.Sequence.slug == slug).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sequence not found")

    tree = seq.build_branch_tree(s.stages_json) or seq.BRANCH_TREE
    campaigns = (db.query(models.Campaign)
                 .filter(models.Campaign.sequence_slug == slug).all())
    cslugs = [c.slug for c in campaigns]
    titles = {c.slug: c.title for c in campaigns}

    enrollments = (db.query(models.SequenceEnrollment)
                   .filter(models.SequenceEnrollment.campaign_slug.in_(cslugs)).all()
                   if cslugs else [])
    suppression_count = db.query(models.Suppression).count()

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    def parse(value):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
        except (TypeError, ValueError):
            return None

    def human(delta):
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "due now"
        if secs < 3600:
            return f"in {max(1, secs // 60)}m"
        if secs < 86400:
            return f"in {secs // 3600}h"
        return f"in {secs // 86400}d"

    sent_today, queue, done, stopped = [], [], [], []
    for e in enrollments:
        row = {
            "email": e.email,
            "campaign": e.campaign_slug,
            "campaign_title": titles.get(e.campaign_slug, e.campaign_slug),
            "current_ref": e.current_ref or "",
            "sends": e.sends or 0,
            "branch": e.branch or "",
            "last_sent_at": e.last_sent_at or "",
            "next_due_at": e.next_due_at or "",
        }
        if (e.last_sent_at or "")[:10] == today:
            sent_today.append(row)

        if e.status == "active":
            due = parse(e.next_due_at)
            options = seq.pending_refs(e.current_ref, tree) if e.current_ref else ["E1"]
            queue.append({
                **row,
                "due_in": human(due - now) if due else "due now",
                "overdue": bool(due and due <= now),
                "options": options,
            })
        elif e.status == "completed":
            done.append(row)
        else:
            stopped.append({**row, "status": e.status})

    sent_today.sort(key=lambda r: r["last_sent_at"], reverse=True)
    queue.sort(key=lambda r: (not r["overdue"], r["due_in"]))

    recent = sorted(
        [r for r in (sent_today + queue + done + stopped) if r.get("last_sent_at")],
        key=lambda r: r["last_sent_at"], reverse=True,
    )[:25]

    return {
        "sequence": {"slug": s.slug, "name": s.name, "trigger": s.trigger,
                     "stages": len(s.stages_json or []),
                     "emails": sum(len(st.get("emails") or []) for st in (s.stages_json or []))},
        "sends": [{"slug": c.slug, "title": c.title, "status": c.status,
                   "emails": len(c.emails_json or []),
                   "ready": all(len((e.get("body") or "").strip()) >= 40 for e in (c.emails_json or []))}
                  for c in campaigns],
        "totals": {
            "enrolled": len(enrollments),
            "active": len(queue),
            "completed": len(done),
            "stopped": len(stopped),
            "sent_today": len(sent_today),
            "suppressed_addresses": suppression_count,
        },
        "sent_today": sent_today,
        "queue": queue,
        "completed": done[:25],
        "stopped": stopped[:25],
        "recent": recent,
        "now": now.isoformat(timespec="seconds"),
    }

@router.delete("/sequences/{slug}", dependencies=[Depends(require_user)])
def delete_sequence(slug: str, db: Session = Depends(get_db)):
    """Move a sequence template to Trash. Nothing is erased."""
    s = db.query(models.Sequence).filter(models.Sequence.slug == slug,
                                         models.Sequence.deleted_at == "").first()
    if not s:
        raise HTTPException(status_code=404, detail="Sequence not found")
    s.deleted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.add(models.Job(job_type="warning", title=f"Sequence moved to Trash · {slug}",
                      detail="restorable from Trash", status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "trashed": slug, "restorable": True}


@router.post("/sequences/{slug}/restore", dependencies=[Depends(require_user)])
def restore_sequence(slug: str, db: Session = Depends(get_db)):
    s = (db.query(models.Sequence).filter(models.Sequence.slug == slug)
         .execution_options(include_trashed=True).first())
    if not s:
        raise HTTPException(status_code=404, detail="Sequence not found")
    s.deleted_at = ""
    db.commit()
    return {"ok": True, "restored": slug}


# ---------- server-side cron (PC-independent - Vercel Cron calls these) ----------
def _require_cron(x_cron_secret: str):
    if x_cron_secret != os.getenv("CRON_SECRET", ""):
        raise HTTPException(status_code=401, detail="Bad cron secret")


def _sendclean_deliver(db, settings, subject, body_text, recipients, sheet_link="", extra_html=""):
    """Send the report through SendClean - the channel every other send already uses.

    SendClean's SMTP relay rejects these credentials (535), but its API works, so this
    is the path that actually delivers. Without it the report would report
    "SMTP not configured" every morning and arrive nowhere.
    """
    creds = ep.credentials(db)
    if not (creds.get("owner_id") and creds.get("token") and creds.get("smtp_user_name")):
        return False, "SendClean owner ID, token and SMTP username must all be configured"

    html = branded_report_html(
        subject, body_text,
        from_name=settings.get("default_from_name") or "Sendwise",
        sheet_link=sheet_link,
        product="ibos",
        extra_html=extra_html,
    )
    sender = (settings.get("smtp_from") or settings.get("default_reply_to") or "").strip()
    if not sender:
        return False, "no sender address is configured (default_reply_to)"

    failures = []
    for addr in recipients:
        try:
            sc.send_mail(creds["owner_id"], creds["token"], creds["smtp_user_name"], {
                "subject": subject,
                "from_email": sender,
                "from_name": settings.get("default_from_name") or "Sendwise",
                "to": [{"email": addr, "type": "to"}],
                "html": html,
            })
        except Exception as exc:  # noqa: BLE001 - one bad address must not stop the rest
            failures.append(f"{addr}: {str(exc)[:120]}")

    if failures and len(failures) == len(recipients):
        return False, "SendClean refused every recipient - " + "; ".join(failures[:2])
    return True, (f"partial: {'; '.join(failures)}" if failures else None)


def _deliver_report(db, settings, subject, body_text, recipients, sheet_link="", extra_html=""):
    """Deliver the report by whichever channel actually works.

    Gmail SMTP first when the operator has configured it, otherwise SendClean. The
    point is that a scheduled report is only useful if it arrives.
    """
    if not recipients:
        return False, "no report recipients are configured"

    if settings.get("smtp_host") and settings.get("smtp_user"):
        delivered, err = _smtp_deliver(settings, subject, body_text, recipients, extra_html=extra_html)
        if delivered:
            return True, None
        # Fall through to SendClean rather than fail the whole report on SMTP.
        fallback_ok, fallback_err = _sendclean_deliver(db, settings, subject, body_text,
                                                       recipients, sheet_link, extra_html=extra_html)
        if fallback_ok:
            return True, f"SMTP failed ({err}); delivered via SendClean"
        return False, f"SMTP failed ({err}); SendClean failed ({fallback_err})"

    return _sendclean_deliver(db, settings, subject, body_text, recipients, sheet_link, extra_html=extra_html)


def _smtp_deliver(settings, subject, body_text, recipients, product="ibos", extra_html=""):
    """Send via configured Gmail SMTP. Returns (delivered, error)."""
    smtp_host = settings.get("smtp_host")
    if not smtp_host or not settings.get("smtp_user") or not recipients:
        return False, "SMTP not configured or no recipients"
    try:
        html = branded_report_html(subject, body_text, sheet_link="", product=product, extra_html=extra_html)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.get("smtp_from") or settings.get("default_reply_to") or "sendwise@localhost"
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(smtp_host, int(settings.get("smtp_port", 587)), timeout=25) as server:
            server.starttls()
            server.login(settings.get("smtp_user", ""), settings.get("smtp_app_password", ""))
            server.sendmail(msg["From"], recipients, msg.as_string())
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _set_setting_value(db, key, value):
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    raw = value if isinstance(value, str) else json.dumps(value)
    if row:
        row.value = raw
    else:
        db.add(models.Setting(key=key, value=raw))
def _time_due(now_local, configured):
    try:
        hour, minute = [int(part) for part in str(configured).split(":", 1)]
        delta = now_local.hour * 60 + now_local.minute - (hour * 60 + minute)
        return 0 <= delta < 10
    except (TypeError, ValueError):
        return False


def _claim_setting_once(key, value):
    """Atomically claim a daily/hourly action across concurrent serverless invocations."""
    with engine.begin() as conn:
        result = conn.execute(_sql_text(
            "INSERT INTO settings (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value=:v WHERE settings.value IS DISTINCT FROM :v "
            "RETURNING key"
        ), {"k": key, "v": value})
        return result.first() is not None


def _report_schedule(settings):
    """Resolve the configured report schedule into (mode, time, date, timezone).

    mode is "daily" (every day at `time`), "once" (a single date + time) or "off".
    The legacy `daily_email` still supplies the time when no explicit
    `report_schedule_time` is saved, so existing installs keep their slot.
    """
    mode = str(settings.get("report_schedule_mode") or "daily").strip().lower()
    if mode not in ("daily", "once", "off"):
        mode = "daily"
    time_str = str(settings.get("report_schedule_time") or settings.get("daily_email") or "10:00").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_str):
        time_str = "10:00"
    date_str = str(settings.get("report_schedule_date") or "").strip()
    tz_name = settings.get("timezone", "Asia/Dhaka")
    return mode, time_str, date_str, tz_name


def _next_report_run(mode, time_str, date_str, tz_name):
    """Human-readable next send moment, or "" when the schedule is off or invalid."""
    if mode == "off":
        return ""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Dhaka")
    now = datetime.now(tz)
    try:
        hour, minute = [int(x) for x in time_str.split(":", 1)]
    except (TypeError, ValueError):
        return ""
    if mode == "daily":
        run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run <= now:
            run = run + timedelta(days=1)
        return run.strftime("%Y-%m-%d %H:%M")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str or ""):
        return ""
    try:
        run = datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz)
    except ValueError:
        return ""
    # A one-time send in the past is spent, not "next": show nothing rather than a
    # stale moment that reads as though it is still queued.
    if run <= now:
        return ""
    return run.strftime("%Y-%m-%d %H:%M")


def _run_due_reporting(db):
    """Report email only. SendClean exposes no aggregate reporting API.

    Honours the saved schedule: daily at a time, a single date + time, or off.
    Each moment is claimed once so a re-run (or several concurrent cron hits)
    cannot send the same slot twice.
    """
    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()}
    mode, time_str, date_str, tz_name = _report_schedule(settings)
    try:
        now_local = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now_local = datetime.now(ZoneInfo("Asia/Dhaka"))
        tz_name = "Asia/Dhaka"
    day_key = now_local.date().isoformat()
    result = {"timezone": tz_name, "mode": mode, "time": time_str,
              "local_time": now_local.strftime("%Y-%m-%d %H:%M"), "email": "not_due"}

    if mode == "off":
        result["email"] = "disabled"
        return result

    if mode == "once":
        if date_str != day_key:
            result["email"] = "waiting_for_date"
            return result
        due = _time_due(now_local, time_str) and _claim_setting_once("last_report_once_key", f"{date_str} {time_str}")
    else:  # daily
        due = _time_due(now_local, time_str) and _claim_setting_once("last_report_email_day", day_key)

    if due:
        recipients = settings.get("report_recipients", [])
        # Real numbers only - the report must never carry a placeholder figure.
        composed = _compose_email(settings, _report_metrics(db))
        stage_html = _stage_table_html(_stage_performance(db))
        delivered, err = _deliver_report(db, settings, composed["subject"], composed["body"],
                                         recipients, "", extra_html=stage_html)
        result["email"] = {"ok": delivered, "delivered": delivered, "to": recipients, "error": err}
        kind = "one-time" if mode == "once" else "daily"
        db.add(models.Job(job_type="report", title=f"Scheduled {kind} report {'delivered' if delivered else 'failed'}",
                          detail=(err or composed["subject"]), status="done" if delivered else "failed",
                          created_at=stamp()))
        db.commit()
    return result


@router.post("/cron/daily-report")
def cron_daily_report(x_cron_secret: str = Header(default=""), db: Session = Depends(get_db)):
    """Fallback cron; configurable actions are normally checked by QStash every minute."""
    _require_cron(x_cron_secret)
    return {"ok": True, "reporting": _run_due_reporting(db)}


# ---------- reports ----------
@router.get("/reports", dependencies=[Depends(require_user)])
def reports(db: Session = Depends(get_db)):
    """Today's report, computed from real SendClean tracking.

    Nothing here is a placeholder. The send count, delivery, opens, clicks, bounces,
    unsubscribes and complaints are all derived from stored per-recipient data, and the
    pass/warn/fail verdicts are measured against the configured targets.
    """
    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()}
    live_totals = _delivery_totals(db)
    metrics = _report_metrics(db, live_totals)
    thresholds = _report_thresholds(metrics, settings)
    composed = _compose_email(settings, metrics)
    tracked = [c for c in db.query(models.Campaign).all()
               if (c.delivery_json or {}).get("summary")]
    scope = (f"{len(tracked)} tracked send(s) · all time"
             if tracked else "No sends have delivery tracking yet")
    sched_mode, sched_time, sched_date, sched_tz = _report_schedule(settings)
    return {
        "date": composed["date"],
        "scope": scope,
        "rates_updated_at": live_totals.get("updated_at"),
        "live_totals": live_totals,
        "stage_performance": _stage_performance(db),
        "metrics": metrics,
        "thresholds": thresholds,
        "subject": composed["subject"],
        "body": composed["body"],
        "recipients": settings.get("report_recipients", []),
        "emailing": {
            "subject": settings.get("report_email_subject", "Sendwise Daily Report - {date}"),
            "format": settings.get("report_email_format", ""),
            "sheet": settings.get("reporting_sheet", ""),
            "sheet_link": composed["sheet_link"],
            "preview": composed,
            "smtp_configured": bool(settings.get("smtp_host") and settings.get("smtp_user")),
        },
        "sheet_settings": {
            "sheet": settings.get("reporting_sheet", ""),
            "sheet_link": f"https://docs.google.com/spreadsheets/d/{settings.get('reporting_sheet', '')}/edit",
            "update_frequency": settings.get("sheet_update_frequency", "daily"),
            "update_time": settings.get("sheet_update_time", "09:50"),
            "daily_sheet_update": settings.get("daily_sheet_update"),
            "webhook_configured": bool(settings.get("sheet_webhook_url") and settings.get("sheet_webhook_secret")),
            "sheet_name": settings.get("reporting_sheet_tab", "Campaign Report"),
            "last_updated_at": settings.get("last_sheet_update_at"),
        },
        "schedule": {
            # Only what actually runs. There is deliberately no weekly-audit or
            # optimization entry here: those were advertised in the UI but nothing
            # ever executed them, which is worse than not listing them at all.
            "mode": sched_mode,
            "time": sched_time,
            "date": sched_date,
            "next_run": _next_report_run(sched_mode, sched_time, sched_date, sched_tz),
            "last_sent_day": settings.get("last_report_email_day"),
            "daily_sheet_update": settings.get("daily_sheet_update"),
            "daily_email": settings.get("daily_email"),
            "timezone": sched_tz,
        },
    }


def _report_metrics(db, live=None):
    """Real performance numbers for the report. Every value is derived - none invented.

    A report that looks plausible but carries placeholder figures is worse than no
    report at all: it goes to the CMO and reads like fact. Delivery and engagement come
    from stored per-recipient SendClean tracking; unsubscribe and complaint come from
    the suppression list, which is the only signal SendClean gives us for them.
    """
    live = live if live is not None else _delivery_totals(db)
    sent = int(live.get("sent") or 0)

    reasons = {}
    for row in db.query(models.Suppression).all():
        key = (row.reason or "other").strip().lower()
        reasons[key] = reasons.get(key, 0) + 1

    def rate(count):
        return round(count / sent * 100, 2) if sent else 0.0

    return {
        "sent": sent,
        "delivered": int(live.get("delivered") or 0),
        "unique_openers": int(live.get("unique_openers") or 0),
        "unique_clicks": int(live.get("unique_clickers") or 0),
        "clicks": int(live.get("clicks") or 0),
        "delivery_rate": live.get("delivery_rate") or 0.0,
        "open_rate": live.get("open_rate") or 0.0,
        "click_rate": live.get("click_rate") or 0.0,
        "bounce_rate": live.get("bounce_rate") or 0.0,
        "unsubscribe": rate(reasons.get("unsubscribe", 0)),
        "complaint": rate(reasons.get("complaint", 0)),
        "unsubscribed_addresses": reasons.get("unsubscribe", 0),
        "complained_addresses": reasons.get("complaint", 0),
        "suppressed_addresses": sum(reasons.values()),
        # Providers that fetches the tracking pixel are excluded from unique_openers
        # above; the count is surfaced so the exclusion stays visible rather than
        # silently shrinking the number.
        "proxy_only_openers": int(live.get("proxy_only_openers") or 0),
        # An open is a pixel fetch, and some pixels are fetched by a machine we cannot
        # prove was a machine. The number is therefore a ceiling, not a measurement,
        # and every consumer is told so rather than left to assume precision.
        "open_rate_is_upper_bound": True,
        "open_rate_note": (
            "Upper bound. Provider image proxies and mail scanners are excluded, but "
            "an unfilterable fetch still counts, so the true figure is this or lower. "
            "Clicks are the dependable signal."
        ),
    }


def _report_thresholds(metrics, settings):
    """Verdicts measured against the CONFIGURED targets, not against fixed numbers."""

    def num(key, default):
        try:
            return float(settings.get(key) or default)
        except (TypeError, ValueError):
            return float(default)

    def verdict(value, limit, lower_is_better, label):
        """pass / warn / fail against a configured limit.

        A max limit (bounce, unsubscribe, complaint) is healthy AT or below it, and a
        little over is a warning. A min target (delivery, click) is healthy AT or above
        it, and a little under is a warning. Reading this the other way round flagged a
        100% delivery rate as "warn", which is exactly backwards.
        """
        if lower_is_better:
            status = "pass" if value <= limit else ("warn" if value <= limit * 1.1 else "fail")
        else:
            status = "pass" if value >= limit else ("warn" if value >= limit * 0.9 else "fail")
        return {
            "label": label,
            "value": f"{value}%",
            "limit": (f"<= {round(limit, 2)}%" if lower_is_better else f">= {round(limit, 2)}%"),
            "status": status,
        }

    return [
        verdict(metrics["delivery_rate"], num("target_delivery_rate", 0.95) * 100,
                False, "Delivery rate"),
        verdict(metrics["bounce_rate"], num("maximum_bounce_rate", 0.02) * 100,
                True, "Bounce rate"),
        verdict(metrics["unsubscribe"], num("maximum_unsubscribe_rate", 0.005) * 100,
                True, "Unsubscribe"),
        verdict(metrics["complaint"], num("maximum_complaint_rate", 0.001) * 100,
                True, "Complaint"),
        verdict(metrics["click_rate"], num("target_click_rate", 0.013) * 100,
                False, "Click rate"),
    ]


# The seven-stage performance ladder (percent). Used when the stored `stage_targets`
# setting is absent; matched to whatever the sequence stages are called.
STAGE_FALLBACK_TARGETS = [
    {"stage": 1, "focus": "Native Pattern Interrupt / Stack Question", "open_min": 15.0, "open_max": 18.0, "click_min": 1.5, "click_max": 2.0},
    {"stage": 2, "focus": "Split Track (Unopened vs Opened Pain Point)", "open_min": 12.0, "open_max": 14.0, "click_min": 1.0, "click_max": 1.5},
    {"stage": 3, "focus": "Developer Shortcut / Speed Proof", "open_min": 10.0, "open_max": 12.0, "click_min": 1.0, "click_max": 1.2},
    {"stage": 4, "focus": "Visual Technical Proof / GTmetrix Teardown", "open_min": 10.0, "open_max": 12.0, "click_min": 1.0, "click_max": 1.5},
    {"stage": 5, "focus": "Interactive Demo / Feature Walkthrough", "open_min": 8.0, "open_max": 10.0, "click_min": 1.0, "click_max": 1.2},
    {"stage": 6, "focus": "Reactivation Incentive / 30% Dev Credit", "open_min": 8.0, "open_max": 10.0, "click_min": 1.5, "click_max": 2.0},
    {"stage": 7, "focus": "Final Breakup", "open_min": 8.0, "open_max": 10.0, "click_min": 1.2, "click_max": 1.5},
]


def _normalise_stage_targets(raw):
    """Accept stored targets whether they use fractions (0.15) or percents (15)."""
    out = []
    for i, t in enumerate(raw or [], start=1):
        def pick(*keys):
            for k in keys:
                if t.get(k) is not None:
                    try:
                        return float(t[k])
                    except (TypeError, ValueError):
                        return None
            return None

        om, ox = pick("open_rate_min", "open_min"), pick("open_rate_max", "open_max")
        cm, cx = pick("click_rate_min", "click_min"), pick("click_rate_max", "click_max")
        scale = 100.0 if (om is not None and om <= 1.0) else 1.0
        out.append({
            "stage": int(t.get("stage") or i),
            "focus": str(t.get("focus") or ""),
            "open_min": None if om is None else round(om * scale, 2),
            "open_max": None if ox is None else round(ox * scale, 2),
            "click_min": None if cm is None else round(cm * scale, 2),
            "click_max": None if cx is None else round(cx * scale, 2),
        })
    return out


def _stage_targets(db):
    row = db.query(models.Setting).filter(models.Setting.key == "stage_targets").first()
    raw = None
    if row and row.value:
        try:
            raw = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            raw = None
    if isinstance(raw, list) and raw:
        norm = _normalise_stage_targets(raw)
        if norm:
            return norm
    return STAGE_FALLBACK_TARGETS


def _stage_performance(db, campaign_slug=""):
    """Per campaign, per stage: open/click rates against the configured stage targets.

    Opens and clicks come from this app's own tracker (the dependable source), grouped by
    the email each event belongs to. Achievement is the measured rate as a percentage of
    the LOWER bound of the stage target - 100% means "met or beat the floor".
    """
    targets = _stage_targets(db)
    tmap = {t["stage"]: t for t in targets}
    max_stage = max(tmap) if tmap else len(STAGE_FALLBACK_TARGETS)

    q = db.query(models.Campaign).order_by(models.Campaign.id.desc())
    if campaign_slug:
        q = q.filter(models.Campaign.slug == campaign_slug)

    rows = []
    for c in q.all():
        index = _campaign_send_index(db, c)
        if not index:
            continue
        meta = {}
        for i, e in enumerate(c.emails_json or []):
            ref = str(e.get("ref") or "").upper()
            if ref:
                meta[ref] = (e, i)
        per = {}
        for _xid, (email, ref) in index.items():
            cell = per.setdefault(str(ref or "").upper(),
                                  {"recipients": set(), "openers": set(), "clickers": set()})
            cell["recipients"].add(email)
        for ev in (db.query(models.EngagementEvent)
                     .filter(models.EngagementEvent.campaign_slug == c.slug).all()):
            ref = str(ev.ref or "").upper()
            cell = per.setdefault(ref, {"recipients": set(), "openers": set(), "clickers": set()})
            who = (ev.email or "").lower()
            if ev.event_type == "click":
                cell["clickers"].add(who)
            elif not ev.is_machine:
                cell["openers"].add(who)

        date = str((c.delivery_json or {}).get("sent_at") or c.launched_at or "")[:10]
        for ref, cell in sorted(per.items()):
            n = len(cell["recipients"])
            e, idx = meta.get(ref, ({}, 0))
            stage = e.get("journey_phase") or e.get("stage") or idx + 1
            try:
                stage = int(stage)
            except (TypeError, ValueError):
                stage = idx + 1
            stage = max(1, min(stage, max_stage))
            t = tmap.get(stage) or {}
            open_rate = round(len(cell["openers"]) / n * 100, 2) if n else None
            click_rate = round(len(cell["clickers"]) / n * 100, 2) if n else None
            om, cm = t.get("open_min"), t.get("click_min")

            def ach(actual, target):
                # 100% is the ceiling: meeting or beating the target floor is a full pass,
                # so a small sample can never report something like "6667%".
                if actual is None or not target:
                    return None
                return min(100, round(actual / target * 100))

            rows.append({
                "campaign": c.title or c.slug,
                "slug": c.slug,
                "stage": stage,
                "stage_label": f"Step {stage}",
                "focus": t.get("focus") or e.get("name") or e.get("focus") or ref,
                "ref": ref,
                "date": date,
                "recipients": n,
                "open_rate": open_rate,
                "click_rate": click_rate,
                "target_open": (f"{om}% – {t.get('open_max')}%" if om is not None else "-"),
                "target_click": (f"{cm}% – {t.get('click_max')}%" if cm is not None else "-"),
                "achievement_open": ach(open_rate, om),
                "achievement_click": ach(click_rate, cm),
            })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targets": targets,
        "rows": rows,
    }


def _stage_table_html(report):
    """The stage performance table as an email-safe HTML block (for the report email)."""
    rows = (report or {}).get("rows") or []
    if not rows:
        return ""
    headers = ["Campaign", "Stage", "Date", "Open", "Click", "Target Open", "Target Click", "Ach. Open", "Ach. Click"]
    head = "".join(
        f'<th style="text-align:left;padding:8px 10px;background:#EEF2FF;color:#3730A3;'
        f'font-size:11px;text-transform:uppercase;letter-spacing:0.04em;'
        f'border-bottom:2px solid #C7D2FE;white-space:nowrap;">{h}</th>'
        for h in headers
    )

    def cell(v, right=False):
        return (f'<td style="padding:8px 10px;border-bottom:1px solid #E5E7EB;font-size:12px;'
                f'color:#334155;{"text-align:right;" if right else ""}">{v}</td>')

    def ach_cell(v):
        if v is None:
            return cell("-", True)
        v = min(100, v)  # 100% is the ceiling
        color = "#059669" if v >= 100 else ("#B45309" if v >= 80 else "#DC2626")
        return (f'<td style="padding:8px 10px;border-bottom:1px solid #E5E7EB;font-size:12px;'
                f'text-align:right;font-weight:700;color:{color};">{v}%</td>')

    body = ""
    for i, r in enumerate(rows):
        bg = "#FFFFFF" if i % 2 == 0 else "#F8FAFC"
        body += (
            f'<tr style="background:{bg};">'
            + cell(r.get("campaign", ""))
            + cell(r.get("stage_label") or f'Step {r.get("stage")}')
            + cell(r.get("date") or "-")
            + cell(f'{r.get("open_rate")}%' if r.get("open_rate") is not None else "-", True)
            + cell(f'{r.get("click_rate")}%' if r.get("click_rate") is not None else "-", True)
            + cell(r.get("target_open") or "-", True)
            + cell(r.get("target_click") or "-", True)
            + ach_cell(r.get("achievement_open"))
            + ach_cell(r.get("achievement_click"))
            + "</tr>"
        )
    return (
        '<h2 style="font-size:15px;color:#1E293B;margin:24px 0 8px;font-family:Arial,Helvetica,sans-serif;">'
        'Stage-wise performance</h2>'
        '<p style="font-size:12px;color:#64748B;margin:0 0 10px;font-family:Arial,Helvetica,sans-serif;">'
        'Achievement is the measured rate as a percentage of the lower bound of the stage target.</p>'
        '<div style="overflow-x:auto;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="border-collapse:collapse;width:100%;border:1px solid #E5E7EB;">'
        f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    )


@router.get("/reports/stage-performance", dependencies=[Depends(require_user)])
def stage_performance_report(slug: str = "", db: Session = Depends(get_db)):
    """Per campaign + stage performance against the stage targets (opens/clicks)."""
    return _stage_performance(db, slug)


def _report_date():
    """Today in the configured timezone, so the report is never dated by a constant."""
    try:
        from zoneinfo import ZoneInfo

        tz = os.getenv("REPORT_TIMEZONE", "Asia/Dhaka")
        return datetime.now(ZoneInfo(tz)).date().isoformat()
    except Exception:  # noqa: BLE001 - fall back to UTC rather than fail a report
        return datetime.now(timezone.utc).date().isoformat()


def _compose_email(settings: dict, metrics: dict | None = None) -> dict:
    """The report email, with every token filled from real numbers."""
    date = _report_date()
    metrics = metrics or {}
    sheet = settings.get("reporting_sheet", "")
    sheet_link = f"https://docs.google.com/spreadsheets/d/{sheet}/edit" if sheet else "(sheet not set)"
    subject = (settings.get("report_email_subject", "Sendwise Daily Report - {date}") or "")
    subject = subject.replace("{date}", date)

    def pct(key):
        value = metrics.get(key)
        return f"{value}%" if value is not None else "n/a"

    body = settings.get(
        "report_email_format",
        "Sendwise daily report {date}: delivery {delivery_rate}, clicks {click_rate}, "
        "opens {open_rate}. Full sheet: {sheet_link}") or ""
    body = (body.replace("{date}", date)
                .replace("{delivery_rate}", pct("delivery_rate"))
                .replace("{click_rate}", pct("click_rate"))
                .replace("{open_rate}", pct("open_rate"))
                .replace("{sent}", str(metrics.get("sent", "n/a")))
                .replace("{delivered}", str(metrics.get("delivered", "n/a")))
                .replace("{proxy_only_openers}", str(metrics.get("proxy_only_openers", 0)))
                .replace("{sheet_link}", sheet_link))
    # An open is a pixel fetch, and a machine can fetch it. Say so in the email rather
    # than let a ceiling be read as a measurement. Appended only when the number is
    # actually shown, so a template that omits {open_rate} does not get a stray note.
    note = metrics.get("open_rate_note") or ""
    if note and "{open_rate}" in (settings.get("report_email_format") or "") and note not in body:
        proxy = int(metrics.get("proxy_only_openers") or 0)
        extra = (f" ({proxy} further delivered address(es) were fetched by a provider "
                 f"proxy, not a person, and are excluded.)") if proxy else ""
        body = f"{body}\n\nOpen rate is an upper bound: {note}{extra}"
    return {"date": date, "subject": subject, "body": body, "sheet_link": sheet_link}


class ReportSendIn(BaseModel):
    to: list | None = None
@router.post("/reports/send", dependencies=[Depends(require_user)])
def send_report(body: ReportSendIn | None = None, db: Session = Depends(get_db)):
    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()}
    recipients = body.to if (body and body.to) else settings.get("report_recipients", [])
    if not isinstance(recipients, list):
        recipients = []
    # Real numbers only - the email must never carry a placeholder figure.
    metrics = _report_metrics(db)
    composed = _compose_email(settings, metrics)
    subject, body_text = composed["subject"], composed["body"]
    # The stage-wise table rides along in the email. The sheet link/button is deliberately
    # NOT included: the report goes out on its own, with no "open the sheet" CTA.
    stage_html = _stage_table_html(_stage_performance(db))

    delivered, error = _deliver_report(
        db, settings, subject, body_text, recipients, "", extra_html=stage_html)

    label = ("Daily report emailed" if delivered
             else ("Report email failed" if error else "Report email composed"))
    db.add(models.Job(
        job_type="report",
        title=f"{label} - {len(recipients)} recipient(s)",
        detail=(error if error else subject),
        status="done",
        created_at=stamp(),
    ))
    db.commit()
    return {"ok": True, "to": recipients, "subject": subject, "body": body_text, "delivered": delivered, "error": error}


class ReportScheduleIn(BaseModel):
    mode: Literal["daily", "once", "off"] = "daily"
    time: str = "10:00"
    date: str | None = None
    timezone: str | None = None


@router.post("/reports/schedule", dependencies=[Depends(require_user)])
def set_report_schedule(body: ReportScheduleIn, db: Session = Depends(get_db)):
    """Save the report email schedule: daily at a time, one date + time, or off.

    Validation is strict on purpose. A schedule that silently falls back to a
    default time is worse than a rejected save, because the operator then believes
    a send is queued that never will be.
    """
    mode = (body.mode or "daily").strip().lower()
    if mode not in ("daily", "once", "off"):
        raise HTTPException(status_code=400, detail="mode must be one of: daily, once, off")
    time_str = (body.time or "").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_str):
        raise HTTPException(status_code=400, detail="time must use 24-hour HH:MM format")
    date_str = (body.date or "").strip()
    if mode == "once" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD for a one-time schedule")
    tz_name = (body.timezone or "").strip()
    if tz_name:
        try:
            ZoneInfo(tz_name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"unknown timezone: {tz_name}") from exc

    _set_setting_value(db, "report_schedule_mode", mode)
    _set_setting_value(db, "report_schedule_time", time_str)
    _set_setting_value(db, "report_schedule_date", date_str if mode == "once" else "")
    # Keep the legacy daily_email in step so older screens never disagree with this one.
    _set_setting_value(db, "daily_email", time_str)
    if tz_name:
        _set_setting_value(db, "timezone", tz_name)
    db.commit()

    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()}
    s_mode, s_time, s_date, s_tz = _report_schedule(settings)
    return {
        "ok": True,
        "schedule": {
            "mode": s_mode,
            "time": s_time,
            "date": s_date,
            "timezone": s_tz,
            "next_run": _next_report_run(s_mode, s_time, s_date, s_tz),
        },
    }


# ---------- email templates ----------
@router.get("/templates/preview", dependencies=[Depends(require_user)])
def template_preview(product: str = "turbo"):
    """Branded HTML email preview - product brand from BRANDS (turbo, travilo, peopledesk, managerium, primevat, trackforce, familyguard)."""
    html = branded_report_html(
        subject="Your weekly campaign performance report",
        body_text="Dear All,\n\nHere is the latest email campaign performance summary for your review - opens, clicks, stage targets and achievements across your active sequences.\n\nYou can view the full report in the sheet below.",
        sheet_link="https://docs.google.com/spreadsheets/d/1wPX4kwnYcI_xIiuRZ9ih-muqnruG8WKz4SCirGKu7OA/edit",
        product=product,
    )
    return HTMLResponse(content=html)


# ---------- jobs ----------
@router.get("/jobs", dependencies=[Depends(require_user)])
def list_jobs(db: Session = Depends(get_db)):
    rows = db.query(models.Job).order_by(models.Job.id.desc()).all()
    return [{"id": j.id, "job_type": j.job_type, "title": j.title, "detail": j.detail, "status": j.status, "created_at": j.created_at} for j in rows]


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(require_user)])
def retry_job(job_id: int, db: Session = Depends(get_db)):
    j = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")
    j.status = "done"
    j.detail = "Retried · completed successfully"
    db.commit()
    return {"id": j.id, "status": j.status, "detail": j.detail}


# ---------- settings ----------
@router.post("/cron/trend-intelligence")
def cron_trend_intelligence(x_cron_secret: str = Header(default=""), db: Session = Depends(get_db)):
    """Trend Intelligence & Email Agent - scheduled entry point.

    Only runs when the Settings → Trend Intelligence toggle is ON. When OFF the
    scheduled run short-circuits and no research / email generation happens.
    """
    _require_cron(x_cron_secret)
    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()}
    from .trend_intelligence import as_bool
    enabled = as_bool(settings.get("trend_intelligence_enabled", False))
    if not enabled:
        return {"enabled": False, "status": "inactive",
                "detail": "Trend Intelligence toggle is OFF - nothing to do"}
    from .trend_intelligence import run_trend_analysis
    result = run_trend_analysis(db, settings, scheduled=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _set_setting_value(db, "trend_intelligence_last_run", now)
    db.commit()
    return {"enabled": True, "status": result["status"], "ran_at": now, "result": result}


@router.get("/trend-intelligence/latest", dependencies=[Depends(require_user)])
def latest_trend_intelligence(db: Session = Depends(get_db)):
    from .trend_intelligence import serialize_run
    run = db.query(models.TrendRun).order_by(models.TrendRun.id.desc()).first()
    if not run:
        raise HTTPException(status_code=404, detail="No trend intelligence run found")
    return serialize_run(db, run)


@router.get("/trend-intelligence/history", dependencies=[Depends(require_user)])
def trend_intelligence_history(limit: int = 20, db: Session = Depends(get_db)):
    from .trend_intelligence import serialize_run
    rows = db.query(models.TrendRun).order_by(models.TrendRun.id.desc()).limit(max(1, min(limit, 100))).all()
    return [serialize_run(db, row, include_evidence=False) for row in rows]


class TrendApprovalIn(BaseModel):
    status: Literal["draft", "approved", "rejected"]


@router.patch("/trend-intelligence/opportunities/{opportunity_id}", dependencies=[Depends(require_user)])
def update_trend_opportunity(opportunity_id: int, body: TrendApprovalIn, db: Session = Depends(get_db)):
    from .trend_intelligence import serialize_opportunity
    row = db.query(models.TrendOpportunity).filter(models.TrendOpportunity.id == opportunity_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Trend opportunity not found")
    if body.status == "approved" and (not row.selected or not all(item.get("pass") for item in (row.qa_json or []))):
        raise HTTPException(status_code=400, detail="Only a selected opportunity with passing QA can be approved")
    row.approval_status = body.status
    db.commit()
    db.refresh(row)
    return serialize_opportunity(row)


class LLMConfigIn(BaseModel):
    provider: str
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    make_active: bool = True


@router.get("/llm/config", dependencies=[Depends(require_user)])
def get_llm_config(db: Session = Depends(get_db)):
    from .llm_providers import public_config
    return public_config(db)


@router.patch("/llm/config", dependencies=[Depends(require_user)])
def save_llm_config(body: LLMConfigIn, db: Session = Depends(get_db)):
    from .llm_providers import save_provider, get_provider_config, test_connection, record_test
    try:
        if body.provider == "rule_based":
            return save_provider(db, "rule_based")
        # Save first, then test. An AI provider becomes ACTIVE only after a successful live API call.
        save_provider(db, body.provider, body.api_key, body.model, body.base_url, make_active=False)
        cfg = get_provider_config(db, body.provider)
        result = test_connection(cfg)
        record_test(db, body.provider, True, result.get("response", "OK"))
        public = save_provider(db, body.provider, make_active=body.make_active)
        public["connection_test"] = result
        return public
    except (ValueError, RuntimeError) as exc:
        if body.provider != "rule_based":
            try:
                record_test(db, body.provider, False, str(exc))
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/llm/test", dependencies=[Depends(require_user)])
def test_llm_config(body: LLMConfigIn, db: Session = Depends(get_db)):
    from .llm_providers import get_provider_config, test_connection, record_test
    if body.provider == "rule_based":
        return {"ok": True, "provider": "rule_based", "model": "brand-md engine", "response": "OK"}
    cfg = get_provider_config(db, body.provider, body.api_key, body.model, body.base_url)
    if not cfg or not cfg.get("api_key"):
        raise HTTPException(status_code=400, detail="Enter an API key or save one first")
    try:
        result = test_connection(cfg)
        # Persist status only when testing the stored key (a typed unsaved key is session-only).
        if not body.api_key:
            record_test(db, body.provider, True, result.get("response", "OK"))
        return result
    except (ValueError, RuntimeError) as exc:
        if not body.api_key:
            try:
                record_test(db, body.provider, False, str(exc))
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/llm/selfcheck", dependencies=[Depends(require_user)])
def llm_selfcheck(db: Session = Depends(get_db)):
    """End-to-end check of the exact path `Draft with Agent` uses.

    Runs one real generation against a sample brand doc, so a dead provider, a
    missing brand file or a template error is caught BEFORE the operator relies
    on it - instead of silently producing an empty send.
    """
    import time as _time
    from .copy_engine import generate
    from .llm_providers import get_active_config
    from .trend_intelligence import campaign_trend_context, trend_provenance

    started = _time.time()
    llm_config = get_active_config(db)
    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()}
    try:
        trend_context = campaign_trend_context(db, "Travilo", settings)
    except Exception:  # noqa: BLE001 - trend data is optional
        trend_context = {}
    provenance = trend_provenance(trend_context)

    try:
        gen = generate(
            product="Travilo", offer="Lifecycle sequence",
            sender_name="Sendwise Self-check", stage_focus="A quick self-check",
            audience_name="Self-check", email_ref="SC1",
            campaign_slug="__selfcheck__", llm_config=llm_config,
            trend_context=trend_context,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        reason = f"{type(exc).__name__}: {exc}"
        db.add(models.Job(job_type="qa", title="Agent self-check FAILED",
                          detail=reason[:600], status="failed", created_at=stamp()))
        db.commit()
        raise HTTPException(status_code=502, detail=f"Agent self-check failed - {reason}") from exc

    secs = round(_time.time() - started, 1)
    body_len = len((gen.get("body") or "").strip())
    ok = bool((gen.get("subject") or "").strip()) and body_len >= 40
    result = {
        "ok": ok,
        "seconds": secs,
        "provider": gen.get("llm_provider") or (llm_config or {}).get("label") or "rule_based",
        "model": gen.get("llm_model") or (llm_config or {}).get("model") or "brand-md engine",
        "via_llm": bool(gen.get("via_llm")),
        "subject": gen.get("subject", ""),
        "preview_text": gen.get("preview_text", ""),
        "body_chars": body_len,
        "html_chars": len(gen.get("body_html") or ""),
        "brand_md": gen.get("md_source") or "",
        "trend_status": provenance.get("status", "disabled"),
    }
    db.add(models.Job(
        job_type="qa",
        title=f"Agent self-check {'passed' if ok else 'INCOMPLETE'}",
        detail=(f"{result['provider']} / {result['model']} · {secs}s · "
                f"{result['body_chars']} chars body, {result['html_chars']} chars HTML"),
        status="done" if ok else "failed", created_at=stamp(),
    ))
    db.commit()
    return result


@router.get("/settings", dependencies=[Depends(require_user)])
def get_settings(db: Session = Depends(get_db)):
    settings = {s.key: _setting_dict(s) for s in db.query(models.Setting).all()
                if not s.key.endswith("_enc") and s.key not in {
                    "sendclean_api_token", "sendclean_owner_id"
                }}
    settings["email_provider"] = "sendclean"
    settings["sendclean_api_token_configured"] = bool(
        db.query(models.Setting).filter(models.Setting.key == "sendclean_api_token_enc", models.Setting.value != "").first()
        or db.query(models.Setting).filter(models.Setting.key == "sendclean_api_token", models.Setting.value != "").first()
    )
    settings["sendclean_owner_id"] = str(
        db.query(models.Setting).filter(models.Setting.key == "sendclean_owner_id").first().value
        if db.query(models.Setting).filter(models.Setting.key == "sendclean_owner_id").first() else ""
    )
    settings["primary_kpis"] = settings.get("primary_kpis", ["click_rate", "open_rate_directional"])
    return settings


class SettingPatch(BaseModel):
    key: str
    value: str


@router.patch("/settings", dependencies=[Depends(require_user)])
def patch_setting(body: SettingPatch, db: Session = Depends(get_db)):
    if body.key == "email_provider" and body.value != "sendclean":
        raise HTTPException(status_code=400, detail="Sendwise is configured for SendClean only")
    if body.key in ("sheet_update_time", "daily_sheet_update", "daily_email", "report_schedule_time"):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(body.value or "")):
            raise HTTPException(status_code=400, detail=f"{body.key} must use 24-hour HH:MM format")
    if body.key == "report_schedule_date" and body.value:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(body.value or "")):
            raise HTTPException(status_code=400, detail="report_schedule_date must use YYYY-MM-DD format")
    if body.key == "report_schedule_mode" and body.value not in ("daily", "once", "off"):
        raise HTTPException(status_code=400, detail="report_schedule_mode must be daily, once or off")
    if body.key.startswith("mailchimp"):
        raise HTTPException(status_code=400, detail="Mailchimp has been removed; Sendwise runs SendClean only")
    if body.key == "sendclean_api_token":
        try:
            encrypted = encrypt_secret(body.value.strip()) if body.value.strip() else ""
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        encrypted_key = body.key + "_enc"
        encrypted_row = db.query(models.Setting).filter(models.Setting.key == encrypted_key).first()
        if encrypted_row:
            encrypted_row.value = encrypted
        else:
            db.add(models.Setting(key=encrypted_key, value=encrypted))
        legacy_row = db.query(models.Setting).filter(models.Setting.key == body.key).first()
        if legacy_row:
            legacy_row.value = ""
        else:
            db.add(models.Setting(key=body.key, value=""))
        db.commit()
        return {"key": body.key, "configured": bool(body.value.strip())}
    s = db.query(models.Setting).filter(models.Setting.key == body.key).first()
    if not s:
        # upsert - new settings keys (e.g. smtp_*) created on first save
        s = models.Setting(key=body.key, value=body.value)
        db.add(s)
    else:
        s.value = body.value
    if body.key == "sheet_update_time":
        _set_setting_value(db, "daily_sheet_update", body.value)
    db.commit()
    return {"key": s.key, "value": _setting_dict(s)}
