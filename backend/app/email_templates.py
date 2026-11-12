"""Responsive HTML email templates for outbound campaigns.

Single source of truth for the branded Turbo Addons look. Email-friendly
(table-based layout, inline CSS, web-safe fallbacks). No external assets that
would break offline - colors and typography are inline.
"""
import re

BRAND = {
    "primary": "#6C2BD9",       # deep violet (Turbo vibe)
    "primary_dark": "#4C1D95",
    "accent": "#FF6B2C",        # warm orange for CTA
    "accent_dark": "#E85A1F",
    "bg": "#F6F4FB",
    "card": "#FFFFFF",
    "text": "#1A1A2E",
    "muted": "#5A5A72",
    "border": "#E8E4F0",
}

FONT = "Arial, Helvetica, sans-serif"


def _esc(s):
    import html
    return html.escape(str(s), quote=True)


# Links inside the plain-text body. Body text is escaped before it is rendered, so a
# pasted address used to arrive as dead text: the reader could see the URL but could not
# click it (and only the single arrow line became a button). These two forms are turned
# into real links that read as ordinary underlined text, not a button:
#
#     [discount page](https://example.com/offer)     - custom label
#     https://example.com/offer                      - the address itself as the label
#
# Only http and https are linked. Anything else (javascript:, data:, mailto:) is left as
# literal text, so a body can never invent a link the sender did not write.
_LINK_TOKEN = re.compile(
    r"\[([^\]]+)\]\((https?://[^\s)]+)\)"   # [label](url)
    r"|(https?://[^\s<>\"']+)"              # bare url
)
# Sentence punctuation that follows a bare URL belongs to the sentence, not the link.
_LINK_TRAILING = ".,;:!?"

# Marks a body block that is already HTML and must not be escaped again.
_RAW_HTML_MARK = "\x00raw-html\x00"

# Tags an author may use in a body. Anything else is dropped: an email body is authored by
# an operator, not by a stranger, but a stray <script> or an onerror= attribute would still
# ride straight into a recipient's client (and into our own preview iframe).
_HTML_TAG_RE = re.compile(
    r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)[^>]*>", re.S)
_HTML_ALLOWED = {
    "a", "b", "strong", "i", "em", "u", "s", "span", "div", "p", "br", "hr",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
    "table", "thead", "tbody", "tr", "td", "th", "img", "small", "font",
}


def _looks_like_html(text):
    """True when a body is markup rather than plain text.

    Deliberately narrow: a sentence that merely mentions "<" must keep rendering as text,
    so at least one real tag from the allowed set has to appear.
    """
    for match in _HTML_TAG_RE.finditer(str(text or "")):
        if match.group(2).lower() in _HTML_ALLOWED:
            return True
    return False


def _sanitize_html(text):
    """Strip the parts of an authored body that can execute or frame something.

    Dangerous elements are removed with their content; unknown tags are removed but their
    inner text is kept, so an unsupported <marquee> degrades to plain words instead of a
    blank email. Inline event handlers and javascript:/data: URLs do not survive.
    """
    out = str(text or "")
    for element in ("script", "style", "iframe", "object", "embed", "form", "input",
                    "button", "meta", "link", "base", "svg", "math"):
        out = re.sub(rf"<\s*{element}\b.*?<\s*/\s*{element}\s*>", "", out, flags=re.S | re.I)
        out = re.sub(rf"<\s*/?\s*{element}\b[^>]*>", "", out, flags=re.I)
    out = re.sub(r"\son[a-zA-Z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", out)
    out = re.sub(r"(href|src)\s*=\s*(\"|')?\s*(javascript|data|vbscript):[^\"'>\s]*",
                 r'\1="#"', out, flags=re.I)

    def keep(match):
        closing, name = match.group(1), match.group(2).lower()
        return match.group(0) if name in _HTML_ALLOWED else ""

    return _HTML_TAG_RE.sub(keep, out)


def _inline_html(text, link_color="#183870"):
    """Escape the text, then make links clickable as underlined text.

    Matching runs on the RAW text, not the escaped copy: escaping turns quotes into
    `&#x27;`, and a URL pattern that then ran over the escaped string would swallow that
    entity and build a broken link. Each piece is escaped separately instead.
    """
    raw = str(text or "")
    out = []
    last = 0
    for match in _LINK_TOKEN.finditer(raw):
        out.append(_esc(raw[last:match.start()]))
        label, url, bare = match.group(1), match.group(2), match.group(3)
        tail = ""
        if bare:
            url = bare
            trimmed = url.rstrip(_LINK_TRAILING)
            tail = url[len(trimmed):]
            url = trimmed
            label = url
        if not url.lower().startswith(("http://", "https://")):
            out.append(_esc(match.group(0)))
            last = match.end()
            continue
        out.append(f'<a href="{_esc(url)}" style="color:{link_color};'
                   f'text-decoration:underline;">{_esc(label)}</a>')
        out.append(_esc(tail))
        last = match.end()
    out.append(_esc(raw[last:]))
    return "".join(out)


