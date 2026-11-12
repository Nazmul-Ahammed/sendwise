"""The report must never carry an invented number.

Regression tests for a real fault: the daily report was assembled from hardcoded
placeholder values - 96.8% delivery, 1.4% clicks, 11.9% opens, 6834 delivered - so it
looked exactly like a performance report while reporting nothing at all. It was
addressed to the CMO.

Two things are pinned here:
  * no reporting path may hardcode a figure
  * every threshold verdict must follow from the configured limit, in the right direction
"""
import inspect
import unittest

import app.routes as routes


class ReportThresholdTests(unittest.TestCase):
    def thresholds(self, metrics, settings=None):
        rows = routes._report_thresholds(metrics, settings or {})
        return {row["label"]: row["status"] for row in rows}

    def metrics(self, **over):
        base = {
            "sent": 1000, "delivered": 990, "unique_clicks": 20,
            "delivery_rate": 99.0, "open_rate": 20.0, "click_rate": 2.0,
            "bounce_rate": 1.0, "unsubscribe": 0.0, "complaint": 0.0,
        }
        base.update(over)
        return base

    def test_a_high_delivery_rate_passes(self):
        """100% delivery must PASS. The band read backwards made this a warning."""
        self.assertEqual(self.thresholds(self.metrics(delivery_rate=100.0))["Delivery rate"], "pass")

    def test_delivery_slightly_below_target_warns(self):
        # default target is 95%, so 90% is inside the 10% warn band
        self.assertEqual(self.thresholds(self.metrics(delivery_rate=90.0))["Delivery rate"], "warn")

    def test_delivery_well_below_target_fails(self):
        self.assertEqual(self.thresholds(self.metrics(delivery_rate=60.0))["Delivery rate"], "fail")

    def test_bounce_at_the_limit_passes_and_slightly_over_warns(self):
        self.assertEqual(self.thresholds(self.metrics(bounce_rate=2.0))["Bounce rate"], "pass")
        self.assertEqual(self.thresholds(self.metrics(bounce_rate=2.1))["Bounce rate"], "warn")
        self.assertEqual(self.thresholds(self.metrics(bounce_rate=5.0))["Bounce rate"], "fail")

    def test_targets_are_read_from_settings_not_from_constants(self):
        strict = self.thresholds(self.metrics(click_rate=5.0), {"target_click_rate": "0.10"})
        self.assertEqual(strict["Click rate"], "fail", "a 10% click target must be honoured")
        loose = self.thresholds(self.metrics(click_rate=5.0), {"target_click_rate": "0.01"})
        self.assertEqual(loose["Click rate"], "pass")

    def test_the_limit_is_shown_next_to_the_value(self):
        rows = routes._report_thresholds(self.metrics(), {})
        for row in rows:
            self.assertIn("%", row["value"])
            self.assertTrue(row["limit"].startswith(">=") or row["limit"].startswith("<="),
                            f"{row['label']} must state its direction")


class NoPlaceholderNumbersTests(unittest.TestCase):
    """Source-level guards. These are the exact values that were hardcoded."""

    PLACEHOLDERS = ("6834", "96.8", "11.9", "1.4%", "0.21", "0.02", "2026-09-01")

    def source(self, fn):
        return inspect.getsource(fn)

    def test_compose_email_hardcodes_nothing(self):
        src = self.source(routes._compose_email)
        for bad in self.PLACEHOLDERS:
            self.assertNotIn(bad, src, f"_compose_email still contains the placeholder {bad!r}")

    def test_the_reports_endpoint_hardcodes_nothing(self):
        src = self.source(routes.reports)
        for bad in self.PLACEHOLDERS:
            self.assertNotIn(bad, src, f"reports() still contains the placeholder {bad!r}")

    def test_metrics_are_derived_not_literal(self):
        src = self.source(routes._report_metrics)
        self.assertIn("live", src, "metrics must be derived from live SendClean totals")
        self.assertIn("Suppression", src, "unsubscribe/complaint come from the suppression list")

    def test_the_report_date_is_computed(self):
        src = self.source(routes._report_date)
        self.assertIn("now(", src, "the report date must be the current date, never a constant")


if __name__ == "__main__":
    unittest.main()
