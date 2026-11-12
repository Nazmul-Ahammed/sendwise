"""SendClean API surface for Sendwise.

Kept separate from routes.py so the provider surface stays small and auditable.
Every write action is guarded by an explicit SendClean capability check.
"""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from . import email_provider as ep
from . import models
from . import sendclean_client as sc
from .auth import require_user
from .db import get_db, stamp

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Public brand assets for email templates.
#
# Email clients fetch images anonymously and block SVG and data: URIs, so logos
# must be served as real raster files over public HTTPS. This route is therefore
# intentionally unauthenticated and restricted to image files in brand_assets/.
# ---------------------------------------------------------------------------
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand_assets")
ASSET_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@router.get("/assets/{filename}")
def brand_asset(filename: str):
    """Serve a brand logo so email templates can reference a public HTTPS URL."""
    name = os.path.basename(filename)
    if name != filename or name.startswith(".") or "/" in name or "\\" in name:
        raise HTTPException(status_code=404, detail="Not found")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ASSET_TYPES:
        raise HTTPException(status_code=404, detail="Not found")
    path = os.path.join(ASSET_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not found")
    with open(path, "rb") as fh:
        payload = fh.read()
    return Response(
        content=payload,
        media_type=ASSET_TYPES[ext],
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


class EmailProviderTestIn(BaseModel):
    owner_id: str = ""
    token: str = ""


class RecipientIn(BaseModel):
    email: str
    name: str = ""
    type: str = "to"


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    from_email: str
    from_name: str = ""
    reply_to: str = ""
    html: str = ""
    text: str = ""
    recipients: list[RecipientIn] = []
    x_unique_id: str = ""
    template_id: str = ""
    dynamic_value: dict = {}


def _guard(db, capability):
    try:
        return ep.require_capability(db, capability)
    except ep.ProviderCapabilityError as exc:
        raise HTTPException(status_code=400, detail=f"BLOCKED: {exc}") from exc


@router.get("/email-provider/status", dependencies=[Depends(require_user)])
def email_provider_status(db=Depends(get_db)):
    return ep.status(db)


@router.post("/email-provider/test", dependencies=[Depends(require_user)])
def email_provider_test(body: EmailProviderTestIn, db=Depends(get_db)):
    try:
        return ep.test_connection(db, owner_id=body.owner_id, token=body.token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Connection failed: {exc}") from exc


@router.get("/sendclean/overview", dependencies=[Depends(require_user)])
def sendclean_overview(db=Depends(get_db)):
    return ep.overview(db)


@router.get("/sendclean/domains/check", dependencies=[Depends(require_user)])
def sendclean_check_domain(domain: str, db=Depends(get_db)):
    if not domain.strip():
        raise HTTPException(status_code=400, detail="domain is required")
    _guard(db, "sending_domains")
    try:
        return ep.check_sending_domain(db, domain.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"SendClean error: {exc}") from exc


@router.get("/sendclean/messages", dependencies=[Depends(require_user)])
def sendclean_messages(x_unique_id: str, skip_page: int | None = None, db=Depends(get_db)):
    if not x_unique_id.strip():
        raise HTTPException(status_code=400, detail="x_unique_id is required")
    _guard(db, "message_lookup")
    try:
        return ep.message_info(db, x_unique_id.strip(), skip_page)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"SendClean error: {exc}") from exc


@router.post("/sendclean/send", dependencies=[Depends(require_user)])
def sendclean_send(body: SendIn, db=Depends(get_db)):
    """Guarded transactional send. Recipients must be supplied explicitly."""
    _guard(db, "transactional_send")
    creds = ep.credentials(db)
    if not creds["smtp_user_name"]:
        raise HTTPException(status_code=400, detail="BLOCKED: SendClean SMTP username is not configured in Settings")
    if not body.recipients:
        raise HTTPException(status_code=400, detail="BLOCKED: at least one recipient is required")
    if not (body.html.strip() or body.text.strip()):
        raise HTTPException(status_code=400, detail="BLOCKED: provide html or text content")

    seen, recipients = set(), []
    for r in body.recipients:
        addr = (r.email or "").strip().lower()
        if "@" not in addr:
            raise HTTPException(status_code=400, detail=f"BLOCKED: invalid recipient address '{r.email}'")
        if addr in seen:
            continue
        seen.add(addr)
        entry = {"email": addr, "type": r.type if r.type in ("to", "cc", "bcc") else "to"}
        if r.name:
            entry["name"] = r.name
        recipients.append(entry)

    x_unique_id = (body.x_unique_id or "").strip() or f"sw-api-{int(datetime.now(timezone.utc).timestamp())}"
    message = {
        "subject": body.subject,
        "from_email": body.from_email,
        "to": recipients,
        "headers": {"X-Unique-Id": x_unique_id},
    }
    if body.from_name:
        message["from_name"] = body.from_name
    if body.reply_to:
        message["headers"]["Reply-To"] = body.reply_to
    if body.html:
        message["html"] = body.html
    if body.text:
        message["text"] = body.text
    if body.dynamic_value:
        message["dynamic_value"] = body.dynamic_value

    try:
        if body.template_id:
            message["template_id"] = body.template_id
            result = sc.send_template(creds["owner_id"], creds["token"], creds["smtp_user_name"], message)
        else:
            result = sc.send_mail(creds["owner_id"], creds["token"], creds["smtp_user_name"], message)
    except Exception as exc:  # noqa: BLE001
        db.add(models.Job(
            job_type="launch", title="SendClean send failed",
            detail=str(exc)[:300], status="failed", created_at=stamp(),
        ))
        db.commit()
        raise HTTPException(status_code=400, detail=f"SendClean error: {exc}") from exc

    db.add(models.Job(
        job_type="launch",
        title=f"SendClean sent - {len(recipients)} recipient(s)",
        detail=f"x_unique_id={x_unique_id} - {result.get('message', '')}"[:400],
        status="done", created_at=stamp(),
    ))
    db.commit()
    return {
        "ok": True,
        "x_unique_id": x_unique_id,
        "recipients": len(recipients),
        "status": result.get("status", "success"),
        "message": result.get("message", ""),
    }
