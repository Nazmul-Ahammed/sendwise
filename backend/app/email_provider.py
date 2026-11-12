"""SendClean-only email provider layer for Sendwise.

Sendwise runs exclusively on SendClean. There is deliberately no Mailchimp
adapter and no cross-provider fallback: an operation SendClean cannot perform
fails closed rather than silently routing somewhere else.
"""
import json
import os

from . import models
from . import sendclean_client as sendclean
from .llm_providers import decrypt_secret

PROVIDER_KEY = "sendclean"

PROVIDERS = {
    "sendclean": {
        "label": "SendClean",
        "capabilities": {
            "account", "transactional_send", "template_send", "message_lookup",
            "sending_domains", "tracking_domains", "webhooks", "smtp_users",
        },
    },
}

# Operations SendClean's documented API does not provide. Kept explicit so the
# reason for a refusal is always explainable to an operator.
UNSUPPORTED = {
    "audiences": "audience lists",
    "segments": "saved segments",
    "campaign_drafts": "marketing campaign drafts",
    "campaign_send": "marketing campaign sending",
    "send_test": "campaign test sends",
    "preflight": "pre-send checklists",
    "recipient_events": "per-recipient open/click event streams",
    "aggregate_reports": "aggregate campaign analytics",
    "contact_lifecycle": "contact suppression and archiving",
    "automations": "marketing automations",
}


class ProviderCapabilityError(RuntimeError):
    """Raised when an operation is not supported by the active provider."""


SECRET_FIELD_MARKERS = ("password", "passwd", "secret", "token", "api_key", "apikey", "private")
SECRET_FIELD_NAMES = {"key", "signing_key", "webhook_key", "smtp_password"}


def _redact(row):
    """Strip credential material from a SendClean settings row before it is returned."""
    if not isinstance(row, dict):
        return row
    out = {}
    for key, value in row.items():
        name = str(key).lower()
        if value and (name in SECRET_FIELD_NAMES or any(m in name for m in SECRET_FIELD_MARKERS)):
            out[key] = "***"
        else:
            out[key] = value
    return out


def _setting(db, key, default=""):
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    if not row or row.value is None:
        return default
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        return row.value


def selected_provider(db=None):
    """Sendwise is SendClean-only, so selection is not configurable."""
    return PROVIDER_KEY


def credentials(db):
    """Resolve SendClean credentials: encrypted token first, then plaintext, then env.

    The encrypted form wins because it is the one the UI writes. Plaintext is kept for a
    row written by an older build (startup migrates it). Environment variables are the
    last resort, because a deployment can only be repaired from the outside when the
    database or the API token has already failed - and without a fallback, a rotated
    SECRET_KEY leaves a working deployment unreachable with no way in.

    `token_source` and `token_undecryptable` are returned so a caller can say WHY the
    connection is down. Before this, an encrypted token that could not be decrypted
    (SECRET_KEY changed) and a token that was never saved both produced the same
    "credentials are not configured", which pointed every investigation at the wrong
    place.
    """
    enc_row = db.query(models.Setting).filter(
        models.Setting.key == "sendclean_api_token_enc").first()
    enc_value = (enc_row.value or "") if enc_row else ""
    plain_value = str(_setting(db, "sendclean_api_token", "") or "")

    token, token_source, undecryptable = "", "", False
    if enc_value:
        token = str(decrypt_secret(enc_value) or "").strip()
        if token:
            token_source = "encrypted"
        else:
            # A row exists but will not decrypt: the Fernet key derives from SECRET_KEY,
            # so this is a key change, not a missing credential.
            undecryptable = True
            token_source = "encrypted_undecryptable"
    if not token and plain_value.strip():
        token = plain_value.strip()
        token_source = token_source or "plaintext"
    if not token:
        env_token = str(os.getenv("SENDCLEAN_API_TOKEN", "") or "").strip()
        if env_token:
            token = env_token
            token_source = "env"

    owner_id = str(_setting(db, "sendclean_owner_id", "") or "").strip() \
        or str(os.getenv("SENDCLEAN_OWNER_ID", "") or "").strip()
    smtp_user = str(_setting(db, "sendclean_smtp_user_name", "") or "").strip() \
        or str(os.getenv("SENDCLEAN_SMTP_USER_NAME", "") or "").strip()

    return {
        "owner_id": owner_id,
        "token": token,
        "smtp_user_name": smtp_user,
        "token_source": token_source,
        "token_undecryptable": undecryptable,
    }


