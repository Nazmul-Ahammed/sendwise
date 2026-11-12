"""The delay a sequence declares must be the delay that is actually used.

Regression tests for a real bug: `enroll()` computed its FIRST wait itself from a
global default instead of asking the sequence, so a sequence configured with
5-minute steps still waited 3 days for email 2. E1 went out immediately and then the
whole journey slept. Nothing failed, nothing logged - a human had to notice.

These tests pin the behaviour so it cannot come back quietly.
"""
import inspect
import unittest
from datetime import timedelta
from types import SimpleNamespace

from app import sequence_engine as seq


def campaign(stages, slug="probe"):
    return SimpleNamespace(slug=slug, sequence_json=stages)


def stage(number, refs, value, unit):
    return {
        "stage": number,
        "focus": f"stage {number}",
        "delay_value": value,
        "delay_unit": unit,
        "emails": [
            {"ref": r, "name": r, "branch": "main",
             "open_click_next": "", "open_no_click_next": "", "not_open_next": ""}
            for r in refs
        ],
    }


class SequenceDelayTests(unittest.TestCase):
    def test_a_five_minute_sequence_waits_five_minutes(self):
        c = campaign([stage(1, ["E1"], 5, "minute"), stage(2, ["E2"], 5, "minute")])
        self.assertEqual(seq.wait_before(c, "E1"), timedelta(minutes=5))
        self.assertEqual(seq.wait_before(c, "E2"), timedelta(minutes=5))
        # the exact shape of the original bug
        self.assertNotEqual(seq.wait_before(c, "E1"), timedelta(days=seq.NEXT_GAP_DAYS))

    def test_a_three_day_sequence_still_waits_three_days(self):
        c = campaign([stage(1, ["E1"], 0, "day"), stage(2, ["E2", "E3"], 3, "day")])
        self.assertEqual(seq.wait_before(c, "E2"), timedelta(days=3))
        self.assertEqual(seq.wait_before(c, "E3"), timedelta(days=3))

    def test_every_unit_is_honoured(self):
        for unit, expected in (("minute", timedelta(minutes=2)),
                               ("hour", timedelta(hours=2)),
                               ("day", timedelta(days=2)),
                               ("week", timedelta(weeks=2))):
            c = campaign([stage(1, ["E1"], 2, unit)])
            self.assertEqual(seq.wait_before(c, "E1"), expected, f"unit {unit}")

    def test_zero_delay_sends_on_the_next_tick(self):
        c = campaign([stage(1, ["E1"], 0, "day")])
        self.assertLessEqual(seq.wait_before(c, "E1"), timedelta(minutes=1))

    def test_an_unknown_ref_announces_its_fallback(self):
        c = campaign([stage(1, ["E1"], 5, "minute")])
        with self.assertLogs("sendwise", level="WARNING") as caught:
            gap = seq.wait_before(c, "E9")
        self.assertEqual(gap, timedelta(days=seq.NEXT_GAP_DAYS))
        self.assertTrue(any("does not define" in line for line in caught.output),
                        "a silent fallback is what caused the bug - it must log")

    def test_enroll_takes_its_wait_from_the_sequence(self):
        """Source-level guard: enroll must ask the sequence, never invent a wait.

        A behavioural test would need a live DB and a mocked SendClean call for every
        case; this asserts the single lines that were wrong cannot be wrong again.
        """
        source = inspect.getsource(seq.enroll)
        self.assertIn("wait_after(campaign", source,
                      "enroll must derive its first wait from wait_after(campaign, ref)")
        self.assertNotIn("timedelta(days=NEXT_GAP_DAYS)", source,
                         "enroll must not use the global default for its first wait")

    def test_the_self_check_uses_the_slowest_possible_next_step(self):
        """The repair paths must not repeat the bug they exist to repair.

        Reading the SENT email's own stage made a 3-day journey due at once - which is
        how a demo E2 went out roughly three days early.
        """
        audit_source = inspect.getsource(seq.audit_delays)
        self.assertIn("wait_span_after(camp, e.current_ref)", audit_source,
                      "the audit must compare against the slowest possible next step")

        import app.routes as routes
        fix_source = inspect.getsource(routes.sequences_self_check_fix)
        self.assertIn("wait_after(camp", fix_source,
                      "the fix must use wait_after(...) - the next email's stage")
        self.assertNotIn("_stage_delay(camp, e.current_ref)", fix_source,
                         "the fix must never read the stage of the email already sent")

    def test_dispatch_check_delays_from_the_stage_not_the_sent_one(self):
        """The wait belongs to the NEXT email's stage.

        This is the second half of the same bug: a sequence that is immediate at stage 1
        and waits 3 days at stage 2 must wait 3 days after E1 - not 30 seconds, which is
        what reading stage 1's own delay produced.
        """
        c = campaign([
            stage(1, ["E1"], 0, "day"),
            stage(2, ["E2", "E3"], 3, "day"),
        ])
        self.assertEqual(seq.wait_before(c, "E1"), timedelta(seconds=30))       # stage 1 itself
        self.assertEqual(seq.wait_after(c, "E1"), timedelta(days=3))        # the real wait

    def test_wait_after_returns_none_at_the_end_of_a_journey(self):
        c = campaign([
            {"stage": 1, "delay_value": 0, "delay_unit": "day",
             "emails": [{"ref": "E1", "open_click_next": "E2",
                         "open_no_click_next": "E2", "not_open_next": "E2"}]},
            {"stage": 2, "delay_value": 1, "delay_unit": "day", "emails": [{"ref": "E2"}]},
        ])
        self.assertIsNone(seq.wait_after(c, "E2"))

    def test_dispatch_check_delays_from_the_sequence(self):
        source = inspect.getsource(seq.dispatch)
        self.assertIn("wait_before(campaign", source,
                      "dispatch must derive every later wait from wait_before(campaign, ref)")
        self.assertIn("wait_after(campaign", source,
                      "dispatch must schedule the following wait with wait_after")
        self.assertNotIn("timedelta(days=NEXT_GAP_DAYS)", source,
                         "dispatch must not use the global default")

    # ------------------------------------------------ delivery safety (production)
    def test_only_one_wait_function_can_be_reached(self):
        """A wrong formula cannot be picked if there is only one formula.

        `_stage_delay` is private on purpose; callers use wait_before / wait_after.
        """
        self.assertFalse(hasattr(seq, "gap_for"),
                         "the old public name must be gone, not merely unused")
        self.assertTrue(callable(seq.wait_before))
        self.assertTrue(callable(seq.wait_after))

    def test_a_missing_due_date_is_never_read_as_due_now(self):
        """The silent-default shape that has bitten this engine: absent means NOW."""
        source = inspect.getsource(seq.dispatch)
        self.assertIn("if due is None:", source,
                      "an unreadable next_due_at must be handled explicitly")
        self.assertIn('enr.status = "needs_review"', source,
                      "and it must park the enrollment rather than send it")
        self.assertNotIn("if due and due > now:", source,
                         "`if due and ...` lets a falsy date fall through to a send")

    def test_an_interrupted_send_is_parked_not_repeated(self):
        source = inspect.getsource(seq.dispatch)
        self.assertIn("enr.pending_ref", source,
                      "dispatch must check for an interrupted send before sending")
        self.assertIn('enr.status = "needs_review"', source,
                      "an interrupted send is parked, never auto-resent")

    def test_the_send_is_claimed_and_committed_before_the_network_call(self):
        source = inspect.getsource(seq.dispatch)
        claim = source.index("enr.pending_ref = nxt")
        send = source.index("_send_one(")
        self.assertLess(claim, send,
                        "the claim must be written and committed BEFORE the send, so a "
                        "crash mid-send is visible instead of silently repeating")
        self.assertIn("db.commit()", source[claim:send],
                      "the claim must be committed, not just set")

    def test_each_recipient_is_committed_so_a_crash_cannot_resend_the_batch(self):
        source = inspect.getsource(seq.dispatch)
        self.assertGreaterEqual(source.count("db.commit()"), 4,
                                "dispatch must commit per recipient, not once at the end")
        enroll_source = inspect.getsource(seq.enroll)
        self.assertIn("db.commit()", enroll_source.split("for raw in recipients")[-1],
                      "enroll must commit per recipient too")

    def test_failures_are_retried_but_bounded(self):
        source = inspect.getsource(seq._record_failure)
        self.assertIn("MAX_ATTEMPTS", source)
        self.assertIn("enr.attempts", source)

    def test_missing_content_parks_the_enrollment(self):
        source = inspect.getsource(seq.dispatch)
        self.assertIn("has no content on this send", source,
                      "a missing email body is a content fault, not a finished journey")
        self.assertNotIn('completed.append(f"{enr.email} (no {nxt} content)")', source)


