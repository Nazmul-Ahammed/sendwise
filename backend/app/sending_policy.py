"""Non-bypassable sender and reply-address rules.

Keep these checks at the transport boundary. UI validation and campaign status are
useful feedback, but neither is a safety control because scheduled jobs, MCP tools and
direct provider routes can send without visiting the UI.
"""
from email.utils import parseaddr


BLOCKED_SENDING_IDENTITIES = frozenset({"hello@ibos.io"})


class BlockedSendingIdentityError(ValueError):
    """A message attempted to use an address that is forbidden for sending."""


def normalise_address(value) -> str:
    """Extract and normalize an address from either email or Name <email> form."""
    _name, address = parseaddr(str(value or "").strip())
    return address.strip().lower()


def is_blocked(value) -> bool:
    return normalise_address(value) in BLOCKED_SENDING_IDENTITIES


def require_allowed(value, role="sender"):
    address = normalise_address(value)
    if address in BLOCKED_SENDING_IDENTITIES:
        raise BlockedSendingIdentityError(
            f"BLOCKED: {address} cannot be used as a sending or reply email"
        )
    return address


def require_message_allowed(message):
    """Validate every sender-like identity accepted by the transport payload."""
    payload = message or {}
    for key in ("from_email", "from", "sender", "reply_to", "reply_email"):
        if payload.get(key):
            require_allowed(payload[key], role=key)
    for key, value in (payload.get("headers") or {}).items():
        if str(key or "").strip().lower() in {
            "from", "reply-to", "reply_to", "sender", "return-path"
        }:
            require_allowed(value, role=str(key))
    return True
