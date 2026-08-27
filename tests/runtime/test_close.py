"""Shutting the runtime down — normative source: spec section 6.

`broker shutdown은 모든 active run에 cancel을 수행한 뒤 drain task와 process handle을
전수 확인한다.`

`close()`가 반환될 때 이 runtime이 만든 child process와 stdout/stderr drain task는
0개여야 한다.`

The counts are the point. "We asked everything to stop" is not the promise —
the promise is that when `close()` returns there is nothing left, so the tests
count children and drain tasks rather than trusting that cancellation was
issued.
"""

import asyncio
import sys
import unittest
from pathlib import Path

from tests.runtime.test_lifecycle import still_running
from tests.runtime.test_spawner import force_stop, settled
from tests.support.launch_fixtures import (
    codex_delegation_measured,
    launch_plan,
    turn_request,
)
from tests.support.lifecycle_fixtures import (
    KILLER_UNAVAILABLE,
    without_windows_roots,
)
from tests.support.preflight_fixtures import (
    WORKING_DIRECTORY,
    CODEX_HOME,
    codex_runner,
)
from whole_life.runtime.codex import CodexRuntime
from whole_life.runtime.delegation import REPORTED_ENFORCEMENT
from whole_life.runtime.contract import (
    Provider,
    CancelOutcome,
    RunHandle,
    RunStatus,
    TurnMode,
)
from whole_life.runtime.launch import (
    ActiveNativeSessions,
    PreStartRefusal,
    RefusalCode,
)
from whole_life.runtime.normalize import normalize_codex_line
from whole_life.runtime.observe import RunObserver
from whole_life.runtime.spawn import SubprocessSpawner


#: This module's subject is not delegation capability. See the helper.
_CODEX_MEASURED = None


def setUpModule():
    global _CODEX_MEASURED
    _CODEX_MEASURED = codex_delegation_measured()
    _CODEX_MEASURED.start()


def tearDownModule():
    _CODEX_MEASURED.stop()


CLEAN_PARENT_ENV = {"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\Windows\system32"}
MESSAGE = '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"hi"}}'
COMPLETED = '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}'

#: Outlives any graceful window, so shutdown has to escalate to reach it.
UNRESPONSIVE = "import time\ntime.sleep(120)\n"

#: Emits one terminal line and keeps running, so a run can be mid-flight.
STREAMING = (
    "import sys, time\n"
    f"sys.stdout.write({COMPLETED!r} + chr(10))\n"
    "sys.stdout.flush()\n"
    "time.sleep(120)\n"
)


async def adapter(script, *, sessions=None, spawner=None):
    runtime = CodexRuntime(
        executable=Path(sys.executable),
        runner=codex_runner(),
        working_directory=WORKING_DIRECTORY,
        parent_env=CLEAN_PARENT_ENV,
        codex_home=CODEX_HOME,
        spawner=spawner or SubprocessSpawner(),
        sessions=sessions,
    )
    await runtime.preflight()
    runtime.turn_args_override = ("-c", script)
    return runtime


class CloseLeavesNothingTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_ends_every_active_run(self):
        runtime = await adapter(UNRESPONSIVE)
        handles = [await runtime.start_turn(turn_request()) for _ in range(3)]

        await asyncio.wait_for(runtime.close(), timeout=40)

        self.assertEqual(3, len(handles))
        self.assertEqual(0, runtime.active_child_count())
        self.assertEqual(0, runtime.drain_task_count())

    async def test_close_ends_a_run_nobody_ever_observed(self):
        """Carried from #15: a run started and never consumed had no owner.

        Its pipes were never drained and its cleanup never ran, so it lived
        until interpreter shutdown surfaced it as an unclosed transport.
        """
        runtime = await adapter(UNRESPONSIVE)
        await runtime.start_turn(turn_request())

        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, runtime.active_child_count())

    async def test_close_ends_a_run_that_is_being_consumed(self):
        """A consumer mid-stream must not keep the drain task alive."""
        runtime = await adapter(STREAMING)
        handle = await runtime.start_turn(turn_request())

        stream = runtime.events(handle)
        first = await anext(stream)
        self.assertEqual("turn.completed", first.kind)

        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, runtime.active_child_count())
        self.assertEqual(0, runtime.drain_task_count())
        await stream.aclose()

    async def test_close_is_idempotent(self):
        runtime = await adapter(UNRESPONSIVE)
        await runtime.start_turn(turn_request())

        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)
        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, runtime.active_child_count())

    async def test_closing_with_no_runs_at_all_is_fine(self):
        runtime = await adapter(UNRESPONSIVE)

        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, runtime.active_child_count())


