import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, routes
from app.audience_routes import audience_contacts, list_audiences
from app.db import Base


class FastListTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)
        self.db = self.session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_sends_pages_summary_and_uses_real_enrollment_state(self):
        self.db.add_all([
            models.Campaign(slug=f"send-{i}", title=f"Send {i}", status="draft",
                            emails_json=[{"body_html": "long content"}, {}],
                            delivery_json={"recipients": [{"email": "sample@example.test"}]})
            for i in range(26)
        ])
        self.db.add(models.SequenceEnrollment(campaign_slug="send-25", status="active", sends=0,
                                              email="sample@example.test", token="fixture-25"))
        self.db.commit()

        result = routes.list_campaigns(self.db, view="list")
        self.assertEqual(result["counts"], {"all": 26, "live": 1, "draft": 25})
        self.assertEqual(result["pages"], 2)
        self.assertEqual(len(result["items"]), 25)
        self.assertEqual(result["items"][0]["status"], "sending")
        self.assertEqual(result["items"][0]["email_count"], 2)
        self.assertEqual(result["items"][0]["delivery"]["sent_count"], 1)
        self.assertNotIn("emails", result["items"][0])
        self.assertNotIn("audience_options", result["items"][0])
        self.assertEqual(routes.list_campaigns(self.db, view="list", page=2)["items"][0]["slug"], "send-0")
        self.assertEqual(len(routes.list_campaigns(self.db, view="list", status_filter="live")["items"]), 1)

    def test_batched_metrics_match_existing_tracking_metrics(self):
        campaign = models.Campaign(slug="test", title="Test")
        self.db.add(campaign)
        self.db.add(models.SequenceEnrollment(
            campaign_slug="test", email="sample@example.test", token="fixture-metrics",
            history_json=[{"x_unique_id": "sent-1", "ref": "E1"}],
        ))
        self.db.add(models.EngagementEvent(campaign_slug="test", email="sample@example.test",
                                           event_type="open", is_machine=False))
        self.db.add(models.DeliveryStatus(campaign_slug="test", email="sample@example.test", status="delivered"))
        self.db.commit()
        old = routes._campaign_rates(campaign, self.db)
        new = routes.sends_list_metrics("test", self.db)["test"]
        self.assertEqual({k: v for k, v in new.items() if k != "computed_at"},
                         {k: v for k, v in old.items() if k != "computed_at"})
        self.assertEqual(new["unique_openers"], 1)
        self.assertEqual(new["delivered"], 1)

    def test_audience_names_and_contacts_do_not_require_summary_inventory(self):
        audience = models.Audience(name="People")
        contact = models.Contact(name="Sample", email="sample@example.test")
        self.db.add_all([audience, contact])
        self.db.flush()
        self.db.add(models.AudienceMember(audience_id=audience.id, contact_id=contact.id, tags=["example"]))
        self.db.commit()
        self.assertNotIn("members", list_audiences(self.db, view="list")[0])
        summary = list_audiences(self.db)[0]
        self.assertEqual((summary["members"], summary["tags"]), (1, {"example": 1}))
        detail = audience_contacts(audience.id, page=1, page_size=25, include_tags=False, db=self.db)
        self.assertIsNone(detail["tags"])
        self.assertEqual(detail["contacts"][0]["name"], "Sample")

    @patch("app.routes.refresh_delivery", return_value={"refreshed": 1, "failed": 0})
    def test_refresh_job_returns_before_running_and_deduplicates(self, refresh):
        pending = BackgroundTasks()
        first = routes.queue_delivery_refresh(pending, self.db)
        second = routes.queue_delivery_refresh(BackgroundTasks(), self.db)
        self.assertEqual(first, second)
        self.assertEqual(len(pending.tasks), 1)
        refresh.assert_not_called()
        with patch("app.db.SessionLocal", self.session):
            routes._run_delivery_refresh(first["id"])
        self.db.expire_all()
        self.assertEqual(routes.delivery_refresh_status(first["id"], self.db)["status"], "done")


if __name__ == "__main__":
    unittest.main()
