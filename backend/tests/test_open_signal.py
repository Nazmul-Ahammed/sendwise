"""A provider's image proxy must never be mistaken for a reader.

The bug this locks down: SendClean (like every pixel tracker) counts a fetch of the
tracking image as an "open". Gmail caches every image through its own proxy the
moment a message is delivered, so a delivered-but-unread email is recorded as
opened. Because the sequence engine routes on `opens > 0`, that sent non-openers
down the "opened" branch - for Gmail recipients, effectively always.

The regression test is the real User-Agent string captured from this account.
"""
import pytest

from app import open_signal
from app.sequence_engine import decide_next_ref

# Captured verbatim from SendClean's openData for mubasshir@ibos.io. Gmail's image
# proxy has presented this same string for years.
GMAIL_PROXY_UA = (
    "Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 "
    "(via ggpht.com GoogleImageProxy)"
)
# A genuine open: a real handset on a Bangladeshi mobile IP.
REAL_ANDROID_UA = "Dalvik/2.1.0 (Linux; U; Android 16; V2527A Build/BP2A.250605.031.A3_V000L1)"


def row(opens=0, clicks=0, uas=(), ips=()):
    return {
        "status": "delivered",
        "open": opens,
        "click": clicks,
        "openData": {"ua": list(uas), "ip": list(ips), "time": []},
    }


# --------------------------------------------------------------- classification

def test_gmail_proxy_ua_is_a_machine():
    assert open_signal.is_machine_ua(GMAIL_PROXY_UA) is True


def test_real_android_ua_is_not_a_machine():
    assert open_signal.is_machine_ua(REAL_ANDROID_UA) is False


@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (via ggpht.com GoogleImageProxy)",
    "YahooMailProxy; https://help.yahoo.com/kb/yahoo-mail-proxy",
    "Proofpoint Secure Email",
    "Barracuda Sentinel",
    "Mimecast Image Fetch",
    "Googlebot/2.1",
])
def test_known_machines_are_caught(ua):
    assert open_signal.is_machine_ua(ua) is True


# ------------------------------------------------- Apple, caught by address not agent

def test_apple_proxy_ip_is_a_machine():
    """Mail Privacy Protection sends a normal Safari UA; the address gives it away."""
    assert open_signal.is_proxy_ip("17.58.12.9") is True
    assert open_signal.is_machine_ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15") is False


def test_apple_open_with_safari_ua_is_not_counted_as_human():
    """The gap the User-Agent alone could not close."""
    r = row(opens=1,
            uas=["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"],
            ips=["17.58.12.9"])
    assert open_signal.human_opens(r) == 0
    assert open_signal.opens_breakdown(r)["machine_opens"] == 1


def test_apple_ip_with_no_user_agent_is_still_caught():
    r = row(opens=2, uas=[], ips=["17.58.12.9", "17.58.12.10"])
    assert open_signal.human_opens(r) == 0
    assert open_signal.opens_breakdown(r)["machine_opens"] == 2


def test_google_ip_with_no_user_agent_is_caught():
    r = row(opens=1, uas=[], ips=["66.249.84.33"])
    assert open_signal.human_opens(r) == 0


@pytest.mark.parametrize("ip", ["17.58.12.9", "17.253.1.1", "66.249.84.33", "209.85.128.1"])
def test_proxy_ranges_detected(ip):
    assert open_signal.is_proxy_ip(ip) is True


@pytest.mark.parametrize("ip", [
    "37.111.212.33",     # the real Bangladeshi mobile this account actually saw
    "8.8.8.8",
    "203.0.113.10",
    "171.58.12.9",       # starts with 17 but is NOT in Apple's /8
    "",
    None,
])
def test_real_addresses_are_not_proxies(ip):
    assert open_signal.is_proxy_ip(ip) is False


def test_a_real_reader_on_apple_hardware_still_counts():
    """Owning a Mac is not a proxy. Only Apple's own network is."""
    r = row(opens=1, uas=[REAL_ANDROID_UA], ips=["37.111.212.33"])
    assert open_signal.human_opens(r) == 1


def test_gmail_only_opens_are_not_human_opens():
    """The exact case from production: 1 open, all proxy, must yield 0 human opens."""
    r = row(opens=1, uas=[GMAIL_PROXY_UA])
    assert open_signal.human_opens(r) == 0
    assert open_signal.opens_breakdown(r)["only_machine"] is True


def test_three_proxy_opens_are_not_human_opens():
    r = row(opens=3, uas=[GMAIL_PROXY_UA] * 3)
    assert open_signal.human_opens(r) == 0


