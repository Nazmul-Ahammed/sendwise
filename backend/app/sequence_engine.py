"""Branched email sequence engine for SendClean.

SendClean is transactional-only: it has no campaign, audience, automation,
unsubscribe or suppression API. This module supplies what is missing -
per-recipient progress, branch decisions taken from real open/click data,
scheduling, and unsubscribe/suppression enforcement.

Flow
----
enroll(db, campaign, recipients)
    -> creates one enrollment per address and sends E1 immediately
       (one SendClean call each, so every recipient has its own tracking id)

dispatch(db)
    -> on every tick, for each enrollment whose next_due_at has passed:
         * skip and flag suppressed / unsubscribed addresses
         * read the last sent email's open/click from SendClean
         * choose the next ref from BRANCH_TREE
         * send it, or mark the enrollment completed

Open data is indicative (image-blocking clients never fire the pixel; Gmail and
Apple prefetch can fire it without a real open). Click data is the reliable
branch signal, which is why `clicked` is evaluated before `opened`.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from . import email_provider as ep
from . import models
from . import open_signal
from . import sendclean_client as sc
from . import tracking
from . import delivery_ledger as ledger
from .db import SessionLocal
from .copy_engine import product_key
from .email_templates import body_to_html

_log = logging.getLogger("sendwise")

# One dispatcher at a time. Two overlapping ticks would both see the same due
# enrollments and send each one twice, so the tick claims this slot first.
_DISPATCH_LOCK_KEY = "dispatch_lock"
_DISPATCH_LOCK_TTL = 900  # a tick that dies releases the slot on its own after this

# Branch tree of the iBOS 9-email framework. None = terminal (sequence ends).
BRANCH_TREE = {
    "E1": {"opened": "E2", "not_opened": "E3"},
    "E2": {"clicked": "E4", "opened": "E5", "not_opened": "E6"},
    "E3": {"clicked": "E7", "opened": "E8", "not_opened": "E9"},
    "E4": None, "E5": None, "E6": None,
    "E7": None, "E8": None, "E9": None,
}

NEXT_GAP_DAYS = int(os.getenv("SEQUENCE_GAP_DAYS", "3"))
# How many times a failed send is retried before it is given up on.
MAX_ATTEMPTS = int(os.getenv("SEQUENCE_MAX_ATTEMPTS", "3"))
# Account-wide hourly ceiling, kept below SendClean's own cap (observed at ~1000/hour on
# this account) so a pass stops on our terms instead of being refused message by message.
MAX_SENDS_PER_HOUR = int(os.getenv("SEQUENCE_MAX_SENDS_PER_HOUR", "900"))
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "https://sendwise.ibos.agency").rstrip("/")
# How long one engagement lookup may take. The provider's per-message endpoint is its
# slowest and has been observed to hang rather than fail, so a dispatch that reads it for
# every recipient must bound each call: six unresponsive lookups used to block for four
# minutes and outlive the request that was waiting on them.
LOOKUP_TIMEOUT = int(os.getenv("SEQUENCE_LOOKUP_TIMEOUT", "8"))
# Total wall-clock budget for one dispatch pass. When it runs out the remaining
# enrollments simply stay due and are picked up by the next tick, which is always safe:
# nothing is marked done that was not sent.
DISPATCH_BUDGET_SECONDS = int(os.getenv("SEQUENCE_DISPATCH_BUDGET", "90"))


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _parse(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")) if iso else None
    except (TypeError, ValueError):
        return None


def unsubscribe_url(token):
    return f"{PUBLIC_BASE}/api/unsubscribe?token={token}"


# ---------------------------------------------------------------- suppression
def is_suppressed(db, email):
    e = (email or "").strip().lower()
    if not e:
        return False
    return db.query(models.Suppression).filter(models.Suppression.email == e).first() is not None


def suppress(db, email, reason="unsubscribe", source="", detail=""):
    e = (email or "").strip().lower()
    if not e:
        return None
    row = db.query(models.Suppression).filter(models.Suppression.email == e).first()
    if row:
        if reason:
            row.reason = reason
        if detail:
            row.detail = str(detail)[:300]
        return row
    row = models.Suppression(email=e, reason=reason or "unsubscribe",
                             source=source, detail=str(detail)[:300])
    db.add(row)
    return row


# --------------------------------------------------------------- branches
def build_branch_tree(stages):
    """Branch tree built from a sequence's OWN definition.

    Every email declares where a contact goes next:
        open_click_next      opened AND clicked the CTA
        open_no_click_next   opened, no click
        not_open_next        did not open

    An email that declares none of the three ends the sequence (`None`). Returns
    None when the definition carries no branching at all, so the caller can fall
    back to the built-in framework.
    """
    tree = {}
    found = False
    for stage in stages or []:
        for em in (stage.get("emails") or []):
            ref = str(em.get("ref") or "").strip()
            if not ref:
                continue
            clicked = str(em.get("open_click_next") or "").strip()
            opened = str(em.get("open_no_click_next") or "").strip()
            not_opened = str(em.get("not_open_next") or "").strip()
            if clicked or opened or not_opened:
                found = True
            node = {}
            if clicked:
                node["clicked"] = clicked
            if opened:
                node["opened"] = opened
            if not_opened:
                node["not_opened"] = not_opened
            tree[ref] = node or None
    return tree if found else None


def branch_tree_for(campaign):
    """The branch rules this campaign actually runs.

    Driven by the sequence attached to the send, so editing a sequence genuinely
    changes the journey. Falls back to the built-in 9-email framework only when the
    send carries no branching of its own.
    """
    return build_branch_tree(getattr(campaign, "sequence_json", None)) or BRANCH_TREE


def decide_next_ref(current_ref, opens, clicks, tree=None):
    """Next email for this recipient, from their real behaviour on `current_ref`.

    `opens` must be HUMAN opens (`open_signal.human_opens`), not SendClean's raw
    `open` count. A raw count includes the provider's own image-proxy fetches, which
    would send a non-opener down the "opened" branch.
    """
    node = (tree or BRANCH_TREE).get(current_ref)
    if node is None:
        return None
    if clicks > 0 and node.get("clicked"):
        return node["clicked"]
    if opens > 0 and node.get("opened"):
        return node["opened"]
    return node.get("not_opened")


def pending_refs(current_ref, tree=None):
    node = (tree or BRANCH_TREE).get(current_ref) or {}
    return sorted(set(v for v in node.values() if v))


_DELAY_UNITS = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
}


def _stage_delay(campaign, ref):
    """The delay declared on the stage that HOLDS `ref`.

    Deliberately private. Reading a stage's delay directly is exactly how the wrong
    stage gets used - a 5-minute journey waited 3 days for its second email because one
    caller read the stage of the email it had just sent. Callers must go through
    `wait_before` / `wait_after`, which is the only vocabulary for timing.
    """
    for stage in (campaign.sequence_json or []):
        for em in (stage.get("emails") or []):
            if str(em.get("ref") or "").strip().upper() == str(ref or "").strip().upper():
                try:
                    value = int(stage.get("delay_value") or 0)
                except (TypeError, ValueError):
                    _log.warning("stage delay for %s in %s is not a number (%r) - treating as 0",
                                 ref, getattr(campaign, "slug", "?"), stage.get("delay_value"))
                    value = 0
                if value <= 0:
                    return timedelta(seconds=30)   # due on the very next tick
                unit = str(stage.get("delay_unit") or "day").lower()
                if unit not in _DELAY_UNITS:
                    _log.warning("unknown delay unit %r on %s of %s - treating as days",
                                 unit, ref, getattr(campaign, "slug", "?"))
                return _DELAY_UNITS.get(unit, _DELAY_UNITS["day"]) * value
    # The sequence does not declare this email at all. Announce it loudly: a silent
    # default here is what turned a 5-minute journey into a 3-day one.
    _log.warning(
        "sequence %s does not define %r, so it declares no wait - using %s day(s). "
        "The send's sequence_json is probably out of step with its emails.",
        getattr(campaign, "slug", "?"), ref, NEXT_GAP_DAYS)
    return timedelta(days=NEXT_GAP_DAYS)


def wait_before(campaign, ref):
    """How long after the previous send `ref` may go out - THE definition of timing.

    A stage's delay means "this stage's emails go out this long after the previous
    stage", so this answers for the email about to be sent. One function defines it, so
    there is no second formula anyone can pick by mistake.
    """
    return _stage_delay(campaign, ref)


def wait_span_after(campaign, sent_ref, tree=None):
    """(soonest, slowest) wait that may follow `sent_ref`, across its possible branches.

    Which branch is taken depends on the recipient's opens and clicks, and is unknown
    until a tick reads them. A schedule therefore has to cover the whole range: schedule
    on the soonest, and never authorise a send before its own requirement is met.
    Returns (None, None) when nothing follows.
    """
    options = pending_refs(sent_ref, tree if tree is not None else branch_tree_for(campaign))
    if not options:
        return None, None
    gaps = [wait_before(campaign, ref) for ref in options]
    return min(gaps), max(gaps)


def wait_after(campaign, sent_ref, tree=None):
    """Soonest the journey could continue after `sent_ref` - None at the end."""
    return wait_span_after(campaign, sent_ref, tree)[0]


def _terminal_gap(campaign, ref):
    """Stored value when nothing follows `ref` - a malformed sequence, logged loudly."""
    _log.warning("nothing follows %s in %s - the sequence declares no next stage",
                 ref, getattr(campaign, "slug", "?"))
    return timedelta(days=NEXT_GAP_DAYS)


def _stage_order(campaign):
    """Stage number per ref, so 'is this the first send?' is answerable."""
    order = {}
    for stage in (campaign.sequence_json or []):
        for em in (stage.get("emails") or []):
            ref = str(em.get("ref") or "").strip().upper()
            if ref:
                order[ref] = int(stage.get("stage") or 0)
    return order


# --------------------------------------------------------------- safety net
def audit_delays(db, tolerance_hours=2):
    """Find enrollments waiting far longer than their own sequence says they should.

    This guards the class of bug that hid in plain sight: a journey scheduled on the
    wrong clock looks completely normal from the outside. Nothing errored, nothing was
    unsent - the wait was simply 3 days instead of 5 minutes.

    Compares, for every active enrollment, the wait it is actually on against the wait
    its sequence declares. Returns a list of mismatches (empty means healthy).
    """
    problems = []
    campaigns = {c.slug: c for c in db.query(models.Campaign).all()}
    rows = (db.query(models.SequenceEnrollment)
            .filter(models.SequenceEnrollment.status == "active").all())
    for e in rows:
        camp = campaigns.get(e.campaign_slug)
        if not camp or not e.current_ref or not e.last_sent_at or not e.next_due_at:
            continue
        last = _parse(e.last_sent_at)
        due = _parse(e.next_due_at)
        if not last or not due:
            continue
        # Which email comes next is not known without reading opens/clicks, so compare
        # against the SLOWEST possible next step. Waiting longer than even that is
        # definitively wrong; anything within the range is legitimately in flight.
        _soonest, expected = wait_span_after(camp, e.current_ref)
        if expected is None:
            continue
        actual = due - last
        if actual - expected > timedelta(hours=tolerance_hours):
            problems.append({
                "email": e.email,
                "campaign": e.campaign_slug,
                "ref": e.current_ref,
                "expected": str(expected),
                "actual": str(actual),
                "next_due_at": e.next_due_at,
            })
    return problems


# --------------------------------------------------------------- sending
def _email_row(campaign, ref):
    for row in (campaign.emails_json or []):
        if (row.get("ref") or "") == ref:
            return row
    return None



# --------------------------------------------------------------- SMTP transport
# SendClean's API only accepts SMTP users registered under the same owner id
# ("Invalid Smtp User !!"), while the SMTP relay accepts any valid SMTP user. An
# operator can therefore hold a perfectly good SMTP user that the API refuses; the send
# path is switchable to the relay so that user can still be used.
_SMTP_STATE = {"conn": None, "key": None}


def _setting_value(db, key, default=""):
    from . import models as _m
    row = db.query(_m.Setting).filter(_m.Setting.key == key).first()
    value = row.value if row and row.value is not None else ""
    return value if str(value).strip() else default


def _smtp_config(db):
    try:
        port = int(_setting_value(db, "smtp_port", "587") or 587)
    except (TypeError, ValueError):
        port = 587
    return {
        "host": str(_setting_value(db, "smtp_host", "")).strip(),
        "port": port,
        "user": str(_setting_value(db, "smtp_user", "")).strip(),
        "password": str(_setting_value(db, "smtp_app_password", "")),
    }


def _smtp_connection(cfg):
    import smtplib
    import ssl
    key = (cfg["host"], cfg["port"], cfg["user"])
    conn = _SMTP_STATE.get("conn")
    if conn is not None and _SMTP_STATE.get("key") == key:
        try:
            conn.noop()
            return conn
        except Exception:  # noqa: BLE001
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    conn = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
    conn.ehlo()
    conn.starttls(context=ssl.create_default_context())
    conn.ehlo()
    conn.login(cfg["user"], cfg["password"])
    _SMTP_STATE["conn"] = conn
    _SMTP_STATE["key"] = key
    return conn


def _smtp_send(db, message):
    """Send one already-instrumented message through the SMTP relay."""
    from email.message import EmailMessage
    cfg = _smtp_config(db)
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        raise RuntimeError(
            "SMTP transport selected but smtp_host / smtp_user / smtp_app_password are not set")
    msg = EmailMessage()
    msg["Subject"] = message.get("subject") or ""
    from_email = message.get("from_email") or ""
    from_name = message.get("from_name") or ""
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = ", ".join(r.get("email", "") for r in (message.get("to") or []))
    for header, value in (message.get("headers") or {}).items():
        try:
            msg[header] = value
        except Exception:  # noqa: BLE001 - a header we cannot set must not stop a send
            pass
    if message.get("html"):
        msg.set_content(message.get("text") or " ")
        msg.add_alternative(message["html"], subtype="html")
    else:
        msg.set_content(message.get("text") or "")
    conn = _smtp_connection(cfg)
    try:
        conn.send_message(msg)
    except Exception:
        # A relay session that died mid-transaction cannot be reused: SendClean then
        # answers every later send on it with "(503, Use MAIL before RCPT)", or the
        # socket fails with "cannot read from timed out object". Dropping the cached
        # connection makes the next recipient reconnect cleanly instead of inheriting
        # the failure - one shared socket previously took down 404 recipients.
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        _SMTP_STATE["conn"] = None
        _SMTP_STATE["key"] = None
        raise
    return {"status": "queued", "message": "smtp"}


def _transport(db):
    return str(_setting_value(db, "send_transport", "api")).strip().lower() or "api"


def _send_one(db, campaign, row, email, token):
    """One SendClean call for one recipient, with its own tracking id."""
    creds = ep.credentials(db)
    transport = _transport(db)
    if transport != "smtp" and not (creds["owner_id"] and creds["token"]
                                    and creds["smtp_user_name"]):
        raise RuntimeError("SendClean owner ID, API token and SMTP username must all be configured")

    xid = f"sw-seq-{campaign.slug}-{row.get('ref')}-{uuid.uuid4().hex[:10]}"
    html = body_to_html(
        row.get("body") or "",
        product=product_key(campaign.product),
        cta_url=row.get("cta_url") or "",
        cta_text=row.get("cta_label") or "",
        headline=row.get("subject") or campaign.title or "",
        unsubscribe_url=unsubscribe_url(token),
    )
    # Our own pixel and click redirects. The provider's tracking is switched off below:
    # it cannot be read back (their message lookup hangs and their webhook never
    # delivers), so leaving it on would only produce a second, unreadable set of numbers.
    html = tracking.instrument_for(db, html, xid, email, PUBLIC_BASE)
    headers = {
        "X-Unique-Id": xid,
        # The provider's own tracking is off: we cannot read it back (their lookup hangs
        # and their webhook never delivers), so it would only add numbers nobody can see.
        "X-Store-TrackOpens": "no",
        "X-Store-TrackClicks": "no",
    }
    message = {
        "subject": row.get("subject") or campaign.title,
        "from_email": campaign.sender_email,
        "from_name": campaign.sender_name,
        "to": [{"email": email, "type": "to"}],
        "html": html,
        "headers": headers,
    }
    if transport == "smtp":
        _smtp_send(db, message)
    else:
        sc.send_mail(creds["owner_id"], creds["token"], creds["smtp_user_name"], message)
    return xid


# --------------------------------------------------------------- dispatch lock
def acquire_dispatch_lock(db):
    """Claim the single dispatch slot. True when this tick owns it.

    Two ticks overlapping (a slow batch, a retry, a manual run during a scheduled one)
    would each read the same due enrollments and send every one of them twice. The claim
    is a timestamped row, so a tick that dies cannot wedge the scheduler - the slot
    frees itself after _DISPATCH_LOCK_TTL seconds.
    """
    from sqlalchemy import text
    stamp = _iso(_now())
    cutoff = _iso(_now() - timedelta(seconds=_DISPATCH_LOCK_TTL))
    try:
        if db.query(models.Setting).filter(models.Setting.key == _DISPATCH_LOCK_KEY).first() is None:
            db.add(models.Setting(key=_DISPATCH_LOCK_KEY, value=""))
            db.commit()
        res = db.execute(
            text("UPDATE settings SET value = :now WHERE key = :k "
                 "AND (value IS NULL OR value = '' OR value < :cutoff)"),
            {"now": stamp, "k": _DISPATCH_LOCK_KEY, "cutoff": cutoff})
        db.commit()
        return res.rowcount == 1
    except Exception as exc:  # noqa: BLE001
        # Not fatal: better to run unlocked than to skip a whole tick. Logged so the
        # reason is visible if a duplicate ever happens.
        _log.warning("dispatch lock unavailable (%s) - running unlocked", exc)
        return True


def release_dispatch_lock(db):
    """Free the dispatch slot.

    A failure here is logged rather than swallowed: the slot then stays claimed until its
    TTL expires, so every dispatch for the next 15 minutes returns "another dispatch is
    already in progress" and nothing sends. That is a total stall of the sequence engine,
    and it used to happen with no trace of why.
    """
    from sqlalchemy import text
    try:
        db.execute(text("UPDATE settings SET value = '' WHERE key = :k"),
                   {"k": _DISPATCH_LOCK_KEY})
        db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.error("dispatch lock could not be released (%s) - the slot stays claimed "
                   "until its %ss TTL expires and dispatch will report itself busy "
                   "until then", exc, _DISPATCH_LOCK_TTL)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------- enroll
def queue_enrollments(db, campaign, recipients, first_ref="E1"):
    """Persist a cohort quickly; the dispatcher sends E1 after the HTTP response.

    The old enrollment endpoint performed one provider call per recipient before
    answering the browser.  Large cohorts therefore always crossed the proxy timeout
    and could be killed while holding several GB of request state.  Enrollment rows are
    the durable queue: if the process restarts, the normal dispatch cron resumes them.
    """
    if not _email_row(campaign, first_ref):
        raise ValueError(f"{campaign.slug} has no {first_ref} email row; generate copy first")

    clean = sorted({str(raw or "").strip().lower() for raw in recipients if str(raw or "").strip()})
    if not clean:
        return {"requested": 0, "queued": 0, "already_enrolled": 0, "skipped_suppressed": 0}

    # Serialize cohort creation before checking existing rows. Production contains
    # historical duplicate enrollments, so a global unique index would fail at startup.
    # This transaction lock prevents concurrent launch/enroll requests from creating
    # any new duplicates without rewriting that historical audit trail.
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"sequence-enroll:{campaign.slug}"},
        )

    existing = {
        email for (email,) in
        db.query(models.SequenceEnrollment.email).filter(
            models.SequenceEnrollment.campaign_slug == campaign.slug,
            models.SequenceEnrollment.email.in_(clean),
        ).all()
    }
    suppressed = {
        email for (email,) in
        db.query(models.Suppression.email).filter(models.Suppression.email.in_(clean)).all()
    }
    now = _iso(_now())
    rows = [
        models.SequenceEnrollment(
            campaign_slug=campaign.slug,
            sequence_slug=getattr(campaign, "sequence_slug", "") or campaign.sequence_type or "",
            email=email,
            token=uuid.uuid4().hex,
            current_ref="",
            stage=1,
            branch="main",
            status="active",
            next_due_at=now,
            sends=0,
            pending_ref="",
            pending_at="",
            attempts=0,
            history_json=[],
        )
        for email in clean
        if email not in existing and email not in suppressed
    ]
    db.add_all(rows)
    db.commit()
    return {
        "requested": len(clean),
        "queued": len(rows),
        "already_enrolled": len(existing),
        "skipped_suppressed": len(suppressed - existing),
    }


def enroll(db, campaign, recipients, first_ref="E1"):
    """Create enrollments and send the first email straight away."""
    row = _email_row(campaign, first_ref)
    if not row:
        raise ValueError(f"{campaign.slug} has no {first_ref} email row; generate copy first")

    out = []
    for raw in recipients:
        email = str(raw or "").strip().lower()
        if not email:
            continue
        if is_suppressed(db, email):
            out.append({"email": email, "status": "skipped_suppressed"})
            continue
        exists = db.query(models.SequenceEnrollment).filter(
            models.SequenceEnrollment.campaign_slug == campaign.slug,
            models.SequenceEnrollment.email == email,
        ).first()
        if exists:
            out.append({"email": email, "status": "already_enrolled",
                        "current_ref": exists.current_ref, "state": exists.status})
            continue

        enr = models.SequenceEnrollment(
            campaign_slug=campaign.slug,
            sequence_slug=getattr(campaign, "sequence_slug", "") or campaign.sequence_type or "",
            email=email, token=uuid.uuid4().hex,
            current_ref="", stage=1, branch="main", status="active",
        )
        db.add(enr)
        db.flush()
        # Claim before the send and commit, exactly as dispatch does: an interrupted
        # enroll is then visible to the next tick instead of quietly sending E1 twice.
        enr.pending_ref = first_ref
        enr.pending_at = _iso(_now())
        db.commit()
        try:
            xid = _send_one(db, campaign, row, email, enr.token)
            enr.current_ref = first_ref
            enr.last_x_unique_id = xid
            enr.last_sent_at = _iso(_now())
            # The FIRST wait must come from the sequence too. It used to use the global
            # default, so a 5-minute test sequence still waited 3 days for email 2.
            first_wait = wait_after(campaign, first_ref)
            if first_wait is None:
                first_wait = _terminal_gap(campaign, first_ref)
            enr.next_due_at = _iso(_now() + first_wait)
            enr.sends = 1
            enr.attempts = 0
            enr.pending_ref = ""
            enr.pending_at = ""
            enr.history_json = [{"ref": first_ref, "sent_at": enr.last_sent_at, "x_unique_id": xid}]
            out.append({"email": email, "status": "sent", "ref": first_ref, "x_unique_id": xid})
        except Exception as exc:  # noqa: BLE001
            enr.status = "failed"
            enr.pending_ref = ""
            enr.pending_at = ""
            enr.history_json = [{"ref": first_ref, "error": str(exc)[:200]}]
            out.append({"email": email, "status": "failed", "error": str(exc)[:200]})
        # Commit per recipient: a crash part-way through a long list must not lose the
        # recipients already enrolled, which would re-send the first email to them.
        db.commit()

    return out


def _record_failure(db, enr, ref, exc):
    """Record a failed send and decide whether it is worth trying again.

    A timeout, a network blip or a 5xx is worth retrying; the retry is bounded. The
    pending claim is cleared so the next attempt is read as a fresh one rather than as
    an interrupted send.

    Known trade-off: if SendClean accepted the message and the connection then broke,
    the retry can duplicate it. That risk is accepted and bounded to MAX_ATTEMPTS -
    the alternative, giving up on the first blip, silently drops a real recipient.
    """
    detail = str(exc)[:200]
    hist = json.loads(json.dumps(enr.history_json or []))
    enr.attempts = (enr.attempts or 0) + 1
    hist.append({"ref": ref, "error": detail, "attempt": enr.attempts})
    enr.history_json = hist
    enr.pending_ref = ""
    enr.pending_at = ""

    # An account-wide hourly cap is not this recipient's fault, and it clears by itself.
    # Retrying in 5/10/15 minutes spent all three attempts inside the same blocked hour
    # and then marked the recipient "failed" for good - which is exactly how 1,662 people
    # were written off on 2026-10-28. Wait for the window to reset and do not charge the
    # attempt counter.
    if "hourly limit" in detail.lower():
        enr.attempts = max(0, enr.attempts - 1)
        enr.status = "active"
        enr.next_due_at = _iso(_now() + timedelta(minutes=60))
        db.commit()
        return

    if enr.attempts < MAX_ATTEMPTS:
        # Back off, then let a later tick try again.
        enr.next_due_at = _iso(_now() + timedelta(minutes=5 * enr.attempts))
        enr.status = "active"
    else:
        enr.status = "failed"
    db.commit()


def tracking_started_at(db):
    """When this app began measuring opens itself, as an ISO timestamp.

    Messages sent before this moment carry the provider's tracking pixel, not ours, so no
    engagement data exists for them and none ever will. Branching on that silence would
    treat every recipient as a non-opener - which is exactly the behaviour that made the
    first campaign wrong - so those sends are held back instead.
    """
    row = (db.query(models.Setting)
             .filter(models.Setting.key == "tracking_started_at").first())
    if row and (row.value or "").strip():
        return row.value
    stamp = _iso(_now())
    if row:
        row.value = stamp
    else:
        db.add(models.Setting(key="tracking_started_at", value=stamp))
    db.commit()
    return stamp


def _is_measurable(db, enr):
    """Can engagement for this enrollment's last send be known at all?"""
    started = tracking_started_at(db)
    sent = str(enr.last_sent_at or "")
    if not sent:
        return True
    return sent >= started