class CountersActuallyCountTests(unittest.IsolatedAsyncioTestCase):
    """A zero has to mean "emptied", not "never counted".

    Every assertion above checks a count of zero after `close()`. On their own
    those pass just as happily against a counter hardwired to return 0 — the
    mutation proof found exactly that, three times. So each counter is watched
    reporting a live run first; only then does the zero afterwards mean the
    thing it claims.
    """

    async def test_a_live_child_is_counted_before_close_empties_it(self):
        runtime = await adapter(UNRESPONSIVE)
        await runtime.start_turn(turn_request())

        self.assertEqual(1, runtime.active_child_count())

        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, runtime.active_child_count())

    async def test_live_drain_tasks_are_counted_before_close_ends_them(self):
        runtime = await adapter(STREAMING)
        handle = await runtime.start_turn(turn_request())

        stream = runtime.events(handle)
        await anext(stream)

        # Both readers: stdout and stderr are drained by independent tasks.
        self.assertEqual(2, runtime.drain_task_count())

        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, runtime.drain_task_count())
        await stream.aclose()


class ParkedReaderShutdownTests(unittest.IsolatedAsyncioTestCase):
    """Killing the child does not release a reader parked on a full queue.

    A drain task normally ends by itself: the child dies, stdout reaches EOF,
    the loop finishes. That is why removing shutdown's explicit cancel survived
    the first mutation run — in every case tested, the task was going to end
    anyway.

    It stops being true the moment a consumer stops reading. Then the stdout
    reader is blocked in `queue.put`, not in a read, and no amount of killing
    the child will wake it. The precondition is established deterministically
    here rather than hoped for, the same way #15's deadlock had to be.
    """

    async def test_a_reader_blocked_on_a_full_queue_is_still_ended(self):
        # Non-terminal lines on purpose: after a terminal event the observer
        # suppresses everything that follows, so a flood of them would never
        # fill the queue and the reader would never park.
        process = await SubprocessSpawner().spawn(_plan(_floods(MESSAGE)))
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-16", queue_maxsize=1
        )

        stream = observer.events()
        async for _event in stream:
            break

        for _ in range(200):
            if observer.queue_depth() >= 1:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(1, observer.queue_depth(), "the reader never parked")

        await asyncio.wait_for(observer.shutdown(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, observer.pending_drain_tasks())
        self.assertIsNotNone(process.returncode)


class GracefulPathResidueTests(unittest.IsolatedAsyncioTestCase):
    """The graceful path closes the transport too.

    On the forced path `terminate_process_tree` closes it, so shutdown's own
    close looks redundant — and the mutation proof duly survived removing it.
    It is not redundant where the tree kill never runs: a child that ends
    inside the graceful window is reaped with its pipes still open, and those
    surface later as `unclosed transport` from a destructor.

    A spy, because "was the transport closed" is a fact about a call, and
    asking a real one leaves it to garbage-collection timing.
    """

    async def test_a_gracefully_ended_run_leaves_no_open_transport(self):
        process = _AlreadyDone()
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-16"
        )

        outcome = await observer.shutdown(graceful_wait=0.2)

        self.assertEqual(CancelOutcome.GRACEFUL, outcome)
        self.assertEqual(["close"], process.journal)


def _floods(line):
    """A child that writes far more than a queue of one can hold."""
    return (
        "import sys, time" + chr(10)
        + "for _ in range(2000):" + chr(10)
        + f"    sys.stdout.write({line!r} + chr(10))" + chr(10)
        + "sys.stdout.flush()" + chr(10)
        + "time.sleep(120)" + chr(10)
    )


def _plan(script):
    return launch_plan(
        executable=Path(sys.executable),
        args=("-c", script),
        turn_request=turn_request(prompt=""),
    )


class _AlreadyDone:
    """A child that has already exited, so cancel takes the graceful path."""

    def __init__(self):
        self.journal = []
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.returncode = 0
        self.pid = 0
        self._transport = _JournallingTransport(self)

    def kill(self):
        self.journal.append("kill")

    async def wait(self):
        return self.returncode


class _JournallingTransport:
    def __init__(self, process):
        self._process = process

    def close(self):
        self._process.journal.append("close")


class ShutdownOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_run_stopped_by_shutdown_is_unknown_not_success(self):
        """Spec 213: an unresolved ending is never quietly upgraded."""
        runtime = await adapter(UNRESPONSIVE)
        handle = await runtime.start_turn(turn_request())

        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)
        outcome = await runtime.wait(handle)

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("CancelledBeforeTerminal", outcome.diagnostic)


class AdapterCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_through_the_adapter_reaches_the_run(self):
        runtime = await adapter(UNRESPONSIVE)
        handle = await runtime.start_turn(turn_request())
        self.addCleanup(lambda: None)

        result = await runtime.cancel(handle, graceful_wait=0.2)

        self.assertIn(result, (CancelOutcome.FORCED, CancelOutcome.GRACEFUL))
        self.assertEqual(0, runtime.active_child_count())
        await runtime.close(graceful_wait=0.2)

    async def test_an_unknown_handle_is_rejected(self):
        """A handle this adapter never issued is a programming error."""
        runtime = await adapter(UNRESPONSIVE)
        stranger = RunHandle(
            run_id="never-started",
            participant_id="claude-01",
            provider=runtime.provider,
        )

        with self.assertRaises(KeyError):
            await runtime.cancel(stranger)

        await runtime.close(graceful_wait=0.2)



class NormativeDefaultTests(unittest.IsolatedAsyncioTestCase):
    """The short window used above is a test seam, not the shipped number.

    Every fast test overrides the graceful window so the suite does not spend
    five seconds per unresponsive child. That is only safe while the default is
    still the spec value — otherwise the tests would agree with each other and
    with nothing else.
    """

    def test_close_and_cancel_default_to_the_spec_window(self):
        import inspect

        from whole_life.runtime.claude import ClaudeRuntime
        from whole_life.runtime.lifecycle import GRACEFUL_WAIT_SECONDS

        for runtime in (CodexRuntime, ClaudeRuntime):
            for method in ("close", "cancel"):
                with self.subTest(runtime=runtime.__name__, method=method):
                    signature = inspect.signature(getattr(runtime, method))
                    self.assertEqual(
                        GRACEFUL_WAIT_SECONDS,
                        signature.parameters["graceful_wait"].default,
                    )


class UnresolvedTerminationTests(unittest.IsolatedAsyncioTestCase):
    """`종료 결과를 확정할 수 없으면 성공이나 재시도 가능으로 추정하지 않는다.` Spec 213.

    Two halves. A termination we could not confirm must say so rather than
    report a tidy `forced`; and the native session it may still be holding must
    not become available for an automatic resume, because a turn that partially
    executed against provider-owned conversation state cannot be safely
    repeated — replaying it compounds the damage instead of repairing it.
    """

    async def test_a_kill_that_cannot_be_confirmed_reports_unknown(self):
        """Hermetic: a process that never reports an exit, so nothing races."""
        process = _NeverExits()
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-16"
        )

        result = await observer.cancel(
            graceful_wait=0.05, forced_wait=0.05
        )

        self.assertEqual(CancelOutcome.UNKNOWN, result)

    async def test_an_unknown_run_still_holds_its_native_session(self):
        """An automatic resume of the same session is refused, not attempted."""
        runtime = await adapter(UNRESPONSIVE, sessions=ActiveNativeSessions())
        resuming = turn_request(mode=TurnMode.RESUME, native_session_id="th_16")

        await runtime.start_turn(resuming)
        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        with self.assertRaises(PreStartRefusal) as caught:
            await runtime.start_turn(resuming)

        self.assertEqual(
            RefusalCode.CONCURRENT_RESUME_REJECTED, caught.exception.code
        )
class _NeverExits:
    """A child that cannot be killed, so termination cannot be confirmed."""

    def __init__(self):
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.returncode = None
        self.pid = 0
        self._transport = None

    def kill(self):
        pass

    async def wait(self):
        await asyncio.sleep(3600)


class _UnreapableSpawner:
    """A spawner whose child never reports an exit.

    The point is not that the child survives — Windows ends a process we
    `kill()` — but that nothing ever *reports* it ended: the exit waiter the
    bounded wait parks on never resolves. That is the case the bounded wait
    exists for, and whether it holds is asyncio bookkeeping rather than a
    property a real child can be asked to guarantee, so the documented
    spawner seam carries it while everything above stays real.
    """

    async def spawn(self, plan):
        return _NeverExits()


