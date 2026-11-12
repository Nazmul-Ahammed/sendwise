"""Regression coverage for timeout-safe campaign enrollment and first dispatch."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app import sequence_engine as seq


class SequenceQueueTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.campaign = models.Campaign(
            slug="queue-probe",
            title="Queue probe",
            status="approved",
            product="Turbo Guard",
            sender_name="Sendwise",
            sender_email="sender@example.com",
            sequence_json=[{
                "stage": 1,
                "delay_value": 0,
                "delay_unit": "day",
                "emails": [{"ref": "E1"}],
            }],
            emails_json=[{
                "ref": "E1",
                "subject": "Probe",
                "body": "Hello",
                "cta_url": "https://example.com",
                "cta_label": "Learn more",
            }],
        )
        self.db.add(self.campaign)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_queue_is_idempotent_and_does_not_send(self):
        with patch.object(seq, "_send_one") as send:
            first = seq.queue_enrollments(
                self.db, self.campaign, ["A@Example.com", "a@example.com", "b@example.com"])
            second = seq.queue_enrollments(
                self.db, self.campaign, ["a@example.com", "b@example.com"])

        self.assertEqual(first["queued"], 2)
        self.assertEqual(first["already_enrolled"], 0)
        self.assertEqual(second["queued"], 0)
        self.assertEqual(second["already_enrolled"], 2)
        self.assertEqual(self.db.query(models.SequenceEnrollment).count(), 2)
        send.assert_not_called()

    def test_dispatch_sends_the_queued_initial_email_once(self):
        seq.queue_enrollments(self.db, self.campaign, ["a@example.com"])
        credentials = {"owner_id": "owner", "token": "token", "smtp_user_name": "smtp"}

        with patch.object(seq.ep, "credentials", return_value=credentials), \
                patch.object(seq, "_send_one", return_value="xid-1") as send:
            result = seq.dispatch(self.db, limit=10)
            second = seq.dispatch(self.db, limit=10)

        row = self.db.query(models.SequenceEnrollment).one()
        self.assertEqual(len(result["sent"]), 1)
        self.assertEqual(len(second["sent"]), 0)
        self.assertEqual(row.current_ref, "E1")
        self.assertEqual(row.pending_ref, "")
        self.assertEqual(row.status, "active")
        self.assertEqual(self.db.query(models.SendRecord).count(), 1)
        send.assert_called_once()

    def test_dispatch_stops_at_the_hourly_budget(self):
        seq.queue_enrollments(self.db, self.campaign, ["a@example.com", "b@example.com"])
        credentials = {"owner_id": "owner", "token": "token", "smtp_user_name": "smtp"}

        with patch.object(seq.ep, "credentials", return_value=credentials), \
                patch.object(seq, "MAX_SENDS_PER_HOUR", 1), \
                patch.object(seq, "_sends_last_hour", return_value=0), \
                patch.object(seq, "_send_one", return_value="xid") as send:
            result = seq.dispatch(self.db, limit=10)

        self.assertEqual(len(result["sent"]), 1)
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            self.db.query(models.SequenceEnrollment)
            .filter(models.SequenceEnrollment.current_ref == "").count(),
            1,
        )

    def test_dispatch_window_follows_the_due_date_not_table_order(self):
        """A pass must look at the earliest due work, not an arbitrary 300 rows.

        The production failure: 6,472 enrollments were past due, but every tick read
        the same 300 rows that were waiting on a future date, sent nothing, and the
        due work was never reached. E1 had gone out; E2-E9 stalled behind it.
        """
        now = seq._now()
        # Inserted first, so a table-order LIMIT would pick these and send nothing.
        for offset_days in (10, 8, 6):
            self.db.add(models.SequenceEnrollment(
                campaign_slug=self.campaign.slug, email=f"waiting{offset_days}@example.com",
                status="active", current_ref="", token=f"tok-{offset_days}",
                next_due_at=seq._iso(now + timedelta(days=offset_days))))
        self.db.add(models.SequenceEnrollment(
            campaign_slug=self.campaign.slug, email="due-now@example.com",
            status="active", current_ref="", token="tok-due",
            next_due_at=seq._iso(now - timedelta(hours=4))))
        self.db.commit()

        credentials = {"owner_id": "owner", "token": "token", "smtp_user_name": "smtp"}
        with patch.object(seq.ep, "credentials", return_value=credentials), \
                patch.object(seq, "_send_one", return_value="xid") as send:
            result = seq.dispatch(self.db, limit=2)

        self.assertEqual(len(result["sent"]), 1)
        self.assertIn("due-now@example.com", result["sent"][0])
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