class BranchTreeTests(unittest.TestCase):
    def test_branches_come_from_the_sequence(self):
        stages = [
            {"stage": 1, "delay_value": 0, "delay_unit": "day", "emails": [
                {"ref": "E1", "open_click_next": "E9",
                 "open_no_click_next": "E9", "not_open_next": "E9"}]},
            {"stage": 2, "delay_value": 1, "delay_unit": "day", "emails": [{"ref": "E9"}]},
        ]
        tree = seq.build_branch_tree(stages)
        self.assertEqual(tree["E1"], {"clicked": "E9", "opened": "E9", "not_opened": "E9"})
        # the built-in framework would have chosen E2/E3 here
        self.assertEqual(seq.decide_next_ref("E1", 0, 0, tree), "E9")

    def test_click_is_evaluated_before_open(self):
        tree = seq.build_branch_tree([
            {"stage": 1, "emails": [{"ref": "E1", "open_click_next": "A",
                                     "open_no_click_next": "B", "not_open_next": "C"}]},
            {"stage": 2, "emails": [{"ref": "A"}, {"ref": "B"}, {"ref": "C"}]},
        ])
        self.assertEqual(seq.decide_next_ref("E1", 3, 1, tree), "A")   # clicked wins
        self.assertEqual(seq.decide_next_ref("E1", 3, 0, tree), "B")   # opened only
        self.assertEqual(seq.decide_next_ref("E1", 0, 0, tree), "C")   # neither

    def test_a_terminal_email_ends_the_journey(self):
        """E1 branches to E2; E2 declares nothing, so the journey stops there."""
        tree = seq.build_branch_tree([
            {"stage": 1, "emails": [{"ref": "E1", "open_click_next": "E2",
                                     "open_no_click_next": "E2", "not_open_next": "E2"}]},
            {"stage": 2, "emails": [{"ref": "E2"}]},
        ])
        self.assertIsNone(tree["E2"], "an email with no branch fields ends the sequence")
        self.assertEqual(seq.decide_next_ref("E1", 1, 1, tree), "E2")
        self.assertIsNone(seq.decide_next_ref("E2", 1, 1, tree))

    def test_a_sequence_with_no_branching_at_all_is_not_a_tree(self):
        """No branching anywhere means 'no opinion' - the built-in framework applies."""
        self.assertIsNone(seq.build_branch_tree([{"stage": 1, "emails": [{"ref": "E1"}]}]))

    def test_a_send_without_branching_uses_the_built_in_framework(self):
        tree = seq.branch_tree_for(campaign([{"stage": 1, "emails": [{"ref": "E1"}]}]))
        self.assertEqual(tree["E1"], {"opened": "E2", "not_opened": "E3"})

    def test_branch_tree_for_prefers_the_send_own_rules(self):
        c = campaign([
            {"stage": 1, "emails": [{"ref": "E1", "open_click_next": "X",
                                     "open_no_click_next": "X", "not_open_next": "X"}]},
            {"stage": 2, "emails": [{"ref": "X"}]},
        ])
        self.assertEqual(seq.branch_tree_for(c)["E1"]["not_opened"], "X")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------- provider slowness must not stall a pass

