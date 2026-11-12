"""Open and click tracking that Sendwise owns end to end.

Why this exists
---------------
We were reading engagement from SendClean, by two routes, and both failed:

  * `messages/getMessageInfo` hangs rather than answers (30s+, every call, while every
    other endpoint of theirs answers in ~0.2s).
  * Their webhook calls our URL at registration and gets a 200, but has never once
    delivered an event - not even `delivered`, across many sends.

With both dead, the open rate, the click rate and every sequence branch had nothing to
work from, and a sequence could not safely advance at all.

So the tracking is ours now. Outgoing mail carries:

  * a 1x1 pixel served from this app, requested when the message renders with images on
  * every links rewritten through a redirect on this app

Both write straight into `engagement_events`, the same table the webhook was meant to
fill, so the reports, the live engagement table and the sequence dispatcher keep reading
one source - and that source no longer depends on the provider answering.

What this does not fix
----------------------
A provider's image proxy still fetches the pixel, so a fetch is still not proof that a
person read the message; the same `open_signal` rules classify it, and the same caveat
applies. Apple Mail Privacy Protection still presents an ordinary browser agent. Clicks
remain the dependable signal.
"""
import base64
import hashlib
import hmac
import os
import re

# 1x1 transparent GIF - the smallest thing a mail client will fetch and not display.
PIXEL = base64.b64decode(
    b"R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text):
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sign(payload, secret):
    """URL-safe signed token carrying `payload`, so a recipient cannot forge one.

    Short on purpose: it appears in every link, and a long token makes the mail look
    suspicious. 12 bytes of MAC is ample for a value that only has to be unguessable
    within the life of a campaign.
    """
    body = _b64e(str(payload).encode())
    mac = hmac.new(str(secret).encode(), body.encode(), hashlib.sha256).digest()[:12]
    return f"{body}.{_b64e(mac)}"


def verify(token, secret):
    """Return the payload, or None when the token is malformed or the signature is off."""
    try:
        body, mac = str(token).split(".", 1)
    except (ValueError, AttributeError):
        return None
    expected = hmac.new(str(secret).encode(), body.encode(), hashlib.sha256).digest()[:12]
    if not hmac.compare_digest(_b64e(expected), mac):
        return None
    try:
        return _b64d(body).decode()
    except Exception:  # noqa: BLE001 - a corrupt token is simply not valid
        return None


# Field separator inside the signed payload. It must be a character that can never
# appear in an X-Unique-Id or an email address: "|" was used before, and a campaign
# slug that legitimately contained "|" split the payload in the wrong place, which
# filed every open under a mangled email and an empty campaign. U+001F cannot occur
# in either field, so the split is unambiguous.
FIELD_SEP = "\x1f"


def make_token(x_unique_id, email):
    """One token per (send, recipient). Encodes both so a hit needs no lookup."""
    return f"{x_unique_id}{FIELD_SEP}{email}"


def read_token(payload):
    """Split a verified payload back into (x_unique_id, email).

    Legacy tokens (minted before FIELD_SEP) joined the two fields with "|" and then
    split on the FIRST "|", so any slug containing "|" truncated the id and put slug
    fragments into the email. Emails can never contain "|", so rsplit is the correct
    parse for those already-delivered messages and repairs them on read.
    """
    text = str(payload or "")
    if FIELD_SEP in text:
        xid, _, email = text.partition(FIELD_SEP)
    else:
        xid, _, email = text.rpartition("|")
    return xid.strip(), email.strip().lower()


SECRET_SETTING = "tracking_secret"


def secret_for(db):
    """The signing key for tracking tokens, created once and then reused.

    Stable across restarts on purpose: a token sits inside an email and has to stay valid
    for as long as somebody might open it, which is far longer than a process lives.
    """
    from . import models

    row = (db.query(models.Setting)
             .filter(models.Setting.key == SECRET_SETTING).first())
    if row and (row.value or "").strip():
        return row.value
    secret = _b64e(os.urandom(32))
    if row:
        row.value = secret
    else:
        db.add(models.Setting(key=SECRET_SETTING, value=secret))
    db.commit()
    return secret


def instrument_for(db, html, x_unique_id, email, base):
    """Instrument an outgoing message, or return it untouched if that is not possible.

    Never raises. A send must not fail because tracking could not be attached: no email
    is worse than an unmeasured one.
    """
    try:
        token = sign(make_token(x_unique_id, email), secret_for(db))
        return instrument(html, base, token)
    except Exception:  # noqa: BLE001
        return html


# ------------------------------------------------------------------ URLs

def open_url(base, token):
    return f"{str(base).rstrip('/')}/api/track/o/{token}.gif"


def click_url(base, token, target):
    from urllib.parse import quote
    return f"{str(base).rstrip('/')}/api/track/c/{token}?u={quote(str(target), safe='')}"


# Links that must never be rewritten through a redirect:
#   mailto:/tel:  are not navigations
#   #...          is an in-page anchor
#   unsubscribe   must work even if tracking is broken, so it stays direct
_SKIP = re.compile(r"^(?:mailto:|tel:|#|javascript:|\s*$)", re.I)


def rewrite_links(html, base, token, skip_contains=("unsubscribe",)):
    """Send every link through our redirect, except the ones that must stay direct.

    A click is the only dependable engagement signal we have, so it is worth capturing;
    but an unsubscribe that depends on a redirect is a compliance risk, and one that
    depends on this app being reachable is worse. Those are left exactly as they are.
    """
    def swap(match):
        quote_char, target = match.group(1), match.group(2)
        if _SKIP.match(target):
            return match.group(0)
        low = target.lower()
        if any(s in low for s in skip_contains):
            return match.group(0)
        if "/api/track/" in low:
            return match.group(0)
        return f"href={quote_char}{click_url(base, token, target)}{quote_char}"

    return re.sub(r'href=(["\'])(.*?)\1', swap, html, flags=re.I | re.S)


def inject_pixel(html, base, token):
    """Append the tracking pixel just before </body>, or at the end when there is none."""
    tag = (f'<img src="{open_url(base, token)}" width="1" height="1" alt="" '
           f'style="display:block;width:1px;height:1px;border:0;outline:none;" />')
    if re.search(r"</body\s*>", html, re.I):
        return re.sub(r"</body\s*>", tag + "</body>", html, count=1, flags=re.I)
    return html + tag


def instrument(html, base, token):
    """Everything an outgoing message needs to report its own opens and clicks."""
    return inject_pixel(rewrite_links(html, base, token), base, token)


def _is_public(ip):
    """True for an address that can belong to a real client on the internet."""
    value = str(ip or "").strip()
    if not value or ":" in value:            # IPv6 left alone rather than mis-parsed
        return bool(value)
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a in (10, 127, 0):
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    if a == 192 and b == 168:
        return False
    if a == 169 and b == 254:                # link-local
        return False
    return True


def client_ip(request):
    """The recipient's address, preferring a routable one.

    Every request reaches us through the Openship edge, so the socket address is always
    the proxy; the forwarded headers are the only place the real client address survives.

    The chain is read left to right and the first PUBLIC address is taken, because an
    intermediate hop can contribute a private one. A Gmail fetch was recorded as
    `10.209.99.254` for exactly that reason, which makes the address useless for the
    network half of the machine check and for any later investigation. If every entry is
    private the first is returned anyway - a private address is still more informative
    than none.
    """
    for header in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        value = request.headers.get(header)
        if not value:
            continue
        hops = [h.strip() for h in str(value).split(",") if h.strip()]
        for hop in hops:
            if _is_public(hop):
                return hop
        if hops:
            return hops[0]
    return (request.client.host if request.client else "") or ""