def test_mixed_opens_keep_only_the_human_one():
    """4 opens, one of them a real client: 1 human, 3 machine."""
    r = row(opens=4, uas=[GMAIL_PROXY_UA, GMAIL_PROXY_UA, GMAIL_PROXY_UA, REAL_ANDROID_UA])
    b = open_signal.opens_breakdown(r)
    assert b["human_opens"] == 1
    assert b["machine_opens"] == 3
    assert b["only_machine"] is False


def test_unannotated_opens_stay_human():
    """No User-Agent means no evidence, so we must not silently discard the open."""
    r = row(opens=2, uas=[])
    b = open_signal.opens_breakdown(r)
    assert b["human_opens"] == 2
    assert b["machine_opens"] == 0
    assert b["unclassified_opens"] == 2


def test_partially_annotated_opens_stay_human():
    """2 opens counted, only 1 UA supplied: the unannotated one is not machine."""
    r = row(opens=2, uas=[GMAIL_PROXY_UA])
    b = open_signal.opens_breakdown(r)
    assert b["human_opens"] == 1
    assert b["machine_opens"] == 1


def test_clicks_are_never_discounted():
    r = row(opens=3, clicks=2, uas=[GMAIL_PROXY_UA] * 3)
    assert open_signal.clicks(r) == 2


def test_missing_row_is_safe():
    for bad in (None, {}, {"open": None}, {"open": "nonsense"}):
        assert open_signal.human_opens(bad) == 0
        assert open_signal.clicks(bad) == 0


def test_malformed_open_data_is_safe():
    assert open_signal.human_opens({"open": 1, "openData": "not-a-dict"}) == 1
    assert open_signal.human_opens({"open": 1, "openData": {"ua": None}}) == 1
    assert open_signal.human_opens({"open": 1, "openData": {"ua": "single"}}) == 1


# --------------------------------------------------------------- routing

TREE = {
    "E1": {"clicked": "E4", "opened": "E2", "not_opened": "E3"},
    "E2": None,
    "E3": None,
    "E4": None,
}


def test_proxy_open_routes_to_the_not_opened_branch():
    """THE REGRESSION. mubasshir@ibos.io got E2 because a proxy fetch counted."""
    r = row(opens=1, uas=[GMAIL_PROXY_UA])
    human = open_signal.human_opens(r)
    assert decide_next_ref("E1", human, open_signal.clicks(r), TREE) == "E3"


def test_real_open_still_routes_to_the_opened_branch():
    r = row(opens=1, uas=[REAL_ANDROID_UA])
    human = open_signal.human_opens(r)
    assert decide_next_ref("E1", human, open_signal.clicks(r), TREE) == "E2"


def test_click_wins_over_a_proxy_open():
    """5 proxy opens plus a real click: the click decides, not the pixels."""
    r = row(opens=5, clicks=1, uas=[GMAIL_PROXY_UA] * 5)
    human = open_signal.human_opens(r)
    assert human == 0
    assert open_signal.clicks(r) == 1
    assert decide_next_ref("E1", human, open_signal.clicks(r), TREE) == "E4"


def test_click_falls_back_when_the_email_declares_no_clicked_branch():
    """Precedence is clicked > opened > not_opened, and each step is optional.

    E1 here offers only two destinations, so a click must still land somewhere
    sensible rather than returning None and silently ending the journey.
    """
    two_way = {"E1": {"opened": "E2", "not_opened": "E3"}, "E2": None, "E3": None}
    r = row(opens=5, clicks=1, uas=[GMAIL_PROXY_UA] * 5)
    human = open_signal.human_opens(r)
    assert decide_next_ref("E1", human, open_signal.clicks(r), two_way) == "E3"
    # and a genuine open with no click still takes the opened branch
    r2 = row(opens=1, uas=[REAL_ANDROID_UA])
    assert decide_next_ref("E1", open_signal.human_opens(r2), 0, two_way) == "E2"


def test_raw_open_count_would_have_misrouted_but_human_does_not():
    """Guard on the old behaviour, so a future refactor cannot reintroduce it."""
    r = row(opens=1, uas=[GMAIL_PROXY_UA])
    assert int(r["open"]) > 0                                  # what we used to pass
    assert decide_next_ref("E1", int(r["open"]), 0, TREE) == "E2"   # the old, wrong answer
    assert decide_next_ref("E1", open_signal.human_opens(r), 0, TREE) == "E3"  # the fix


# --------------------------------------------------------------- aggregates