def test_lookup_timeout_is_bounded():
    """The dispatch reads the provider's slowest endpoint once per recipient.

    It has been seen to hang rather than fail, so the timeout must be short enough that
    a handful of unresponsive lookups cannot outlive the request waiting on them.
    """
    from app import sequence_engine as seq
    assert seq.LOOKUP_TIMEOUT <= 15, "a slow lookup must not hold the whole pass"
    assert seq.DISPATCH_BUDGET_SECONDS <= 300, "a pass must have an upper bound"


def test_get_message_info_accepts_a_timeout():
    """The bound is only real if the client lets callers set it."""
    import inspect
    from app import sendclean_client as sc
    params = inspect.signature(sc.get_message_info).parameters
    assert "timeout" in params
    assert params["timeout"].default <= 40


def test_dispatch_returns_every_bucket_it_promises():
    """Every key a caller reads must exist even on an early return.

    The UI read `sent.length` off a response that omitted it and threw instead of
    showing the reason, so the shape is now part of the contract.
    """
    import inspect
    from app import sequence_engine as seq
    src = inspect.getsource(seq.dispatch)
    for key in ("plan", "sent", "completed", "blocked_suppressed",
                "failed", "parked", "paused", "deferred"):
        assert f'"{key}"' in src, f"dispatch must return {key}"


def test_trashed_campaign_is_not_a_failure():
    """A send in Trash is restorable, so its enrollments must wait, not be written off.

    Looking the campaign up without include_trashed finds nothing, and the old code
    called that a failed send for every recipient.
    """
    import inspect
    from app import sequence_engine as seq
    src = inspect.getsource(seq.dispatch)
    assert "include_trashed" in src
    assert '"paused"' in src, "a trashed send must pause its enrollments"