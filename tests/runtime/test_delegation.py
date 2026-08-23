"""Holding a turn to its native-worker budget. Spec sections 4 and 8.

`profile의 turn delegation budget보다 하나 많은 worker start를 관찰하면 즉시
cancel·unknown_outcome`

The boundary is what these tests are about. A ledger that cancels one start too
early spends a budget the operator paid for; one that cancels one start too late
has already let the turn exceed it.
"""

import asyncio
import json
import sys
import unittest
from unittest import mock
from pathlib import Path

from tests.support.launch_fixtures import (
    codex_delegation_measured,
    RecordingSpawner,
    launch_plan,
    turn_request,
)
from tests.support.preflight_fixtures import (
    CLAUDE_EXECUTABLE,
    CODEX_EXECUTABLE,
    CODEX_HOME,
    PARENT_ENV,
    claude_runner,
    codex_runner,
)
from whole_life.runtime.claude import ClaudeRuntime
from whole_life.runtime.codex import CodexRuntime
from whole_life.runtime.contract import (
    DelegationBudget,
    Provider,
    EnforcementLevel,
    RunStatus,
)
from whole_life.runtime.normalize import normalize_claude_line
from whole_life.runtime.launch import (
    SUPPORTED_VERSIONS,
    launch,
    PreStartRefusal,
    RefusalCode,
    enforce_launch_safety,
)
from whole_life.runtime.observe import RunObserver
from whole_life.runtime.outcome import TerminalEvent
from whole_life.runtime.spawn import SubprocessSpawner
from whole_life.runtime.delegation import (
    REPORTED_ENFORCEMENT,
    DelegationLedger,
)

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
        ledger.worker_started(native_child_id="a", depth=1)
        # Arrives while "a" is live. Sent before anything started, it could
        # not have subtracted a live worker even if the code let it, so the
        # ordering is what makes this test able to fail.
        ledger.worker_finished(native_child_id="never-seen")

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

#: Long enough for a real child to be spawned, watched and ended, short
#: enough that a broken cancellation path is a failure rather than a hang.
OBSERVE_TIMEOUT_SECONDS = 60


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
    """Start the turn the way production starts one.

    Through `launch`, not straight to the spawner: section 12 asks that the
    capability check and the budget checks be the same door, and a test that
    reaches around the door proves nothing about what the door does.
    """
    process = await launch(emitting(lines, linger=linger), SubprocessSpawner())
    observer = RunObserver(
        process,
        normalize=normalize_claude_line,
        run_id="run-7",
        delegation_budget=budget,
    )

    async def drain():
        return [event async for event in observer.events()]

    # Bounded on purpose. A child that lingers is ended by the breach path,
    # so if that path ever stops working the read below waits on an EOF that
    # never comes. Unbounded, the failure is a hung suite — which reports as
    # nothing at all; bounded, it is a test that fails and names itself.
    events = await asyncio.wait_for(drain(), timeout=OBSERVE_TIMEOUT_SECONDS)
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