def upsell_email(heading, intro, bullets, cta_url, cta_text, footer_note, price_note,
                 template_preview=None, unsubscribe_link=None, tracking_pixel="",
                 template_link=None):
    """Build the branded Pro-upsell HTML email.

    heading: attention-grabbing headline (kept short)
    intro: 1-2 sentence greeting/context
    bullets: list of (title, desc) feature bullets
    cta_url / cta_text: primary call-to-action
    footer_note: unsubscribe + support line
    price_note: one-line pricing/guarantee under the CTA
    template_preview: dict {img_url, link_url, alt} - optional clickable template shot
    unsubscribe_link: mailto link for the unsubscribe button (optional).
    template_link: a real URL to the matching template (optional) - rendered as a
        secondary "View the matching template" button so the template is always
        reachable even when the image preview is absent.
    """
    b = BRAND
    bullet_html = ""
    for title, desc in bullets:
        bullet_html += f"""
    <tr>
      <td style="padding:10px 0;border-bottom:1px solid {b['border']};">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
          <td width="26" valign="top" style="font-size:16px;color:{b['accent']};">&#10003;</td>
          <td valign="top">
            <div style="font-size:15px;color:{b['text']};font-weight:bold;font-family:{FONT};">{_esc(title)}</div>
            <div style="font-size:13px;color:{b['muted']};font-family:{FONT};line-height:1.5;margin-top:2px;">{_esc(desc)}</div>
          </td>
        </tr></table>
      </td>
    </tr>"""

    # Unsubscribe button - a complete self-contained box at the bottom (not a
    # stray table cell). Renders as its own bordered box under the main body.
    unsub_html = f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
          <td align="center" style="border:1px solid {b['border']};border-radius:8px;background:{b['card']};padding:14px 20px;margin-top:0;margin-bottom:20px;">
            <p style="font-size:12px;color:{b['muted']};font-family:{FONT};line-height:1.5;margin:0 0 6px;">Changed your mind? No hard feelings.</p>
            <a href="{_esc(unsubscribe_link)}" style="display:inline-block;color:{b['muted']};font-family:{FONT};font-size:12px;text-decoration:underline;border-bottom:1px solid {b['muted']};">Unsubscribe from these emails</a>
          </td>
        </tr></table>""" if unsubscribe_link else ""

    # Optional template screenshot (clickable image -> live template page).
    preview_html = ""
    if template_preview and template_preview.get("img_url"):
        img = template_preview["img_url"]
        link = template_preview.get("link_url", cta_url)
        alt = template_preview.get("alt", "Template preview")
        preview_html = f"""
          <!-- Template preview -->
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
            <td align="center" style="padding:6px 0 24px;">
              <a href="{_esc(link)}" target="_blank" style="text-decoration:none;">
                <img src="{_esc(img)}" alt="{_esc(alt)}" width="536" style="display:block;width:100%;max-width:536px;height:auto;border:1px solid {b['border']};border-radius:8px;" />
              </a>
              <div style="font-size:12px;color:{b['muted']};font-family:{FONT};margin-top:6px;">{_esc(alt)} - click to view</div>
            </td>
          </tr></table>"""

    # Secondary "view template" link button - shown when a template URL is given
    # but no image preview is embedded (the "link" A/B arm). Guarantees the
    # template is always reachable, not just the generic pricing CTA.
    template_link_html = ""
    if template_link and not (template_preview and template_preview.get("img_url")):
        template_link_html = f"""
          <!-- Template link -->
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
            <td align="center" style="padding:4px 0 20px;">
              <a href="{_esc(template_link)}" target="_blank" style="display:inline-block;border:1px solid {b['primary']};color:{b['primary']};font-family:{FONT};font-size:14px;font-weight:bold;text-decoration:none;padding:12px 26px;border-radius:8px;">View the matching template &rarr;</a>
            </td>
          </tr></table>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(heading)}</title>
</head>
<body style="margin:0;padding:0;background:{b['bg']};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">
  {_esc(intro)} {_esc(price_note)}
