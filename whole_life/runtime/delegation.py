"""Holding one turn to its native-worker budget. Normative source: sections 4, 8.

`profile의 turn delegation budget보다 하나 많은 worker start를 관찰하면
즉시 cancel하고 결과를 unknown_outcome으로 둔다.`

The counting lives here rather than in the observer because the interesting
question — is this start one too many? — is arithmetic on what has been seen,
and arithmetic is worth being able to test without starting a process.

Nothing here cancels anything. It reports a breach and the caller, which owns
the child, is the one that can end it.
"""

from whole_life.runtime.contract import DelegationBudget

#: The allowlisted diagnostic for a turn stopped by its delegation budget.
#: Section 7 keeps provider prose out of diagnostics; this is a code.
BUDGET_EXCEEDED = "DelegationBudgetExceeded"

#: Kept apart from the budget code because the two are different failures.
#: One says the turn spent more than it was given; this one says a worker
#: went somewhere v0 does not allow, and on Claude the provider was measured
#: not to stop it.
DEPTH_EXCEEDED = "DelegationDepthExceeded"


class DelegationLedger:
    """What one turn has spent on native workers so far.

    Owned by the caller for the length of one run, like `ActiveNativeSessions`
    is owned for the length of one broker: nothing here is global, so two runs
    cannot share a count by accident.
    """

    __slots__ = ("_budget", "_started", "_live")

    def __init__(self, budget: DelegationBudget) -> None:
        self._budget = budget
        self._started = 0
        #: Workers announced as started and not yet announced as finished.
        #: A set rather than a counter so a repeated finish cannot buy back
        #: concurrency that was never spent.
        self._live: set[str] = set()

    @property
    def started(self) -> int:
        """How many worker starts this turn has been observed to make."""
        return self._started

    def worker_started(
        self, *, native_child_id: str, depth: int | None
    ) -> str | None:
        """Record one observed start. Returns a diagnostic when it is a breach.

        The start that *reaches* the budget is allowed: a budget is what may be
        spent, not what may be approached. Only the one past it is a breach, and
        the specification says the first such start ends the turn rather than
        being ignored or throttled.
        """
        # Judged before counting: a worker that must not exist is not a use of
        # the budget, and reporting it as one would name the wrong failure.
        if depth is not None and depth > self._budget.max_depth:
            return DEPTH_EXCEEDED

        self._started += 1
        self._live.add(native_child_id)
        if self._started > self._budget.max_total_worker_starts:
            return BUDGET_EXCEEDED
        if len(self._live) > self._budget.max_concurrent_workers:
            return BUDGET_EXCEEDED
        return None

    def worker_finished(self, *, native_child_id: str) -> None:
        """Record one observed finish.

        A finish for a worker whose start was never seen is dropped rather
        than subtracted. The provider decides what it announces, and treating
        an unmatched finish as a decrement would let a turn buy back
        concurrency it never spent.
        """
        self._live.discard(native_child_id)