def test_summarise_separates_proxy_only_openers():
    rows = [
        row(opens=1, uas=[GMAIL_PROXY_UA]),          # proxy only  -> not an opener
        row(opens=4, uas=[GMAIL_PROXY_UA] * 4),      # proxy only  -> not an opener
        row(opens=1, uas=[REAL_ANDROID_UA]),         # real        -> opener
        row(opens=1, clicks=1, uas=[REAL_ANDROID_UA]),  # real + click -> opener
        row(opens=0),                                # untouched
    ]
    s = open_signal.summarise(rows)
    assert s["unique_openers"] == 2
    assert s["proxy_only_openers"] == 2
    assert s["unique_clickers"] == 1
    assert s["clicks"] == 1
    assert s["machine_opens"] == 5


def test_summarise_ignores_empty_rows():
    s = open_signal.summarise([None, {}, row(opens=0)])
    assert s["unique_openers"] == 0
    assert s["proxy_only_openers"] == 0


def test_error_rows_do_not_raise():
    s = open_signal.summarise([None, {"open": "x"}, {}])
    assert s["unique_openers"] == 0


# --------------------------------------------------------------- our own tracking

def test_tracking_token_round_trips():
    from app import tracking as t
    tok = t.sign(t.make_token("sw-seq-demo-E1-abc", "a@b.com"), "secret")
    assert t.read_token(t.verify(tok, "secret")) == ("sw-seq-demo-E1-abc", "a@b.com")


def test_tracking_token_rejects_a_wrong_secret():
    from app import tracking as t
    tok = t.sign(t.make_token("x", "a@b.com"), "secret")
    assert t.verify(tok, "other-secret") is None


def test_tracking_token_rejects_tampering():
    from app import tracking as t
    tok = t.sign(t.make_token("x", "a@b.com"), "secret")
    assert t.verify(tok[:-3] + "zzz", "secret") is None
    assert t.verify("not-a-token", "secret") is None


def test_links_are_rewritten_but_unsubscribe_is_not():
    """A click is the dependable signal, so links are wrapped.

    The unsubscribe link is the exception: it has to work even if tracking is broken, so
    it is never routed through a redirect.
    """
    from app import tracking as t
    tok = t.sign(t.make_token("x", "a@b.com"), "s")
    html = ('<a href="https://t.io/pricing?x=1">P</a>'
            '<a href="https://t.io/api/unsubscribe?token=z">U</a>')
    out = t.instrument(html, "https://sw.io", tok)
    assert "/api/track/c/" in out
    assert "t.io/pricing?x=1" not in out              # wrapped, so the raw url is gone
    assert "t.io/api/unsubscribe?token=z" in out      # left alone on purpose


def test_non_links_are_left_alone():
    from app import tracking as t
    tok = t.sign(t.make_token("x", "a@b.com"), "s")
    html = '<a href="mailto:a@b.com">M</a><a href="#top">T</a>'
    out = t.instrument(html, "https://sw.io", tok)
    assert 'href="mailto:a@b.com"' in out
    assert 'href="#top"' in out


def test_pixel_is_added_once_and_before_the_body_close():
    from app import tracking as t
    tok = t.sign(t.make_token("x", "a@b.com"), "s")
    out = t.instrument("<html><body>hi</body></html>", "https://sw.io", tok)
    assert out.count("/api/track/o/") == 1
    import re as _re
    assert _re.search(r"<img[^>]*/api/track/o/[^>]*>\s*</body>", out, _re.I)


def test_dispatcher_reads_only_our_own_tracker():
    """The provider's message lookup must not be consulted when branching.

    It hangs for 30s+ per call, so consulting it stalls the whole pass; and its numbers
    are what misrouted recipients in the first place. Branching reads our own records.
    """
    import inspect
    from app import sequence_engine as seq
    src = inspect.getsource(seq.dispatch)
    assert "get_message_info" not in src
    assert "_engagement_from_events" in src


def test_send_paths_turn_provider_tracking_off():
    """Two tracking systems would mean two disagreeing sets of numbers."""
    import inspect
    from app import sequence_engine as seq
    from app import routes
    assert '"X-Store-TrackOpens": "no"' in inspect.getsource(seq._send_one)
    assert '"X-Store-TrackOpens": "no"' in inspect.getsource(routes._sendclean_send)


def test_send_paths_instrument_their_html():
    import inspect
    from app import sequence_engine as seq
    from app import routes
    assert "instrument_for" in inspect.getsource(seq._send_one)
    assert "instrument_for" in inspect.getsource(routes.launch_campaign)