class TreeKillUnavailableTests(unittest.IsolatedAsyncioTestCase):
    """Spec section 6 via issue #34: a failed cleanup must not become the story.

    When the escalation cannot run, the caller is still owed the answer it asked
    for. A cleanup failure that arrives *instead* of that answer sends the
    operator after the wrong thing while the child is still running.
    """

    async def started_unresponsive_run(self):
        runtime = await adapter(UNRESPONSIVE)
        handle = await runtime.start_turn(turn_request())
        pid = runtime._runs[handle.run_id]._process.pid
        # Registered before the environment is touched, so it runs after the
        # patch has been lifted and can locate the killer it needs.
        self.addCleanup(force_stop, pid)
        return runtime, handle, pid

    async def test_a_cancel_reports_its_outcome_when_the_tree_killer_is_missing(self):
        for name, broken, _reason in KILLER_UNAVAILABLE:
            with self.subTest(name):
                runtime, handle, _pid = await self.started_unresponsive_run()

                with broken():
                    outcome = await runtime.cancel(handle, graceful_wait=0.2)

                self.assertEqual(CancelOutcome.UNKNOWN, outcome)
                await runtime.close(graceful_wait=0.2)

    async def test_why_the_escalation_failed_is_still_available(self):
        """Alongside, not instead. `UNKNOWN` says the tree was not confirmed
        dead; it does not say the killer could not be located, and the two want
        different things from an operator. Both refusals name themselves, so the
        reason is kept where the run that hit it can be asked for it.
        """
        for name, broken, reason in KILLER_UNAVAILABLE:
            with self.subTest(name):
                runtime, handle, _pid = await self.started_unresponsive_run()
                observer = runtime._runs[handle.run_id]

                with broken():
                    await runtime.cancel(handle, graceful_wait=0.2)

                self.assertIn(reason, observer.cleanup_failure or "")
                await runtime.close(graceful_wait=0.2)

    async def test_a_cancellation_that_could_tree_kill_reports_no_failure(self):
        """The control. A run whose escalation worked has nothing to explain,
        and a field that is always populated says nothing when it matters.
        """
        runtime, handle, _pid = await self.started_unresponsive_run()
        observer = runtime._runs[handle.run_id]

        outcome = await runtime.cancel(handle, graceful_wait=0.2)

        self.assertEqual(CancelOutcome.FORCED, outcome)
        self.assertIsNone(observer.cleanup_failure)
        await runtime.close(graceful_wait=0.2)

    async def test_close_still_leaves_nothing_when_the_escalation_failed(self):
        """Spec section 6 does not get an exemption for a bad environment.

        `shutdown` cancels first and cancels the drain tasks afterwards, so an
        escalation that raised out of `cancel` took the rest of that method with
        it — the counts `close()` promises were never made zero, they were
        never reached.
        """
        runtime, handle, _pid = await self.started_unresponsive_run()

        with without_windows_roots():
            await runtime.cancel(handle, graceful_wait=0.2)

        await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, runtime.active_child_count())
        self.assertEqual(0, runtime.drain_task_count())

    async def test_close_alone_leaves_nothing_when_the_escalation_failed(self):
        """The same promise for the path that never calls `cancel` directly.

        `close` reaches the escalation through `shutdown`, so a failure there
        has to be survived at that entry point too, not only when a caller
        cancelled first.
        """
        runtime, _handle, _pid = await self.started_unresponsive_run()

        with without_windows_roots():
            await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, runtime.active_child_count())
        self.assertEqual(0, runtime.drain_task_count())

    async def test_the_child_does_not_survive_a_failed_escalation(self):
        """Reporting the failure honestly is not enough on its own.

        `taskkill /T` is what reaches descendants, and without it the run keeps
        whatever it started. Killing the process we do have a handle on is still
        strictly better than leaving the whole tree alive, and it is the part we
        can do without the helper.
        """
        runtime, handle, pid = await self.started_unresponsive_run()
        self.assertTrue(still_running(pid), "the fixture child never started")

        with without_windows_roots():
            await runtime.cancel(handle, graceful_wait=0.2)

        self.assertFalse(
            await settled(pid, False),
            "the child outlived a cancellation that could not tree-kill it",
        )
        await runtime.close(graceful_wait=0.2)


class FallbackReapTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """Issue #46: a fallback kill that ran but did not finish the job cannot
    pass for a close that reached zero.

    #34 made the escalation survivable: when the tree killer cannot be
    located, the child we hold a handle on is ended directly. Whether that
    direct kill actually reaped the child was answered and then discarded,
    so a close whose bounded wait expired returned exactly like one that
    had emptied everything.

    Per the ticket this is proven on the assembly path — the adapter's own
    close, over a run started through `start_turn`, with the killer made
    unavailable — not by calling the termination helper directly. What a
    real subprocess cannot guarantee is that an expired bound *stays*
    expired: whether a killed-but-unreaped child remains unreaped is asyncio
    bookkeeping, and a bound set one tick under a natural reap measures the
    race instead of the report. So the process at the end of the documented
    spawner seam is `_Unreapable`, whose exit is never reported — the case
    the bounded wait exists for — while preflight, launch, the run registry
    and `close()` all run for real. The reaped controls below stay on real
    children, because "the fallback reaped within its bound" is exactly the
    property a real child does exhibit.
    """

    async def started_unresponsive_run(self):
        runtime = await adapter(UNRESPONSIVE)
        handle = await runtime.start_turn(turn_request())
        pid = runtime._runs[handle.run_id]._process.pid
        # Registered before the environment is touched, so it runs after the
        # patch has been lifted and can locate the killer it needs.
        self.addCleanup(force_stop, pid)
        return runtime, handle, pid

    async def started_unreapable_run(self):
        runtime = await adapter(UNRESPONSIVE, spawner=_UnreapableSpawner())
        handle = await runtime.start_turn(turn_request())
        return runtime, handle

    async def test_a_fallback_kill_that_expires_its_wait_is_not_reported_as_zero(self):
        """The kill was issued; its bounded wait expired anyway. The close
        that comes back may not look like one that reached zero children."""
        for name, broken, _refusal in KILLER_UNAVAILABLE:
            with self.subTest(name):
                runtime, _handle = await self.started_unreapable_run()

                with broken():
                    report = await asyncio.wait_for(
                        runtime.close(graceful_wait=0.2, forced_wait=0.05),
                        timeout=40,
                    )

                self.assertEqual(1, report.child_processes)

    async def test_the_report_says_the_missing_killer_and_the_unreaped_child_apart(self):
        """Both facts hold at once on this path, and both are then said.

        Why the escalation could not run and what the direct kill managed
        are two different repairs. One reason covering both would send an
        operator after the wrong one.
        """
        for name, broken, refusal in KILLER_UNAVAILABLE:
            with self.subTest(name):
                runtime, _handle = await self.started_unreapable_run()

                with broken():
                    report = await asyncio.wait_for(
                        runtime.close(graceful_wait=0.2, forced_wait=0.05),
                        timeout=40,
                    )

                said = list(report.reasons)
                self.assertEqual(2, len(said), f"expected both facts, got {said}")
                self.assertTrue(
                    any(refusal in reason for reason in said),
                    f"the missing killer went unsaid: {said}",
                )
                self.assertTrue(
                    any("was not reaped" in reason for reason in said),
                    f"the unreaped child went unsaid: {said}",
                )

    async def test_a_fallback_kill_that_reaps_still_reports_zero_children(self):
        """The reaped case is unchanged. When the direct kill does reap the
        child within its bound, close reports zero children and says nothing
        extra — a close that reports failure everywhere would turn this red."""
        for name, broken, _refusal in KILLER_UNAVAILABLE:
            with self.subTest(name):
                runtime, _handle, _pid = await self.started_unresponsive_run()

                with broken():
                    report = await asyncio.wait_for(
                        runtime.close(graceful_wait=0.2), timeout=40
                    )

                self.assertEqual(0, report.child_processes)
                self.assertEqual(0, report.drain_tasks)
                self.assertEqual((), report.reasons)

    async def test_an_ordinary_close_still_reports_nothing_left(self):
        """The other control. A close that emptied everything returns the
        zero it always has, with nothing invented for it."""
        runtime = await adapter(UNRESPONSIVE)
        await runtime.start_turn(turn_request())

        report = await asyncio.wait_for(runtime.close(graceful_wait=0.2), timeout=40)

        self.assertEqual(0, report.child_processes)
        self.assertEqual(0, report.drain_tasks)
        self.assertEqual((), report.reasons)


if __name__ == "__main__":
    unittest.main()
