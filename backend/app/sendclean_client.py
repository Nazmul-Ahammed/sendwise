"""Small, server-side client for SendClean's transactional email API.

SendClean does not expose audience or marketing-campaign management in the
supplied API documentation.  This client therefore models only documented
operations and must not be used as a fallback for operations SendClean does
not provide.
"""
import json
import os
import urllib.error
import urllib.request


DEFAULT_BASE_URL = "https://api.sendclean.net/v1.0"


class SendCleanError(RuntimeError):
    """A transport or application-level SendClean API error."""


def _base_url():
    return os.getenv("SENDCLEAN_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _request(path, owner_id, token, payload=None, timeout=40):
    if not str(owner_id or "").strip() or not str(token or "").strip():
        raise SendCleanError("SendClean owner ID and API token are required")
    data = {"owner_id": str(owner_id).strip(), "token": str(token).strip()}
    if payload:
        data.update(payload)
    request = urllib.request.Request(
        _base_url() + path,
        data=json.dumps(data).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SendCleanError(f"SendClean API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SendCleanError(f"SendClean API connection failed: {exc.reason}") from exc
    try:
        result = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise SendCleanError("SendClean API returned invalid JSON") from exc
    if str(result.get("status", "")).lower() in {"error", "failed", "failure"}:
        name = result.get("name") or result.get("type") or "API error"
        message = result.get("message") or "Request failed"
        raise SendCleanError(f"{name}: {message}")
    return result


def account(owner_id, token):
    """Read account details; safest documented credential-validation call."""
    return _request("/account/viewUserDetail", owner_id, token)


def send_mail(owner_id, token, smtp_user_name, message):
    if not str(smtp_user_name or "").strip():
        raise SendCleanError("SendClean SMTP username is required")
    return _request(
        "/messages/sendMail",
        owner_id,
        token,
        {"smtp_user_name": str(smtp_user_name).strip(), "message": message},
    )


def send_template(owner_id, token, smtp_user_name, message):
    if not str(smtp_user_name or "").strip():
        raise SendCleanError("SendClean SMTP username is required")
    return _request(
        "/messages/sendTemplate",
        owner_id,
        token,
        {"smtp_user_name": str(smtp_user_name).strip(), "message": message},
    )


def get_message_info(owner_id, token, x_unique_id, skip_page=None, timeout=40):
    """Per-message delivery/open/click status.

    `timeout` is exposed because this endpoint has been seen to hang for minutes rather
    than fail: the default 40s is fine for one interactive lookup, but a caller that
    loops over many sends (the sequence dispatcher) must bound each call or a single
    slow provider stalls the whole batch.
    """
    payload = {"x_unique_id": str(x_unique_id).strip()}
    if skip_page is not None:
        payload["skip_page"] = int(skip_page)
    return _request("/messages/getMessageInfo", owner_id, token, payload, timeout=timeout)


def settings_call(operation, owner_id, token, **fields):
    """Call a documented settings operation without exposing credentials."""
    allowed = {
        "addSmtp", "editSmtp", "resetSmtpPassword", "listSmtp",
        "addSendingDomain", "checkSendingDomain", "verifySendingDomain",
        "listSendingDomain", "deleteSendingDomain", "addTrackingDomain",
        "checkTrackingDomain", "listTrackingDomain", "deleteTrackingDomain",
        "addWebhook", "editWebhook", "keyResetWebhook", "listWebhook",
        "getWebhookinfo", "deleteWebhook",
    }
    if operation not in allowed:
        raise SendCleanError(f"Unsupported SendClean settings operation: {operation}")
    return _request(f"/settings/{operation}", owner_id, token, fields)
