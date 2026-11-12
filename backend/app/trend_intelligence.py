"""Credential-free, draft-only trend intelligence pipeline."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterable, List
from zoneinfo import ZoneInfo

from . import models


PRODUCT_CONFIG: Dict[str, Dict[str, Any]] = {
    "managerium": {"name": "Managerium ERP", "geography": "Bangladesh", "keywords": ["erp software bangladesh", "erp software", "retail erp software", "manufacturing erp"], "competitors": ["PrismERP", "PrideERP", "Southtech ERP", "SAP Business One", "Microsoft Dynamics 365", "Oracle NetSuite", "Odoo", "ERPNext"], "cta": "Explore Managerium ERP", "cta_url": "https://ibos.io/managerium/"},
    "peopledesk": {"name": "PeopleDesk HRMS", "geography": "Bangladesh", "keywords": ["hr software bangladesh", "payroll software", "hrms", "hr and payroll software"], "competitors": ["Empress HR", "BIPO HRMS", "MIHCM", "PeoplesHR", "Jibika Plexus HRM", "PiHR", "Systech HR", "RedDot Digital HRMS"], "cta": "Explore PeopleDesk", "cta_url": "https://ibos.io/best-hr-software-in-bangladesh/"},
    "primevat": {"name": "PrimeVAT", "geography": "Bangladesh", "keywords": ["vat management software bangladesh", "vat software bangladesh", "vat management software"], "competitors": ["OVAT", "Uddogi", "VATPro", "PrismVAT", "iVAT"], "cta": "Explore PrimeVAT", "cta_url": "https://ibos.io/vat-management-software-solution-primevat/"},
    "trackforce": {"name": "TrackForce", "geography": "Global", "keywords": ["employee monitoring software", "employee monitoring", "productivity tracking software", "workforce analytics"], "competitors": ["ActivTrak", "Hubstaff", "Time Doctor", "Teramind", "DeskTime"], "cta": "Explore TrackForce", "cta_url": "https://trackforce.io/"},
    "turbo_addons": {"name": "Turbo Add-ons", "geography": "Global", "keywords": ["elementor addons", "elementor widgets", "wordpress addons"], "competitors": ["Essential Addons for Elementor", "ElementsKit", "HappyAddons", "Premium Addons for Elementor", "Ultimate Addons for Elementor", "PowerPack Addons for Elementor", "The Plus Addons for Elementor", "Royal Elementor Addons"], "cta": "Explore Turbo Add-ons", "cta_url": "https://turbo-addons.com/"},
    "turbo_guard": {"name": "Turbo Guard", "geography": "Global", "keywords": ["wordpress malware recovery", "wordpress malware cleanup", "wordpress seo spam removal", "wordpress security plugin"], "competitors": ["Wordfence", "MalCare", "Sucuri", "Kadence Security", "All-In-One Security", "Jetpack Protect", "Shield Security", "SecuPress"], "cta": "Scan Your Site Free", "cta_url": "https://wordpress.org/plugins/turbo-guard/"},
    "travilo": {"name": "Travilo", "geography": "Bangladesh", "keywords": ["travel agency software", "travel erp", "booking engine", "ota software"], "competitors": ["Trawex", "Travelopro", "FlightsLogic", "Travelomatix", "PHPTRAVELS", "TravelCarma", "Juniper Booking Engine", "Technoheaven"], "cta": "Explore Travilo", "cta_url": "https://travilo.io/"},
    "aeroapps": {"name": "AeroApps (Shopify)", "geography": "Global", "keywords": ["shopify apps", "shopify product options app", "shopify form builder"], "competitors": ["Easify Custom Product Options", "Globo Product Options", "Color Swatch King", "Rapid Bundles & Upsell", "Easy Bundles", "Powerful Contact Form Builder", "Dropshipman", "Smart SEO AI"], "cta": "Explore AeroApps", "cta_url": "https://aeroapps.io/"},
    "aerodrop": {"name": "AeroDrop", "geography": "Global", "keywords": ["dropshipping", "shopify dropshipping", "dropshipping app"], "competitors": ["DSers", "AutoDS", "Zendrop", "Spocket", "CJdropshipping", "Dropshipman", "EPROLO", "Syncee"], "cta": "Explore AeroDrop", "cta_url": "https://aerodrop.ai/"},
}

GEO_CODE = {"Bangladesh": "BD", "Global": "", "": ""}
BROWSER_UA = "Mozilla/5.0 (compatible; SendwiseTrend/1.0; +https://ibos.io/)"
FRESH_DAYS = 30
DEFAULT_CACHE_HOURS = 12
TRENDS_CACHE_HOURS = 72
DISCOVERY_CACHE_HOURS = 24 * 7
GOOGLE_TRENDS_COOLDOWN_HOURS = 6
CAMPAIGN_TREND_FRESH_DAYS = 7
FACEBOOK_IDENTITY_THRESHOLD = 0.80
WEBSITE_IDENTITY_THRESHOLD = 0.80
INTENT_WORDS = {"best", "buy", "choose", "compare", "cost", "pricing", "recommend", "software", "tool", "app", "alternative", "how", "need"}
STOP_WORDS = {"a", "an", "and", "are", "for", "from", "in", "is", "of", "on", "the", "to", "with", "your", "bangladesh"}
UNAVAILABLE_SOURCES = {
    "gsc": {"status": "not_connected", "detail": "Google Search Console credentials are required"},
}
_google_trends_cooldown_until: datetime | None = None

_CAMPAIGN_PRODUCT_ALIASES = {
    "turbo guards": "turbo_guard",
    "turbo guard": "turbo_guard",
    "turbo": "turbo_addons",
    "turbo addons": "turbo_addons",
    "turbo add ons": "turbo_addons",
    "managerium": "managerium",
    "managerium erp": "managerium",
    "peopledesk": "peopledesk",
    "peopledesk hrms": "peopledesk",
    "primevat": "primevat",
    "prime vat": "primevat",
    "trackforce": "trackforce",
    "track force": "trackforce",
    "travilo": "travilo",
    "aeroapps": "aeroapps",
    "aero apps": "aeroapps",
    "aeroapps shopify": "aeroapps",
    "aerodrop": "aerodrop",
    "aero drop": "aerodrop",
}


class ConnectorError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status


class _FeedLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        if "alternate" in values.get("rel", "").lower() and values.get("type", "").lower() in {
            "application/rss+xml", "application/atom+xml"
        } and values.get("href"):
            self.urls.append(values["href"])


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def campaign_product_key(product: str) -> str | None:
    """Resolve only explicit supported aliases; unknown products never inherit another profile."""
    normalized = re.sub(r"[^a-z0-9]+", " ", str(product or "").lower()).strip()
    return _CAMPAIGN_PRODUCT_ALIASES.get(normalized)


def _clean_context_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _context(status: str, product_key: str | None, **values: Any):
    evidence = tuple(
        MappingProxyType({
            "id": item["id"],
            "title": _clean_context_text(item.get("title"), 240),
            "source_url": item["source_url"],
        })
        for item in values.pop("evidence", ())
    )
    return MappingProxyType({"status": status, "product_key": product_key, **values, "evidence": evidence})


def _eligible_campaign_opportunity(db, product_key: str, now: datetime):
    cutoff = now - timedelta(days=CAMPAIGN_TREND_FRESH_DAYS)
    candidates = (
        db.query(models.TrendOpportunity)
        .filter(
            models.TrendOpportunity.product_key == product_key,
            models.TrendOpportunity.selected.is_(True),
            models.TrendOpportunity.fatigue_blocked.is_(False),
            models.TrendOpportunity.created_at >= cutoff,
        )
        .order_by(models.TrendOpportunity.score.desc(), models.TrendOpportunity.created_at.desc())
        .all()
    )
    for opportunity in candidates:
        run = db.query(models.TrendRun).filter(models.TrendRun.id == opportunity.run_id).first()
        qa = opportunity.qa_json or []
        if not run or run.status != "completed" or not run.completed_at or not qa or not all(row.get("pass") for row in qa):
            continue
        evidence_rows = (
            db.query(models.TrendEvidence)
            .filter(
                models.TrendEvidence.opportunity_id == opportunity.id,
                models.TrendEvidence.product_key == product_key,
            )
            .order_by(models.TrendEvidence.id)
            .all()
        )
        fresh_evidence = any(
            (_utc(row.published_at) or _utc(row.observed_at) or cutoff) >= cutoff
            for row in evidence_rows
        )
        if not evidence_rows or not fresh_evidence:
            continue
        evidence = [
            {"id": row.id, "title": row.title, "source_url": row.source_url}
            for row in evidence_rows
            if row.source_url and urllib.parse.urlparse(row.source_url).scheme in {"http", "https"}
        ]
        if not evidence:
            continue
        rationale = (opportunity.draft_json or {}).get("preheader") or "Validated independent signals support this editorial angle."
        return _context(
            "grounded", product_key,
            run_id=run.id,
            opportunity_id=opportunity.id,
            topic=_clean_context_text(opportunity.topic, 180),
            score=opportunity.score,
            confidence=_clean_context_text(opportunity.confidence, 20),
            rationale=_clean_context_text(rationale, 300),
            analyzed_at=_utc(run.completed_at).isoformat(),
            evidence=evidence,
        )
    return None


def campaign_trend_context(db, product: str, settings: Dict[str, Any] | None = None,
                           now: datetime | None = None):
    """Obtain one validated, product-only trend context for normal copy generation."""
    now = now or datetime.now(timezone.utc)
    key = campaign_product_key(product)
    if not key:
        return _context("unsupported_product", None)
    settings = settings or {}
    if not as_bool(settings.get("trend_intelligence_enabled", False)):
        return _context("disabled", key)

    existing = _eligible_campaign_opportunity(db, key, now)
    if existing:
        return existing
    try:
        result = run_trend_analysis(db, settings, scheduled=False, product_keys=[key])
    except Exception:  # Copy generation must remain available when research fails.
        db.rollback()
        return _context("analysis_failed", key)
    if result.get("status") == "failed":
        return _context("analysis_failed", key, run_id=result.get("id"),
                        analyzed_at=result.get("completed_at"))
    selected = _eligible_campaign_opportunity(db, key, datetime.now(timezone.utc))
    if selected:
        return selected
    return _context("no_validated_opportunity", key, run_id=result.get("id"),
                    analyzed_at=result.get("completed_at"))


def trend_provenance(context: Any) -> Dict[str, Any]:
    """Return the JSON-safe subset stored with each generated email."""
    evidence = context.get("evidence") or ()
    return {
        "status": context.get("status"),
        "run_id": context.get("run_id"),
        "opportunity_id": context.get("opportunity_id"),
        "topic": context.get("topic"),
        "score": context.get("score"),
        "confidence": context.get("confidence"),
        "evidence_ids": [row.get("id") for row in evidence],
        "evidence_urls": [row.get("source_url") for row in evidence],
        "analyzed_at": context.get("analyzed_at"),
    }


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def normalize_topic(value: str) -> str:
    text = re.sub(r"[^a-z0-9\s]", " ", str(value or "").lower())
    words = [word for word in text.split() if word not in STOP_WORDS]
    return " ".join(words)


def _tokens(value: str) -> set[str]:
    return set(normalize_topic(value).split())


def topic_similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return max(len(a & b) / len(a | b), SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio())


def cluster_topics(evidence: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for item in evidence:
        topic = item.get("topic") or item.get("title") or ""
        match = next((row for row in clusters if topic_similarity(row["topic"], topic) >= 0.72), None)
        if match:
            match["evidence"].append(item)
        else:
            clusters.append({"topic": topic.strip(), "normalized_topic": normalize_topic(topic), "evidence": [item]})
    return clusters


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    try:
        return _utc(parsedate_to_datetime(str(value)))
    except (TypeError, ValueError, OverflowError):
        try:
            return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except (TypeError, ValueError):
            return None


def _fetch(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node: ET.Element, names: set[str]) -> str:
    for child in node.iter():
        if _local_name(child.tag) in names and (child.text or "").strip():
            return (child.text or "").strip()
    return ""


def _parse_feed(xml: str, limit: int = 8) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml)
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    out = []
    for entry in entries[:limit]:
        link = _child_text(entry, {"link"})
        if not link:
            link_node = next((node for node in entry.iter() if _local_name(node.tag) == "link"), None)
            link = link_node.get("href", "") if link_node is not None else ""
        out.append({
            "title": _child_text(entry, {"title"}),
            "url": link,
            "published_at": _child_text(entry, {"pubDate", "published", "updated", "date"}) or None,
            "description": _child_text(entry, {"description", "summary"}),
        })
    return out


def _bing_search_rss(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "format": "rss"})
    return _parse_feed(_fetch(url), limit)


def _quora_discovery(keyword: str, limit: int = 6) -> List[Dict[str, Any]]:
    rows = _bing_search_rss(f"site:quora.com {keyword}", limit * 2)
    return [
        {"title": row["title"], "url": row["url"], "published_at": row.get("published_at")}
        for row in rows
        if (urllib.parse.urlparse(row.get("url", "")).hostname or "").lower() in {"quora.com", "www.quora.com"}
    ][:limit]


def _host_tokens(url: str) -> set[str]:
    host = (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    return set(re.findall(r"[a-z0-9]+", host.rsplit(".", 1)[0]))


def _identity_confidence(name: str, row: Dict[str, Any], category: str, facebook: bool = False) -> tuple[float, Dict[str, bool]]:
    name_tokens = _tokens(name)
    text = f"{row.get('title', '')} {row.get('description', '')}"
    text_tokens = _tokens(text)
    host = (urllib.parse.urlparse(row.get("url", "")).hostname or "").lower()
    name_match = bool(name_tokens) and len(name_tokens & text_tokens) / len(name_tokens) >= 0.75
    domain_match = bool(name_tokens & _host_tokens(row.get("url", "")))
    if facebook:
        domain_signals = re.findall(r"(?:https?://)?(?:www\.)?([a-z0-9-]+\.[a-z]{2,})", text.lower())
        domain_match = domain_match or any(name_tokens & _host_tokens(f"https://{domain}") for domain in domain_signals)
    category_match = bool(_tokens(category) & text_tokens)
    platform_match = host == "facebook.com" or host.endswith(".facebook.com")
    confidence = (0.50 if name_match else 0) + (0.30 if domain_match else 0) + (0.20 if category_match else 0)
    if facebook and not platform_match:
        confidence = 0
    return round(confidence, 2), {"name": name_match, "domain": domain_match, "category": category_match}


def _facebook_page_discovery(competitor: str, category: str, limit: int = 5) -> List[Dict[str, Any]]:
    rows = _bing_search_rss(f'site:facebook.com "{competitor}" official website {category}', limit)
    out = []
    for row in rows:
        confidence, signals = _identity_confidence(competitor, row, category, facebook=True)
        verified = confidence >= FACEBOOK_IDENTITY_THRESHOLD
        out.append({
            "competitor": competitor,
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "published_at": row.get("published_at"),
            "identity_confidence": confidence,
            "identity_signals": signals,
            "verification_status": "verified" if verified else "needs_verification",
            "verification_label": "Verified" if verified else "Needs Verification",
        })
    return out


def _official_website_discovery(competitor: str, category: str, limit: int = 6) -> Dict[str, Any] | None:
    excluded = {"facebook.com", "linkedin.com", "youtube.com", "x.com", "twitter.com", "instagram.com", "wikipedia.org"}
    for row in _bing_search_rss(f'"{competitor}" official website {category}', limit):
        host = (urllib.parse.urlparse(row.get("url", "")).hostname or "").lower().removeprefix("www.")
        if not host or any(host == value or host.endswith(f".{value}") for value in excluded):
            continue
        confidence, signals = _identity_confidence(competitor, row, category)
        if confidence >= WEBSITE_IDENTITY_THRESHOLD:
            parsed = urllib.parse.urlparse(row["url"])
            return {"website": f"{parsed.scheme or 'https'}://{parsed.netloc}/", "identity_confidence": confidence, "identity_signals": signals}
    return None


def _website_feed_rows(website: str, limit: int = 8) -> Dict[str, Any]:
    homepage = _fetch(website)
    parser = _FeedLinkParser()
    parser.feed(homepage)
    candidates = [urllib.parse.urljoin(website, url) for url in parser.urls]
    candidates.extend(urllib.parse.urljoin(website, path) for path in ("/feed/", "/rss.xml", "/feed.xml", "/blog/feed/", "/atom.xml"))
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            rows = _parse_feed(_fetch(url), limit)
            if rows:
                return {"kind": "feed", "feed_url": url, "items": rows}
        except Exception:  # Candidate paths commonly are absent or not XML feeds.
            continue
    sitemap_url = urllib.parse.urljoin(website, "/sitemap.xml")
    try:
        root = ET.fromstring(_fetch(sitemap_url))
        rows = []
        for node in root.iter():
            if _local_name(node.tag) != "url":
                continue
            url = _child_text(node, {"loc"})
            if url:
                title = urllib.parse.unquote(urllib.parse.urlparse(url).path).strip("/").replace("-", " ") or url
                rows.append({"title": title, "url": url, "published_at": _child_text(node, {"lastmod"}) or None})
        if rows:
            return {"kind": "sitemap", "feed_url": sitemap_url, "items": rows[:limit]}
    except Exception:
        pass
    return {"kind": "none", "feed_url": None, "items": []}


def _trends_rss(geography: str, limit: int = 20) -> List[Dict[str, Any]]:
    geo = "BD" if geography == "Bangladesh" else "US"
    url = "https://trends.google.com/trending/rss?" + urllib.parse.urlencode({"geo": geo})
    rows = _parse_feed(_fetch(url), limit)
    for row in rows:
        row.update({"fallback": True, "keyword_specific": False, "geography": geography, "geo_code": geo, "feed_url": url})
    return rows


def _retry(call: Callable[[], Any], attempts: int = 2) -> Any:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, ConnectorError) and exc.status == "rate_limited":
                raise
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.6 * (2 ** attempt))
    raise last or RuntimeError("connector failed")


def _google_trends(keywords: List[str], geography: str, timeframe: str = "today 3-m") -> List[Dict[str, Any]]:
    """Return relative Google Trends indexes, never search volume."""
    global _google_trends_cooldown_until
    now = datetime.now(timezone.utc)
    if _google_trends_cooldown_until and now < _google_trends_cooldown_until:
        raise ConnectorError("rate_limited", f"Google Trends cooldown until {_google_trends_cooldown_until.isoformat()}")
    try:
        from pytrends.request import TrendReq
    except ImportError as exc:
        # pytrends/pandas are deliberately not installed (large, build-flaky wheels).
        # Report it as an unavailable connector so the rest of the app stays healthy.
        raise ConnectorError(
            "unavailable",
            "Google Trends connector is not installed (pytrends/pandas absent)",
        ) from exc
    selected = keywords[:4]  # one batched request per product
    try:
        pt = TrendReq(hl="en-US", tz=0, timeout=(8, 18), retries=0, backoff_factor=0)
        pt.build_payload(selected, timeframe=timeframe, geo=GEO_CODE.get(geography, ""))
        frame = pt.interest_over_time()
    except Exception as exc:
        detail = str(exc)
        if "429" in detail or "/sorry" in detail.lower() or "too many requests" in detail.lower():
            _google_trends_cooldown_until = now + timedelta(hours=GOOGLE_TRENDS_COOLDOWN_HOURS)
            raise ConnectorError("rate_limited", "Google Trends rate limited; cooldown activated") from exc
        raise
    reference = "https://trends.google.com/trends/explore?" + urllib.parse.urlencode({"q": ",".join(selected), "geo": GEO_CODE.get(geography, "")})
    if frame is None or frame.empty:
        return []
    out = []
    for keyword in selected:
        values = [int(value) for value in frame[keyword].tolist()]
        recent, previous = values[-7:], values[-14:-7]
        recent_mean = sum(recent) / len(recent) if recent else 0
        previous_mean = sum(previous) / len(previous) if previous else recent_mean
        out.append({"keyword": keyword, "relative_interest_recent": round(recent_mean, 1), "momentum": round(recent_mean - previous_mean, 1), "points": len(values), "url": reference})
    return out


def _reddit_search(query: str, limit: int = 6) -> List[Dict[str, Any]]:
    url = "https://www.reddit.com/search.rss?" + urllib.parse.urlencode({"q": query, "limit": limit, "sort": "relevance", "t": "month"})
    root = ET.fromstring(_fetch(url))
    ns = "{http://www.w3.org/2005/Atom}"
    out = []
    for entry in root.findall(f".//{ns}entry")[:limit]:
        link = entry.find(f"{ns}link")
        out.append({"title": entry.findtext(f"{ns}title") or "", "url": link.get("href") if link is not None else "", "published_at": entry.findtext(f"{ns}published") or entry.findtext(f"{ns}updated")})
    return out


def _news_rss(query: str, geography: str, limit: int = 6) -> List[Dict[str, Any]]:
    local = geography == "Bangladesh"
    params = {"q": query, "hl": "en-BD" if local else "en-US", "gl": "BD" if local else "US", "ceid": "BD:en" if local else "US:en"}
    root = ET.fromstring(_fetch("https://news.google.com/rss/search?" + urllib.parse.urlencode(params)))
    return [{"title": item.findtext("title") or "", "source": item.findtext("source") or "", "url": item.findtext("link") or "", "published_at": item.findtext("pubDate")} for item in root.findall(".//item")[:limit]]


def _cache_key(source: str, product_key: str, query: Any) -> str:
    raw = json.dumps([source, product_key, query], sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cached(db, source: str, product_key: str, query: Any, loader: Callable[[], Any], ttl_hours: int = DEFAULT_CACHE_HOURS) -> tuple[Any, bool]:
    now = datetime.now(timezone.utc)
    key = _cache_key(source, product_key, query)
    row = db.query(models.TrendSourceCache).filter(models.TrendSourceCache.cache_key == key).first()
    if row and _utc(row.expires_at) > now:
        return row.payload_json, True
    time.sleep(0.25)  # keep the credential-free public endpoints below burst rates
    payload = _retry(loader)
    if row:
        row.payload_json, row.fetched_at, row.expires_at = payload, now, now + timedelta(hours=ttl_hours)
    else:
        db.add(models.TrendSourceCache(cache_key=key, source=source, payload_json=payload, fetched_at=now, expires_at=now + timedelta(hours=ttl_hours)))
    db.commit()
    return payload, False


def _health(status: str = "ok", detail: str = "") -> Dict[str, Any]:
    return {"status": status, "detail": detail, "requests": 0, "cached": 0, "items": 0, "errors": []}


def _record_rows(evidence: List[Dict[str, Any]], rows: list, source: str, topic: str, now: datetime, competitor: bool = False) -> int:
    added = 0
    for row in rows:
        published = _parse_date(row.get("published_at"))
        if source not in {"google_trends", "google_trends_rss"} and (published is None or now - published > timedelta(days=FRESH_DAYS)):
            continue
        title, url = row.get("title", ""), row.get("url", "")
        if not title or not url:
            continue
        evidence.append({
            "source": source,
            "topic": row.get("keyword") or topic or title,
            "title": title,
            "source_url": url,
            "published_at": published,
            "observed_at": now,
            "competitor": competitor,
            "data": row,
        })
        added += 1
    return added


def collect_product_signals(db, product_key: str, cfg: Dict[str, Any], settings: Dict[str, Any] | None = None) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    settings = settings or {}
    now = datetime.now(timezone.utc)
    evidence: List[Dict[str, Any]] = []
    health = {key: dict(value) for key, value in UNAVAILABLE_SOURCES.items()}
    cache_hours = max(1, int(settings.get("trend_source_cache_ttl_hours") or DEFAULT_CACHE_HOURS))
    trends_cache_hours = max(TRENDS_CACHE_HOURS, int(settings.get("trend_google_trends_cache_ttl_hours") or TRENDS_CACHE_HOURS))
    calls = [
        ("reddit", cfg["keywords"][0], lambda: _reddit_search(cfg["keywords"][0]), False),
        ("google_news", cfg["keywords"][0], lambda: _news_rss(cfg["keywords"][0], cfg["geography"]), False),
        ("google_news", "competitors", lambda: _news_rss(f"({cfg['competitors'][0]} OR {cfg['competitors'][1]}) {cfg['keywords'][0]}", cfg["geography"], 4), True),
        ("bing", cfg["keywords"][0], lambda: _bing_search_rss(cfg["keywords"][0], 6), False),
        ("quora", cfg["keywords"][0], lambda: _quora_discovery(cfg["keywords"][0]), False),
    ]
    for source, query, loader, competitor in calls:
        health.setdefault(source, _health())
        health[source]["requests"] += 1
        try:
            rows, cached = _cached(db, source, product_key, query, loader, cache_hours)
            health[source]["cached"] += int(cached)
            health[source]["items"] += len(rows)
            _record_rows(evidence, rows, source, cfg["keywords"][0], now, competitor)
        except Exception as exc:  # noqa: BLE001
            health[source]["status"] = exc.status if isinstance(exc, ConnectorError) else "error"
            health[source]["errors"].append(str(exc)[:180])

    health["google_trends"] = _health()
    health["google_trends"]["requests"] = 1
    try:
        rows, cached = _cached(db, "google_trends", product_key, cfg["keywords"], lambda: _google_trends(cfg["keywords"], cfg["geography"]), trends_cache_hours)
        health["google_trends"].update({"cached": int(cached), "items": len(rows), "status": "ok" if rows else "partial"})
        for row in rows:
            _record_rows(evidence, [{**row, "title": f"Google Trends: {row['keyword']}"}], "google_trends", row["keyword"], now)
    except Exception as exc:  # noqa: BLE001
        health["google_trends"]["status"] = exc.status if isinstance(exc, ConnectorError) else "error"
        health["google_trends"]["errors"].append(str(exc)[:180])

    if health["google_trends"]["status"] != "ok":
        health["google_trends_rss"] = _health(detail="Daily trending topics fallback; not keyword-specific interest")
        health["google_trends_rss"]["requests"] = 1
        try:
            rows, cached = _cached(db, "google_trends_rss", product_key, cfg["geography"], lambda: _trends_rss(cfg["geography"]), cache_hours)
            health["google_trends_rss"].update({"cached": int(cached), "items": len(rows), "status": "ok" if rows else "unavailable"})
            _record_rows(evidence, rows, "google_trends_rss", "", now)
        except Exception as exc:  # noqa: BLE001
            health["google_trends_rss"]["status"] = "error"
            health["google_trends_rss"]["errors"].append(str(exc)[:180])

    health["facebook"] = _health(detail="Official Page identity discovery only; organic post monitoring is unavailable")
    health["competitor_feeds"] = _health()
    for competitor in cfg["competitors"][:2]:
        category = cfg["keywords"][0]
        health["facebook"]["requests"] += 1
        try:
            rows, cached = _cached(db, "facebook_discovery", product_key, competitor, lambda competitor=competitor: _facebook_page_discovery(competitor, category), DISCOVERY_CACHE_HOURS)
            health["facebook"]["cached"] += int(cached)
            health["facebook"]["items"] += len(rows)
            health["facebook"].setdefault("pages", []).extend(rows)
        except Exception as exc:  # noqa: BLE001
            health["facebook"]["status"] = "partial" if health["facebook"]["items"] else "error"
            health["facebook"]["errors"].append(str(exc)[:180])

        health["competitor_feeds"]["requests"] += 1
        try:
            website, cached = _cached(db, "competitor_website", product_key, competitor, lambda competitor=competitor: _official_website_discovery(competitor, category), DISCOVERY_CACHE_HOURS)
            health["competitor_feeds"]["cached"] += int(cached)
            if not website:
                health["competitor_feeds"].setdefault("unavailable", []).append(competitor)
                continue
            mapping, mapping_cached = _cached(db, "competitor_feed_mapping", product_key, website["website"], lambda: _website_feed_rows(website["website"]), cache_hours)
            health["competitor_feeds"]["cached"] += int(mapping_cached)
            health["competitor_feeds"].setdefault("mappings", []).append({"competitor": competitor, **website, "feed_url": mapping.get("feed_url"), "kind": mapping.get("kind")})
            health["competitor_feeds"]["items"] += len(mapping.get("items", []))
            _record_rows(evidence, mapping.get("items", []), "competitor_feeds", category, now, True)
        except Exception as exc:  # noqa: BLE001
            health["competitor_feeds"]["status"] = "partial" if health["competitor_feeds"]["items"] else "error"
            health["competitor_feeds"]["errors"].append(str(exc)[:180])
    if health["facebook"]["status"] == "ok" and not any(page.get("verification_status") == "verified" for page in health["facebook"].get("pages", [])):
        health["facebook"]["status"] = "unavailable"
    if health["competitor_feeds"]["status"] == "ok" and not health["competitor_feeds"]["items"]:
        health["competitor_feeds"]["status"] = "unavailable"
    return evidence, health


def _campaign_texts(db, product_key: str) -> List[str]:
    rows = db.query(models.Campaign).order_by(models.Campaign.id.desc()).limit(100).all()
    texts = []
    for campaign in rows:
        if campaign_product_key(campaign.product) != product_key:
            continue
        texts.extend([campaign.title or "", campaign.offer or ""])
        for email in campaign.emails_json or []:
            texts.extend([email.get("subject", ""), email.get("preview_text", ""), email.get("body", "")])
    return [text for text in texts if text]


def _fatigue(topic: str, campaign_texts: Iterable[str]) -> tuple[bool, str]:
    best = max((topic_similarity(topic, text) for text in campaign_texts), default=0.0)
    return best >= 0.72, f"similarity={best:.2f}" if best >= 0.72 else ""


def _score(cluster: Dict[str, Any], cfg: Dict[str, Any], now: datetime) -> tuple[int, Dict[str, int]]:
    items = cluster["evidence"]
    sources = {item["source"] for item in items}
    topic_tokens = _tokens(cluster["topic"])
    relevant = max((len(topic_tokens & _tokens(keyword)) / max(1, len(_tokens(keyword))) for keyword in cfg["keywords"]), default=0)
    relevance = round(30 * min(1.0, relevant))
    trend_rows = [item["data"] for item in items if item["source"] == "google_trends"]
    momentum_value = max((float(row.get("momentum") or 0) for row in trend_rows), default=0)
    interest = max((float(row.get("relative_interest_recent") or 0) for row in trend_rows), default=0)
    momentum = min(20, round(interest / 10 + max(0, momentum_value)))
    cross_source = 20 if len(sources) >= 3 else 12 if len(sources) == 2 else 0
    intent_hits = sum(bool(_tokens(item["title"]) & INTENT_WORDS) for item in items)
    customer_intent = min(10, round(10 * intent_hits / max(1, len(items))))
    competitor = 10 if any(item.get("competitor") for item in items) else 0
    dated = [item["published_at"] for item in items if item.get("published_at")]
    age = min(((now - _utc(value)).total_seconds() / 86400 for value in dated), default=0)
    freshness = 10 if age <= 3 else 8 if age <= 7 else 5 if age <= 14 else 2
    parts = {"business_relevance": relevance, "momentum": momentum, "cross_source": cross_source, "customer_intent": customer_intent, "competitor_activity": competitor, "freshness": freshness}
    return sum(parts.values()), parts


def analyze_evidence(product_key: str, cfg: Dict[str, Any], evidence: List[Dict[str, Any]], campaign_texts: Iterable[str] = (), source_errors: int = 0, now: datetime | None = None) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    opportunities = []
    fresh_evidence = [item for item in evidence if item.get("source") == "google_trends" or (item.get("published_at") and now - _utc(item["published_at"]) <= timedelta(days=FRESH_DAYS))]
    for cluster in cluster_topics(fresh_evidence):
        sources = sorted({item["source"] for item in cluster["evidence"]})
        if not any(topic_similarity(cluster["topic"], keyword) >= 0.72 for keyword in cfg["keywords"]):
            continue
        score, parts = _score(cluster, cfg, now)
        validated = len(sources) >= 2
        blocked, reason = _fatigue(cluster["topic"], campaign_texts)
        recent = any(item["source"] == "google_trends" or (item.get("published_at") and now - _utc(item["published_at"]) <= timedelta(days=7)) for item in cluster["evidence"])
        confidence = "High" if len(sources) >= 3 and recent and source_errors == 0 else "Medium" if validated and recent and source_errors <= 1 else "Low"
        opportunities.append({**cluster, "sources": sources, "score": score, "score_components": parts, "confidence": confidence, "validated": validated, "selected": validated and score >= 60 and not blocked, "fatigue_blocked": blocked, "fatigue_reason": reason})
    return sorted(opportunities, key=lambda row: (-row["score"], row["normalized_topic"]))


def _draft(cfg: Dict[str, Any], opportunity: Dict[str, Any]) -> Dict[str, str]:
    topic = opportunity["topic"]
    source_count = len(opportunity["sources"])
    subject = f"A practical look at {topic}"[:100]
    preheader = f"Recent independent signals point to questions around {topic}."[:140]
    body = (f"Hi there,\n\nRecent discussions and coverage are highlighting {topic}. "
            f"We found supporting evidence from {source_count} independent source types.\n\n"
            f"If {topic} is on your team's agenda, this is a useful time to review your current workflow and the gaps you want to remove.\n\n"
            f"{cfg['cta']} -> {cfg['cta_url']}\n\nBest regards,\n{cfg['name']} Team\n\n"
            "You are receiving this email from our marketing list. Unsubscribe: *|UNSUB|*\n*|LIST:ADDRESS|*")
    return {"subject": subject, "preheader": preheader, "body": body, "cta_label": cfg["cta"], "cta_url": cfg["cta_url"]}


def _qa(opportunity: Dict[str, Any], draft: Dict[str, str]) -> List[Dict[str, Any]]:
    checks = [
        ("Independent evidence", opportunity["validated"]),
        ("Source URLs preserved", all(item.get("source_url") for item in opportunity["evidence"])),
        ("No unsupported numeric claims", not re.search(r"\b\d+(?:\.\d+)?%|\b\d{3,}\+?\b", draft["body"])),
        ("Single product scope", True),
        ("CTA is configured", draft["cta_url"].startswith("https://")),
        ("Unsubscribe and address merge tags", "*|UNSUB|*" in draft["body"] and "*|LIST:ADDRESS|*" in draft["body"]),
        ("Draft only", True),
    ]
    return [{"name": name, "pass": passed} for name, passed in checks]


def _selected_product(key: str, cfg: Dict[str, Any], enabled_products: list) -> bool:
    if not enabled_products:
        return True
    aliases = {key, key.replace("_", " "), cfg["name"].lower(), cfg["name"].lower().split("(")[0].strip()}
    return any(any(alias in str(product).lower() or str(product).lower() in alias for alias in aliases) for product in enabled_products)


def select_product_keys(settings: Dict[str, Any], scheduled: bool = True, now: datetime | None = None) -> List[str]:
    """Choose two products in Dhaka time; weekdays rotate through all configured profiles."""
    explicit = _as_list(settings.get("trend_product_keys"))
    enabled = _as_list(settings.get("products"))
    pool = [key for key, cfg in PRODUCT_CONFIG.items() if _selected_product(key, cfg, explicit or enabled)]
    if not pool:
        pool = list(PRODUCT_CONFIG)
    if not scheduled and as_bool(settings.get("trend_manual_all_products", False)):
        return pool
    if not as_bool(settings.get("trend_rotation_enabled", True)):
        return pool[:2]
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Dhaka"))
    if local.weekday() <= 4:
        start = local.weekday() * 2
    else:
        # Weekends retain Friday's pair.
        start = 8
    return [pool[index % len(pool)] for index in range(start, start + min(2, len(pool)))]


def serialize_opportunity(row: models.TrendOpportunity) -> Dict[str, Any]:
    return {"id": row.id, "run_id": row.run_id, "product_key": row.product_key, "product": row.product_name, "topic": row.topic, "score": row.score, "score_components": row.score_json or {}, "confidence": row.confidence, "independent_source_count": row.independent_source_count, "selected": row.selected, "fatigue_blocked": row.fatigue_blocked, "fatigue_reason": row.fatigue_reason, "draft": row.draft_json or {}, "qa": row.qa_json or [], "approval_status": row.approval_status, "created_at": row.created_at.isoformat() if row.created_at else None}


def serialize_run(db, run: models.TrendRun, include_evidence: bool = True) -> Dict[str, Any]:
    opportunities = db.query(models.TrendOpportunity).filter(models.TrendOpportunity.run_id == run.id).order_by(models.TrendOpportunity.score.desc()).all()
    output = {"id": run.id, "status": run.status, "mode": run.mode, "started_at": run.started_at.isoformat() if run.started_at else None, "completed_at": run.completed_at.isoformat() if run.completed_at else None, "products_scanned": run.products_scanned, "opportunities_found": run.opportunities_found, "drafts_created": run.drafts_created, "source_health": run.source_health_json or {}, "errors": run.error_json or [], "opportunities": [serialize_opportunity(row) for row in opportunities]}
    if include_evidence:
        rows = db.query(models.TrendEvidence).filter(models.TrendEvidence.run_id == run.id).all()
        output["evidence"] = [{"id": row.id, "opportunity_id": row.opportunity_id, "product_key": row.product_key, "source": row.source, "topic": row.topic, "title": row.title, "source_url": row.source_url, "published_at": row.published_at.isoformat() if row.published_at else None, "observed_at": row.observed_at.isoformat() if row.observed_at else None, "data": row.evidence_json or {}} for row in rows]
    return output


def run_trend_analysis(db, settings: Dict[str, Any], scheduled: bool = True,
                       product_keys: Iterable[str] | None = None) -> Dict[str, Any]:
    """Collect, validate, score and persist opportunities. This function never sends."""
    mode = str(settings.get("trend_email_mode") or "draft_only").strip().lower()
    if mode != "draft_only":
        mode = "draft_only"
    run = models.TrendRun(status="running", mode=mode, started_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.id
    all_health: Dict[str, Any] = {}
    errors = []
    products_scanned = opportunities_found = drafts_created = successful_connectors = 0
    selected_keys = set(product_keys) if product_keys is not None else set(select_product_keys(settings, scheduled=scheduled))
    selected_keys &= set(PRODUCT_CONFIG)
    try:
        for key, cfg in PRODUCT_CONFIG.items():
            if key not in selected_keys:
                continue
            products_scanned += 1
            evidence, health = collect_product_signals(db, key, cfg, settings)
            all_health[key] = health
            source_errors = sum(item.get("status") == "error" for item in health.values())
            confidence_penalty = source_errors + int(any(item.get("status") in {"not_connected", "unavailable"} for item in health.values()))
            successful_connectors += sum(item.get("status") in {"ok", "partial"} for name, item in health.items() if name in {"google_trends", "google_trends_rss", "reddit", "google_news", "bing", "quora"})
            errors.extend(f"{key}/{name}: {err}" for name, item in health.items() for err in item.get("errors", []))
            opportunities = analyze_evidence(key, cfg, evidence, _campaign_texts(db, key), confidence_penalty)
            for opportunity in opportunities:
                opportunities_found += 1
                draft = _draft(cfg, opportunity) if opportunity["selected"] else {}
                qa = _qa(opportunity, draft) if draft else []
                selected = bool(draft and all(check["pass"] for check in qa))
                drafts_created += int(selected)
                row = models.TrendOpportunity(run_id=run.id, product_key=key, product_name=cfg["name"], topic=opportunity["topic"], normalized_topic=opportunity["normalized_topic"], score=opportunity["score"], score_json=opportunity["score_components"], confidence=opportunity["confidence"], independent_source_count=len(opportunity["sources"]), selected=selected, fatigue_blocked=opportunity["fatigue_blocked"], fatigue_reason=opportunity["fatigue_reason"], draft_json=draft, qa_json=qa, approval_status="draft")
                db.add(row)
                db.flush()
                for item in opportunity["evidence"]:
                    db.add(models.TrendEvidence(run_id=run.id, opportunity_id=row.id, product_key=key, source=item["source"], topic=opportunity["topic"], title=item["title"], source_url=item["source_url"], published_at=item.get("published_at"), observed_at=item["observed_at"], evidence_json=item.get("data") or {}))
        keyless_attempts = products_scanned * 5
        if successful_connectors == 0:
            status = "failed"
        elif errors or successful_connectors < keyless_attempts:
            status = "partial_failure"
        else:
            status = "completed"
        run.status, run.completed_at = status, datetime.now(timezone.utc)
        run.products_scanned, run.opportunities_found, run.drafts_created = products_scanned, opportunities_found, drafts_created
        status_counts: Dict[str, int] = defaultdict(int)
        for product_health in all_health.values():
            for item in product_health.values():
                status_counts[item.get("status", "unknown")] += 1
        run.source_health_json = {"summary": dict(status_counts), "products": all_health}
        run.error_json = errors
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.query(models.TrendRun).filter(models.TrendRun.id == run_id).first()
        run.status, run.completed_at, run.error_json = "failed", datetime.now(timezone.utc), errors + [str(exc)[:300]]
        db.commit()
    return serialize_run(db, run)