def test_send_id_parsing_tolerates_a_non_hex_suffix():
    """The trailing random part must not be required to be hex.

    A live send uses `uuid4().hex`, so hex was assumed. A test id ending in "cachetest"
    failed to parse, and the event was filed under an empty campaign with no error raised
    anywhere - silently invisible instead of loudly wrong.
    """
    from app.open_signal import parse_x_unique_id as p
    assert p("sw-seq-zz-cache-probe-E1-cachetest") == {
        "slug": "zz-cache-probe", "ref": "E1", "kind": "sequence"}
    # and the real shape still parses
    assert p("sw-seq-new-campaign-E1-14f214c9fd")["slug"] == "new-campaign"


def test_pixel_forbids_caching():
    """Repeat opens are only countable if the client re-asks for the image.

    We cannot force that, but we can at least not invite a cache to keep it.
    """
    import inspect
    from app import routes
    src = inspect.getsource(routes.track_open)
    assert "no-store" in src and "no-cache" in src

def test_branch_counts_a_proxy_load_as_an_open_by_default():
    """A Gmail read and a Gmail preload are fetched by the same proxy with the same agent.

    Treating that as "not a person" reported real readers as non-readers, and the branch
    rule then sent them the non-opener email. The default basis is therefore the raw load
    count - the same figure Mailchimp reports - so nobody who read is treated as not having.
    """
    from app import sequence_engine as seq
    eng = {"pixel_loads": 1, "human_opens": 0, "machine_opens": 1, "clicks": 0}
    assert seq.BRANCH_OPEN_BASIS == "pixel_loads"
    assert seq._opens_for_branch(eng) == 1


def test_branch_can_be_switched_to_the_strict_count():
    """The stricter reading stays reachable, so the choice is reversible without a deploy."""
    import importlib
    import os
    os.environ["SEQUENCE_BRANCH_OPEN_BASIS"] = "confirmed"
    from app import sequence_engine as seq
    importlib.reload(seq)
    try:
        eng = {"pixel_loads": 1, "human_opens": 0, "machine_opens": 1, "clicks": 0}
        assert seq.BRANCH_OPEN_BASIS == "confirmed"
        assert seq._opens_for_branch(eng) == 0
    finally:
        os.environ.pop("SEQUENCE_BRANCH_OPEN_BASIS", None)
        importlib.reload(seq)


def test_engagement_from_events_reports_both_counts():
    import inspect
    from app import sequence_engine as seq
    src = inspect.getsource(seq._engagement_from_events)
    for key in ("pixel_loads", "human_opens", "machine_opens", "clicks"):
        assert key in src

# ---------------------------------------------------------------------------------
# The test that was missing. Everything above checks that the CLASSIFIER works; none of
# it checks that a real reader ends up in the right BRANCH. That gap is why 24 passing
# tests sat on top of a routing bug.
#
# This is the exact production case: a Gmail reader whose only recorded event is a fetch
# by Google's image proxy, carrying the agent `GoogleImageProxy`. It is indistinguishable
# from a delivery preload, so classifying it as "machine" is defensible - but acting on
# that classification is not, because it routes a reader down the non-opener path.
# ---------------------------------------------------------------------------------

GMAIL_READER_EVENT = {
    "event_type": "open",
    "is_machine": True,                     # Google's proxy fetched it
    "machine_reason": "agent:googleimageproxy",
    "user_agent": GMAIL_PROXY_UA,
    "ip": "10.209.99.254",
}


def test_a_gmail_reader_reaches_the_opened_branch():
    """THE OUTCOME TEST. A real Gmail read must not be sent the non-opener email.

    Reported from production: lead@ibos.io read E2, the branch rule said 'not opened',
    and E6 (the non-opener email) was sent instead of E5. The cause was acting on the
    machine classification rather than on the fact that the image had been loaded at all.
    """
    from app import sequence_engine as seq

    loads = 1
    eng = {"pixel_loads": loads, "human_opens": 0,       # classified as machine
           "machine_opens": 1, "clicks": 0}

    # the rule E2 carries in the 9-email framework
    tree = {"E2": {"clicked": "E4", "opened": "E5", "not_opened": "E6"}}

    nxt = seq.decide_next_ref("E2", seq._opens_for_branch(eng), eng["clicks"], tree)
    assert nxt == "E5", (
        "a Gmail reader must reach the opened branch; got "
        f"{nxt!r}. Reading the machine classification as 'not opened' sends the "
        "non-opener email to someone who just read ours.")


def test_a_reader_who_also_clicked_reaches_the_clicked_branch():
    """A click outranks an open and must still win under the load-based basis."""
    from app import sequence_engine as seq
    eng = {"pixel_loads": 1, "human_opens": 0, "machine_opens": 1, "clicks": 1}
    tree = {"E2": {"clicked": "E4", "opened": "E5", "not_opened": "E6"}}
    assert seq.decide_next_ref("E2", seq._opens_for_branch(eng), eng["clicks"], tree) == "E4"


