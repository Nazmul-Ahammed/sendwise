import os
import re
import unittest
from unittest.mock import patch

from app import brand_loader
from app.copy_engine import generate, product_key
from app.trend_intelligence import PRODUCT_CONFIG, campaign_product_key


class TurboGuardTests(unittest.TestCase):
    def test_aliases_are_isolated_from_turbo_addons(self):
        for alias in ("Turbo Guards", "Turbo Guard", "turbo_guard", "turbo-guard"):
            self.assertEqual(product_key(alias), "turbo_guard")
            self.assertEqual(campaign_product_key(alias), "turbo_guard")
        self.assertEqual(product_key("Turbo Add-ons"), "turbo")
        self.assertEqual(campaign_product_key("Turbo Add-ons"), "turbo_addons")

    def test_brand_loader_selects_current_turbo_guard_knowledge(self):
        cfg = brand_loader.load("turbo_guard")
        context = brand_loader.load_skill_context("turbo_guard")
        self.assertEqual(cfg["name"], "Turbo Guard")
        self.assertEqual(cfg["cta"], "Explore Turbo Guard Pro")
        self.assertIn("# Turbo Guard Operational Product Knowledge", context)
        self.assertIn("reference release 1.1.4", context)
        self.assertIn("Peace Of Mind. Same Price.", context)
        self.assertIn("does not explicitly confirm a `PHP2Shell` signature", context)
        self.assertNotIn("TURBO ADDONS - FULL BRAND", context)
        self.assertTrue(os.path.basename(brand_loader._FILE_NAMES["turbo_guard"]) == "turbo-guard-email-copy.md")
        self.assertIn("turbo_guard", brand_loader.available())

    def test_generated_copy_is_singular_safe_and_uses_approved_cta(self):
        result = generate("Turbo Guards", offer="Turbo Guards security", email_ref="E6", campaign_slug="guard-test")
        combined = " ".join((result["subject"], result["preview_text"], result["body"]))
        self.assertNotIn("Turbo Guards", combined)
        self.assertIn("Turbo Guard", combined)
        self.assertEqual(result["product_name"], "Turbo Guard")
        self.assertEqual(result["cta_label"], "Explore Turbo Guard Pro")
        self.assertTrue(result["cta_url"].startswith("https://wp-turbo.com/turbo-guard-offer/?"))
        self.assertEqual(result["md_source"], "brand_docs/turbo-guard-email-copy.md")
        self.assertIn(result["subject"], result["body_html"])
        self.assertIn("Protect. Detect. Review.", result["body_html"])
        self.assertIn("A clean scan does not prove", result["body_html"])
        self.assertFalse(re.search(r"\b(?:guaranteed|100\s*%|zero risk|zero slowdown)\b", combined, re.I))
        self.assertFalse(re.search(r"(?:\$|£|€)\s*\d|\b\d+\s*(?:installs?|reviews?)\b", combined, re.I))

    def test_approved_coffee_offer_builds_distinct_flow_and_campaign_html(self):
        offer = "US$5.55 first year | Up to 2 WordPress sites | One-month offer"
        results = [
            generate("Turbo Guard", offer=offer, email_ref=f"E{n}", campaign_slug="peace-of-mind")
            for n in range(1, 10)
        ]
        self.assertEqual(len({row["subject"] for row in results}), 9)
        self.assertEqual(results[0]["cta_label"], "Get Pro For US$5.55")
        self.assertTrue(results[0]["cta_url"].startswith("https://wp-turbo.com/turbo-guard-offer/?"))
        self.assertIn("Coffee comparison", results[0]["body_html"])
        self.assertIn("PEACE OF MIND. SAME PRICE.", results[0]["body_html"])
        self.assertIn("background:#FD0000", results[0]["body_html"])
        self.assertIn("No partnership, sponsorship, or endorsement is implied.", results[0]["body_html"])
        self.assertIn("PHP2Shell", results[1]["body"])
        self.assertIn("does not confirm PHP2Shell detection by name", results[1]["body"])
        self.assertIn("C99, R57 and WSO", results[1]["body"])
        self.assertTrue(all("Starbucks" in row["body"] and "coffee" in row["body"].lower() for row in results))
        self.assertTrue(all(re.search(r"malware|web[ -]shell|security", row["body"], re.I) for row in results))
        self.assertTrue(all("PHP2Shell" in results[index - 1]["body"] for index in (1, 2, 4, 7)))
        self.assertTrue(all("\u2014" not in (row["subject"] + row["preview_text"] + row["body"] + row["body_html"]) for row in results))

    @patch("app.llm_providers.generate_email")
    def test_coffee_campaign_rejects_llm_copy_that_drops_required_motifs(self, provider):
        provider.return_value = {
            "subject": "A generic security note",
            "preview_text": "A generic preview",
            "body": "Hi there,\n\nKeep your site secure.\n\nBest regards,\nTurbo Guard",
        }
        result = generate(
            "Turbo Guard",
            offer="US$5.55 first year | Up to 2 WordPress sites | One-month offer",
            email_ref="E1",
            campaign_slug="peace-of-mind",
            llm_config={"provider": "test", "model": "test"},
        )
        self.assertFalse(result["via_llm"])
        self.assertIn("PHP2Shell", result["body"])
        self.assertIn("Starbucks coffee", result["body"])

    def test_incomplete_price_claim_is_not_published(self):
        result = generate(
            "Turbo Guard", offer="Only US$5.55 today",
            email_ref="E1", campaign_slug="guard-test",
        )
        combined = " ".join((result["subject"], result["preview_text"], result["body"]))
        self.assertNotIn("5.55", combined)
        self.assertEqual(result["cta_label"], "Explore Turbo Guard Pro")

    def test_trend_profile_is_distinct_and_global(self):
        guard = PRODUCT_CONFIG["turbo_guard"]
        addons = PRODUCT_CONFIG["turbo_addons"]
        self.assertEqual(guard["geography"], "Global")
        self.assertIn("wordpress malware recovery", guard["keywords"])
        self.assertIn("Wordfence", guard["competitors"])
        self.assertTrue(set(guard["keywords"]).isdisjoint(addons["keywords"]))
        self.assertEqual(guard["cta"], "Scan Your Site Free")

    def test_unsafe_offer_is_not_published(self):
        result = generate(
            "Turbo Guard", offer="Guaranteed rankings and 100% security",
            email_ref="E7", campaign_slug="guard-test",
        )
        combined = " ".join((result["subject"], result["preview_text"], result["body"]))
        self.assertNotRegex(combined.lower(), r"guaranteed rankings|100% security")
        self.assertNotIn("\u2014", combined)

    @patch("app.llm_providers.generate_email")
    def test_unsafe_provider_copy_falls_back_to_safe_copy(self, provider):
        provider.return_value = {
            "subject": "Guaranteed Turbo Guards cleanup",
            "preview_text": "100% protection with 60% off",
            "body": "Zero risk for 10,000 active installations.",
        }
        result = generate(
            "Turbo Guards", email_ref="E1", campaign_slug="guard-test",
            llm_config={"provider": "test", "model": "test"},
        )
        combined = " ".join((result["subject"], result["preview_text"], result["body"]))
        self.assertFalse(result["via_llm"])
        self.assertNotRegex(combined.lower(), r"guaranteed|100%|zero risk|60% off|10,000 active")


if __name__ == "__main__":
    unittest.main()
