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

from tests.support.launch_fixtures import launch_plan, turn_request
from tests.support.preflight_fixtures import CODEX_HOME, codex_runner
from whole_life.runtime.codex import CodexRuntime
from whole_life.runtime.contract import (
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


async def adapter(script, *, sessions=None):
    runtime = CodexRuntime(
        executable=Path(sys.executable),
        runner=codex_runner(),
        parent_env=CLEAN_PARENT_ENV,
        codex_home=CODEX_HOME,
        spawner=SubprocessSpawner(),
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
        """Spec 210: an unresolved ending is never quietly upgraded."""
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
    """`종료 결과를 확정할 수 없으면 성공이나 재시도 가능으로 추정하지 않는다.` Spec 210.

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

if __name__ == "__main__":
    unittest.main()