def test_a_genuine_non_opener_still_gets_the_non_opener_email():
    """The fix must not make everyone an opener: no load means the not-opened branch."""
    from app import sequence_engine as seq
    eng = {"pixel_loads": 0, "human_opens": 0, "machine_opens": 0, "clicks": 0}
    tree = {"E2": {"clicked": "E4", "opened": "E5", "not_opened": "E6"}}
    assert seq.decide_next_ref("E2", seq._opens_for_branch(eng), eng["clicks"], tree) == "E6"


def test_branch_decision_never_consults_the_machine_flag():
    """The routing must not depend on a distinction it cannot reliably make.

    A Gmail read and a Gmail preload are the same request with the same agent, so any
    rule that branches on `is_machine` is branching on noise.
    """
    import inspect
    from app import sequence_engine as seq
    src = inspect.getsource(seq.dispatch)
    assert "is_machine" not in src, (
        "dispatch must not branch on the machine flag: it cannot separate a Gmail read "
        "from a Gmail preload, so using it routes real readers down the wrong path")

def test_client_ip_prefers_a_routable_address():
    """An intermediate hop can put a private address in the chain.

    A Gmail fetch was recorded as 10.209.99.254, which made the address useless for the
    network half of the machine check and for investigating anything later.
    """
    from app import tracking as t

    class Req:
        def __init__(self, h, host="127.0.0.1"):
            self.headers = h
            self.client = type("C", (), {"host": host})()

    assert t.client_ip(Req({"x-forwarded-for": "10.209.99.254, 37.111.212.33"})) == "37.111.212.33"
    assert t.client_ip(Req({"x-forwarded-for": "37.111.212.33, 10.0.0.9"})) == "37.111.212.33"
    # all private: return something rather than nothing
    assert t.client_ip(Req({"x-forwarded-for": "10.0.0.1, 192.168.1.1"})) == "10.0.0.1"
    # falls back to the socket when no header is present
    assert t.client_ip(Req({}, host="203.0.113.9")) == "203.0.113.9"


def test_private_ranges_are_recognised():
    from app import tracking as t
    for ip in ("10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
               "127.0.0.1", "169.254.1.1", "0.0.0.0"):
        assert t._is_public(ip) is False, ip
    for ip in ("37.111.212.33", "8.8.8.8", "66.249.84.33", "203.0.113.10"):
        assert t._is_public(ip) is True, ip

def test_a_repeat_open_inside_the_window_is_one_open():
    """One render can ask for the image twice, and that is one open, not two.

    Measured on real mail: the duplicate arrives 0.0-0.6s after the first, from the same
    address and agent. Genuine repeat views were 8-10s apart, so a short window separates
    the two without merging real reopens - which matters because the branch rule acts on
    these counts.
    """
    import inspect
    from app import routes
    from app.db import stamp

    src = inspect.getsource(routes._record_hit)
    assert "_duplicate_open" in src, "opening once must not be recorded twice"
    assert "OPEN_DEDUPE_SECONDS" in src
    assert routes.OPEN_DEDUPE_SECONDS <= 30, (
        "too wide a window would merge genuine repeat views into one")
    assert routes.OPEN_DEDUPE_SECONDS >= 1, (
        "too narrow a window would let the same render count twice")
    assert callable(stamp)


def test_clicks_are_never_deduplicated():
    """Only opens are collapsed. Two clicks are two clicks, however fast."""
    import inspect
    from app import routes
    src = inspect.getsource(routes._record_hit)
    line = [l for l in src.splitlines() if "duplicate_open" in l and "if " in l]
    assert any('kind == "open"' in l for l in line), (
        "the duplicate check must apply to opens only")

def test_delivery_is_never_inferred_from_a_send():
    """Delivery must come from the provider, not from "we handed it over".

    Those are different claims and only one of them is true. The Delivery column sat
    permanently blank on every sequence send because nothing stored the confirmation the
    provider does send - only opens and clicks were kept.
    """
    import inspect
    from app import routes
    src = inspect.getsource(routes._engagement_summary)
    assert "delivered" in src
    assert "no delivery confirmation" in src, "the absent case must say so"
    # and the webhook receiver must actually store them now
    recv = inspect.getsource(routes.sendclean_webhook)
    assert "_record_delivery" in recv


def test_the_receiver_stores_what_it_used_to_discard():
    import inspect
    from app import routes
    src = inspect.getsource(routes._record_delivery)
    assert '"delivered"' in src or "'delivered'" in src