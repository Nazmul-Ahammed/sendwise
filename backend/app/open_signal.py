"""Separate a real human open from a machine that fetched the tracking pixel.

SendClean counts a pixel fetch as an open. That is not the same as a person reading
the email. The moment a message lands, Gmail fetches every image through its own
proxy to cache it, and Yahoo, Outlook and corporate mail scanners do comparable
things. The pixel fires, SendClean records an open, and nobody has looked at it.

Measured on this account, 14 of 17 open events were Google proxy fetches. One real
send recorded "opened 18 seconds after delivery" for five recipients at once.

Why this is not merely a reporting nicety
-----------------------------------------
`sequence_engine` routes a contact on `opens > 0`: an opener gets the "opened"
branch, everyone else gets the "not opened" branch. If a proxy fetch counts as an
open, then for Gmail recipients - which prefetch essentially always - the
non-opener branch is unreachable and the non-opener copy is never sent. The
branching silently inverts for the single largest mail provider.

How we tell them apart
----------------------
The proxy identifies itself. `openData.ua` carries one User-Agent per open event,
and the proxies use their own unmistakable strings:

    Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 (via ggpht.com GoogleImageProxy)

We classify on that evidence rather than on timing, because timing alone cannot
separate a fast reader from a prefetch. And we only discount an open when we can
*prove* it was a machine: an open with no evidence either way still counts as human,
so this never quietly deletes engagement we simply could not classify.

Known limit, and how far we close it: Apple Mail Privacy Protection fetches through
Apple's own proxy, and it presents an ordinary Safari User-Agent, so it cannot be
recognised by string. It can still be recognised by *address* - Apple serves that
proxy from its own network, and a real reader's device never fetches a pixel from
Apple's datacenter. `is_proxy_ip` covers that case. What remains unclassifiable is
narrow, but not zero, so an open rate is still an upper bound rather than a fact.
"""

# Substrings that identify a machine fetching the pixel rather than a person reading.
# Kept explicit and narrow: a false positive here would discard a real open.
_MACHINE_MARKERS = (
    # Gmail / Google cache. "ggpht.com" is Google's image host; the UA also claims
    # Firefox 11 on Windows XP, a combination that has not been current since 2012.
    "googleimageproxy",
    "ggpht.com",
    # Yahoo's equivalent
    "yahoomailproxy",
    # Security scanners and link/image sandboxes that fetch on delivery
    "proofpoint",
    "barracuda",
    "mimecast",
    "symantec",
    "forcepoint",
    "ironport",
    "cloudmark",
    # Generic crawlers
    "bot",
    "crawler",
    "spider",
)

# Networks that fetch mail images on behalf of the recipient rather than for them.
# Matching is on the leading octets, so these are deliberately coarse: each one is a
# block that is not handed out to end users.
#
#   17.        Apple. Prefetch under Mail Privacy Protection and iCloud Private Relay.
#              This is the case the User-Agent cannot catch, because Apple sends an
#              ordinary Safari string. A person's own device does not fetch a pixel
#              from Apple's network, so an open from here is Apple, not a reader.
#   66.249.    Google's image proxy (also identified by User-Agent).
#   66.102.    Google's fetcher range.
#   209.85.    Google's fetcher range.
_PROXY_IP_PREFIXES = ("17.", "66.249.", "66.102.", "209.85.")


def _open_data(row):
    """The openData block, or an empty dict. SendClean omits it on some rows."""
    if not isinstance(row, dict):
        return {}
    od = row.get("openData")
    return od if isinstance(od, dict) else {}


def _user_agents(row):
    """One User-Agent per open event, in the order the opens arrived."""
    uas = _open_data(row).get("ua")
    if isinstance(uas, (list, tuple)):
        return [str(u) for u in uas if u]
    if uas:
        return [str(uas)]
    return []


def _addresses(row):
    """One source IP per open event, in the order the opens arrived."""
    ips = _open_data(row).get("ip")
    if isinstance(ips, (list, tuple)):
        return [str(i) for i in ips if i]
    if ips:
        return [str(ips)]
    return []


def is_machine_ua(ua):
    """True when this User-Agent belongs to a proxy, scanner or crawler."""
    low = str(ua or "").lower()
    if not low:
        return False
    return any(marker in low for marker in _MACHINE_MARKERS)


def is_proxy_ip(ip):
    """True when this address belongs to a network that prefetches on the reader's behalf.

    The decisive case is Apple: Mail Privacy Protection sends an ordinary Safari
    User-Agent, so `is_machine_ua` cannot see it, but the request arrives from Apple's
    own network rather than from the recipient's device.
    """
    value = str(ip or "").strip()
    if not value:
        return False
    return value.startswith(_PROXY_IP_PREFIXES)


def _event_is_machine(ua, ip):
    """One open event: blame the machine if either the agent or the address says so."""
    return is_machine_ua(ua) or is_proxy_ip(ip)