class ReportedEnforcementTests(unittest.IsolatedAsyncioTestCase):
    """What each provider can honestly hold, reported per limit. Spec §4.

    Reported as measured. An `unsupported` that is quietly reported as
    `cooperative` is the failure this whole slice exists to prevent: it reads
    as a bound being held by something that is not holding it."""

    async def test_claude_reports_depth_as_cooperative_not_hard(self):
        """Corrected from the specification's original `hard` by measurement:
        a recorded 2.1.240 turn ran a worker at depth 2, finished successfully,
        and refused nothing."""
        runtime = ClaudeRuntime(
            executable=CLAUDE_EXECUTABLE,
            runner=claude_runner(),
            parent_env=PARENT_ENV,
        )

        status = await runtime.preflight()

        self.assertEqual(
            EnforcementLevel.COOPERATIVE, status.delegation_depth_enforcement
        )

    async def test_the_three_limits_are_reported_separately(self):
        """One field per limit, because a provider can hold one and not
        another. Codex holds concurrency; nothing else is a hard cap."""
        runtime = ClaudeRuntime(
            executable=CLAUDE_EXECUTABLE,
            runner=claude_runner(),
            parent_env=PARENT_ENV,
        )

        status = await runtime.preflight()

        self.assertEqual(
            EnforcementLevel.COOPERATIVE,
            status.worker_concurrency_enforcement,
        )
        self.assertEqual(
            EnforcementLevel.COOPERATIVE,
            status.worker_total_start_enforcement,
        )

    async def test_codex_total_starts_are_unsupported_until_measured(self):
        """Spec line 119: a runtime whose worker starts cannot be counted
        before the budget is broken reports `unsupported`. The 0.149.0 stream
        has no worker lifecycle item, and the live re-measurement is blocked
        until the subscription window reopens — so this is what is known, not
        what is hoped."""
        runtime = CodexRuntime(
            executable=CODEX_EXECUTABLE,
            runner=codex_runner(),
            parent_env=PARENT_ENV,
            codex_home=CODEX_HOME,
        )

        status = await runtime.preflight()

        self.assertEqual(
            {
                EnforcementLevel.UNSUPPORTED: 3,
            },
            {
                EnforcementLevel.UNSUPPORTED: [
                    status.worker_concurrency_enforcement,
                    status.worker_total_start_enforcement,
                    status.delegation_depth_enforcement,
                ].count(EnforcementLevel.UNSUPPORTED)
            },
        )

    async def test_no_axis_is_reported_as_held_by_an_unmeasured_provider(self):
        """The inline concurrency cap is configuration this project sets, not
        an enforcement anyone watched happen. Reporting it as `hard` would be
        the exact substitution this table exists to refuse — a claim standing
        in for a measurement."""
        runtime = CodexRuntime(
            executable=CODEX_EXECUTABLE,
            runner=codex_runner(),
            parent_env=PARENT_ENV,
            codex_home=CODEX_HOME,
        )

        status = await runtime.preflight()

        self.assertNotIn(
            EnforcementLevel.HARD,
            (
                status.worker_concurrency_enforcement,
                status.worker_total_start_enforcement,
                status.delegation_depth_enforcement,
            ),
        )

class FailClosedCapabilityTests(unittest.IsolatedAsyncioTestCase):
    """Spec line 121: delegation capability that preflight could not confirm
    refuses the turn. Every v0 profile grants delegation, so a runtime that
    cannot show it holds the limits is not quietly demoted to a single-agent
    turn — it does not run."""

    async def test_a_runtime_that_cannot_show_its_limits_refuses_before_spawn(
        self,
    ):
        runtime = CodexRuntime(
            executable=CODEX_EXECUTABLE,
            runner=codex_runner(),
            parent_env=PARENT_ENV,
            codex_home=CODEX_HOME,
        )
        await runtime.preflight()

        plan = runtime.assemble_launch_plan(turn_request(prompt="hi"))

        with self.assertRaises(PreStartRefusal) as caught:
            enforce_launch_safety(plan)

        self.assertEqual(
            RefusalCode.DELEGATION_UNSUPPORTED, caught.exception.code
        )

    async def test_a_runtime_that_can_show_them_assembles_normally(self):
        runtime = ClaudeRuntime(
            executable=CLAUDE_EXECUTABLE,
            runner=claude_runner(),
            parent_env=PARENT_ENV,
        )
        await runtime.preflight()

        plan = runtime.assemble_launch_plan(turn_request(prompt="hi"))

        self.assertIsNone(enforce_launch_safety(plan))

