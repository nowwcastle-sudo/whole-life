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

from whole_life.runtime.contract import (
    CancelOutcome,
    CloseReport,
    RunOutcome,
    RuntimeEvent,
)
from whole_life.runtime.delegation import DelegationLedger
from whole_life.runtime.lifecycle import (
    FORCED_WAIT_SECONDS,
    GRACEFUL_WAIT_SECONDS,
    LifecycleFailure,
    terminate_process_only,
    terminate_process_tree,
)
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
        delegation_budget=None,
    ) -> None:
        self._process = process
        self._normalize = normalize
        self._run_id = run_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._stderr = StderrRing()
        self._terminal = TerminalEvent.NONE
        self._failure: StreamFailure | None = None
        #: Absent when the caller supplied no budget, which is how every run
        #: that predates delegation keeps behaving exactly as before.
        self._ledger = (
            DelegationLedger(delegation_budget)
            if delegation_budget is not None
            else None
        )
        #: The diagnostic for a turn stopped by its own delegation limits.
        self._breach: str | None = None
        self._exit_code: int | None = None
        #: False until the stream ends on its own. An early exit — a consumer
        #: breaking out, or an exception passing through — leaves it False, and
        #: that is what tells the cleanup to end the child rather than wait.
        self._stream_ended = False
        #: Set when a cancellation arrives before any terminal event. The
        #: ordering is recorded once and never revisited: spec 282 makes a
        #: late clean exit irrelevant, so this cannot be un-set.
        self._cancelled_before_terminal = False
        #: Set when a cancellation arrives *after* a terminal result. Then
        #: the exit status is our own kill, not the provider's verdict.
        self._cancelled_after_terminal = False
        #: Why the escalation could not run, when it could not. Kept apart from
        #: the run's diagnostic on purpose: this is a fact about our own
        #: cleanup, and filing it under the turn's outcome would send a reader
        #: after the provider for something the broker's environment caused.
        self._cleanup_failure: str | None = None
        #: Set when the direct kill — the fallback for a missing tree killer —
        #: was issued but its bounded wait expired without reaping the child.
        #: Kept apart from `_cleanup_failure` for the same reason that one is
        #: kept apart from the outcome: a killer that cannot be located and a
        #: kill that ran but did not finish are two different repairs, and both
        #: facts can hold at once. Issue #46.
        self._fallback_reap_failure: str | None = None
        #: The drain tasks, held so they can be counted and stopped from
        #: outside. `close()` has to promise there are none left, and a
        #: task nobody holds a reference to cannot be counted.
        self._readers: tuple[asyncio.Task, ...] = ()

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
                    if self._breached(event):
                        # Recorded before returning, so the ordering is
                        # already settled when the outcome is resolved — the
                        # same rule `cancel` follows.
                        self._cancelled_before_terminal = True
                        return
                    await self._emit(event)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except BaseException as error:
            self._failure = error
        finally:
            if not cancelled:
                await self._queue.put(_END)

    def _breached(self, event) -> bool:
        """Charge one event to the budget. True when it broke it.

        Only starts and finishes the provider actually announced are counted.
        A run without a budget counts nothing, which is what keeps this out of
        the way of everything that does not delegate.
        """
        if self._ledger is None:
            return False
        if event.data.get("activity_kind") != "native_worker":
            return False

        native_child_id = event.data.get("native_child_id")
        if event.kind == "runtime.activity.finished":
            self._ledger.worker_finished(native_child_id=native_child_id)
            return False

        self._breach = self._ledger.worker_started(
            native_child_id=native_child_id, depth=event.worker_depth
        )
        return self._breach is not None

    async def _emit(self, event) -> None:
        """Queue one canonical event, unless the turn has already ended."""
        if self._terminal is not TerminalEvent.NONE:
            # Spec line 282: once a terminal result is committed, that state is
            # the end — so nothing after it is recorded, not a second terminal
            # (line 284 forbids holding both) and not a message or activity
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

    async def cancel(
        self,
        *,
        graceful_wait: float = GRACEFUL_WAIT_SECONDS,
        forced_wait: float = FORCED_WAIT_SECONDS,
    ) -> CancelOutcome:
        """End this run, gracefully if it will, forcibly if it will not.

        The graceful signal for both CLIs is stdin EOF, which the spawner
        already delivers when it finishes writing the prompt. Closing again
        here is idempotent and deliberate: it keeps the contract true for any
        future provider path that holds stdin open, so the escalation does not
        silently become "wait, then kill".

        The ordering fact is recorded before anything is ended. Spec 271, 282,
        487 and 488 decide on which came first — a terminal result already
        committed survives the cancellation, and a cancellation that came first
        makes the turn `unknown_outcome` permanently. Recording it here, before
        the process can produce anything else, is what makes that race already
        settled by the time the outcome is resolved.

        One exception, and it is not about ordering: line 282. A committed
        result carries through the cancellation for everything except a native
        worker that was announced and never announced finished. The provider
        said the conversation ended; it never said the worker stopped, and the
        cancellation ends the only stream that could have said so.

        Deliberately no `cause`. Spec line 96 signs this as
        `cancel(self, run) -> CancelOutcome`, and none of 271, 281, 282, 487 or
        488 branches on why a run was stopped — user cancellation and the
        twenty-minute timeout are one rule, and the diagnostic is
        `CancelledBeforeTerminal` either way. A cause was carried here for one
        commit, stored in a field nothing read. When a consumer exists it comes
        back with the test that reads it.
        """
        if self._terminal is TerminalEvent.NONE:
            self._cancelled_before_terminal = True
        else:
            self._cancelled_after_terminal = True

        self._close_stdin()

        try:
            await asyncio.wait_for(self._process.wait(), timeout=graceful_wait)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        else:
            return CancelOutcome.GRACEFUL

        # Still alive after the window. `UNKNOWN` rather than `FORCED` when the
        # tree cannot be confirmed dead: descendants may still be running, and
        # a tidy report of "forced" would claim the request stopped.
        try:
            ended = await terminate_process_tree(
                self._process, forced_wait=forced_wait
            )
        except LifecycleFailure as failure:
            # The escalation could not run. That is a fact about cleanup, and
            # letting it leave here in place of the cancellation would hand the
            # caller an unrelated diagnostic for a run it asked to stop.
            # `UNKNOWN` is already this method's word for "the tree could not be
            # confirmed dead", which is exactly what happened.
            #
            # Kept rather than dropped: `UNKNOWN` says the tree was not
            # confirmed dead and says nothing about why, and "the killer is not
            # on this machine" is the one cause an operator can act on.
            self._cleanup_failure = str(failure)
            #
            # The process we hold a handle on is still ours to end, even without
            # the helper that would have reached its descendants. Doing less
            # than we can because we cannot do everything would leave the whole
            # tree running instead of only what we never had a handle on.
            #
            # This time the answer is kept rather than discarded (issue #46):
            # an expired bounded wait means the child was killed but never
            # confirmed reaped, so a close reporting zero children would be
            # lying. The reason is kept apart from `_cleanup_failure` above,
            # and both are said when both hold.
            reaped = await terminate_process_only(
                self._process, forced_wait=forced_wait
            )
            if not reaped:
                self._fallback_reap_failure = (
                    "the direct kill was issued but the child "
                    "was not reaped within the forced wait"
                )
            return CancelOutcome.UNKNOWN
        return CancelOutcome.FORCED if ended else CancelOutcome.UNKNOWN

    @property
    def cleanup_failure(self) -> str | None:
        """Why ending this run's process tree could not be attempted, if it
        could not. `None` when the escalation ran, whatever it concluded."""
        return self._cleanup_failure

    @property
    def fallback_reap_failure(self) -> str | None:
        """Why the direct kill did not confirm the child reaped, if it did
        not. `None` when the child was reaped, whatever the escalation
        concluded."""
        return self._fallback_reap_failure

    def has_child(self) -> bool:
        """True while this run's process has not been reaped."""
        return self._process.returncode is None

    def pending_drain_tasks(self) -> int:
        """How many drain tasks are still running for this run."""
        return sum(1 for task in self._readers if not task.done())

    async def shutdown(
        self,
        *,
        graceful_wait: float = GRACEFUL_WAIT_SECONDS,
        forced_wait: float = FORCED_WAIT_SECONDS,
    ) -> CancelOutcome:
        """End the run and everything this observer owns.

        Cancelling the process is not enough on its own. A consumer that
        stopped reading leaves the stdout drain task parked on a full queue,
        and that task will not end just because the child did — so the tasks
        are cancelled explicitly and awaited, and only then does this return.
        Whoever calls it can say the count is zero because it was made zero.
        """
        outcome = await self.cancel(
            graceful_wait=graceful_wait, forced_wait=forced_wait
        )

        for task in self._readers:
            task.cancel()
        if self._readers:
            await asyncio.gather(*self._readers, return_exceptions=True)
            self._readers = ()

        self._close_transport()
        return outcome

    def _close_stdin(self) -> None:
        """Deliver stdin EOF, whether or not the spawner already did."""
        stdin = getattr(self._process, "stdin", None)
        if stdin is not None and not stdin.is_closing():
            stdin.close()

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
        self._readers = (stdout_task, stderr_task)

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
            aborted = (
                self._failure is not None
                or self._breach is not None
                or not self._stream_ended
            )
            if aborted:
                await self._stop_child()
                stdout_task.cancel()
                stderr_task.cancel()

            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            self._readers = ()

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
        return resolve_outcome(
            self._terminal,
            exit_code=self._exit_code,
            cancelled_before_terminal=self._cancelled_before_terminal,
            cancelled_after_terminal=self._cancelled_after_terminal,
            cancel_diagnostic=self._breach,
            unresolved_worker=(
                self._ledger is not None and self._ledger.unresolved
            ),
        )


