"""Email address validation.

Does the checks that actually predict a bounce, and labels them honestly:

  syntax       is the address well-formed at all?
  mx           does the domain publish MX (or A) records, i.e. can it receive mail?
  disposable   throwaway mailbox providers (mailinator, yopmail, …)
  role         info@, sales@, admin@ - deliverable, but usually not a person

Resolution goes over DNS-over-HTTPS, so no DNS library is required. Lookups are
cached per process: verifying 200 contacts on the same domain makes one request.

A result is deliberately conservative. `unknown` means the lookup failed and is
NOT treated as invalid - a transient DNS failure must never silently drop a real
recipient. Only `invalid` and `no_mx` are hard problems.
"""
import json
import re
import time
import urllib.parse
import urllib.request

# Two independent resolvers; the second is only used if the first errors.
DOH_ENDPOINTS = (
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
)
_DOH_TIMEOUT = 6
_MX_CACHE: dict[str, tuple[str, object, float]] = {}
_MX_TTL = 6 * 60 * 60  # seconds

SYNTAX_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")

# Throwaway / temporary mailbox providers. Not exhaustive - a signal, not a verdict.
DISPOSABLE_DOMAINS = {
    "mailinator.com", "yopmail.com", "yopmail.fr", "guerrillamail.com", "guerrillamail.net",
    "sharklasers.com", "grr.la", "guerrillamailblock.com", "pokemail.net", "spam4.me",
    "10minutemail.com", "10minutemail.net", "tempmail.com", "temp-mail.org", "tempmail.net",
    "throwawaymail.com", "trashmail.com", "trashmail.de", "getnada.com", "nada.email",
    "dispostable.com", "maildrop.cc", "mailnesia.com", "mintemail.com", "mohmal.com",
    "fakeinbox.com", "mailcatch.com", "spamgourmet.com", "mytemp.email", "tempr.email",
    "discard.email", "emailondeck.com", "burnermail.io", "inboxkitten.com", "33mail.com",
    "tempinbox.com", "mail-temp.com", "mailtemp.info", "throwaway.email", "mailnator.com",
}

# Shared/role mailboxes - legitimate, but reaching a human is less likely.
ROLE_PREFIXES = {
    "info", "sales", "support", "admin", "administrator", "contact", "hello", "help",
    "billing", "accounts", "accounting", "office", "enquiries", "enquiry", "inquiries",
    "inquiry", "marketing", "hr", "careers", "jobs", "team", "mail", "email", "webmaster",
    "postmaster", "hostmaster", "abuse", "noc", "security", "legal", "privacy", "no-reply",
    "noreply", "donotreply", "do-not-reply", "customerservice", "service", "orders",
}

CONSUMER_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "ymail.com",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com", "aol.com",
    "icloud.com", "me.com", "mac.com", "protonmail.com", "proton.me", "gmx.com", "gmx.net",
    "mail.com", "zoho.com", "yandex.com", "rediffmail.com", "fastmail.com", "tutanota.com",
}