def _engagement_from_events(db, x_unique_id):
    """What our own tracker recorded for one send.

    Returns every figure, because the branch rule needs one and the reports need another:

      pixel_loads  - every load of the tracking image
      human_opens  - only the loads that did not come from a known proxy
      clicks       - link follows, never cached and never proxied

    The two open counts differ in exactly the case that matters here. A Gmail recipient's
    read is fetched by Google's own proxy, carrying the same `GoogleImageProxy` agent as a
    delivery-time preload, so it is indistinguishable from one. Classifying it as a machine
    therefore does not lose a little accuracy - it reports a real reader as a non-reader,
    and the branch rule acts on that.
    """
    if not x_unique_id:
        return {"pixel_loads": 0, "human_opens": 0, "machine_opens": 0, "clicks": 0}
    rows = (db.query(models.EngagementEvent)
              .filter(models.EngagementEvent.x_unique_id == x_unique_id).all())
    return {
        "pixel_loads": sum(1 for r in rows if r.event_type == "open"),
        "human_opens": sum(1 for r in rows if r.event_type == "open" and not r.is_machine),
        "machine_opens": sum(1 for r in rows if r.event_type == "open" and r.is_machine),
        "clicks": sum(1 for r in rows if r.event_type == "click"),
    }


# What counts as "opened" when choosing a branch. Two defensible answers, and the choice
# is deliberate rather than a detail:
#
#   pixel_loads  Any load of the image, which is what Mailchimp reports as an open. A
#                mailbox that preloads looks like a reader who never read, but nobody who
#                did read is ever mistaken for someone who did not.
#   confirmed    Only loads that did not come from a known proxy. Stricter, and wrong for
#                Gmail: a genuine read arrives from Google's proxy with the same
#                `GoogleImageProxy` agent as a preload, so it is counted as not-read and
#                the recipient is sent the non-opener email after reading it.
#
# Default is `pixel_loads`, because sending a non-opener email to someone who just read
# yours is the worse of the two errors.
BRANCH_OPEN_BASIS = os.getenv("SEQUENCE_BRANCH_OPEN_BASIS", "pixel_loads").strip().lower()


