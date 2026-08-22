"""Observing a live run — normative source: spec section 6.

`stdout과 stderr는 서로 독립된 task가 동시에 읽는다.`
`queue가 가득 차면 stream reader가 bounded backpressure를 적용하며 무한 memory
증가를 허용하지 않는다.`

These start real child processes through the assembled launch path, because the
properties under test are about concurrency and process exit — a stubbed stream
cannot deadlock, and a fake process cannot exit mid-sentence.

The child is this interpreter running a short script, so nothing provider-side
is touched and no turn is spent.
"""

import asyncio
import sys
import unittest
from pathlib import Path

from tests.support.launch_fixtures import launch_plan, turn_request
from whole_life.runtime.contract import RunStatus, RuntimeEvent
from whole_life.runtime.normalize import normalize_codex_line
from whole_life.runtime.observe import RunObserver
from whole_life.runtime.spawn import SubprocessSpawner
from whole_life.runtime.streams import StreamFailure

COMPLETED = '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}'
MESSAGE = '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"hi"}}'


def emitting(script: str):
    """A plan that runs this interpreter with `script`."""
    return launch_plan(
        executable=Path(sys.executable),
        args=("-c", script),
        turn_request=turn_request(prompt=""),
    )


def emit_lines(lines, *, exit_code=0, stderr_bytes=0, repeat_first=0):
    """A child script emitting `lines`.

    `repeat_first` is emitted as a loop rather than unrolled: Windows refuses a
    command line past ~32 KiB, and unrolling five hundred writes exceeds it
    (`WinError 206`). The child builds the volume, not the argument vector.
    """
    body = "import sys\n"
    if stderr_bytes:
        body += f"sys.stderr.write('e' * {stderr_bytes})\n"
    if repeat_first:
        body += (
            f"for _ in range({repeat_first}):\n"
            f"    sys.stdout.write({lines[0]!r} + chr(10))\n"
        )
        lines = lines[1:]
    for line in lines:
        body += f"sys.stdout.write({line!r} + chr(10))\n"
    body += "sys.stdout.flush()\n"
    body += f"sys.exit({exit_code})\n"
    return body


async def observe(plan, *, queue_maxsize=64):
    process = await SubprocessSpawner().spawn(plan)
    observer = RunObserver(
        process,
        normalize=normalize_codex_line,
        run_id="run-7",
        queue_maxsize=queue_maxsize,
    )
    events = [event async for event in observer.events()]
    return events, await observer.outcome()