def opens_breakdown(row):
    """Split a SendClean row's opens into human, machine and unclassified.

    ``open`` is a count; ``openData.ua`` and ``openData.ip`` describe each event.
    Pairing them means a proxy is caught by whichever signal it failed to hide - the
    Google proxy by its User-Agent, Apple's by its address.

    Events SendClean counted but did not describe are left on the human side on
    purpose: we discount only what we can prove, so this never silently deletes
    engagement we simply could not classify.
    """
    try:
        total = int((row or {}).get("open") or 0)
    except (TypeError, ValueError):
        total = 0

    uas = _user_agents(row)
    ips = _addresses(row)
    described = max(len(uas), len(ips))

    def at(seq, i):
        return seq[i] if i < len(seq) else ""

    machine = sum(1 for i in range(described) if _event_is_machine(at(uas, i), at(ips, i)))
    unclassified = max(0, total - described)
    human = max(0, total - machine)

    return {
        "opens": total,
        "human_opens": human,
        "machine_opens": machine,
        "unclassified_opens": unclassified,
        "only_machine": total > 0 and human == 0,
    }


def human_opens(row):
    """Opens attributable to a person. Use this for decisions, not `open`."""
    return opens_breakdown(row)["human_opens"]


def clicks(row):
    """Clicks are the dependable signal and are never discounted."""
    try:
        return int((row or {}).get("click") or 0)
    except (TypeError, ValueError):
        return 0


def engagement(row):
    """The decision-grade reading of one SendClean row: human opens plus clicks.

    Returns ``human_opens`` for branch decisions and both raw counts so a caller can
    still show what the provider claimed.
    """
    b = opens_breakdown(row)
    return {
        "human_opens": b["human_opens"],
        "clicks": clicks(row),
        "raw_opens": b["opens"],
        "machine_opens": b["machine_opens"],
    }


def summarise(rows):
    """Aggregate a list of SendClean rows into report-grade counters.

    ``unique_openers`` counts people who genuinely opened. Proxy-only fetches are
    reported separately as ``proxy_only_openers`` rather than folded into the open
    rate, so the number the CMO reads is the number that means something.
    """
    unique_openers = 0
    proxy_only_openers = 0
    clickers = 0
    total_clicks = 0
    machine_opens = 0

    for row in rows:
        if not row:
            continue
        b = opens_breakdown(row)
        clk = clicks(row)
        machine_opens += b["machine_opens"]
        total_clicks += clk
        if clk > 0:
            clickers += 1
        if b["human_opens"] > 0:
            unique_openers += 1
        elif b["only_machine"]:
            proxy_only_openers += 1

    return {
        "unique_openers": unique_openers,
        "proxy_only_openers": proxy_only_openers,
        "unique_clickers": clickers,
        "clicks": total_clicks,
        "machine_opens": machine_opens,
    }


# ------------------------------------------------- decoding our own send ids
#
# Every send carries an X-Unique-Id we generate, and the provider echoes it back on
# tracking events, so the id is how an event is tied to a send and an email. The two
# shapes in use:
#
#     sequence   sw-seq-<campaign-slug>-<ref>-<random hex>   sw-seq-final-travilo-E1-736a4ffd3a
#     one-off    sw-<campaign-slug>-<epoch>-<index>          sw-travilo-2026-1790686984-003
#
# Slugs contain hyphens and digits, so the parts cannot simply be split on "-": the
# patterns are anchored and matched from the right instead. The trailing random part is
# deliberately NOT restricted to hex: a live send uses `uuid4().hex`, but anything that
# generates a different suffix would otherwise fail to parse and the event would be filed
# under an empty campaign with no error anywhere - which is exactly what happened to a
# test id ending in "cachetest".
import re as _re

_SEQ_ID = _re.compile(r"^sw-seq-(?P<slug>.+)-(?P<ref>E\d+)-(?P<rand>[A-Za-z0-9]{4,64})$")
_CMP_ID = _re.compile(r"^sw-(?P<slug>.+)-(?P<epoch>\d{9,12})-(?P<idx>\d{1,4})$")
_TEST_ID = _re.compile(r"^sw-test-(?P<slug>.+)-(?P<epoch>\d{9,12})$")
_OTHER_ID = _re.compile(r"^sw-(?P<kind>mcp|api)-(?P<epoch>\d{9,12})$")


def parse_x_unique_id(x_unique_id):
    """Split an X-Unique-Id into its campaign and email reference.

    Returns ``{"slug": ..., "ref": ..., "kind": ...}`` with empty strings for whatever
    cannot be determined. Never raises: an unrecognised id is a normal outcome, since
    test sends and ad-hoc API sends share the same namespace, and losing an event
    because its id looked unfamiliar would be worse than storing it unattributed.
    """
    value = str(x_unique_id or "").strip()
    out = {"slug": "", "ref": "", "kind": ""}
    if not value:
        return out

    m = _SEQ_ID.match(value)
    if m:
        out.update(slug=m.group("slug"), ref=m.group("ref").upper(), kind="sequence")
        return out

    m = _CMP_ID.match(value)
    if m:
        out.update(slug=m.group("slug"), kind="campaign")
        return out

    m = _TEST_ID.match(value)
    if m:
        out.update(slug=m.group("slug"), kind="test")
        return out

    m = _OTHER_ID.match(value)
    if m:
        out.update(kind=m.group("kind"))
        return out

    return out


def machine_reason(ua, ip):
    """Why an event was classed as a machine, in words, for the audit trail."""
    reasons = []
    low = str(ua or "").lower()
    for marker in _MACHINE_MARKERS:
        if marker in low:
            reasons.append(f"agent:{marker}")
            break
    if is_proxy_ip(ip):
        reasons.append(f"network:{str(ip).split('.')[0]}.x")
    return ",".join(reasons)