def _doh(name: str, rtype: str):
    """Query DNS-over-HTTPS. Returns the parsed JSON, or raises.

    A response is only returned when the resolver actually gave a verdict. DNS-over-HTTPS
    answers carry a `Status` field, and a non-zero status is NOT "no records" - it is the
    resolver saying it could not answer. `Answer: []` with `Status: 2` (SERVFAIL) means
    "try again later"; read as an empty answer it becomes "this domain has no mail server",
    which is a permanent-looking verdict on a temporary failure.

    That distinction matters downstream: an address classed `no_mx` is dropped from every
    send, stored on the contact, and cached for hours. A resolver hiccup would therefore
    silently remove a real recipient, with nothing anywhere recording why.

    Status 0 (NOERROR) and 3 (NXDOMAIN) are real answers and are returned. Everything else
    raises, so the caller records it as `unknown` - which is never dropped.
    """
    last = None
    for endpoint in DOH_ENDPOINTS:
        url = f"{endpoint}?{urllib.parse.urlencode({'name': name, 'type': rtype})}"
        req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
        try:
            with urllib.request.urlopen(req, timeout=_DOH_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001 - try the next resolver
            last = exc
            continue
        status = data.get("Status")
        if status in (0, 3):
            return data
        last = RuntimeError(f"resolver returned DNS status {status}")
    raise RuntimeError(f"DNS lookup failed for {name}/{rtype}: {last}")


def resolve_mail_host(domain: str):
    """(status, host_or_reason) where status is 'ok', 'no_mx' or 'unknown'."""
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        return "no_mx", "empty domain"
    cached = _MX_CACHE.get(domain)
    if cached and (time.time() - cached[2]) < _MX_TTL:
        return cached[0], cached[1]
    try:
        data = _doh(domain, "MX")
        answers = [a for a in (data.get("Answer") or []) if a.get("type") == 15]
        hosts = []
        for a in answers:
            # "10 mx1.example.com." -> "mx1.example.com"
            parts = str(a.get("data", "")).split()
            host = (parts[-1] if parts else "").rstrip(".")
            if host:
                hosts.append(host)
        if hosts:
            result = ("ok", sorted(hosts)[0])
        else:
            # No MX is not always fatal: an A/AAAA record means implicit MX (RFC 5321).
            a = _doh(domain, "A")
            if [x for x in (a.get("Answer") or []) if x.get("type") == 1]:
                result = ("ok", domain)
            else:
                result = ("no_mx", "domain publishes no MX or A record")
    except Exception as exc:  # noqa: BLE001 - a lookup failure is not a verdict
        result = ("unknown", str(exc)[:160])
    _MX_CACHE[domain] = (result[0], result[1], time.time())
    return result


def classify(email: str) -> dict:
    """Validate one address. Never raises."""
    raw = str(email or "").strip()
    addr = raw.lower()
    out = {
        "email": addr,
        "status": "unknown",
        "reason": "",
        "domain": "",
        "mx_host": "",
        "checks": {"syntax": False, "mx": False, "disposable": False, "role": False, "consumer": False},
    }
    if not addr:
        out.update(status="invalid", reason="empty address")
        return out
    if len(addr) > 254 or not SYNTAX_RE.match(addr):
        out.update(status="invalid", reason="not a valid email shape")
        return out

    out["checks"]["syntax"] = True
    local, _, domain = addr.rpartition("@")
    out["domain"] = domain
    out["checks"]["disposable"] = domain in DISPOSABLE_DOMAINS
    out["checks"]["role"] = local.split("+")[0] in ROLE_PREFIXES
    out["checks"]["consumer"] = domain in CONSUMER_DOMAINS

    state, detail = resolve_mail_host(domain)
    if state == "ok":
        out["checks"]["mx"] = True
        out["mx_host"] = str(detail)
    elif state == "no_mx":
        out.update(status="no_mx", reason=str(detail))
        return out
    else:
        out.update(status="unknown", reason=f"could not verify this domain ({detail})")
        return out

    if out["checks"]["disposable"]:
        out.update(status="disposable", reason="throwaway mailbox provider")
    elif out["checks"]["role"]:
        out.update(status="role", reason="shared/role mailbox, not a person")
    else:
        out.update(status="valid", reason="syntax and mail server both check out")
    return out


def validate_many(emails) -> list:
    """Classify a batch, resolving each unique domain once."""
    seen = {}
    out = []
    for raw in emails or []:
        addr = str(raw or "").strip().lower()
        if addr in seen:
            out.append(dict(seen[addr]))
            continue
        res = classify(addr)
        seen[addr] = res
        out.append(dict(res))
    return out


def summarise(results) -> dict:
    counts = {}
    for r in results or []:
        counts[r.get("status", "unknown")] = counts.get(r.get("status", "unknown"), 0) + 1
    return {
        "total": len(results or []),
        "counts": counts,
        # Only these two are hard blockers; everything else is advisory.
        "blocked": counts.get("invalid", 0) + counts.get("no_mx", 0),
        "risky": counts.get("disposable", 0) + counts.get("role", 0),
        "unknown": counts.get("unknown", 0),
    }