def _opens_for_branch(eng):
    """The open count the branch rule acts on."""
    if BRANCH_OPEN_BASIS == "confirmed":
        return int(eng.get("human_opens") or 0)
    return int(eng.get("pixel_loads") or 0)


# --------------------------------------------------------------- dispatch
def _sends_last_hour(db):
    """How many messages this account actually put out in the last rolling hour.

    SendClean enforces an hourly cap and answers with `Hourly limit reached` once it is
    reached - an error that looks like a per-message failure but is really an account-wide
    budget. On 2026-10-28 that budget was exhausted after ~1000 sends, and 1,662
    recipients were recorded as failed for it. The count is read from our own ledger, so
    the dispatcher can stop BEFORE the provider refuses, instead of discovering it 1,662
    times.
    """
    try:
        since = _iso(_now() - timedelta(hours=1))
        return int(db.query(models.SendRecord)
                   .filter(models.SendRecord.status == "sent",
                           models.SendRecord.first_sent_at >= since)
                   .count())
    except Exception:  # noqa: BLE001 - a missing table must not stop a dispatch
        return 0


def dispatch(db, dry_run=False, limit=300):
    """Advance every enrollment whose next step is due."""
    now = _now()
    creds = ep.credentials(db)
    # The window MUST be the earliest-due work, not an arbitrary slice of the table.
    # Without the ORDER BY, the database returns whatever rows it happens to find first
    # (heap order) up to `limit`. Once those rows all sit on a future due date - which is
    # exactly what happens after E1 goes out - every tick re-reads them, sends nothing,
    # and the enrollments that ARE due are never reached. That is how E1 went out and
    # E2-E9 stalled: 6,472 enrollments were past due while each pass looked at 300
    # not-yet-due rows. Ordering by next_due_at (ISO-8601, so lexicographic ==
    # chronological) puts the oldest due work first; an empty date sorts first and is
    # parked by the safety check below instead of hiding.
    rows = (db.query(models.SequenceEnrollment)
            .filter(models.SequenceEnrollment.status == "active")
            .order_by(models.SequenceEnrollment.next_due_at.asc(),
                      models.SequenceEnrollment.id.asc())
            .limit(limit).all())

    plan, sent, completed, blocked, failed, parked, paused = [], [], [], [], [], [], []
    unmeasurable = []
    started = _now()
    deferred = 0

    # Account-wide hourly budget. A sequence of 900 recipients is fine; two launched in
    # the same hour are not, and the provider only says so per message.
    already = _sends_last_hour(db)
    remaining_budget = MAX_SENDS_PER_HOUR - already
    if not dry_run and remaining_budget <= 0:
        return {"dry_run": False, "plan": [], "sent": [], "completed": [], "blocked_suppressed": [],
                "failed": [], "parked": [], "paused": [], "deferred": len(rows),
                "unmeasurable": [],
                "note": (f"hourly budget spent: {already} sent in the last 60 min "
                         f"(cap {MAX_SENDS_PER_HOUR}); nothing attempted, "
                         f"{len(rows)} enrollment(s) stay due")}

    for enr in rows:
        # A pass must not outlive the request waiting on it. When the budget is spent the
        # remaining enrollments keep their due date and the next tick picks them up, so
        # stopping early costs nothing and can never mark unsent work as done.
        if (_now() - started).total_seconds() > DISPATCH_BUDGET_SECONDS:
            deferred = len(rows) - rows.index(enr)
            plan.append({"note": f"time budget of {DISPATCH_BUDGET_SECONDS}s reached; "
                                 f"{deferred} enrollment(s) left due for the next tick"})
            break
        # Same reasoning for the account-wide hourly cap: stop while the provider is
        # still accepting mail, so the remainder keeps a clean due date instead of being
        # marked failed for a limit that resets on its own.
        if not dry_run and len(sent) >= remaining_budget:
            deferred = len(rows) - rows.index(enr)
            plan.append({"note": f"hourly budget reached ({already + len(sent)}/"
                                 f"{MAX_SENDS_PER_HOUR} in 60 min); "
                                 f"{deferred} enrollment(s) left due for the next tick"})
            break

        # 1. An interrupted send is NEVER repeated automatically. A duplicate email is
        #    worse than a missing one, so the enrollment is parked for a human instead.
        if (enr.pending_ref or "").strip():
            enr.status = "needs_review"
            parked.append(f"{enr.email}: interrupted while sending {enr.pending_ref} - "
                          f"parked on purpose so it cannot go out twice")
            db.commit()
            continue

        # 2. A missing or unreadable due date must never read as "due now". That is the
        #    exact silent-default shape that has bitten this engine before.
        due = _parse(enr.next_due_at)
        if due is None:
            enr.status = "needs_review"
            parked.append(f"{enr.email}: next_due_at missing or unreadable "
                          f"({enr.next_due_at!r}) - not sent")
            db.commit()
            continue
        if due > now:
            continue

        if is_suppressed(db, enr.email):
            enr.status = "suppressed"
            blocked.append(enr.email)
            continue

        campaign = (db.query(models.Campaign)
                    .filter(models.Campaign.slug == enr.campaign_slug).first())
        if not campaign:
            # A campaign that was merely moved to Trash is hidden by the soft-delete
            # guard, so a plain lookup cannot see it. Trashing is reversible, so the
            # enrollment must WAIT rather than be written off. Marking these "failed"
            # told the operator their sends were broken when nothing had been sent at
            # all - and it happened to every enrollment of every trashed send.
            trashed = (db.query(models.Campaign)
                       .execution_options(include_trashed=True)
                       .filter(models.Campaign.slug == enr.campaign_slug,
                               models.Campaign.deleted_at != "").first())
            if trashed:
                enr.status = "paused"
                paused.append(f"{enr.email}: {enr.campaign_slug} is in Trash")
            else:
                enr.status = "failed"
                failed.append(f"{enr.email}: campaign {enr.campaign_slug} missing")
            db.commit()
            continue

        opens = clicks = machine_opens = 0
        if enr.current_ref and enr.last_x_unique_id:
            # A send made before local tracking existed carries the provider's pixel, so
            # there is no engagement for it and there never will be. Hold it rather than
            # read the silence as "did not open": that would send the non-opener email to
            # everyone, which is the fault this whole change exists to fix.
            if not _is_measurable(db, enr):
                # Reported by the preview too, so what the dry run shows is what a real
                # run would actually do.
                if not dry_run:
                    enr.status = "needs_review"
                    db.commit()
                unmeasurable.append(
                    f"{enr.email}: {enr.current_ref} was sent before local tracking "
                    f"existed, so no open data exists for it")
                continue
            # Read ONLY our own tracker's records. The provider's message lookup is not
            # consulted at all any more: it hangs for 30s+ per call, which stalled the
            # whole pass, and its numbers were the ones that misrouted people in the first
            # place. Our pixel fires on every render, so "nothing recorded" is a real
            # measurement rather than a gap in the data.
            eng = _engagement_from_events(db, enr.last_x_unique_id)
            opens = _opens_for_branch(eng)
            clicks = int(eng.get("clicks") or 0)
            machine_opens = int(eng.get("machine_opens") or 0)

        # A queued enrollment has not received anything yet.  E1 is its first step;
        # later steps branch on the sequence this send actually carries.
        tree = branch_tree_for(campaign)
        initial = not (enr.current_ref or "").strip()
        nxt = "E1" if initial else decide_next_ref(enr.current_ref, opens, clicks, tree)
        if not nxt:
            enr.status = "completed"
            completed.append(enr.email)
            continue

        # The wait belongs to the NEXT email's stage, and is authoritative here: the
        # due date is only a wake-up time, so a branch into a slower stage cannot fire
        # early just because the tick happened to arrive sooner.
        needed = timedelta(0) if initial else wait_before(campaign, nxt)
        last = _parse(enr.last_sent_at)
        if last and (now - last) < needed:
            enr.next_due_at = _iso(last + needed)
            continue

        row = _email_row(campaign, nxt)
        if not row:
            # The sequence says this email exists but the send has no copy for it. That
            # is a content fault, not the end of the journey - park it, do not "complete".
            enr.status = "needs_review"
            parked.append(f"{enr.email}: {nxt} is in the sequence but has no content on this send")
            db.commit()
            continue

        decision = {
            "email": enr.email,
            "from_ref": enr.current_ref,
            "opens": opens,
            "clicks": clicks,
            # Recorded so a proxy fetch is visible in the audit trail rather than
            # silently vanishing from the numbers.
            "machine_opens_ignored": machine_opens,
            "branch": "initial" if initial else ("clicked" if clicks else ("opened" if opens else "not_opened")),
            "next_ref": nxt,
        }
        if dry_run:
            plan.append(decision)
            continue

        # 3. Claim the send and COMMIT before the network call. If the process dies
        #    mid-send, the claim survives and the next tick parks it (rule 1) instead of
        #    quietly sending the same email a second time.
        enr.pending_ref = nxt
        enr.pending_at = _iso(now)
        db.commit()

        try:
            xid = _send_one(db, campaign, row, enr.email, enr.token)
            # Sendwise's own record of this step, written before the enrollment is
            # advanced. The provider cannot be asked what happened later, so this is
            # the authoritative answer to "did this address get E5?".
            ledger.record(db, campaign.slug, nxt, enr.email, x_unique_id=xid,
                          status="sent", batch=f"{campaign.slug}:{nxt}",
                          sent_at=_iso(now), commit=False)
            hist = json.loads(json.dumps(enr.history_json or []))
            hist.append({"ref": nxt, "sent_at": _iso(now), "x_unique_id": xid,
                         "from_ref": enr.current_ref, "opens": opens, "clicks": clicks,
                         # Recorded so a reader can tell "nobody opened" from "only the
                         # provider's proxy fetched the pixel" without re-querying.
                         "machine_opens_ignored": machine_opens,
                         "branch": decision["branch"]})
            enr.history_json = hist
            enr.current_ref = nxt
            enr.branch = decision["branch"]
            enr.last_x_unique_id = xid
            enr.last_sent_at = _iso(now)
            # Ask for the wait that follows THIS email, never this email's own stage.
            follow = wait_after(campaign, nxt) or _terminal_gap(campaign, nxt)
            enr.next_due_at = _iso(now + follow)
            enr.sends = (enr.sends or 0) + 1
            enr.stage = _stage_order(campaign).get(str(nxt).upper(), enr.stage or 1)
            enr.pending_ref = ""
            enr.pending_at = ""
            enr.attempts = 0
            sent.append(f"{enr.email} -> {nxt} ({decision['branch']})")
            # Commit per recipient, so a crash later in the batch cannot lose the
            # progress already made and re-send everyone.
            db.commit()
        except Exception as exc:  # noqa: BLE001
            _record_failure(db, enr, nxt, exc)
            ledger.record_failure(db, campaign.slug, nxt, enr.email, str(exc),
                                  batch=f"{campaign.slug}:{nxt}", commit=False)
            failed.append(f"{enr.email}: {str(exc)[:120]}")

    db.commit()
    return {"dry_run": dry_run, "plan": plan, "sent": sent, "completed": completed,
            "blocked_suppressed": blocked, "failed": failed, "parked": parked,
            "paused": paused, "deferred": deferred, "unmeasurable": unmeasurable}