class ClaimedEnforcementTests(unittest.TestCase):
    """What a plan says about itself is not evidence that it is true.

    #12 closed this shape once already: the gate trusted a caller-supplied
    `allowlisted` flag until it was made to compare the whole record against
    the canonical table. A delegation row travelling on the plan is the same
    kind of value — a report, not a measurement — so the gate resolves it the
    same way, by looking it up.
    """

    @staticmethod
    def codex_plan(row):
        return launch_plan(
            provider=Provider.CODEX,
            version_conformance=SUPPORTED_VERSIONS[Provider.CODEX]["0.149.0"],
            turn_request=turn_request(),
            delegation_enforcement=row,
        )

    def refusal(self, row):
        with self.assertRaises(PreStartRefusal) as caught:
            enforce_launch_safety(self.codex_plan(row))
        return caught.exception.code

    def test_the_honest_row_of_an_unmeasured_provider_is_refused(self):
        self.assertEqual(
            RefusalCode.DELEGATION_UNSUPPORTED,
            self.refusal(REPORTED_ENFORCEMENT[Provider.CODEX]),
        )

    def test_a_row_the_caller_made_up_is_refused(self):
        """The plan claims every limit is held. The measurement table says
        none of them is known to be."""
        self.assertEqual(
            RefusalCode.DELEGATION_UNSUPPORTED,
            self.refusal(
                {
                    "worker_concurrency_enforcement": EnforcementLevel.COOPERATIVE,
                    "worker_total_start_enforcement": EnforcementLevel.COOPERATIVE,
                    "delegation_depth_enforcement": EnforcementLevel.COOPERATIVE,
                }
            ),
        )

    def test_a_row_with_no_axes_at_all_is_refused(self):
        """A check written as "no axis says unsupported" passes an empty
        mapping, because an empty mapping says nothing at all. Omission is
        quieter than a lie and has to be refused just as firmly."""
        self.assertEqual(
            RefusalCode.DELEGATION_UNSUPPORTED, self.refusal({})
        )

    def test_a_row_missing_an_axis_is_refused(self):
        self.assertEqual(
            RefusalCode.DELEGATION_UNSUPPORTED,
            self.refusal(
                {"worker_total_start_enforcement": EnforcementLevel.COOPERATIVE}
            ),
        )

    def test_a_row_that_disagrees_with_the_measurement_is_refused(self):
        """The provider here is one whose limits *are* measured, so nothing
        else in the gate objects. What is refused is the disagreement itself:
        the plan reports something the measurement does not say. Without this
        the whole-record comparison could be deleted and every other test
        would stay green."""
        plan = launch_plan(
            provider=Provider.CLAUDE,
            version_conformance=SUPPORTED_VERSIONS[Provider.CLAUDE]["2.1.240"],
            turn_request=turn_request(),
            delegation_enforcement={
                "worker_concurrency_enforcement": EnforcementLevel.HARD,
                "worker_total_start_enforcement": EnforcementLevel.HARD,
                "delegation_depth_enforcement": EnforcementLevel.HARD,
            },
        )

        with self.assertRaises(PreStartRefusal) as caught:
            enforce_launch_safety(plan)

        self.assertEqual(
            RefusalCode.DELEGATION_UNSUPPORTED, caught.exception.code
        )

    def test_a_measurement_missing_an_axis_refuses_every_turn(self):
        """`DELEGATION_AXES` guards the measurement table, not the plan. A row
        that lost an axis reports nothing about that limit, and a check
        written as "no axis says unsupported" passes it silently — so the gate
        asks whether all three are present."""
        partial = {
            key: value
            for key, value in REPORTED_ENFORCEMENT[Provider.CLAUDE].items()
            if key != "delegation_depth_enforcement"
        }
        plan = launch_plan(
            provider=Provider.CLAUDE,
            version_conformance=SUPPORTED_VERSIONS[Provider.CLAUDE]["2.1.240"],
            turn_request=turn_request(),
            delegation_enforcement=partial,
        )

        with mock.patch.dict(
            REPORTED_ENFORCEMENT, {Provider.CLAUDE: partial}
        ):
            with self.assertRaises(PreStartRefusal) as caught:
                enforce_launch_safety(plan)

        self.assertEqual(
            RefusalCode.DELEGATION_UNSUPPORTED, caught.exception.code
        )

    def test_a_measured_provider_still_starts(self):
        """The control. Without this, every assertion above could be passing
        because the gate refuses everything."""
        plan = launch_plan(
            provider=Provider.CLAUDE,
            version_conformance=SUPPORTED_VERSIONS[Provider.CLAUDE]["2.1.240"],
            turn_request=turn_request(),
            delegation_enforcement=REPORTED_ENFORCEMENT[Provider.CLAUDE],
        )

        self.assertIsNone(enforce_launch_safety(plan))

class OneDoorTests(unittest.IsolatedAsyncioTestCase):
    """Section 12: capability, budget, excess-cancel and unresolved-worker are
    checked at one place. The turns in this module all start through `launch`,
    so a plan the gate refuses never reaches a process."""

    async def test_a_refused_plan_never_reaches_the_spawner(self):
        spawner = RecordingSpawner()
        plan = launch_plan(
            provider=Provider.CODEX,
            version_conformance=SUPPORTED_VERSIONS[Provider.CODEX]["0.149.0"],
            turn_request=turn_request(),
            delegation_enforcement=REPORTED_ENFORCEMENT[Provider.CODEX],
        )

        with self.assertRaises(PreStartRefusal) as caught:
            await launch(plan, spawner)

        self.assertEqual(
            RefusalCode.DELEGATION_UNSUPPORTED, caught.exception.code
        )
        self.assertEqual([], spawner.calls)

    async def test_the_budget_turns_above_pass_the_same_gate(self):
        """The control for the assertion above: the plan those turns use is
        accepted by the very gate that refuses the one in this test."""
        plan = emitting([INIT, RESULT], linger=False)

        self.assertIsNone(enforce_launch_safety(plan))

