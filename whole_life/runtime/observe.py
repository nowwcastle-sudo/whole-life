"""Watching one run to its end. Normative source: spec section 6.

`stdout과 stderr는 서로 독립된 task가 동시에 읽는다.`

That sentence is a deadlock rule, not a style preference. Pipes have finite
buffers: a child that writes more to stderr than the buffer holds blocks until
someone reads it. Draining stdout first and stderr afterwards means the child
waits for a reader that is itself waiting for the child — and the run hangs with
no error to show for it.

`queue가 가득 차면 stream reader가 bounded backpressure를 적용한다.` The queue is
bounded and the reader awaits space, so a provider streaming faster than the
consumer slows the reader instead of growing this process.
"""

import asyncio
from collections.abc import AsyncIterator

from whole_life.runtime.contract import RunOutcome, RuntimeEvent
from whole_life.runtime.outcome import TerminalEvent, resolve_outcome
from whole_life.runtime.streams import StderrRing, StreamFailure, read_bounded_lines

#: Enough to keep a fast provider from stalling on every line, small enough that
#: the bound is real. Not configurable: spec section 6 treats these as
#: conformance fixtures rather than tuning knobs.
DEFAULT_QUEUE_SIZE = 64

_END = object()


class RunObserver:
    """Drains one process, yields canonical events, and resolves its outcome.

    The observer owns both reader tasks. `events()` is the only way to consume,
    and `outcome()` is only meaningful after it has finished — by then the exit
    status has been observed, which is half the evidence the outcome needs.
    """

    def __init__(
        self,
        process,
        *,
        normalize,
        run_id: str,
        queue_maxsize: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        self._process = process
        self._normalize = normalize
        self._run_id = run_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._stderr = StderrRing()
        self._terminal = TerminalEvent.NONE
        self._failure: StreamFailure | None = None
        self._exit_code: int | None = None
        #: False until the stream ends on its own. An early exit — a consumer
        #: breaking out, or an exception passing through — leaves it False, and
        #: that is what tells the cleanup to end the child rather than wait.
        self._stream_ended = False

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def stderr_snapshot(self) -> bytes:
        """The bounded tail, for turning into an allowlisted diagnostic code."""
        return self._stderr.snapshot()

    async def _read_stdout(self) -> None:
        # Cancellation is a cleanup signal, not a stream failure, and the two
        # need different endings. On failure the consumer is still reading and
        # must be told the stream is over. On cancellation the consumer *is*
        # the one shutting down, so nobody will ever drain the queue again —
        # and a blocking `put` of the sentinel there waits forever. That is the
        # deadlock: the reader parked in `put`, cancelled, then parking again
        # in its own `finally`.
        cancelled = False
        try:
            async for line in read_bounded_lines(self._process.stdout):
                if not line.strip():
                    continue
                # A line can carry more than one canonical event — an assistant
                # message with both prose and a tool call — or none at all, like
                # `thread.started`. An empty list is "recognised, nothing
                # canonical", not a gap.
                for event in self._normalize(line, run_id=self._run_id):
                    await self._emit(event)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except BaseException as error:
            self._failure = error
        finally:
            if not cancelled:
                await self._queue.put(_END)

    async def _emit(self, event) -> None:
        """Queue one canonical event, unless the turn has already ended."""
        if self._terminal is not TerminalEvent.NONE:
            # Spec line 279: once a terminal result is committed, that state is
            # the end — so nothing after it is recorded, not a second terminal
            # (line 281 forbids holding both) and not a message or activity
            # either. A turn that has ended cannot go on producing history.
            #
            # Reading continues regardless: the pipes still have to be drained
            # so the child is not left blocked on a full one, and the size,
            # UTF-8 and schema checks still run, so a corrupt tail is a failure
            # rather than silence.
            return

        if event.terminal is not TerminalEvent.NONE:
            self._terminal = event.terminal

        # Awaited, not `put_nowait`: this is the backpressure. Queued as the
        # contract type — `terminal` is observer bookkeeping, consumed just
        # above, and not part of what a consumer receives.
        await self._queue.put(
            RuntimeEvent(
                run_id=event.run_id,
                kind=event.kind,
                occurred_at=event.occurred_at,
                data=event.data,
            )
        )

    async def _stop_child(self) -> None:
        """End the child so the readers can finish.

        Called when observation stops early. A provider that emitted one bad
        line may still be running: the stderr reader would wait for an EOF that
        never comes, and the failure could not even be reported. Terminating is
        what makes the report possible, and what keeps the criterion 6 promise
        that no hidden process work outlives the turn.
        """
        if self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass

    def _close_transport(self) -> None:
        """Close the pipes asyncio opened for this child. Idempotent."""
        transport = getattr(self._process, "_transport", None)
        if transport is not None:
            transport.close()

    async def _read_stderr(self) -> None:
        while True:
            chunk = await self._process.stderr.read(8192)
            if not chunk:
                break
            self._stderr.write(chunk)

    async def events(self) -> AsyncIterator:
        """Yield normalized events until the stream ends.

        Both readers start before anything is consumed, so neither stream can
        block the other. A stream failure is raised only once the queue has
        drained, so events already observed are not lost to it.
        """
        stdout_task = asyncio.create_task(self._read_stdout())
        stderr_task = asyncio.create_task(self._read_stderr())

        try:
            while True:
                item = await self._queue.get()
                if item is _END:
                    self._stream_ended = True
                    break
                yield item
        finally:
            # Anything other than the stream ending on its own means the child
            # must go: a consumer that broke out of the loop leaves the stdout
            # reader blocked on a full queue, and the stderr reader waiting for
            # an EOF a live child will not send. Cancelling the readers without
            # ending the child would still leave the child.
            aborted = self._failure is not None or not self._stream_ended
            if aborted:
                await self._stop_child()
                stdout_task.cancel()
                stderr_task.cancel()

            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

            if aborted:
                # Measured, not assumed: after an abort the stdout pipe still
                # holds unread bytes and its reader is gone, so the pipe never
                # reports `disconnected`. `wait()` is entered before the kill
                # has been noticed, so it parks on an exit waiter — and that
                # waiter is only released by `_try_finish`, which requires
                # *every* pipe disconnected. The child dies, the returncode
                # arrives, and the wait still never ends.
                #
                # Closing here breaks that: it disconnects the pipes, so the
                # waiter can be released. Only on the aborted path, because
                # `close()` also kills a process that has not exited yet — on
                # the normal path that would shoot a child which closed its
                # streams and is still finishing, turning exit 0 into a killed
                # exit 1 and a true completion into a reported failure.
                self._close_transport()

            # Reaped unconditionally: an observed run leaves no process behind,
            # whatever ended the observation.
            self._exit_code = await self._process.wait()

            # Waiting reaps the process; it does not close the pipes asyncio
            # opened for it. Left alone they surface later as
            # `unclosed transport ... pid=N running` from a destructor — a
            # warning the interpreter swallows, so a suite can report OK while
            # holding handles open. Closing here is the observer's job because
            # the observer is what opened the run.
            self._close_transport()

        if self._failure is not None:
            raise self._failure

    async def outcome(self) -> RunOutcome:
        """The resolved outcome. Meaningful only after `events()` completes."""
        return resolve_outcome(self._terminal, exit_code=self._exit_code)
