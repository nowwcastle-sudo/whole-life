"""Holding a turn to its native-worker budget. Spec sections 4 and 8.

`profile의 turn delegation budget보다 하나 많은 worker start를 관찰하면 즉시
cancel·unknown_outcome`

The boundary is what these tests are about. A ledger that cancels one start too
early spends a budget the operator paid for; one that cancels one start too late
has already let the turn exceed it.
"""

import unittest

from whole_life.runtime.contract import DelegationBudget
from whole_life.runtime.delegation import DelegationLedger

BUDGET = DelegationBudget(
    max_concurrent_workers=3, max_total_worker_starts=2, max_depth=1
)


class TotalStartBoundaryTests(unittest.TestCase):
    def test_a_start_below_the_budget_is_allowed(self):
        ledger = DelegationLedger(BUDGET)

        self.assertIsNone(ledger.worker_started(native_child_id="a", depth=1))

    def test_the_start_that_reaches_the_budget_is_allowed(self):
        """The budget is what may be spent, not what may be approached."""
        ledger = DelegationLedger(BUDGET)
        ledger.worker_started(native_child_id="a", depth=1)

        self.assertIsNone(ledger.worker_started(native_child_id="b", depth=1))

    def test_the_first_start_past_the_budget_is_a_breach(self):
        ledger = DelegationLedger(BUDGET)
        ledger.worker_started(native_child_id="a", depth=1)
        ledger.worker_started(native_child_id="b", depth=1)

        self.assertEqual(
            "DelegationBudgetExceeded",
            ledger.worker_started(native_child_id="c", depth=1),
        )


class ConcurrencyBoundaryTests(unittest.TestCase):
    #: Room for four starts in total, so nothing here trips the other limit.
    BUDGET = DelegationBudget(
        max_concurrent_workers=1, max_total_worker_starts=4, max_depth=1
    )

    def test_one_worker_at_a_time_is_allowed(self):
        ledger = DelegationLedger(self.BUDGET)

        self.assertIsNone(ledger.worker_started(native_child_id="a", depth=1))

    def test_starting_again_after_the_first_finished_is_allowed(self):
        """Concurrency is about what is running at once, not about a total."""
        ledger = DelegationLedger(self.BUDGET)
        ledger.worker_started(native_child_id="a", depth=1)
        ledger.worker_finished(native_child_id="a")

        self.assertIsNone(ledger.worker_started(native_child_id="b", depth=1))

    def test_a_second_live_worker_is_a_breach(self):
        ledger = DelegationLedger(self.BUDGET)
        ledger.worker_started(native_child_id="a", depth=1)

        self.assertEqual(
            "DelegationBudgetExceeded",
            ledger.worker_started(native_child_id="b", depth=1),
        )

    def test_a_finish_for_a_worker_never_started_is_ignored(self):
        """The provider decides what it announces. A finish without a start is
        the stream telling us something we did not see the beginning of, and
        counting it as negative would let a turn buy back concurrency it never
        spent."""
        ledger = DelegationLedger(self.BUDGET)
        ledger.worker_finished(native_child_id="never-seen")
        ledger.worker_started(native_child_id="a", depth=1)

        self.assertEqual(
            "DelegationBudgetExceeded",
            ledger.worker_started(native_child_id="b", depth=1),
        )

class DepthBoundaryTests(unittest.TestCase):
    """v0 delegation depth is 1, and on Claude 2.1.240 the provider does not
    hold it — a recorded turn ran a worker at depth 2 and refused nothing. So
    the limit is held here or nowhere."""

    BUDGET = DelegationBudget(
        max_concurrent_workers=4, max_total_worker_starts=4, max_depth=1
    )

    def test_a_worker_at_the_permitted_depth_is_allowed(self):
        ledger = DelegationLedger(self.BUDGET)

        self.assertIsNone(ledger.worker_started(native_child_id="a", depth=1))

    def test_a_worker_one_level_too_deep_is_a_breach(self):
        ledger = DelegationLedger(self.BUDGET)
        ledger.worker_started(native_child_id="a", depth=1)

        self.assertEqual(
            "DelegationDepthExceeded",
            ledger.worker_started(native_child_id="b", depth=2),
        )

    def test_an_unannounced_depth_is_not_treated_as_a_violation(self):
        """Spec line 118 forbids inventing what the provider did not publish.
        A start whose depth is absent is a start we cannot judge on depth, and
        guessing it is deep would cancel a turn on a number nobody sent."""
        ledger = DelegationLedger(self.BUDGET)

        self.assertIsNone(
            ledger.worker_started(native_child_id="a", depth=None)
        )

if __name__ == "__main__":
    unittest.main()