class ProductionPathTests(unittest.IsolatedAsyncioTestCase):
    """The budget has to reach the run the adapter actually starts.

    Everything above builds a `RunObserver` directly, which proves the ledger
    works and proves nothing about whether a real turn is given one. This
    repository has been bitten by that shape three times — a control defined
    and tested, and never wired to the path that runs. So this class starts
    the turn through `start_turn` and reads it through `events`, the way the
    broker will.
    """

    @staticmethod
    async def claude_running(lines, *, request, linger=True):
        runtime = ClaudeRuntime(
            executable=Path(sys.executable),
            runner=claude_runner(),
            parent_env=PARENT_ENV,
            spawner=SubprocessSpawner(),
        )
        await runtime.preflight()
        body = "import sys, time" + chr(10)
        for line in lines:
            body += f"sys.stdout.write({line!r} + chr(10))" + chr(10)
        body += "sys.stdout.flush()" + chr(10)
        # Lingering is how a cancellation gets something to prove. A turn that
        # is supposed to end on its own must not linger, or the reader waits
        # for an EOF the child was told never to send.
        if linger:
            body += "time.sleep(300)" + chr(10)
        runtime.turn_args_override = ("-c", body)
        return runtime, await runtime.start_turn(request)

    async def test_a_started_turn_carries_its_delegation_budget(self):
        """Read off the run the adapter started, not off a value this test
        handed it."""
        request = turn_request(
            prompt="hi",
            delegation_budget=DelegationBudget(
                max_concurrent_workers=3,
                max_total_worker_starts=1,
                max_depth=1,
            ),
        )
        runtime, handle = await self.claude_running(
            [INIT], request=request, linger=False
        )

        try:
            observer = runtime._runs[handle.run_id]
            self.assertIsNotNone(
                observer._ledger,
                "start_turn gave the run no budget to hold it to",
            )
        finally:
            await runtime.close()

    async def test_a_codex_turn_also_carries_its_budget(self):
        """The other adapter. Mutation `U` — deleting the argument from the
        Codex adapter alone — survived a run where only Claude was checked,
        which is what a coverage gap looks like from the inside: every
        assertion passing and one of two call sites unexamined.

        The measurement table is stood in for here because this test is about
        wiring, not capability: Codex reports `unsupported` until its
        delegation measurement is made, and the gate would refuse the turn
        before the question this test asks could be reached.
        """
        request = turn_request(prompt="hi")
        with codex_delegation_measured():
            runtime = CodexRuntime(
                executable=Path(sys.executable),
                runner=codex_runner(),
                parent_env=PARENT_ENV,
                codex_home=CODEX_HOME,
                spawner=SubprocessSpawner(),
            )
            await runtime.preflight()
            runtime.turn_args_override = ("-c", "pass")
            handle = await runtime.start_turn(request)

            try:
                observer = runtime._runs[handle.run_id]
                self.assertIsNotNone(
                    observer._ledger,
                    "start_turn gave the run no budget to hold it to",
                )
            finally:
                await runtime.close()

    async def test_a_started_turn_is_cancelled_when_it_overspends(self):
        """The whole chain, through the door the broker uses: assemble, gate,
        spawn, observe, count, cancel, resolve."""
        request = turn_request(
            prompt="hi",
            delegation_budget=DelegationBudget(
                max_concurrent_workers=3,
                max_total_worker_starts=1,
                max_depth=1,
            ),
        )
        runtime, handle = await self.claude_running(
            [INIT, started("a"), started("b"), RESULT], request=request
        )

        try:
            async def drain():
                return [event async for event in runtime.events(handle)]

            await asyncio.wait_for(drain(), timeout=OBSERVE_TIMEOUT_SECONDS)
            outcome = await runtime.wait(handle)
        finally:
            await runtime.close()

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("DelegationBudgetExceeded", outcome.diagnostic)

    async def test_a_started_turn_is_unknown_while_a_worker_is_unresolved(self):
        request = turn_request(
            prompt="hi",
            delegation_budget=DelegationBudget(
                max_concurrent_workers=3,
                max_total_worker_starts=3,
                max_depth=1,
            ),
        )
        runtime, handle = await self.claude_running(
            [INIT, started("a"), RESULT], request=request, linger=False
        )

        try:
            async def drain():
                return [event async for event in runtime.events(handle)]

            await asyncio.wait_for(drain(), timeout=OBSERVE_TIMEOUT_SECONDS)
            outcome = await runtime.wait(handle)
        finally:
            await runtime.close()

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("NativeWorkerUnresolved", outcome.diagnostic)

    async def test_a_cancelled_turn_is_unknown_when_a_worker_never_resolved(self):
        """Spec 282 over 279, through the door the broker uses.

        A terminal result committed before the cancellation survives it — that
        is 279, and it is why an ordinary post-terminal shutdown is not rewritten
        into a failure. But 282 names the case where the same terminal result
        arrives *over an announced worker nobody said stopped*, and there the
        honest answer is that we do not know. The two are only distinguishable
        after the terminal event has been consumed and the cancellation has
        landed on top of it, so this drives the real stream in two phases rather
        than resolving the outcome directly.
        """
        request = turn_request(
            prompt="hi",
            delegation_budget=DelegationBudget(
                max_concurrent_workers=3,
                max_total_worker_starts=3,
                max_depth=1,
            ),
        )
        # Lingering on purpose: a child that had already exited would let the
        # cancellation land on a corpse, and then this test would pass without
        # the ordering it means to pin.
        runtime, handle = await self.claude_running(
            [INIT, started("a"), RESULT], request=request, linger=True
        )
        stream = runtime.events(handle)
        seen = []

        try:
            # `terminal` is observer bookkeeping and deliberately not on the
            # contract type, so the consumer's signal that the turn committed
            # a result is the event kind.
            async def until_terminal():
                async for event in stream:
                    seen.append(event)
                    if event.kind == "turn.completed":
                        return

            await asyncio.wait_for(
                until_terminal(), timeout=OBSERVE_TIMEOUT_SECONDS
            )
            self.assertTrue(
                any(event.kind == "turn.completed" for event in seen),
                "the turn never committed a terminal result, so there is no "
                "cancel-after-terminal ordering to test",
            )

            await runtime.cancel(handle, graceful_wait=0.2)

            async def rest():
                async for event in stream:
                    seen.append(event)

            await asyncio.wait_for(rest(), timeout=OBSERVE_TIMEOUT_SECONDS)
            outcome = await runtime.wait(handle)
        finally:
            await runtime.close()

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("NativeWorkerUnresolved", outcome.diagnostic)

    async def test_a_cancelled_turn_with_every_worker_settled_stays_completed(self):
        """The control for the test above, and the thing #16 is owed.

        Same shape — terminal result, then a cancellation on a child still
        running — but every worker the provider announced was announced
        finished. Spec 279 stands here: an ordinary post-terminal shutdown is
        not a failure and not an unknown. Without this, the test above could be
        satisfied by deleting the branch it depends on, which would silently
        rewrite every clean broker shutdown.
        """
        request = turn_request(
            prompt="hi",
            delegation_budget=DelegationBudget(
                max_concurrent_workers=3,
                max_total_worker_starts=3,
                max_depth=1,
            ),
        )
        runtime, handle = await self.claude_running(
            [INIT, started("a"), finished("a"), RESULT],
            request=request,
            linger=True,
        )
        stream = runtime.events(handle)
        seen = []

        try:
            async def until_terminal():
                async for event in stream:
                    seen.append(event)
                    if event.kind == "turn.completed":
                        return

            await asyncio.wait_for(
                until_terminal(), timeout=OBSERVE_TIMEOUT_SECONDS
            )

            await runtime.cancel(handle, graceful_wait=0.2)

            async def rest():
                async for event in stream:
                    seen.append(event)

            await asyncio.wait_for(rest(), timeout=OBSERVE_TIMEOUT_SECONDS)
            outcome = await runtime.wait(handle)
        finally:
            await runtime.close()

        self.assertEqual(RunStatus.COMPLETED, outcome.status)
        self.assertIsNone(outcome.diagnostic)

if __name__ == "__main__":
    unittest.main()