</div>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:{b['bg']};">
<tr>
  <td align="center" style="padding:24px 12px;">
    <!-- Container -->
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;width:100%;background:{b['card']};border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
      
      <!-- Header band -->
      <tr>
        <td style="background:linear-gradient(135deg,{b['primary']} 0%,{b['primary_dark']} 100%);padding:28px 32px;text-align:center;">
          <div style="font-size:22px;font-weight:800;color:#ffffff;font-family:{FONT};letter-spacing:0.5px;">&#9889; TURBO ADDONS</div>
          <div style="font-size:13px;color:rgba(255,255,255,0.85);font-family:{FONT};margin-top:4px;">Supercharge your Elementor site</div>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="padding:32px 32px 24px;">
          <h1 style="font-size:24px;line-height:1.3;color:{b['text']};font-family:{FONT};margin:0 0 12px;font-weight:800;">{_esc(heading)}</h1>
          <p style="font-size:15px;line-height:1.6;color:{b['muted']};font-family:{FONT};margin:0 0 20px;">{_esc(intro)}</p>

          <!-- Feature bullets -->
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:4px 0 20px;">
            {bullet_html}
          </table>

          {preview_html}
          {template_link_html}

          <!-- CTA -->
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td align="center" style="padding:8px 0;">
            <a href="{_esc(cta_url)}" target="_blank" style="display:inline-block;background:{b['accent']};color:#ffffff;font-family:{FONT};font-size:16px;font-weight:bold;text-decoration:none;padding:14px 34px;border-radius:8px;border:1px solid {b['accent_dark']};">{_esc(cta_text)} &rarr;</a>
          </td></tr></table>

          <p style="font-size:13px;color:{b['muted']};font-family:{FONT};text-align:center;margin:12px 0 0;line-height:1.5;">{_esc(price_note)}</p>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background:{b['bg']};padding:24px 32px;text-align:center;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr><td align="center">{unsub_html}</td></tr>
          </table>
          <p style="font-size:12px;color:{b['muted']};font-family:{FONT};line-height:1.6;margin:0;">{_esc(footer_note)}</p>
          <p style="font-size:11px;color:#A9A3B8;font-family:{FONT};margin:10px 0 0;">&copy; 2026 Turbo Addons &middot; turbo-addons.com</p>
        </td>
      </tr>

    </table>
  </td>