def reconcile_campaign_state(db, campaign_slug):
    """Derive campaign state from durable enrollment/send proof."""
    campaign = db.query(models.Campaign).filter(models.Campaign.slug == campaign_slug).first()
    if not campaign:
        return "missing"
    pending_initial = db.query(models.SequenceEnrollment).filter(
        models.SequenceEnrollment.campaign_slug == campaign_slug,
        models.SequenceEnrollment.status == "active",
        models.SequenceEnrollment.current_ref == "",
    ).count()
    sent_rows = db.query(models.SequenceEnrollment).filter(
        models.SequenceEnrollment.campaign_slug == campaign_slug,
        models.SequenceEnrollment.sends > 0,
    ).all()
    if pending_initial:
        campaign.status = "sending"
    elif sent_rows:
        campaign.status = "live"
        if not campaign.launched_at:
            times = [r.last_sent_at for r in sent_rows if r.last_sent_at]
            campaign.launched_at = min(times) if times else _iso(_now())
    db.commit()
    return campaign.status


def run_queued_dispatch(campaign_slug, passes=3):
    """Best-effort immediate drain; durable rows remain for cron after a restart."""
    db = SessionLocal()
    try:
        for _ in range(max(1, int(passes or 1))):
            if not acquire_dispatch_lock(db):
                break
            try:
                result = dispatch(db, dry_run=False, limit=300)
            finally:
                release_dispatch_lock(db)
            reconcile_campaign_state(db, campaign_slug)
            if not result.get("sent") or result.get("deferred"):
                break
        reconcile_campaign_state(db, campaign_slug)
    except Exception as exc:  # noqa: BLE001
        _log.exception("queued dispatch failed for %s: %s", campaign_slug, exc)
        try:
            db.rollback()
            reconcile_campaign_state(db, campaign_slug)
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
