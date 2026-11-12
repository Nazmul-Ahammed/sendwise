"""Sendwise as a hosted MCP server (Model Context Protocol, Streamable HTTP).

Remote AI agents (Claude Desktop, Cursor, opencode, ...) can connect and call
Sendwise tools directly.

Auth: every request must send  Authorization: Bearer <MCP_TOKEN>

Sendwise runs on SendClean only. SendClean's documented API is transactional:
there is no audience, marketing-campaign, automation, per-recipient event or
aggregate-report API, so those capabilities are intentionally absent here.
"""
import json
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from .db import SessionLocal, stamp
from . import models
from . import email_provider as ep
from . import sendclean_client as sc

mcp = FastMCP("Sendwise", instructions=(
    "Sendwise is the iBOS (AKIJ Resource) SendClean email orchestrator. "
    "Tools cover transactional sends, message lookup, SendClean account/domain/webhook "
    "administration, and copy generation from the product brand MD files. "
    "SendClean has no audience or marketing-campaign API, so every send must name its "
    "recipients explicitly. Always confirm the recipient list before a real send."
))

TOOL_KEYS = None


def _db():
    return SessionLocal()


def _json(x):
    return json.dumps(x, default=str, ensure_ascii=False)


# ---------------- provider ----------------
@mcp.tool()
def sendclean_status() -> str:
    """Active provider, configuration state and the exact SendClean capabilities available."""
    db = _db()
    try:
        return _json(ep.status(db))
    finally:
        db.close()


@mcp.tool()
def sendclean_diagnose() -> str:
    """Item-by-item reason the SendClean connection works or not, plus a live probe.

    Use this instead of guessing from `sendclean_status`: it separates 'nothing saved',
    'token stored but cannot be decrypted after a SECRET_KEY change', 'credentials
    rejected by SendClean' and 'network unreachable', each of which needs a different fix.
    """
    db = _db()
    try:
        return _json(ep.diagnostics(db))
    finally:
        db.close()


@mcp.tool()
def delivery_ledger(campaign_slug: str = "", batch: str = "", limit: int = 100) -> str:
    """Delivery counts from Sendwise's own ledger - no SendClean call is made.

    Answers 'how many were actually sent' even when the provider is unreachable, rate
    limiting, or its API token has been rotated.
    """
    from . import delivery_ledger as ledger
    db = _db()
    try:
        return _json({
            "summary": ledger.summary(db, campaign_slug=campaign_slug or None,
                                      batch=batch or None),
            "recent": ledger.recent(db, campaign_slug=campaign_slug or None, limit=limit),
        })
    finally:
        db.close()


@mcp.tool()
def sendclean_overview() -> str:
    """Live SendClean account, SMTP users, sending domains, tracking domains and webhooks."""
    db = _db()
    try:
        return _json(ep.overview(db))
    finally:
        db.close()


@mcp.tool()
def sendclean_message_info(x_unique_id: str, skip_page: int = 0) -> str:
    """Look up SendClean delivery/open/click activity for one X-Unique-Id."""
    db = _db()
    try:
        return _json(ep.message_info(db, x_unique_id, skip_page or None))
    finally:
        db.close()


@mcp.tool()
def sendclean_check_domain(domain: str) -> str:
    """Check DKIM/SPF validity for a SendClean sending domain."""
    db = _db()
    try:
        return _json(ep.check_sending_domain(db, domain))
    finally:
        db.close()