class _FakeStream:
    """An `asyncio.StreamReader` as far as `read_bounded_lines` is concerned."""

    def __init__(self, chunks=()):
        self._chunks = list(chunks)

    async def read(self, _size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _RecordingTransport:
    def __init__(self, process):
        self._process = process

    def close(self):
        self._process.journal.append("close")
        # asyncio's real transport does not merely close pipes: it kills a
        # process that has not exited yet. Modelled because that is precisely
        # the hazard the ordering has to avoid.
        if self._process.returncode is None:
            self._process.killed_by_close = True
            self._process.pending_exit = 1


class _RecordingProcess:
    """Records the order of `kill`, transport `close` and `wait`.

    The real-process tests around this one exercise asyncio for real, but they
    cannot *pin* the ordering: whether the hang appears depends on a race
    between the kill being noticed and `wait()` being entered, so a mutant that
    removes the fix passes whenever it wins that race. Measured, not assumed —
    dropping the early close and running the class alone goes green in 1.7s,
    while the same code under a loaded full suite hangs.

    So the ordering is fixed here, where there is no race to win.
    """

    def __init__(self, chunks=(), *, exit_code=0):
        self.journal = []
        self.stdout = _FakeStream(chunks)
        self.stderr = _FakeStream()
        self.returncode = None
        self.pending_exit = exit_code
        self.killed_by_close = False
        self._transport = _RecordingTransport(self)

    def kill(self):
        self.journal.append("kill")

    async def wait(self):
        self.journal.append("wait")
        self.returncode = self.pending_exit
        return self.returncode

    def first(self, entry):
        return self.journal.index(entry) if entry in self.journal else None


class CleanupContractTests(unittest.IsolatedAsyncioTestCase):
    """What the observer must call, and in what order, on each path."""

    def line(self, text=COMPLETED):
        return (text + chr(10)).encode("utf-8")

    async def test_an_abort_closes_the_transport_before_waiting(self):
        """The early close is what releases a `wait()` that would never end.

        asyncio only resolves the exit waiter once every pipe reports
        disconnected, and an abort leaves the stdout pipe connected forever.
        Closing after the wait is therefore too late — by then it is stuck.
        """
        process = _RecordingProcess([self.line(MESSAGE)] * 10)
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-9", queue_maxsize=1
        )

        # `aclose()` explicitly: breaking out of an `async for` only suspends
        # the generator, and the cleanup under test lives in its `finally`.
        stream = observer.events()
        async for _event in stream:
            break  # abort: the stream never ends on its own
        await stream.aclose()

        self.assertIn("close", process.journal)
        self.assertLess(process.first("close"), process.first("wait"))

    async def test_a_normal_end_never_closes_before_waiting(self):
        """Closing early on a healthy run would shoot the child.

        The transport kills a process that has not exited, so an unconditional
        early close turns a child that is still finishing into a killed one —
        exit 0 reported as a failure that never happened.
        """
        process = _RecordingProcess([self.line()])
        observer = RunObserver(process, normalize=normalize_codex_line, run_id="run-9")

        kinds = [event.kind async for event in observer.events()]
        outcome = await observer.outcome()

        self.assertEqual(["turn.completed"], kinds)
        self.assertNotIn("kill", process.journal)
        self.assertFalse(process.killed_by_close)
        self.assertLess(process.first("wait"), process.first("close"))
        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_every_path_closes_the_transport_last(self):
        """Reaping does not close the pipes; leaving them open leaks handles."""
        for label, abort in (("normal", False), ("abort", True)):
            with self.subTest(path=label):
                process = _RecordingProcess([self.line(MESSAGE)] * 10 if abort else [self.line()])
                observer = RunObserver(
                    process,
                    normalize=normalize_codex_line,
                    run_id="run-9",
                    queue_maxsize=1,
                )

                stream = observer.events()
                async for _event in stream:
                    if abort:
                        break
                await stream.aclose()

                self.assertEqual("close", process.journal[-1])
                self.assertIn("wait", process.journal)


class NormalRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_completed_run_yields_events_and_succeeds(self):
        events, outcome = await observe(emitting(emit_lines([MESSAGE, COMPLETED])))

        self.assertEqual(
            ["message.committed", "turn.completed"], [e.kind for e in events]
        )
        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_stderr_is_drained_concurrently_and_never_deadlocks(self):
        """A child that fills the stderr pipe must not block on it.

        With only stdout being read, a process writing more than the pipe buffer
        to stderr blocks forever and the run hangs. This is the test that would
        never finish if the two streams were read in sequence.
        """
        plan = emitting(emit_lines([COMPLETED], stderr_bytes=512 * 1024))

        events, outcome = await asyncio.wait_for(observe(plan), timeout=30)

        self.assertEqual(RunStatus.COMPLETED, outcome.status)
        self.assertEqual(["turn.completed"], [e.kind for e in events])

    async def test_stderr_is_bounded_however_much_arrives(self):
        plan = emitting(emit_lines([COMPLETED], stderr_bytes=512 * 1024))
        process = await SubprocessSpawner().spawn(plan)
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-7"
        )

        [event async for event in observer.events()]
        await observer.outcome()

        self.assertLessEqual(len(observer.stderr_snapshot()), 64 * 1024)


class BackpressureTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_queue_never_grows_past_its_bound(self):
        """A slow consumer must slow the reader, not fill memory."""
        emitted = 500
        plan = emitting(
            emit_lines([MESSAGE, COMPLETED], repeat_first=emitted)
        )
        process = await SubprocessSpawner().spawn(plan)
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-7", queue_maxsize=8
        )

        seen = 0
        async for _event in observer.events():
            seen += 1
            await asyncio.sleep(0)
            self.assertLessEqual(observer.queue_depth(), 8)

        self.assertEqual(emitted + 1, seen)


class MidStreamExitTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_run_that_exits_without_a_terminal_event_is_unknown(self):
        """`exit zero without a terminal event` — not success, not retryable."""
        events, outcome = await observe(emitting(emit_lines([MESSAGE])))

        self.assertEqual(["message.committed"], [e.kind for e in events])
        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("TerminalEventMissing", outcome.diagnostic)

    async def test_terminal_success_then_a_nonzero_exit_is_failure(self):
        plan = emitting(emit_lines([COMPLETED], exit_code=3))

        _events, outcome = await observe(plan)

        self.assertEqual(RunStatus.FAILED, outcome.status)
        self.assertEqual(3, outcome.exit_code)
        self.assertEqual("TerminalExitDisagreement", outcome.diagnostic)

    async def test_no_process_survives_the_observation(self):
        """`hidden process work` — the child is reaped, not left running."""
        plan = emitting(emit_lines([COMPLETED]))
        process = await SubprocessSpawner().spawn(plan)
        observer = RunObserver(process, normalize=normalize_codex_line, run_id="run-7")

        [event async for event in observer.events()]
        await observer.outcome()

        self.assertIsNotNone(process.returncode)


class CorruptStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_unknown_event_stops_the_run(self):
        plan = emitting(emit_lines(['{"type":"mystery"}', COMPLETED]))

        with self.assertRaises(StreamFailure) as caught:
            await observe(plan)

        self.assertEqual("UnknownProviderEvent", caught.exception.diagnostic)

    async def test_malformed_json_stops_the_run(self):
        plan = emitting(emit_lines(['{"type":', COMPLETED]))

        with self.assertRaises(StreamFailure):
            await observe(plan)


class StreamFailureCleanupTests(unittest.IsolatedAsyncioTestCase):
    """A stream failure must end the child, not merely stop reading it.

    The dangerous case is a provider that emits one bad line and then keeps
    running. Reporting the failure while the process lives leaves work happening
    that nothing is watching — the `hidden process work` acceptance criterion 6
    forbids — and, because the stderr reader is still waiting for EOF, the
    failure report itself can block behind a child that will not exit.

    Each test owns its process in a `finally`, so a regression here cannot leave
    a stray child behind or turn into a ResourceWarning in someone else's run.
    """

    async def run_until_failure(self, lines, *, sleep_seconds=30):
        newline = chr(10)
        script = "import sys, time" + newline
        for line in lines:
            script += f"sys.stdout.write({line!r} + chr(10))" + newline
        script += "sys.stdout.flush()" + newline
        script += f"time.sleep({sleep_seconds})" + newline

        process = await SubprocessSpawner().spawn(emitting(script))
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-7"
        )
        try:
            with self.assertRaises(StreamFailure):
                await asyncio.wait_for(
                    self.drain(observer), timeout=20
                )
            return process, observer
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def drain(self, observer):
        return [event async for event in observer.events()]

    async def test_a_schema_failure_reaps_the_child(self):
        process, _observer = await self.run_until_failure(
            ['{"type":"item.completed","item":null}']
        )

        self.assertIsNotNone(
            process.returncode, "the child was still running after the failure"
        )

    async def test_an_unknown_event_reaps_the_child(self):
        process, _observer = await self.run_until_failure(['{"type":"mystery"}'])

        self.assertIsNotNone(process.returncode)

    async def test_malformed_json_reaps_the_child(self):
        process, _observer = await self.run_until_failure(['{"type":'])

        self.assertIsNotNone(process.returncode)

    async def test_the_failure_is_reported_rather_than_hanging(self):
        """The child sleeps far longer than the timeout; the report must not wait."""
        process, _observer = await self.run_until_failure(
            ['{"type":"item.completed","item":null}'], sleep_seconds=120
        )

        self.assertIsNotNone(process.returncode)


