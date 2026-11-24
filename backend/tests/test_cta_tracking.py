import unittest
from urllib.parse import parse_qs, urlsplit

from app.copy_engine import generate, locked_cta_url
from app.email_templates import body_to_html


class MandatoryCtaTrackingTests(unittest.TestCase):
    destinations = {
        "Managerium": ("https://ibos.io/managerium/", "managerium-erp-demo"),
        "PeopleDesk": ("https://ibos.io/best-hr-software-in-bangladesh/", "demo"),
        "PrimeVAT": ("https://ibos.io/vat-management-software-solution-primevat/", "demo"),
        "Travilo": ("https://travilo.io/book-your-free-demo-form", ""),
        "TrackForce": ("https://trackforce.io/contact", ""),
    }

    def assert_mandatory_tracking(self, url, campaign_name):
        parts = urlsplit(url)
        params = parse_qs(parts.query)
        self.assertEqual(params["utm_source"], ["Sendwise"])
        self.assertEqual(params["utm_medium"], ["Email Marketing"])
        self.assertEqual(params["utm_campaign"], [campaign_name])
        self.assertEqual(params["utm_term"], ["Winter_Sale"])
        self.assertEqual(params["utm_content"], ["E3_bottom_primary_cta"])

    def test_locked_destinations_and_utm_rules_for_all_five_products(self):
        campaign_name = "24 Nov | Winter Demo"
        for product, (base, fragment) in self.destinations.items():
            with self.subTest(product=product):
                url = locked_cta_url(product, campaign_name, "E3")
                parts = urlsplit(url)
                self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", base)
                self.assertEqual(parts.fragment, fragment)
                self.assert_mandatory_tracking(url, campaign_name)

    def test_agent_generation_uses_campaign_title_not_internal_slug(self):
        result = generate(
            "Travilo", campaign_slug="internal-slug", campaign_name="24 Nov | Travilo",
            email_ref="E1",
        )
        params = parse_qs(urlsplit(result["cta_url"]).query)
        self.assertEqual(params["utm_campaign"], ["24 Nov | Travilo"])
        self.assertEqual(params["utm_source"], ["Sendwise"])
        self.assertEqual(params["utm_medium"], ["Email Marketing"])
        self.assertEqual(params["utm_term"], ["Winter_Sale"])

    def test_send_time_override_replaces_stale_saved_destination(self):
        forced = locked_cta_url("TrackForce", "Old Live Campaign", "E2")
        html = body_to_html(
            "Hi there,\n\nBook now -> https://old.example/demo\n\nBest regards,\nTrackForce",
            product="trackforce", cta_url="https://old.example/demo", cta_text="Book now",
            force_cta_url=forced,
        )
        self.assertIn("https://trackforce.io/contact?", html)
        self.assertNotIn("https://old.example/demo", html)
        params = parse_qs(urlsplit(forced).query)
        self.assertEqual(params["utm_campaign"], ["Old Live Campaign"])


if __name__ == "__main__":
    unittest.main()