def configured(db):
    creds = credentials(db)
    return bool(creds["owner_id"] and creds["token"])


def require_capability(db, capability):
    if capability in UNSUPPORTED:
        raise ProviderCapabilityError(
            f"SendClean does not provide {UNSUPPORTED[capability]}. "
            "This operation was blocked rather than routed to another provider."
        )
    if capability not in PROVIDERS[PROVIDER_KEY]["capabilities"]:
        raise ProviderCapabilityError(
            f"SendClean does not support {capability.replace('_', ' ')}."
        )
    if not configured(db):
        raise ProviderCapabilityError(
            "SendClean credentials are not configured. Add the owner ID and API token in Settings."
        )
    return PROVIDER_KEY


def test_connection(db, owner_id="", token=""):
    creds = credentials(db)
    result = sendclean.account(owner_id or creds["owner_id"], token or creds["token"])
    user = result.get("user_data") or {}
    return {
        "ok": True,
        "provider": PROVIDER_KEY,
        "account": {
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "reputation": user.get("reputation", ""),
        },
    }


def diagnostics(db):
    """Why the SendClean connection is or is not working, item by item.

    `status()` can only answer configured true/false, and that single boolean hid three
    completely different faults behind one message: nothing saved yet, a token that will
    not decrypt after a SECRET_KEY change, and a token that is fine but rejected by
    SendClean. Each needs a different fix, so each is reported separately here along
    with the live answer from the provider when a probe is possible.
    """
    creds = credentials(db)
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("owner_id", bool(creds["owner_id"]),
        "set" if creds["owner_id"] else
        "missing - Settings → SendClean owner ID (or SENDCLEAN_OWNER_ID)")
    add("api_token", bool(creds["token"]),
        ("set via " + creds["token_source"]) if creds["token"] else
        ("stored but cannot be decrypted - SECRET_KEY changed since it was saved; "
         "re-save the token, or set SENDCLEAN_API_TOKEN"
         if creds["token_undecryptable"] else
         "missing - Settings → SendClean API token (or SENDCLEAN_API_TOKEN)"))
    add("smtp_user_name", bool(creds["smtp_user_name"]),
        "set" if creds["smtp_user_name"] else
        "missing - required for sends, not for read-only calls "
        "(Settings → SendClean SMTP username, or SENDCLEAN_SMTP_USER_NAME)")

    result = {
        "provider": PROVIDER_KEY,
        "configured": configured(db),
        "token_source": creds["token_source"],
        "token_undecryptable": creds["token_undecryptable"],
        "smtp_user_name": creds["smtp_user_name"],
        "base_url": sendclean._base_url(),
        "checks": checks,
        "live": False,
        "account": {},
        "error": "",
        "next_step": "",
    }

    if not (creds["owner_id"] and creds["token"]):
        result["error"] = "credentials incomplete"
        result["next_step"] = (
            "Re-save the API token in Settings (a SECRET_KEY change makes the stored "
            "copy undecryptable), or set SENDCLEAN_OWNER_ID and SENDCLEAN_API_TOKEN on "
            "the deployment."
        )
        return result

    # A real probe: the account endpoint is the documented credential-validation call.
    import time
    started = time.time()
    try:
        payload = sendclean.account(creds["owner_id"], creds["token"])
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:300]
        result["latency_ms"] = int((time.time() - started) * 1000)
        text = str(exc).lower()
        if "401" in text or "unauthor" in text or "token" in text:
            result["next_step"] = "SendClean rejected the credentials - re-issue the API token."
        elif "connection failed" in text or "timed out" in text:
            result["next_step"] = "Network/DNS to api.sendclean.net failed from this deployment."
        else:
            result["next_step"] = "SendClean returned an error - see `error`."
        return result

    user = payload.get("user_data") or {}
    result.update({
        "live": True,
        "latency_ms": int((time.time() - started) * 1000),
        "account": {
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "reputation": user.get("reputation", ""),
            "allowed_speed": user.get("allowed_speed", ""),
        },
        "next_step": "" if creds["smtp_user_name"] else
                      "Connection is live, but sends will be blocked until the SMTP "
                      "username is set.",
    })
    return result