class EarlyConsumerExitTests(unittest.IsolatedAsyncioTestCase):
    """A consumer that stops early must not strand the reader or the child.

    With a bounded queue the stdout reader is very likely parked in `put` when
    the consumer walks away. Closing the generator then runs its `finally`,
    which would gather a task that can never proceed — and the child, still
    running, keeps the stderr reader waiting for an EOF that is not coming.
    Cancelling the readers alone is not enough either: that leaves the child.
    """

    async def start_long_child(self, *, queue_maxsize=2):
        newline = chr(10)
        script = (
            "import sys, time" + newline
            + "for _ in range(500):" + newline
            + f"    sys.stdout.write({MESSAGE!r} + chr(10))" + newline
            + "sys.stdout.flush()" + newline
            + "time.sleep(120)" + newline
        )
        process = await SubprocessSpawner().spawn(emitting(script))
        observer = RunObserver(
            process,
            normalize=normalize_codex_line,
            run_id="run-7",
            queue_maxsize=queue_maxsize,
        )
        return process, observer

    async def test_breaking_out_closes_promptly_and_leaves_no_child(self):
        process, observer = await self.start_long_child()
        try:
            stream = observer.events()
            async for _event in stream:
                break

            await asyncio.wait_for(stream.aclose(), timeout=20)

            self.assertIsNotNone(
                process.returncode, "the child outlived the consumer"
            )
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def test_an_exception_in_the_consumer_also_cleans_up(self):
        process, observer = await self.start_long_child()
        try:
            stream = observer.events()

            with self.assertRaises(RuntimeError):
                async for _event in stream:
                    raise RuntimeError("consumer gave up")

            await asyncio.wait_for(stream.aclose(), timeout=20)

            self.assertIsNotNone(process.returncode)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


class DeterministicEarlyCloseTests(unittest.IsolatedAsyncioTestCase):
    """Closing while the reader is parked on a full queue must still return.

    The earlier early-exit test broke after the first event, which does not
    reliably leave the queue full — so it passed while the deadlock was still
    there. This one makes the state deterministic: a queue of one, and a wait
    until it is actually full before closing.

    The deadlock it pins: the reader is blocked in `queue.put`, the consumer
    cancels it, and the reader's own `finally` then tries another blocking
    `put` of the end sentinel — onto a queue nobody will ever drain, because
    the only consumer is the one closing.
    """

    async def start_producer(self):
        newline = chr(10)
        script = (
            "import sys, time" + newline
            + "for _ in range(2000):" + newline
            + f"    sys.stdout.write({MESSAGE!r} + chr(10))" + newline
            + "sys.stdout.flush()" + newline
            + "time.sleep(120)" + newline
        )
        process = await SubprocessSpawner().spawn(emitting(script))
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-7", queue_maxsize=1
        )
        return process, observer

    async def test_closing_on_a_full_queue_returns_promptly(self):
        process, observer = await self.start_producer()
        try:
            stream = observer.events()
            async for _event in stream:
                break

            # Wait until the producer has genuinely refilled the queue, so the
            # reader is parked in `put` when the close happens.
            for _ in range(200):
                if observer.queue_depth() >= 1:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(1, observer.queue_depth())

            await asyncio.wait_for(stream.aclose(), timeout=10)

            self.assertIsNotNone(process.returncode)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def test_a_child_that_lingers_after_its_streams_close_is_not_killed(self):
        """The normal path must reap the child, never shoot it.

        Guards the fix for the close above rather than a new requirement.
        `BaseSubprocessTransport.close()` kills a process that has not exited,
        so closing before `wait()` on *every* path would end a child that shut
        its streams and is still finishing — exit 0 becomes a killed exit 1,
        and `resolve_outcome` reports a failure that never happened.

        The child here closes stdout and stderr, sleeps, then exits 0.
        """
        newline = chr(10)
        script = (
            "import sys, time" + newline
            + f"sys.stdout.write({COMPLETED!r} + chr(10))" + newline
            + "sys.stdout.flush()" + newline
            + "sys.stdout.close()" + newline
            + "sys.stderr.close()" + newline
            + "time.sleep(1.5)" + newline
            + "sys.exit(0)" + newline
        )
        process = await SubprocessSpawner().spawn(emitting(script))
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-8"
        )

        kinds = [event.kind async for event in observer.events()]
        outcome = await observer.outcome()

        self.assertEqual(["turn.completed"], kinds)
        self.assertEqual(0, process.returncode)
        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_a_normal_stream_still_delivers_every_event(self):
        """The sentinel path must survive the fix: a consumer that reads to the
        end still sees the end."""
        plan = emitting(emit_lines([MESSAGE, COMPLETED], repeat_first=50))
        process = await SubprocessSpawner().spawn(plan)
        observer = RunObserver(
            process, normalize=normalize_codex_line, run_id="run-7", queue_maxsize=1
        )

        seen = [event async for event in observer.events()]
        outcome = await observer.outcome()

        self.assertEqual(51, len(seen))
        self.assertEqual(RunStatus.COMPLETED, outcome.status)


