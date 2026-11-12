"""A transient DNS failure must never become a permanent verdict on a recipient.

`member_emails` drops any address whose `email_status` is `invalid` or `no_mx` - it is
never emailed again. So a status must only ever be `no_mx` when a resolver actually said
the domain has no mail server, never when the resolver merely failed to answer.

Reported class of bug, from production: a routing rule acted on a classification the
classifier could not actually make. This is the same shape in the validation path - a
temporary failure read as a definitive 'no', with a real address dropped as a result.
"""
import pytest

from app import email_validator as ev


class FakeResp:
    """Mimics the urlopen return used as a context manager."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def _patch_doh(monkeypatch, payload):
    import json
    monkeypatch.setattr(ev.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp(json.dumps(payload).encode()))


def test_servfail_is_unknown_not_no_mx(monkeypatch):
    """Status 2 means the resolver could not answer. That is not 'no mail server'."""
    _patch_doh(monkeypatch, {"Status": 2, "Answer": []})
    with pytest.raises(RuntimeError):
        ev._doh("example.com", "MX")


def test_refused_is_unknown_not_no_mx(monkeypatch):
    _patch_doh(monkeypatch, {"Status": 5, "Answer": []})
    with pytest.raises(RuntimeError):
        ev._doh("example.com", "MX")


def test_nxdomain_is_a_real_answer(monkeypatch):
    """Status 3 genuinely means the domain does not exist, so it is returned."""
    _patch_doh(monkeypatch, {"Status": 3, "Answer": []})
    assert ev._doh("nope.invalid", "MX")["Status"] == 3


def test_noerror_is_returned(monkeypatch):
    _patch_doh(monkeypatch, {"Status": 0, "Answer": []})
    assert ev._doh("example.com", "MX")["Status"] == 0


def test_servfail_domain_resolves_to_unknown(monkeypatch):
    """End to end: a failing resolver must not produce no_mx for a real domain."""
    ev._MX_CACHE.clear()
    _patch_doh(monkeypatch, {"Status": 2, "Answer": []})
    state, _ = ev.resolve_mail_host("travilo.io")
    assert state == "unknown", (
        "a resolver failure must not be read as 'this domain has no mail server'; "
        "an address marked no_mx is dropped from every future send")


def test_genuine_no_mx_is_still_caught(monkeypatch):
    """The protection must not go so far that a real dead domain survives."""
    ev._MX_CACHE.clear()
    _patch_doh(monkeypatch, {"Status": 0, "Answer": []})
    state, _ = ev.resolve_mail_host("definitely-no-mail.example")
    assert state == "no_mx"


def test_only_no_mx_and_invalid_are_dropped():
    """The drop list is the contract; `unknown` must never be in it."""
    import inspect
    from app import audience_routes as ar
    src = inspect.getsource(ar.member_emails)
    assert '("invalid", "no_mx")' in src
    assert "unknown" not in src.split('("invalid", "no_mx")')[1][:60], (
        "unknown means the lookup failed, so dropping it would lose a real recipient")