def overview(db):
    """Live SendClean account, SMTP users, domains and webhooks."""
    creds = credentials(db)
    if not (creds["owner_id"] and creds["token"]):
        return {"live": False, "error": "SendClean credentials are not configured"}

    def safe(operation, **fields):
        try:
            return sendclean.settings_call(operation, creds["owner_id"], creds["token"], **fields)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:300]}

    try:
        account = sendclean.account(creds["owner_id"], creds["token"]).get("user_data") or {}
    except Exception as exc:  # noqa: BLE001
        return {"live": False, "error": str(exc)[:300]}

    smtp = safe("listSmtp")
    sending = safe("listSendingDomain")
    tracking = safe("listTrackingDomain")
    hooks = safe("listWebhook")

    def rows(payload, *keys):
        """Pull a list out of a SendClean settings payload and redact secrets.

        SendClean returns SMTP passwords and webhook signing keys inline, so every
        row is scrubbed before it can reach an API response.
        """
        found = []
        if isinstance(payload, list):
            found = payload
        elif isinstance(payload, dict):
            for candidate in keys + ("data", "list", "items"):
                value = payload.get(candidate)
                if isinstance(value, list):
                    found = value
                    break
        return [_redact(row) for row in found]

    return {
        "live": True,
        "account": {
            "name": account.get("name", ""),
            "email": account.get("email", ""),
            "mobile": account.get("mobile", ""),
            "create_date": account.get("create_date", ""),
            "allowed_speed": account.get("allowed_speed", ""),
            "reputation": account.get("reputation", ""),
        },
        "smtp_users": rows(smtp, "smtp_list"),
        "sending_domains": rows(sending, "sending_domain_list", "domain_list"),
        "tracking_domains": rows(tracking, "tracking_domain_list", "domain_list"),
        "webhooks": rows(hooks, "webhook_list"),
        "smtp_user_name": creds["smtp_user_name"],
        "errors": {
            name: payload.get("error")
            for name, payload in (("smtp", smtp), ("sending_domains", sending),
                                  ("tracking_domains", tracking), ("webhooks", hooks))
            if isinstance(payload, dict) and payload.get("error")
        },
    }


def message_info(db, x_unique_id, skip_page=None):
    require_capability(db, "message_lookup")
    creds = credentials(db)
    result = sendclean.get_message_info(creds["owner_id"], creds["token"], x_unique_id, skip_page)
    return {
        "x_unique_id": x_unique_id,
        "messages": result.get("message_data") or [],
        "status": result.get("status", ""),
    }


def check_sending_domain(db, domain):
    require_capability(db, "sending_domains")
    creds = credentials(db)
    return sendclean.settings_call(
        "checkSendingDomain", creds["owner_id"], creds["token"], domain=domain
    )


def status(db):
    creds = credentials(db)
    return {
        "active_provider": PROVIDER_KEY,
        "label": "SendClean",
        "configured": configured(db),
        "smtp_user_name": creds["smtp_user_name"],
        "capabilities": sorted(PROVIDERS[PROVIDER_KEY]["capabilities"]),
        "marketing_campaigns_supported": False,
        "note": (
            "SendClean provides transactional sends, template sends, message lookup, "
            "SMTP-user, sending/tracking domain and webhook administration. "
            "Audience lists, marketing campaigns, automations, per-recipient event streams "
            "and aggregate reporting are not part of the documented API and are blocked."
        ),
    }
