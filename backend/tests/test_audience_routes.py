import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.audience_routes import (
    ContactsIn,
    _parse_pasted,
    add_audience_contacts,
    audience_contacts,
    list_audiences,
)
from app.db import Base


class AudienceRoutesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.audience = models.Audience(name="Customers", description="")
        self.db.add(self.audience)
        self.db.commit()
        self.db.refresh(self.audience)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_list_input_preserves_name_from_angle_address(self):
        self.assertEqual(
            _parse_pasted("", ["Rahim Uddin <RAHIM@example.com>"]),
            [("rahim@example.com", "Rahim Uddin")],
        )

    @patch("app.email_validator.validate_many")
    def test_bulk_add_deduplicates_and_preserves_contact_names(self, validate_many):
        validate_many.side_effect = lambda emails: [
            {"email": email, "status": "valid", "reason": ""} for email in emails
        ]
        result = add_audience_contacts(
            self.audience.id,
            ContactsIn(emails=[
                "Rahim Uddin <rahim@example.com>",
                "rahim@example.com",
                "Karim Ahmed <karim@example.com>",
            ], source="csv:test.csv"),
            self.db,
        )

        self.assertEqual(result["added"], 2)
        self.assertEqual(self.db.query(models.AudienceMember).count(), 2)
        contacts = {row.email: row for row in self.db.query(models.Contact).all()}
        self.assertEqual(contacts["rahim@example.com"].name, "Rahim Uddin")
        self.assertEqual(contacts["karim@example.com"].name, "Karim Ahmed")

    def test_contact_list_is_server_paginated_and_searches_name(self):
        rows = [
            models.Contact(email="a@example.com", name="Amina", status="subscribed", deleted_at=""),
            models.Contact(email="b@example.com", name="Bashir", status="subscribed", deleted_at=""),
        ]
        self.db.add_all(rows)
        self.db.flush()
        self.db.add_all([
            models.AudienceMember(audience_id=self.audience.id, contact_id=row.id, tags=[])
            for row in rows
        ])
        self.db.commit()

        page = audience_contacts(
            self.audience.id, tags="", tag_match="any", q="", page=1, page_size=1, db=self.db,
        )
        self.assertEqual(page["count"], 2)
        self.assertEqual(page["pages"], 2)
        self.assertEqual(len(page["contacts"]), 1)
        self.assertIn("name", page["contacts"][0])

        search = audience_contacts(
            self.audience.id, tags="", tag_match="any", q="bash", page=1, page_size=25, db=self.db,
        )
        self.assertEqual(search["count"], 1)
        self.assertEqual(search["contacts"][0]["name"], "Bashir")

    def test_audience_summary_counts_all_lists_without_per_list_queries(self):
        contact = models.Contact(
            email="member@example.com", name="Member", status="subscribed",
            email_status="valid", deleted_at="",
        )
        self.db.add(contact)
        self.db.flush()
        self.db.add(models.AudienceMember(
            audience_id=self.audience.id, contact_id=contact.id, tags=["customer"],
        ))
        self.db.commit()

        result = list_audiences(self.db)
        self.assertEqual(result[0]["members"], 1)
        self.assertEqual(result[0]["usable"], 1)
        self.assertEqual(result[0]["tags"], {"customer": 1})


if __name__ == "__main__":
    unittest.main()