@mcp.tool()
def sendclean_send_message(
    subject: str,
    from_email: str,
    recipients: list,
    html: str = "",
    text: str = "",
    from_name: str = "",
    reply_to: str = "",
    x_unique_id: str = "",
) -> str:
    """Send one transactional email through SendClean.

    recipients: list of email strings, or objects {"email": "...", "name": "...", "type": "to|cc|bcc"}.
    There is no fixed application-level recipient cap. At least one of html or text is required.
    """
    db = _db()
    try:
        creds = ep.credentials(db)
        if not (creds["owner_id"] and creds["token"] and creds["smtp_user_name"]):
            return _json({"error": "SendClean credentials incomplete: owner ID, token and SMTP username are all required"})
        if not recipients:
            return _json({"error": "at least one recipient is required"})
        if not (html.strip() or text.strip()):
            return _json({"error": "provide html or text content"})

        cleaned, seen = [], set()
        for item in recipients:
            if isinstance(item, dict):
                addr = str(item.get("email") or "").strip().lower()
                entry = {"email": addr}
                if item.get("name"):
                    entry["name"] = item["name"]
                entry["type"] = item.get("type") if item.get("type") in ("to", "cc", "bcc") else "to"
            else:
                addr, entry = str(item or "").strip().lower(), None
                entry = {"email": addr, "type": "to"}
            if "@" not in addr:
                return _json({"error": f"invalid recipient address: {addr or '(blank)'}"})
            if addr in seen:
                continue
            seen.add(addr)
            cleaned.append(entry)

        unique_id = x_unique_id.strip() or f"sw-mcp-{int(datetime.now(timezone.utc).timestamp())}"
        message = {
            "subject": subject,
            "from_email": from_email,
            "to": cleaned,
            "headers": {"X-Unique-Id": unique_id},
        }
        if from_name:
            message["from_name"] = from_name
        if reply_to:
            message["headers"]["Reply-To"] = reply_to
        if html:
            message["html"] = html
        if text:
            message["text"] = text

        result = sc.send_mail(creds["owner_id"], creds["token"], creds["smtp_user_name"], message)
        db.add(models.Job(
            job_type="launch", title=f"SendClean sent - {len(cleaned)} recipient(s)",
            detail=f"x_unique_id={unique_id} · via MCP", status="done", created_at=stamp(),
        ))
        db.commit()
        return _json({"ok": True, "x_unique_id": unique_id, "recipients": len(cleaned),
                      "status": result.get("status", "success"), "message": result.get("message", "")})
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)[:300]})
    finally:
        db.close()


# ---------------- campaigns (local send records) ----------------
@mcp.tool()
def list_campaigns() -> str:
    """List Sendwise send records with status, recipients and per-email send state."""
    db = _db()
    try:
        rows = db.query(models.Campaign).order_by(models.Campaign.id.desc()).limit(500).all()
        return _json([{
            "slug": c.slug, "title": c.title, "status": c.status,
            "recipients": c.audience_count, "product": c.product,
            "send_mode": c.send_mode,
            "provider_message_id": c.provider_message_id,
            "emails": [{"ref": e.get("ref"), "status": e.get("send_status"),
                        "provider_id": e.get("provider_id")} for e in (c.emails_json or [])],
        } for c in rows])
    finally:
        db.close()


@mcp.tool()
def get_campaign(slug: str) -> str:
    """Get one send record: brief, explicit recipients, emails, HTML source and provider message id."""
    db = _db()
    try:
        c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
        if not c:
            return _json({"error": "campaign not found"})
        return _json({
            "slug": c.slug, "title": c.title, "status": c.status,
            "product": c.product, "offer": c.offer,
            "sender": {"name": c.sender_name, "email": c.sender_email},
            "recipients": (c.audience_options or {}).get("manual_emails", []),
            "provider_message_id": c.provider_message_id,
            "emails": c.emails_json or [],
        })
    finally:
        db.close()


# ---------------- sequences (local templates) ----------------
@mcp.tool()
def list_sequences() -> str:
    """List local sequence templates (slug, stages, delays, email refs)."""
    db = _db()
    try:
        rows = db.query(models.Sequence).order_by(models.Sequence.id.desc()).all()
        return _json([{"slug": s.slug, "name": s.name, "trigger": s.trigger,
                       "stages": s.stages_json} for s in rows])
    finally:
        db.close()


