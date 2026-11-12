"""Brand-doc loader - reads the selected product's MD file (app/brand_docs/<key>-brand.md)
and returns the copy fields the email engine uses.

The MD front matter (between `---` markers) is the copy source of truth:
editing a brand MD changes the next "Draft with Agent" output. Full brand doc
text below the front matter is carried as reference context.
"""
import os
import re

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand_docs")

# Maps a product key to its "Master Brand & Marketing Document" section in the
# canonical skill file (Sections 100-110). familyguard has no such section.
_SKILL_SECTION = {
    "aeroapps": 100,
    "aerodrop": 101,
    "aerorank": 102,
    "managerium": 103,
    "peopledesk": 104,   # Consolidated Master (supersedes the old v1 + Final split)
    "retail": 105,       # Managerium Retail ERP (dedicated master doc)
    "primevat": 106,
    "trackforce": 107,
    "travilo": 108,
    "turbo": 109,
    "turbo_guard": 110,
}

_FILE_NAMES = {"turbo_guard": "turbo-guard-email-copy.md"}
_REFERENCE_FILES = {"turbo_guard": "turbo-guard-email-copy.md"}


def load(key):
    """Return engine cfg dict from the brand MD front matter, or None if unavailable."""
    path = os.path.join(_DIR, _FILE_NAMES.get(key, f"{key}-brand.md"))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except Exception:  # noqa: BLE001
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    fm = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()
    if not fm.get("name"):
        return None
    cfg = {
        "product": fm.get("product", key),
        "slug": fm.get("slug", key),
        "purpose": fm.get("purpose", "engage"),
        "name": fm["name"],
        "subjects": [fm.get(f"subject_{i}") for i in (1, 2, 3) if fm.get(f"subject_{i}")],
        "preview": fm.get("preview", ""),
        "hook": fm.get("hook", ""),
        "problem": fm.get("problem", ""),
        "solution": fm.get("solution", ""),
        "cta": fm.get("cta", "Learn more"),
        "cta_url": fm.get("cta_url", ""),
        "proof": [fm.get(f"proof_{i}") for i in range(1, 12) if fm.get(f"proof_{i}")],
    }
    return cfg


def available():
    """List product keys that have a brand MD bundled."""
    reverse_names = {value: key for key, value in _FILE_NAMES.items()}
    out = []
    for name in os.listdir(_DIR):
        if name.endswith("-brand.md"):
            out.append(reverse_names.get(name, name[: -len("-brand.md")]))
    out.extend(key for key in _FILE_NAMES if key not in out)
    return sorted(out)


def load_skill_context(key, max_chars=None):
    """Return the product's FULL Master Brand & Marketing Document (skill Sections 100-110).

    This is the richer brand context - ICP, positioning, messaging pillars, buyer
    personas, pain points, proof, voice/tone and claim governance - used to let the
    LLM generate its OWN creative angle while staying grounded in the skill file.

    Returns the COMPLETE section body (no truncation) so no product data is missed
    at generation time. Pass max_chars to bound it only when explicitly requested.
    """
    key = (key or "").lower()
    if key in _REFERENCE_FILES:
        path = os.path.join(_DIR, _REFERENCE_FILES[key])
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except Exception:  # noqa: BLE001
            return None
        end = text.find("\n---", 3)
        body = text[end + 4:].strip() if text.startswith("---") and end >= 0 else text.strip()
        if max_chars and len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n...(truncated)"
        return body
    section = _SKILL_SECTION.get(key)
    if not section:
        return None
    path = os.path.join(_DIR, "email-writing-skill-v4.md")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except Exception:  # noqa: BLE001
        return None
    headers = [(m.start(), m.group(0)) for m in re.finditer(r"^# \d{3}\.", text, re.MULTILINE)]
    start = None
    for i, (pos, hdr) in enumerate(headers):
        m = re.match(r"^# (\d+)\.", hdr)
        if m and int(m.group(1)) == section:
            start = pos
            end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
            break
    if start is None:
        return None
    body = text[start:end]
    if "\n" in body:
        body = body.split("\n", 1)[1]
    body = body.strip().strip("-").strip()
    if max_chars and len(body) > max_chars:
        body = body[:max_chars].rstrip() + f"\n…(truncated - full brand doc in skill Section {section})"
    return body
