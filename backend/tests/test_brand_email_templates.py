"""Every supplied brand email template must be selected by Agent drafts."""
import os
import unittest
from html import escape

from app.email_templates import APPROVED_TEMPLATES, body_to_html


class BrandEmailTemplateTests(unittest.TestCase):
    EXPECTED = {
        "managerium": ("Managerium ERP Email Template", "All-in-One ERP Solution"),
        "peopledesk": ("PeopleDesk Email Template", "Smart HR Management"),
        "primevat": ("PrimeVAT Email Template", "Smart VAT Compliance"),
        "trackforce": ("TrackForce Email Template", "Work Visibility Made Simple"),
        "travilo": ("Travilo Email Template", "Connected Travel Business Platform"),
        "turbo": ("Turbo Add-ons Email Template", "Build more. Switch less."),
        "turbo_guard": ("Turbo Guard Email Template", "WordPress security, made clear"),
    }

    def test_all_supplied_templates_are_registered_and_present(self):
        brand_docs = os.path.join(os.path.dirname(__file__), "..", "app", "brand_docs")
        for product, (marker, tagline) in self.EXPECTED.items():
            with self.subTest(product=product):
                spec = APPROVED_TEMPLATES[product]
                self.assertTrue(os.path.isfile(os.path.join(brand_docs, spec["file"])))
                html = body_to_html(
                    "Hi there,\n\nA brand-specific message.",
                    product=product,
                    headline="Brand template proof",
                )
                self.assertIn(f"<!-- {marker} -->", html)
                self.assertIn(tagline, html)
                self.assertIn(escape(spec["footer"]), html)
                self.assertIn(spec["signer"], html)


if __name__ == "__main__":
    unittest.main()