class FirstTerminalWinsTests(unittest.IsolatedAsyncioTestCase):
    """Section 7: the first terminal event is final, in both directions.

    A stream that announces completion has ended. Anything after it is not a
    correction — and letting it overwrite would mean the outcome depended on
    which line happened to arrive last.
    """

    async def test_a_later_failure_cannot_overwrite_an_earlier_completion(self):
        plan = emitting(
            emit_lines([COMPLETED, '{"type":"turn.failed","error":{"message":"boom"}}'])
        )

        _events, outcome = await observe(plan)

        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_a_later_completion_cannot_overwrite_an_earlier_failure(self):
        plan = emitting(
            emit_lines(['{"type":"turn.failed","error":{"message":"boom"}}', COMPLETED])
        )

        _events, outcome = await observe(plan)

        self.assertEqual(RunStatus.FAILED, outcome.status)

    async def test_nothing_at_all_is_recorded_after_the_first_terminal(self):
        """Spec line 279: the committed terminal state *is* the end.

        Not merely "no second terminal" — no message and no activity either. A
        turn that has ended cannot keep producing history, and a Journal that
        contradicted the outcome would be the visible symptom.
        """
        tail = [
            MESSAGE,
            '{"type":"item.started","item":{"id":"i","type":"command_execution","command":"ls","aggregated_output":"","status":"completed"}}',
        ]

        for first, last in (
            (COMPLETED, '{"type":"turn.failed","error":{"message":"boom"}}'),
            ('{"type":"turn.failed","error":{"message":"boom"}}', COMPLETED),
        ):
            with self.subTest(first=first):
                plan = emitting(emit_lines([first, *tail, last]))

                events, _outcome = await observe(plan)
                kinds = [event.kind for event in events]

                self.assertEqual(1, len(kinds), f"expected one event, got {kinds}")
                self.assertEqual(
                    1,
                    sum(k in ("turn.completed", "turn.failed") for k in kinds),
                    "a run may hold exactly one terminal event",
                )

    async def test_the_stream_is_still_drained_after_the_terminal(self):
        """Recording stops; reading does not.

        If the reader stopped consuming, a child writing more output would block
        on a full pipe and never exit — the outcome would then be unobservable.
        """
        plan = emitting(emit_lines([COMPLETED, MESSAGE], stderr_bytes=256 * 1024))

        events, outcome = await asyncio.wait_for(observe(plan), timeout=30)

        self.assertEqual(["turn.completed"], [event.kind for event in events])
        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_a_corrupt_line_after_the_terminal_still_fails(self):
        """AC 3 does not switch off once a terminal arrives."""
        plan = emitting(emit_lines([COMPLETED, '{"type":"mystery"}']))

        with self.assertRaises(StreamFailure):
            await observe(plan)


class ContractTypeTests(unittest.IsolatedAsyncioTestCase):
    """The protocol promises `RuntimeEvent`, so that is what arrives."""

    async def test_events_are_the_contract_type(self):
        events, _outcome = await observe(emitting(emit_lines([MESSAGE, COMPLETED])))

        for event in events:
            with self.subTest(kind=event.kind):
                self.assertIsInstance(event, RuntimeEvent)
                self.assertFalse(hasattr(event, "terminal"))


if __name__ == "__main__":
    unittest.main()
