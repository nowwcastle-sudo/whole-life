"""Holding a turn to its native-worker budget. Spec sections 4 and 8.

`profile의 turn delegation budget보다 하나 많은 worker start를 관찰하면 즉시
cancel·unknown_outcome`

The boundary is what these tests are about. A ledger that cancels one start too
early spends a budget the operator paid for; one that cancels one start too late
has already let the turn exceed it.
"""

import json
import sys
import unittest
from pathlib import Path

from tests.support.launch_fixtures import launch_plan, turn_request
from whole_life.runtime.contract import DelegationBudget, RunStatus
from whole_life.runtime.normalize import normalize_claude_line
from whole_life.runtime.observe import RunObserver
from whole_life.runtime.spawn import SubprocessSpawner
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

def claude_line(**fields):
    return json.dumps(fields)


INIT = claude_line(type="system", subtype="init", session_id="s")
RESULT = claude_line(type="result", subtype="success", is_error=False)


def started(task_id, depth=1):
    return claude_line(
        type="system",
        subtype="task_started",
        task_id=task_id,
        spawn_depth=depth,
        session_id="s",
    )


def finished(task_id, status="completed"):
    return claude_line(
        type="system",
        subtype="task_notification",
        task_id=task_id,
        status=status,
        session_id="s",
    )


def emitting(lines, *, linger):
    """A child that writes `lines`, then either exits or waits to be stopped.

    `linger=True` is how a cancellation gets something to prove: a child that
    had already exited would make the cancellation look like it worked when it
    did nothing. `linger=False` is for the turns that are supposed to end on
    their own — a lingering child there would just hang the reader waiting for
    an EOF that never comes."""
    body = "import sys, time" + chr(10)
    for line in lines:
        body += f"sys.stdout.write({line!r} + chr(10))" + chr(10)
    body += "sys.stdout.flush()" + chr(10)
    if linger:
        body += "time.sleep(300)" + chr(10)
    return launch_plan(
        executable=Path(sys.executable),
        args=("-c", body),
        turn_request=turn_request(prompt=""),
    )


async def observe_claude(lines, *, budget, linger=True):
    process = await SubprocessSpawner().spawn(emitting(lines, linger=linger))
    observer = RunObserver(
        process,
        normalize=normalize_claude_line,
        run_id="run-7",
        delegation_budget=budget,
    )
    events = [event async for event in observer.events()]
    return events, await observer.outcome(), process


class BudgetEnforcementTests(unittest.IsolatedAsyncioTestCase):
    """The whole point of counting: what happens at the boundary."""

    ONE_START = DelegationBudget(
        max_concurrent_workers=3, max_total_worker_starts=1, max_depth=1
    )

    async def test_a_turn_inside_its_budget_is_not_disturbed(self):
        """The at-limit case. One start against a budget of one is spending
        the budget, not exceeding it.

        The worker is finished here on purpose. Written without the finish,
        this test asserted a completed turn over a worker whose end was never
        announced — which spec line 282 says is exactly not a completion. It
        passed until the rule existed, which is what a test encoding the wrong
        expectation looks like.
        """
        events, outcome, process = await observe_claude(
            [INIT, started("a"), finished("a"), RESULT],
            budget=self.ONE_START,
            linger=False,
        )

        self.assertEqual(RunStatus.COMPLETED, outcome.status)
        self.assertIn(
            "turn.completed", [event.kind for event in events]
        )

    async def test_the_start_past_the_budget_cancels_the_turn(self):
        events, outcome, process = await observe_claude(
            [INIT, started("a"), started("b"), RESULT],
            budget=self.ONE_START,
        )

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("DelegationBudgetExceeded", outcome.diagnostic)

    async def test_the_cancelled_turn_leaves_no_child_running(self):
        """`unknown_outcome` is about not knowing what the turn did, not about
        leaving it running while we wonder."""
        _events, _outcome, process = await observe_claude(
            [INIT, started("a"), started("b"), RESULT],
            budget=self.ONE_START,
        )

        self.assertIsNotNone(process.returncode)

    async def test_a_worker_too_deep_cancels_the_turn(self):
        """Measured on 2.1.240: the provider does not refuse this."""
        _events, outcome, _process = await observe_claude(
            [INIT, started("a", depth=2), RESULT],
            budget=DelegationBudget(
                max_concurrent_workers=3, max_total_worker_starts=4, max_depth=1
            ),
        )

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("DelegationDepthExceeded", outcome.diagnostic)

class UnresolvedWorkerTests(unittest.IsolatedAsyncioTestCase):
    """Spec line 282: a terminal result arriving while a worker is still
    unresolved does not make the turn complete. The provider says the
    conversation ended; it does not say the worker it launched stopped, and a
    turn reported complete over a live worker is a bill nobody is watching."""

    BUDGET = DelegationBudget(
        max_concurrent_workers=3, max_total_worker_starts=3, max_depth=1
    )

    async def test_a_turn_whose_worker_finished_completes(self):
        _events, outcome, _process = await observe_claude(
            [INIT, started("a"), finished("a"), RESULT],
            budget=self.BUDGET,
            linger=False,
        )

        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_a_terminal_over_a_live_worker_is_unknown(self):
        _events, outcome, _process = await observe_claude(
            [INIT, started("a"), RESULT],
            budget=self.BUDGET,
            linger=False,
        )

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("NativeWorkerUnresolved", outcome.diagnostic)

    async def test_only_the_unfinished_worker_matters(self):
        """Two started, one finished. The turn is still not complete."""
        _events, outcome, _process = await observe_claude(
            [INIT, started("a"), started("b"), finished("a"), RESULT],
            budget=self.BUDGET,
            linger=False,
        )

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)

    async def test_a_turn_without_a_budget_is_judged_as_before(self):
        """No budget means no ledger, so there is nothing to be unresolved
        against — every run written before delegation keeps its behaviour."""
        _events, outcome, _process = await observe_claude(
            [INIT, started("a"), RESULT], budget=None, linger=False
        )

        self.assertEqual(RunStatus.COMPLETED, outcome.status)

if __name__ == "__main__":
    unittest.main()