@mcp.tool()
def duplicate_sequence(slug: str) -> str:
    """Duplicate a sequence template with all emails, branches and delays."""
    db = _db()
    try:
        source = db.query(models.Sequence).filter(models.Sequence.slug == slug).first()
        if not source:
            return _json({"error": "sequence not found"})
        base = f"{source.slug}-copy"
        new_slug, n = base, 2
        while db.query(models.Sequence).filter(models.Sequence.slug == new_slug).first():
            new_slug = f"{base}-{n}"
            n += 1
        clone = models.Sequence(
            slug=new_slug, name=f"{source.name} (Copy)", description=source.description,
            trigger=source.trigger,
            stages_json=json.loads(json.dumps(source.stages_json or [])),
            rules_json=json.loads(json.dumps(source.rules_json or [])), created_at=stamp(),
        )
        db.add(clone)
        db.commit()
        return _json({"slug": new_slug, "name": clone.name, "stages": len(clone.stages_json or [])})
    finally:
        db.close()


# ---------------- copy + launch ----------------
@mcp.tool()
def generate_copy(slug: str, llm_provider: str = "active") -> str:
    """Regenerate subject/preview/body/HTML for every email from the product brand MD.
    llm_provider: 'active' (configured AI) or 'rule_based' (brand engine)."""
    from .copy_engine import generate
    db = _db()
    try:
        c = db.query(models.Campaign).filter(models.Campaign.slug == slug).first()
        if not c:
            return _json({"error": "campaign not found"})
        llm_config = None
        if llm_provider != "rule_based":
            from .llm_providers import get_active_config
            llm_config = get_active_config(db)
        from .trend_intelligence import campaign_trend_context, trend_provenance
        settings = {}
        for setting in db.query(models.Setting).all():
            try:
                settings[setting.key] = json.loads(setting.value)
            except (json.JSONDecodeError, TypeError):
                settings[setting.key] = setting.value
        trend_context = campaign_trend_context(db, c.product, settings)
        provenance = trend_provenance(trend_context)

        def _one(e):
            stage = e.get("stage", 1)
            ref = e.get("ref") or f"E{stage}"
            focus = next((s.get("focus", "") for s in (c.sequence_json or [])
                          if s.get("stage") == stage), "A quick update")
            g = generate(product=c.product, offer=c.offer, sender_name=c.sender_name,
                         stage_focus=focus, audience_name=c.audience_name,
                         email_ref=ref, campaign_slug=c.slug, llm_config=llm_config,
                         trend_context=trend_context)
            return {"stage": stage, "ref": ref, "subject": g["subject"],
                    "preview_text": g["preview_text"], "body": g["body"],
                    "body_html": g["body_html"], "cta_url": g["cta_url"],
                    "cta_label": g.get("cta_label"), "journey": g.get("journey_name"),
                    "via_llm": g.get("via_llm"), "source": g.get("md_source"),
                    "trend_status": provenance["status"],
                    "trend_provenance": dict(provenance)}

        rows = list(c.emails_json or [])
        if rows and llm_config:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(8, len(rows))) as ex:
                emails = list(ex.map(_one, rows))
        else:
            emails = [_one(e) for e in rows]
        from .db import engine
        from sqlalchemy import text as _t
        with engine.begin() as conn:
            conn.execute(_t("UPDATE campaigns SET emails_json=:v WHERE slug=:s"),
                         {"v": json.dumps(emails), "s": slug})
        return _json({"ok": True, "emails": emails})
    finally:
        db.close()


@mcp.tool()
def launch_campaign(slug: str) -> str:
    """Send an approved send record through SendClean to its stored explicit recipients."""
    from .routes import launch_campaign as _launch
    from fastapi import HTTPException
    db = _db()
    try:
        try:
            return _json(_launch(slug=slug, db=db))
        except HTTPException as e:
            return _json({"error": e.detail})
    finally:
        db.close()


@mcp.tool()
def server_time() -> str:
    """Current UTC server time (useful for delay checks)."""
    return datetime.now(timezone.utc).isoformat()
