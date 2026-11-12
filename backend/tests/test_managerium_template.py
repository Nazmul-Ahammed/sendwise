"""Managerium drafts must always use the approved Managerium email shell."""
import unittest
from unittest.mock import patch

from app import copy_engine
from app.copy_engine import product_key
from app.email_templates import body_to_html


class ManageriumTemplateTests(unittest.TestCase):
    def test_managerium_alias_selects_managerium(self):
        self.assertEqual(product_key("Managerium ERP"), "managerium")

    def test_managerium_content_is_rendered_inside_approved_template(self):
        html = body_to_html(
            """Hi there,

Connect procurement, inventory, sales, accounts, and finance.

- Procurement and inventory visibility
- Sales, accounts, and finance visibility
- Real-time decision support

Book a Demo -> https://ibos.io/managerium/#managerium-erp-demo

Best regards,
Another Sender""",
            product="managerium",
            headline="Manage Your Business Operations From One Connected Platform",
            unsubscribe_url="https://example.com/unsubscribe",
        )

        self.assertIn("<!-- Managerium ERP Email Template -->", html)
        self.assertIn("All-in-One ERP Solution", html)
        self.assertIn("An iBOS Business Solution by AKIJ Resource", html)
        self.assertIn("Sayadul Islam Chowdhury", html)
        self.assertIn("width=\"120\"", html)
        self.assertIn("background:#183870", html)
        self.assertIn("Book a Demo", html)
        self.assertIn("https://ibos.io/managerium/#managerium-erp-demo", html)
        self.assertIn("https://example.com/unsubscribe", html)
        self.assertIn("Procurement and inventory visibility", html)

    def test_draft_with_agent_output_uses_managerium_template(self):
        with patch.object(copy_engine, "_llm_generate", return_value=None):
            result = copy_engine.generate(
                "Managerium ERP",
                sender_name="Sayadul Islam Chowdhury",
                campaign_slug="managerium-template-proof",
            )
        self.assertIn("<!-- Managerium ERP Email Template -->", result["body_html"])
        self.assertIn("All-in-One ERP Solution", result["body_html"])
        self.assertIn("An iBOS Business Solution by AKIJ Resource", result["body_html"])

    def test_other_products_keep_the_standard_template(self):
        html = body_to_html("Hi there,\n\nHello", product="peopledesk", headline="Hello")
        self.assertNotIn("Managerium ERP Email Template", html)
        self.assertNotIn("All-in-One ERP Solution", html)
        self.assertNotIn("Sayadul Islam Chowdhury", html)


if __name__ == "__main__":
    unittest.main()
