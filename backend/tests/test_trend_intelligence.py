import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
from app.trend_intelligence import (
    PRODUCT_CONFIG,
    ConnectorError,
    _bing_search_rss,
    _facebook_page_discovery,
    _quora_discovery,
    analyze_evidence,
    as_bool,
    cluster_topics,
    collect_product_signals,
    normalize_topic,
    run_trend_analysis,
    select_product_keys,
)


NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


def signal(source, title, days=1, **extra):
    return {
        "source": source,
        "topic": "elementor addons",
        "title": title,
        "source_url": f"https://example.com/{source}",
        "published_at": None if source == "google_trends" else NOW - timedelta(days=days),
        "observed_at": NOW,
        "competitor": extra.get("competitor", False),
        "data": extra.get("data", {}),
    }


class TrendIntelligenceTests(unittest.TestCase):
    def make_db(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_normalization_and_duplicate_clustering(self):
        self.assertEqual(normalize_topic("The Best Elementor Add-ons!"), "best elementor add ons")
        rows = [signal("reddit", "Elementor addons"), signal("google_news", "Elementor add-ons")]
        clusters = cluster_topics(rows)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]["evidence"]), 2)

    def test_cross_source_scoring_selection_and_fatigue(self):
        evidence = [
            signal("google_trends", "Google Trends: elementor addons", data={"relative_interest_recent": 70, "momentum": 5}),
            signal("reddit", "Best Elementor addons to choose"),
            signal("google_news", "Elementor addons comparison", competitor=True),
        ]
        rows = analyze_evidence("turbo_addons", PRODUCT_CONFIG["turbo_addons"], evidence, now=NOW)
        self.assertTrue(rows[0]["validated"])
        self.assertTrue(rows[0]["selected"])
        self.assertEqual(rows[0]["score"], sum(rows[0]["score_components"].values()))
        self.assertEqual(set(rows[0]["score_components"]), {"business_relevance", "momentum", "cross_source", "customer_intent", "competitor_activity", "freshness"})
        blocked = analyze_evidence("turbo_addons", PRODUCT_CONFIG["turbo_addons"], evidence, ["Elementor addons"], now=NOW)
        self.assertTrue(blocked[0]["fatigue_blocked"])
        self.assertFalse(blocked[0]["selected"])

    def test_single_source_cannot_validate_or_select(self):
        rows = analyze_evidence("turbo_addons", PRODUCT_CONFIG["turbo_addons"], [signal("reddit", "Best Elementor addons")], now=NOW)
        self.assertFalse(rows[0]["validated"])
        self.assertFalse(rows[0]["selected"])
        self.assertEqual(rows[0]["score_components"]["cross_source"], 0)

    def test_stale_and_cross_product_evidence_is_rejected(self):
        stale = signal("reddit", "Best Elementor addons", days=31)
        self.assertEqual(analyze_evidence("turbo_addons", PRODUCT_CONFIG["turbo_addons"], [stale], now=NOW), [])
        unrelated = signal("reddit", "Payroll software comparison")
        unrelated["topic"] = "payroll software"
        self.assertEqual(analyze_evidence("turbo_addons", PRODUCT_CONFIG["turbo_addons"], [unrelated], now=NOW), [])

    def test_robust_boolean_values(self):
        for value in (True, 1, "TRUE", " yes ", "on"):
            self.assertTrue(as_bool(value))
        for value in (False, 0, None, "false", "off"):
            self.assertFalse(as_bool(value))

    @patch("app.trend_intelligence._fetch")
    def test_bing_rss_parsing_preserves_title_url_and_date(self, fetch):
        fetch.return_value = """<rss><channel><item><title>ERP buying guide</title>
            <link>https://example.com/guide</link><description>Independent guide</description>
            <pubDate>Mon, 14 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>"""
        rows = _bing_search_rss("erp software")
        self.assertEqual(rows[0]["title"], "ERP buying guide")
        self.assertEqual(rows[0]["url"], "https://example.com/guide")
        self.assertEqual(rows[0]["published_at"], "Mon, 14 Sep 2026 08:00:00 GMT")
        self.assertIn("format=rss", fetch.call_args.args[0])

    @patch("app.trend_intelligence._bing_search_rss")
    def test_quora_returns_only_indexed_question_metadata(self, bing):
        bing.return_value = [
            {"title": "Which ERP is best?", "url": "https://www.quora.com/Which-ERP-is-best", "published_at": "2026-09-14", "description": "answer excerpt"},
            {"title": "Other result", "url": "https://example.com/erp", "published_at": "2026-09-14"},
        ]
        rows = _quora_discovery("erp software")
        self.assertEqual(rows, [{"title": "Which ERP is best?", "url": "https://www.quora.com/Which-ERP-is-best", "published_at": "2026-09-14"}])
        self.assertEqual(bing.call_args.args[0], "site:quora.com erp software")

    @patch("app.trend_intelligence._bing_search_rss")
    def test_low_confidence_facebook_page_needs_verification(self, bing):
        bing.return_value = [{"title": "PrideERP community", "url": "https://facebook.com/random-page", "published_at": "2026-09-14", "description": "Software discussions"}]
        rows = _facebook_page_discovery("PrideERP", "erp software bangladesh")
        self.assertEqual(rows[0]["verification_label"], "Needs Verification")
        self.assertLess(rows[0]["identity_confidence"], 0.8)

    def test_dhaka_rotation_covers_nine_products_and_keeps_two_per_day(self):
        settings = {"trend_rotation_enabled": "true"}
        weekdays = [datetime(2026, 9, day, 2, tzinfo=timezone.utc) for day in range(14, 19)]
        selected = [select_product_keys(settings, now=day) for day in weekdays]
        self.assertTrue(all(len(keys) == 2 for keys in selected))
        self.assertEqual(set(key for keys in selected for key in keys), set(PRODUCT_CONFIG))
        friday = select_product_keys(settings, now=datetime(2026, 9, 18, 2, tzinfo=timezone.utc))
        self.assertEqual(len(friday), 2)
        self.assertEqual(friday, select_product_keys(settings, now=datetime(2026, 9, 18, 20, tzinfo=timezone.utc)))

    def test_rotation_honors_explicit_product_keys_and_manual_all_opt_in(self):
        settings = {"trend_product_keys": ["managerium", "peopledesk", "primevat"], "trend_rotation_enabled": True}
        self.assertEqual(select_product_keys(settings, scheduled=False, now=NOW), ["managerium", "peopledesk"])
        settings["trend_manual_all_products"] = True
        self.assertEqual(select_product_keys(settings, scheduled=False, now=NOW), ["managerium", "peopledesk", "primevat"])

    @patch("app.trend_intelligence._official_website_discovery", return_value=None)
    @patch("app.trend_intelligence._facebook_page_discovery")
    @patch("app.trend_intelligence._trends_rss")
    @patch("app.trend_intelligence._google_trends")
    @patch("app.trend_intelligence._quora_discovery", return_value=[])
    @patch("app.trend_intelligence._bing_search_rss", return_value=[])
    @patch("app.trend_intelligence._news_rss", return_value=[])
    @patch("app.trend_intelligence._reddit_search", return_value=[])
    def test_trends_rss_fallback_is_distinct_and_facebook_is_not_evidence(
        self, reddit, news, bing, quora, trends, trends_rss, facebook, website
    ):
        trends.side_effect = ConnectorError("rate_limited", "429")
        trends_rss.return_value = [{
            "title": "ERP software", "url": "https://trends.google.com/trending?geo=BD",
            "published_at": datetime.now(timezone.utc).isoformat(), "fallback": True,
            "keyword_specific": False, "geo_code": "BD",
        }]
        facebook.return_value = [{
            "competitor": "PrismERP", "title": "PrismERP community", "url": "https://facebook.com/random",
            "identity_confidence": 0.5, "verification_status": "needs_verification", "verification_label": "Needs Verification",
        }]
        db = self.make_db()
        evidence, health = collect_product_signals(db, "managerium", PRODUCT_CONFIG["managerium"])
        self.assertEqual(health["google_trends"]["status"], "rate_limited")
        self.assertEqual(health["google_trends_rss"]["status"], "ok")
        fallback = next(row for row in evidence if row["source"] == "google_trends_rss")
        self.assertTrue(fallback["data"]["fallback"])
        self.assertFalse(fallback["data"]["keyword_specific"])
        self.assertNotIn("facebook", {row["source"] for row in evidence})
        self.assertEqual(health["facebook"]["status"], "unavailable")
        db.close()

    def test_run_persists_draft_evidence_and_status_without_sending(self):
        db = self.make_db()
        evidence = [
            signal("google_trends", "Google Trends: elementor addons", data={"relative_interest_recent": 70, "momentum": 5}),
            signal("reddit", "Best Elementor addons to choose"),
            signal("google_news", "Elementor addons comparison", competitor=True),
        ]
        health = {
            "google_trends": {"status": "ok", "errors": []},
            "reddit": {"status": "ok", "errors": []},
            "google_news": {"status": "ok", "errors": []},
            "bing": {"status": "ok", "errors": []},
            "quora": {"status": "ok", "errors": []},
            "facebook": {"status": "not_connected"},
            "gsc": {"status": "not_connected"},
        }
        with patch("app.trend_intelligence.collect_product_signals", return_value=(evidence, health)):
            result = run_trend_analysis(db, {"trend_product_keys": ["turbo_addons"], "trend_rotation_enabled": False, "trend_email_mode": "auto"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["mode"], "draft_only")
        self.assertEqual(result["source_health"]["summary"]["ok"], 5)
        self.assertEqual(db.query(models.TrendRun).count(), 1)
        self.assertEqual(db.query(models.TrendOpportunity).count(), 1)
        self.assertEqual(db.query(models.TrendEvidence).count(), 3)
        opportunity = db.query(models.TrendOpportunity).one()
        self.assertEqual(opportunity.approval_status, "draft")
        self.assertTrue(opportunity.selected)
        self.assertEqual(opportunity.confidence, "Medium")
        self.assertIn("*|UNSUB|*", opportunity.draft_json["body"])
        self.assertEqual(db.query(models.Campaign).count(), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
