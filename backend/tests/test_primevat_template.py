"""PrimeVAT drafts must always use the approved PrimeVAT email shell."""
import unittest

from app.copy_engine import product_key
from app.email_templates import APPROVED_TEMPLATES, body_to_html


class PrimeVATTemplateTests(unittest.TestCase):
    def test_primevat_alias_selects_primevat(self):
        for alias in ("PrimeVAT", "primevat", "Prime VAT"):
            self.assertEqual(product_key(alias), "primevat")

    def test_primevat_is_registered_as_an_approved_template(self):
        spec = APPROVED_TEMPLATES["primevat"]
        self.assertEqual(spec["name"], "PrimeVAT")
        self.assertEqual(spec["tag"], "Smart VAT Compliance")
        self.assertEqual(spec["footer"], "An iBOS VAT Management Solution")
        self.assertEqual(spec["logo_width"], 130)
        self.assertEqual(spec["signer"], "Sayadul Islam Chowdhury")

    def test_primevat_content_is_rendered_inside_approved_template(self):
        html = body_to_html(
            """Hi there,

Manage VAT records, Mushak forms, reports and compliance from one place.

- Automated Mushak and VAT reporting
- Multi-branch VAT management
- Audit-ready business records

Book a Demo -> https://ibos.io/vat-management-software-solution-primevat/#demo

Best regards,
Another Sender""",
            product="primevat",
            headline="Simplify VAT Management With One Centralized System",
            unsubscribe_url="https://example.com/unsubscribe",
        )

        self.assertIn("<!-- PrimeVAT Email Template -->", html)
        self.assertIn("Smart VAT Compliance", html)
        self.assertIn("An iBOS VAT Management Solution", html)
        self.assertIn("Sayadul Islam Chowdhury", html)
        self.assertNotIn("Another Sender", html)
        self.assertIn('width="130"', html)
        self.assertIn("background:#183870", html)
        self.assertIn("data:image/svg+xml;base64,", html)
        self.assertIn("Book a Demo", html)
        self.assertIn("https://ibos.io/vat-management-software-solution-primevat/#demo", html)
        self.assertIn("https://example.com/unsubscribe", html)
        self.assertIn("Automated Mushak and VAT reporting", html)

    def test_other_products_keep_the_standard_template(self):
        html = body_to_html("Hi there,\n\nHello", product="ibos", headline="Hello")
        self.assertNotIn("PrimeVAT Email Template", html)
        self.assertNotIn("Smart VAT Compliance", html)
        self.assertNotIn("An iBOS VAT Management Solution", html)


if __name__ == "__main__":
    unittest.main()