</tr>
</table>
{tracking_pixel}
</body>
</html>"""
    return html


def upsell_plain(heading, intro, bullets, cta_url, footer_note, price_note):
    """Plain-text fallback for the same email (deliverability)."""
    lines = [heading, "", intro, ""]
    for title, desc in bullets:
        lines.append(f"- {title}: {desc}")
    lines.append("")
    lines.append(f"{cta_url}")
    lines.append("")
    lines.append(price_note)
    lines.append("")
    lines.append(footer_note)
    return "\n".join(lines)


def branded_report_html(subject, body_text, from_name="Sendwise", sheet_link="", unsubscribe_link=None):
    """Branded HTML shell for the daily report email (Turbo Addons design system)."""
    b = BRAND
    unsub_html = ""
    if unsubscribe_link:
        unsub_html = f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
          <td align="center" style="border:1px solid {b['border']};border-radius:8px;background:{b['card']};padding:14px 20px;">
            <p style="font-size:12px;color:{b['muted']};font-family:{FONT};line-height:1.5;margin:0 0 6px;">Changed your mind? No hard feelings.</p>
            <a href="{_esc(unsubscribe_link)}" style="display:inline-block;color:{b['muted']};font-family:{FONT};font-size:12px;text-decoration:underline;">Unsubscribe from these emails</a>
          </td>
        </tr></table>"""
    sheet_row = ""
    if sheet_link:
        sheet_row = f"""
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td align="center" style="padding:6px 0 20px;">
            <a href="{_esc(sheet_link)}" target="_blank" style="display:inline-block;background:{b['accent']};color:#ffffff;font-family:{FONT};font-size:15px;font-weight:bold;text-decoration:none;padding:13px 30px;border-radius:8px;border:1px solid {b['accent_dark']};">Open the report sheet &rarr;</a>
          </td></tr></table>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(subject)}</title>
</head>
<body style="margin:0;padding:0;background:{b['bg']};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{_esc(subject)}</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:{b['bg']};">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;width:100%;background:{b['card']};border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
    <tr><td style="background:linear-gradient(135deg,{b['primary']} 0%,{b['primary_dark']} 100%);padding:24px 32px;text-align:center;">
      <div style="font-size:20px;font-weight:800;color:#ffffff;font-family:{FONT};letter-spacing:0.5px;">&#9889; SENDWISE</div>
      <div style="font-size:12px;color:rgba(255,255,255,0.85);font-family:{FONT};margin-top:4px;">Email campaign performance report</div>
    </td></tr>
    <tr><td style="padding:28px 32px 20px;">
      <h1 style="font-size:21px;line-height:1.3;color:{b['text']};font-family:{FONT};margin:0 0 14px;font-weight:800;">{_esc(subject)}</h1>
      <div style="font-size:14px;line-height:1.7;color:{b['muted']};font-family:{FONT};white-space:pre-wrap;">{_esc(body_text)}</div>
      {sheet_row}
    </td></tr>
    <tr><td style="background:{b['bg']};padding:20px 32px;text-align:center;">
      {unsub_html}
      <p style="font-size:11px;color:#A9A3B8;font-family:{FONT};margin:0;">Auto-generated by Sendwise Agent OS &middot; iBOS (AKIJ Resource)</p>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


# Per-product brand configs (from email-marketing/brand-docs/*.md)
BRANDS = {
    "ibos": {"name": "iBOS", "tag": "Business technology made simple",
             "primary": "#183870", "dark": "#13264D", "accent": "#1662DC", "accent_dark": "#0F4BA8"},
    "turbo": {"name": "TURBO ADDONS", "tag": "Supercharge your Elementor site",
               "primary": "#2E3192", "dark": "#172554", "accent": "#345ED3", "accent_dark": "#243B9E"},
    "turbo_guard": {"name": "Turbo Guard", "tag": "Protect. Detect. Review.",
                     "primary": "#2563EB", "dark": "#173F8A", "accent": "#2563EB", "accent_dark": "#173F8A",
                     "bg": "#F3F6FC", "text": "#263247", "muted": "#526075", "border": "#E6ECF8"},
    "travilo": {"name": "TRAVILO", "tag": "One connected OTA operating system",
                "primary": "#253F8D", "dark": "#172B63", "accent": "#3D78DB", "accent_dark": "#2B5CB8",
                # The Travilo logo already carries the wordmark, so the header shows the
                # mark alone - printing "TRAVILO" beside it would say the name twice.
                "logo_only": True},
    "peopledesk": {"name": "PEOPLEDESK", "tag": "Human operations, intelligently connected",
                   "primary": "#13264D", "dark": "#13264D", "accent": "#34A853", "accent_dark": "#26803F"},
    "managerium": {"name": "Managerium ERP", "tag": "All-in-One ERP Solution",
                    "primary": "#183870", "dark": "#13264D", "accent": "#FD0000", "accent_dark": "#C40000"},
    "primevat": {"name": "PRIMEVAT", "tag": "Clear VAT. Confident compliance.",
                 "primary": "#1C2880", "dark": "#141D5E", "accent": "#1662DC", "accent_dark": "#0F4BA8"},
    "trackforce": {"name": "TRACKFORCE", "tag": "Clear work insights. Better decisions.",
                   "primary": "#0248E2", "dark": "#0A2F88", "accent": "#1748C7", "accent_dark": "#0F3698"},
    "familyguard": {"name": "FAMILY GUARD", "tag": "সন্তানের সুরক্ষায়",
                    "primary": "#102147", "dark": "#102147", "accent": "#6AC698", "accent_dark": "#4FA87C"},
}


def brand_config(key="turbo"):
    b = BRANDS.get(key, BRANDS["ibos"])
    base = dict(BRAND)
    base.update({k: b.get(k, base.get(k)) for k in (
        "primary", "primary_dark", "accent", "accent_dark", "bg", "text", "muted", "border"
    )})
    return b, base


# A per-email button colour, written as its own line: `[cta_color: #E85A1F]`.
# A setting line rather than content - it is consumed while parsing and never rendered.
# The match is deliberately loose and case-insensitive so that a malformed value is still
# swallowed instead of printing "[cta_color: red]" into the email body.
_CTA_COLOR_LINE = re.compile(r"^\[\s*cta[_-]?colou?r\s*[:=]\s*(.*?)\s*\]$", re.IGNORECASE)
# A colour pinned to a single button, written at the end of its own line:
#   See a demo -> https://example.com/demo | #2563EB
# The match is loose so a malformed value is stripped from the URL too - otherwise
# "| #ZZZ" would end up inside the link and break it.
_CTA_INLINE_COLOR = re.compile(
    r"\s*\|\s*#?([0-9A-Za-z]{3,8})(?:\s+(?:on|text)\s+#?([0-9A-Za-z]{3,8}))?\s*$")

# The label colour, when the author wants to choose it instead of the automatic
# black/white contrast pick: `[cta_text_color: #FFFFFF]`.
_CTA_TEXT_COLOR_LINE = re.compile(
    r"^\[\s*cta[_-]?text[_-]?colou?r\s*[:=]\s*(.*?)\s*\]$", re.IGNORECASE)


def _normalise_hex(value):
    """`#abc` / `abcdef` -> `#aabbcc`. Returns "" when the value is not a colour."""
    raw = str(value or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
        return ""
    return "#" + raw.lower()


def _cta_text_color(hex_color):
    """Choose black/white text with the stronger WCAG contrast against the CTA background."""
    value = str(hex_color or "#000000").lstrip("#")
    if len(value) != 6:
        return "#FFFFFF"
    rgb = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    contrast_white = 1.05 / (luminance + 0.05)
    contrast_black = (luminance + 0.05) / 0.05
    return "#FFFFFF" if contrast_white >= contrast_black else "#111827"


def branded_report_html(subject, body_text, from_name="Sendwise", sheet_link="", unsubscribe_link=None, product="turbo", extra_html=""):
    """Branded HTML shell for the daily report email - brand from BRANDS."""
    b, c = brand_config(product)
    unsub_html = ""
    if unsubscribe_link:
        unsub_html = f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
          <td align="center" style="border:1px solid {c['border']};border-radius:8px;background:{c['card']};padding:14px 20px;">
            <p style="font-size:12px;color:{c['muted']};font-family:{FONT};line-height:1.5;margin:0 0 6px;">Changed your mind? No hard feelings.</p>
            <a href="{_esc(unsubscribe_link)}" style="display:inline-block;color:{c['muted']};font-family:{FONT};font-size:12px;text-decoration:underline;">Unsubscribe from these emails</a>
          </td>
        </tr></table>"""
    sheet_row = ""
    if sheet_link:
        sheet_row = f"""
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td align="center" style="padding:6px 0 20px;">
            <a href="{_esc(sheet_link)}" target="_blank" style="display:inline-block;background:{b['accent']};color:#ffffff;font-family:{FONT};font-size:15px;font-weight:bold;text-decoration:none;padding:13px 30px;border-radius:8px;border:1px solid {b['accent_dark']};">Open the report sheet &rarr;</a>
          </td></tr></table>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(subject)}</title>
</head>
<body style="margin:0;padding:0;background:{c['bg']};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{_esc(subject)}</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:{c['bg']};">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;width:100%;background:{c['card']};border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
    <tr><td style="background:linear-gradient(135deg,{b['primary']} 0%,{b['dark']} 100%);padding:24px 32px;text-align:center;">
      <div style="font-size:20px;font-weight:800;color:#ffffff;font-family:{FONT};letter-spacing:0.5px;">{b['name']}</div>
      <div style="font-size:12px;color:rgba(255,255,255,0.85);font-family:{FONT};margin-top:4px;">{b['tag']}</div>
    </td></tr>
    <tr><td style="padding:28px 32px 20px;">
      <h1 style="font-size:21px;line-height:1.3;color:{c['text']};font-family:{FONT};margin:0 0 14px;font-weight:800;">{_esc(subject)}</h1>
      <div style="font-size:14px;line-height:1.7;color:{c['muted']};font-family:{FONT};white-space:pre-wrap;">{_esc(body_text)}</div>
      {extra_html}
      {sheet_row}
    </td></tr>
    <tr><td style="background:{c['bg']};padding:20px 32px;text-align:center;">
      {unsub_html}
      <p style="font-size:11px;color:#A9A3B8;font-family:{FONT};margin:0;">Auto-generated by Sendwise Agent OS &middot; iBOS (AKIJ Resource)</p>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Standard iBOS email template.
#
# Canonical reference: app/brand_docs/email-template-standard.html
# (mirrored to email-marketing/brand-docs/email-template-standard.html).
#
# Every campaign email uses this one structure:
#   header (brand logo + tagline) -> greeting -> H1 headline -> paragraphs ->
#   feature rows -> single CTA -> sign-off -> brand footer band
# Brand colour, logo and tagline vary per product; the layout does not.
# ---------------------------------------------------------------------------
TEMPLATE_BG = "#f5f7fb"
TEMPLATE_CARD = "#FFFFFF"
TEMPLATE_TEXT = "#333333"
TEMPLATE_MUTED = "#555555"
TEMPLATE_RULE = "#eeeeee"

_LOGOS = None


def _logos():
    """Brand logo data-URIs. A brand without a logo falls back to a text wordmark."""
    global _LOGOS
    if _LOGOS is None:
        import json as _json
        import os as _os
        path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "brand_assets", "logos.json")
        try:
            with open(path, encoding="utf-8") as fh:
                _LOGOS = _json.load(fh)
        except Exception:  # noqa: BLE001
            _LOGOS = {}
    return _LOGOS


def _managerium_template_logo():
    """Read the exact logo embedded in the user-approved Managerium template.

    The canonical source stays in brand_docs, so the renderer and the supplied design
    cannot quietly drift into two different logos.
    """
    import os as _os
    path = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)),
        "brand_docs", "managerium-email-template.html",
    )
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        match = re.search(r'<img\s+src="(data:image/[^\"]+)"', source, re.IGNORECASE)
        return match.group(1) if match else ""
    except OSError:
        return ""


def _tint(hex_color, ratio=0.78):
    """Light tint of the brand colour, used for the footer tagline."""
    value = str(hex_color or "#183870").lstrip("#")
    if len(value) != 6:
        return "#dbe3ff"
    try:
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "#dbe3ff"
    mix = lambda channel: int(round(channel + (255 - channel) * ratio))  # noqa: E731
    return "#%02x%02x%02x" % (mix(r), mix(g), mix(b))


def body_to_html(body_text, product="turbo", cta_url="", cta_text="", variant=1, headline="",
                 unsubscribe_url=""):
    """Render the standard branded email around the plain-text body.

    `variant` is accepted for backwards compatibility only: the design is now a
    single consistent template, so it no longer changes any styling.
    `headline` becomes the H1 (the send subject is the natural value).
    `unsubscribe_url`, when given, renders the compliance unsubscribe line.
    """
    b, c = brand_config(product)
    primary = b["primary"]
    footer_accent = _tint(primary)
    brand_name = b["name"]
    tagline = b["tag"]
    is_managerium = product == "managerium"
    is_turbo_guard = product == "turbo_guard"
    campaign_text = f"{headline} {body_text}"
    is_coffee_offer = is_turbo_guard and "US$5.55" in campaign_text
    page_bg = c.get("bg", TEMPLATE_BG) if is_turbo_guard else TEMPLATE_BG
    text_color = c.get("text", TEMPLATE_TEXT) if is_turbo_guard else TEMPLATE_TEXT
    muted_color = c.get("muted", TEMPLATE_MUTED) if is_turbo_guard else TEMPLATE_MUTED
    rule_color = c.get("border", TEMPLATE_RULE) if is_turbo_guard else TEMPLATE_RULE

    lines = [ln.strip() for ln in str(body_text or "").replace("\r\n", "\n").split("\n")]

    # HTML bodies. An author who writes markup wants it rendered, not printed - so when
    # the body looks like HTML the line parser is skipped entirely and the markup is
    # cleaned and dropped into the same branded shell. The shell, the sign-off and the
    # unsubscribe line are still ours, so an HTML body cannot lose compliance furniture.
    html_body = _looks_like_html(body_text)
    if html_body:
        lines = []

    greeting = ""
    signer = ""
    body_lines = []
    features = []
    cta = None
    # Every `Label → https://url` line is a button of its own, so an email can offer two
    # choices side by side (for example the offer page and a demo). Before this, each
    # arrow line overwrote the previous one and the first button silently disappeared.
    ctas = []
    cta_color_override = ""
    cta_text_override = ""
    if html_body:
        # Set after the plain-text accumulators exist, or the reset below would drop it.
        body_lines = [_RAW_HTML_MARK + _sanitize_html(body_text)]

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        # Per-email button colour: a line that is exactly `[cta_color: #RRGGBB]`.
        # It is a setting, not content, so it never renders as a paragraph. An invalid
        # value is dropped and the brand colour is used, because a typo in a colour must
        # neither break the send nor appear in the email.
        color_line = _CTA_COLOR_LINE.match(line)
        if color_line:
            cta_color_override = _normalise_hex(color_line.group(1))
            i += 1
            continue
        text_line = _CTA_TEXT_COLOR_LINE.match(line)
        if text_line:
            cta_text_override = _normalise_hex(text_line.group(1))
            i += 1
            continue
        if not greeting and line.lower() in ("hi,", "hi there,", "hello,", "hello there,", "dear all,"):
            greeting = line
            i += 1
            continue
        if line.startswith("- "):
            while i < len(lines):
                if lines[i].startswith("- "):
                    features.append(lines[i][2:].strip())
                    i += 1
                    continue
                if not lines[i]:
                    i += 1
                    continue
                break
            continue
        if ("\u2192" in line or "->" in line) and "http" in line:
            arrow = "\u2192" if "\u2192" in line else "->"
            label, _, url = line.partition(arrow)
            # A colour can be pinned to this one button: `See demo → https://… | #2563EB`.
            # That keeps two buttons independent without relying on the order the colour
            # lines were written in.
            own_color = ""
            own_text = ""
            url = url.strip()
            tail = _CTA_INLINE_COLOR.search(url)
            if tail:
                own_color = _normalise_hex(tail.group(1))
                own_text = _normalise_hex(tail.group(2) or "")
                # Strip the suffix even when the colour is invalid: it is a setting, not
                # part of the address, and leaving it would break the link.
                url = url[:tail.start()].strip()
            cta = (label.strip(), url)
            if cta[0] and cta[1]:
                ctas.append((cta[0], cta[1], own_color, own_text))
            i += 1
            continue
        if line.lower().rstrip(",") in ("best regards", "best regard", "regards", "warm regards"):
            j = i + 1
            while j < len(lines):
                if lines[j]:
                    signer = lines[j]
                    break
                j += 1
            i = j + 1
            continue
        body_lines.append(line)
        i += 1

    logo = _managerium_template_logo() if is_managerium else _logos().get(product, "")
    if is_managerium and not logo:
        raise RuntimeError(
            "Managerium email template is missing its approved logo asset; rendering blocked"
        )
    name_mark = (f'<span style="font-size:18px;line-height:1.2;font-weight:bold;color:{primary};'
                 f'letter-spacing:0.3px;">{_esc(brand_name)}</span>')
    if logo:
        # Must be a public HTTPS raster URL: email clients block SVG and strip data: URIs.
        logo_width = 120 if is_managerium else 40
        logo_img = (f'<img src="{logo}" alt="{_esc(brand_name)}" width="{logo_width}" border="0" '
                    f'style="display:block;width:{logo_width}px;max-width:{logo_width}px;height:auto;border:0;'
                    f'outline:none;text-decoration:none;">')
        # The mark and the wordmark sit together, vertically centred and sized to balance:
        # a 40px logo against an 18px bold name. The logo identifies the brand at a glance;
        # the name keeps it readable when a client blocks images, which is when a lone
        # image header would otherwise leave the reader with nothing to identify.
        # A brand whose logo already contains its name sets `logo_only`, and then the
        # header is the mark alone: the name would otherwise appear twice.
        if b.get("logo_only") or is_managerium:
            brand_mark = logo_img
        else:
            brand_mark = (
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
                f'<td valign="middle" style="padding:0 12px 0 0;">{logo_img}</td>'
                f'<td valign="middle">{name_mark}</td>'
                '</tr></table>'
            )
    else:
        brand_mark = name_mark

    parts = []
    if greeting:
        parts.append(f'<p style="font-size:14px;color:{text_color};margin:0 0 16px;">{_inline_html(greeting, primary)}</p>')
    if headline:
        parts.append(f'<h1 style="font-size:24px;line-height:1.4;color:{primary};'
                     f'margin:0 0 18px;">{_esc(headline)}</h1>')
    if is_coffee_offer:
        parts.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin:0 0 22px;background:#FFFFFF;border:1px solid #DCE4F4;border-radius:12px;overflow:hidden;">'
            '<tr><td colspan="2" align="center" style="padding:14px 18px 8px;color:#1C2880;font-size:11px;'
            'font-weight:bold;letter-spacing:1.1px;">A MOMENT FOR YOU&nbsp;&nbsp;|&nbsp;&nbsp;SECURITY FOR YOUR BUSINESS</td></tr>'
            '<tr>'
            '<td width="50%" align="center" valign="top" style="padding:12px 10px 16px;background:#F8F1E8;">'
            '<div style="font-size:11px;color:#6B5B4B;text-transform:uppercase;letter-spacing:0.6px;">Coffee comparison</div>'
            '<div style="font-size:25px;line-height:1.2;color:#1C2880;font-weight:bold;margin-top:5px;">US$5.55</div>'
            '<div style="font-size:11px;line-height:1.5;color:#6B5B4B;margin-top:4px;">A familiar personal pause</div>'
            '</td>'
            '<td width="50%" align="center" valign="top" style="padding:12px 10px 16px;background:#EEF4FF;">'
            '<div style="font-size:11px;color:#3C506F;text-transform:uppercase;letter-spacing:0.6px;">Turbo Guard Pro</div>'
            '<div style="font-size:25px;line-height:1.2;color:#0253FC;font-weight:bold;margin-top:5px;">US$5.55</div>'
            '<div style="font-size:11px;line-height:1.5;color:#3C506F;margin-top:4px;">First year, up to 2 sites</div>'
            '</td>'
            '</tr>'
            '<tr><td colspan="2" align="center" style="padding:10px 16px;background:#1C2880;color:#FFFFFF;'
            'font-size:11px;font-weight:bold;letter-spacing:0.5px;">PEACE OF MIND. SAME PRICE. &nbsp;|&nbsp; ONE-MONTH OFFER</td></tr>'
            '</table>'
        )
    for para in body_lines:
        if para.startswith(_RAW_HTML_MARK):
            # An HTML body goes in as authored (already sanitised), not as paragraphs.
            parts.append(para[len(_RAW_HTML_MARK):])
            continue
        parts.append(f'<p style="font-size:15px;line-height:1.7;color:{muted_color};'
                     f'margin:0 0 16px;">{_inline_html(para, primary)}</p>')

    if features:
        rows = ""
        for idx, item in enumerate(features):
            rule = "" if idx == len(features) - 1 else f"border-bottom:1px solid {rule_color};"
            prefix = f'<span style="font-weight:bold;color:{primary};">{idx + 1:02d}</span>&nbsp;&nbsp;' if is_turbo_guard else "&#10004; "
            rows += (f'<tr><td style="padding:10px 0;{rule}color:{text_color};">'
                     f'{prefix}{_inline_html(item, primary)}</td></tr>')
        feature_style = (f'margin:22px 0;background:#F7F9FE;border:1px solid {rule_color};border-radius:8px;' if is_turbo_guard else "margin:22px 0;")
        feature_padding = ' style="padding:5px 17px;"' if is_turbo_guard else ""
        parts.append(f'<table width="100%" cellpadding="0" cellspacing="0" '
                     f'style="{feature_style}"><tr><td{feature_padding}><table width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr></table>')

    label, url = cta if cta else (cta_text, cta_url)
    if url and label:
        base_color = "#FD0000" if is_coffee_offer else primary
        # An explicit `[cta_color: …]` line sets the background for every button; a
        # `| #hex` on one button's own line overrides it there alone, and
        # `| #hex on #hex` sets that button's label colour as well. `[cta_text_color: …]`
        # sets the label colour for all of them. Without either, the label switches to
        # black or white by contrast, because a light background with white text is
        # unreadable.
        if cta_color_override:
            base_color = cta_color_override
        buttons = ctas or [(label, url, "", "")]

        def button_html(text, link, own_color, own_label_color=""):
            color = own_color or base_color
            label_color = own_label_color or cta_text_override or _cta_text_color(color)
            horizontal_padding = 28 if is_managerium else 24
            style = (f'display:inline-block;background:{color};color:{label_color};'
                      f'text-decoration:none;padding:13px {horizontal_padding}px;border-radius:8px;'
                     f'font-size:14px;font-weight:bold;white-space:nowrap;')
            return f'<a href="{_esc(link)}" style="{style}">{_esc(text)}</a>'

        if len(buttons) == 1:
            one_label, one_url, one_color, one_text = buttons[0]
            parts.append(f'<div style="margin:2px 0 0;">'
                         f'{button_html(one_label, one_url, one_color, one_text)}</div>')
        else:
            # Two or more: side by side in one centred row. A table (not floats or flex)
            # because Outlook and older clients ignore those - and each button sits in its
            # own cell so the rounded block stays a block, not a stretched bar.
            cells = "".join(
                f'<td align="center" valign="top" style="padding:0 5px 10px;">'
                f'{button_html(text, link, own, own_text)}</td>'
                for text, link, own, own_text in buttons
            )
            parts.append(
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                'align="center" style="margin:2px auto 0;"><tr>' + cells + '</tr></table>'
            )

    default_signer = "Sayadul Islam Chowdhury" if is_managerium else brand_name.title()
    parts.append(f'<p style="margin:28px 0 0;font-size:14px;color:{text_color};">'
                 f'Best regards,<br>{_esc(default_signer if is_managerium else (signer or default_signer))}</p>')

    if unsubscribe_url:
        parts.append(
            f'<p style="margin:22px 0 0;padding-top:14px;border-top:1px solid {TEMPLATE_RULE};'
            f'font-size:11.5px;line-height:1.6;color:#8A8A9A;">'
            f'You are receiving this because you opted in to updates from {_esc(brand_name)}. '
            f'<a href="{_esc(unsubscribe_url)}" style="color:#8A8A9A;text-decoration:underline;">'
            f'Unsubscribe</a> to stop receiving these emails.</p>'
        )

    content = "".join(parts)
    # Author instruction: no certainty disclaimers in the footer either.
    disclaimer = ""
    if is_coffee_offer:
        disclaimer += (" Starbucks and its marks are referenced only for the supplied price comparison. "
                       "No partnership, sponsorship, or endorsement is implied.")

    managerium_marker = "<!-- Managerium ERP Email Template -->" if is_managerium else ""
    footer_tagline = ("An iBOS Business Solution by AKIJ Resource"
                      if is_managerium else tagline)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(headline or brand_name)}</title>
<style>
  body,table,td,a{{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;}}
  table,td{{mso-table-lspace:0pt;mso-table-rspace:0pt;}}
  @media only screen and (max-width:620px){{
    .sw-card{{width:100%!important;border-radius:0!important;}}
    .sw-body{{padding:24px 20px!important;}}
  }}
</style>
</head>
{managerium_marker}

<body style="margin:0;padding:0;background:{page_bg};font-family:{FONT};">

<table width="100%" cellpadding="0" cellspacing="0" style="padding:30px 0;background:{page_bg};">
<tr>
<td align="center">

<table class="sw-card" width="600" cellpadding="0" cellspacing="0"
style="background:{TEMPLATE_CARD};border-radius:12px;overflow:hidden;border-top:4px solid {primary};">

<!-- Header -->
<tr>
<td style="padding:16px 32px;border-bottom:1px solid {TEMPLATE_RULE};">
<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td align="left">{brand_mark}</td>
<td align="right">
<p style="margin:0;color:{primary};font-size:12px;font-weight:bold;">{_esc(tagline)}</p>
</td>
</tr>
</table>
</td>
</tr>

<!-- Body -->
<tr>
<td class="sw-body" style="padding:32px;">
{content}
</td>
</tr>

<!-- Footer -->
<tr>
<td style="background:{primary};padding:18px;text-align:center;">
<p style="margin:0;color:#ffffff;font-size:14px;font-weight:bold;">{_esc(brand_name)}</p>
<p style="margin:5px 0 0;color:{footer_accent};font-size:11px;">{_esc(footer_tagline)}</p>
{f'<p style="margin:8px 0 0;color:{footer_accent};font-size:10px;line-height:1.5;">{_esc(disclaimer)}</p>' if disclaimer else ''}
</td>
</tr>

</table>

</td>
</tr>
</table>

</body>
</html>"""
