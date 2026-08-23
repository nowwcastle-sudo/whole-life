"""Cancelling one run — normative source: spec section 6 and 7.

`정상 cancel은 stdin close 또는 provider가 지원하는 graceful signal을 먼저 보낸다.
5초 뒤에도 살아 있으면 Windows process tree를 종료하고, 최대 10초 동안 `wait`한다.`

`user가 시작한 cancel은 cancel 전에 terminal result가 이미 commit된 turn을 제외하고
항상 `unknown_outcome`이다.`

The escalation is tested against real children because "still alive after the
graceful window" is a property of a real process, not of a mock. The graceful
window is injectable so the fast cases stay fast, and one test uses the real
default so the injected value cannot drift away from the spec number.
"""

import asyncio
import sys
import unittest
from pathlib import Path

from tests.runtime.test_lifecycle import SPAWNS_A_GRANDCHILD, still_running
from tests.support.launch_fixtures import launch_plan, turn_request
from whole_life.runtime.contract import CancelOutcome, RunStatus
from whole_life.runtime.lifecycle import GRACEFUL_WAIT_SECONDS
from whole_life.runtime.normalize import normalize_codex_line
from whole_life.runtime.observe import RunObserver
from whole_life.runtime.spawn import SubprocessSpawner

COMPLETED = '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}'

#: Ignores the closed stdin and outlives any graceful window.
UNRESPONSIVE = "import time\ntime.sleep(120)\n"


def emitting(script):
    return launch_plan(
        executable=Path(sys.executable),
        args=("-c", script),
        turn_request=turn_request(prompt=""),
    )


async def observer_for(script):
    process = await SubprocessSpawner().spawn(emitting(script))
    return process, RunObserver(
        process, normalize=normalize_codex_line, run_id="run-16"
    )


class GracefulPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_child_that_ends_within_the_window_ends_gracefully(self):
        process, observer = await observer_for("import sys\nsys.exit(0)\n")

        outcome = await observer.cancel()

        self.assertEqual(CancelOutcome.GRACEFUL, outcome)
        self.assertEqual(0, process.returncode)

    async def test_the_graceful_window_defaults_to_the_spec_value(self):
        """The injected window in the fast tests must not drift from spec 207."""
        import inspect

        signature = inspect.signature(RunObserver.cancel)

        self.assertEqual(
            GRACEFUL_WAIT_SECONDS, signature.parameters["graceful_wait"].default
        )


class EscalationTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_unresponsive_child_is_tree_killed(self):
        process, observer = await observer_for(UNRESPONSIVE)

        outcome = await observer.cancel(graceful_wait=0.2)

        self.assertEqual(CancelOutcome.FORCED, outcome)
        self.assertIsNotNone(process.returncode)

    async def test_the_grandchild_of_an_unresponsive_child_dies_too(self):
        """`kill()` would end the CLI and leave the worker billing the account."""
        process, observer = await observer_for(SPAWNS_A_GRANDCHILD)
        grandchild = int((await process.stdout.readline()).strip())
        self.assertTrue(still_running(grandchild), "the fixture never started")

        outcome = await observer.cancel(graceful_wait=0.2)

        self.assertEqual(CancelOutcome.FORCED, outcome)
        self.assertFalse(still_running(grandchild))

    async def test_escalation_really_waits_the_normative_window(self):
        """With the real default, an unresponsive child is not killed early."""
        process, observer = await observer_for(UNRESPONSIVE)
        loop = asyncio.get_running_loop()

        started = loop.time()
        outcome = await observer.cancel()
        elapsed = loop.time() - started

        self.assertEqual(CancelOutcome.FORCED, outcome)
        self.assertGreaterEqual(elapsed, GRACEFUL_WAIT_SECONDS)
        self.assertIsNotNone(process.returncode)


class CancelledOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_cancel_before_any_terminal_leaves_the_run_unknown(self):
        _process, observer = await observer_for(UNRESPONSIVE)

        await observer.cancel(graceful_wait=0.2)
        outcome = await observer.outcome()

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("CancelledBeforeTerminal", outcome.diagnostic)

    async def test_a_terminal_committed_before_the_cancel_survives_it(self):
        """The turn finished; stopping the process afterwards changes nothing."""
        script = f"import sys, time\nsys.stdout.write({COMPLETED!r} + chr(10))\nsys.stdout.flush()\ntime.sleep(120)\n"
        _process, observer = await observer_for(script)

        kinds = []
        stream = observer.events()
        async for event in stream:
            kinds.append(event.kind)
            break
        self.assertEqual(["turn.completed"], kinds)

        await observer.cancel(graceful_wait=0.2)
        await stream.aclose()
        outcome = await observer.outcome()

        # Completed, not failed: the nonzero exit below is our own kill, and
        # the provider had already committed its result before we sent it.
        self.assertEqual(RunStatus.COMPLETED, outcome.status)
        self.assertNotEqual(0, outcome.exit_code)

    # `test_a_timeout_cancel_is_also_unknown` lived here. Its whole content was
    # passing `CancelCause.TIMEOUT` instead of `USER` and expecting the same
    # answer — which was the point while a cause existed. With the parameter
    # gone it became a character-for-character duplicate of the first test in
    # this class, so it is removed rather than kept as a second name for one
    # assertion. What it was really guarding — spec 279, that the deadline
    # decides whatever arrives afterwards — is held by
    # `test_a_late_clean_exit_does_not_upgrade_a_cancelled_run` in
    # tests/runtime/test_outcome.py, which exercises the ordering directly.


class NoResidueTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_an_unobserved_run_still_reaps_it(self):
        """Nobody ever read this run's stdout — the carried case from #15.

        A run started and never observed has no reader draining its pipes, so
        `wait()` can park on an exit waiter that never resolves. Cancel has to
        end bounded regardless of whether anyone was listening.
        """
        process, observer = await observer_for(UNRESPONSIVE)

        outcome = await asyncio.wait_for(
            observer.cancel(graceful_wait=0.2), timeout=20
        )

        self.assertEqual(CancelOutcome.FORCED, outcome)
        self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