async def close_all_runs(
    observers,
    *,
    graceful_wait: float = GRACEFUL_WAIT_SECONDS,
    forced_wait: float = FORCED_WAIT_SECONDS,
) -> CloseReport:
    """Shut down every run this runtime started. Spec 212.

    Concurrently, because each shutdown may spend the graceful window waiting
    for a child that will not go: run sequentially, a broker with four active
    runs would wait four times over for the same five seconds.

    Shared by both adapters rather than written twice. The rest of their
    surfaces are deliberately parallel implementations, but this one carries
    the promise that nothing is left behind, and two copies of that is two
    places for it to drift.

    What closing found is returned, not claimed. Zero counts mean the promise
    held and nothing more is said; when something survived, the runs still
    holding it name why — a close that did not reach zero children saying so
    instead of returning like one that did is issue #46.
    """
    if not observers:
        return CloseReport(child_processes=0, drain_tasks=0)

    await asyncio.gather(
        *(
            observer.shutdown(
                graceful_wait=graceful_wait, forced_wait=forced_wait
            )
            for observer in observers
        ),
        return_exceptions=True,
    )

    child_processes = sum(1 for observer in observers if observer.has_child())
    drain_tasks = sum(observer.pending_drain_tasks() for observer in observers)
    # Only runs still holding something get to speak. A close that emptied
    # everything returns zeros and no words, exactly as it always has.
    reasons: list[str] = []
    for observer in observers:
        if not (observer.has_child() or observer.pending_drain_tasks()):
            continue
        reasons.extend(
            fact
            for fact in (
                observer.cleanup_failure,
                observer.fallback_reap_failure,
            )
            if fact is not None
        )
    return CloseReport(
        child_processes=child_processes,
        drain_tasks=drain_tasks,
        reasons=tuple(reasons),
    )
