"""Holding one turn to its native-worker budget. Normative source: sections 4, 8.

`profile의 turn delegation budget보다 하나 많은 worker start를 관찰하면 즉시
cancel하고 결과를 unknown_outcome으로 둔다.`

The counting lives here rather than in the observer because the interesting
question — is this start one too many? — is arithmetic on what has been seen,
and arithmetic is worth being able to test without starting a process.

Nothing here cancels anything. It reports a breach and the caller, which owns
the child, is the one that can end it.
"""

from whole_life.runtime.contract import (
    DelegationBudget,
    EnforcementLevel,
    Provider,
)

#: The allowlisted diagnostic for a turn stopped by its delegation budget.
#: Section 7 keeps provider prose out of diagnostics; this is a code.
BUDGET_EXCEEDED = "DelegationBudgetExceeded"

#: Kept apart from the budget code because the two are different failures.
#: One says the turn spent more than it was given; this one says a worker
#: went somewhere v0 does not allow, and on Claude the provider was measured
#: not to stop it.
DEPTH_EXCEEDED = "DelegationDepthExceeded"


#: The three limits section 4 requires to be reported separately. Named once
#: so a row can be checked for completeness rather than for the absence of
#: whichever value happens to be unwanted today.
DELEGATION_AXES = frozenset(
    {
        "worker_concurrency_enforcement",
        "worker_total_start_enforcement",
        "delegation_depth_enforcement",
    }
)

#: What each provider is reported to hold, one entry per limit. Section 4.
#:
#: Reported as measured, and `unsupported` where nothing was measured. The
#: failure this table exists to prevent is a bound that reads as held by
#: something which is not holding it.
#:
#: Claude 2.1.240 announces every worker start and finish with a lifecycle id
#: and a `spawn_depth`, so all three limits are observable — and none of them
#: is refused by the provider, which is what `cooperative` means. Depth was
#: documented as `hard` until a recorded turn ran a worker at depth 2 and
#: refused nothing.
#:
#: Codex 0.149.0 is `unsupported` on all three because its stream gives the
#: counter nothing to count. Measured, not read off the schema: the recorded
#: turn (#35) ran the production argument vector with the subagent workflow
#: enabled and asked for three concurrent subagents — the model answered as
#: though it had launched them, yet the two collaboration calls it made carried
#: no receivers and no agent states, and no other line names a worker. The
#: recording lives at
#: tests/recordings/codex-0.149.0-agents-enabled-turn.jsonl, and what it
#: showed is pinned by tests/runtime/test_codex_delegation_measurement.py.
#: Spec line 122 turns an uncountable start into `unsupported`, and section 12
#: turns `unsupported` into a refusal. The
#: concurrency cap set by inline config is not listed as `hard` either: a cap
#: nobody has watched being enforced is a claim, not a measurement.
REPORTED_ENFORCEMENT = {
    Provider.CLAUDE: {
        "worker_concurrency_enforcement": EnforcementLevel.COOPERATIVE,
        "worker_total_start_enforcement": EnforcementLevel.COOPERATIVE,
        "delegation_depth_enforcement": EnforcementLevel.COOPERATIVE,
    },
    Provider.CODEX: {
        "worker_concurrency_enforcement": EnforcementLevel.UNSUPPORTED,
        "worker_total_start_enforcement": EnforcementLevel.UNSUPPORTED,
        "delegation_depth_enforcement": EnforcementLevel.UNSUPPORTED,
    },
}

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

    @property
    def unresolved(self) -> bool:
        """Whether a worker was announced as started and never as finished."""
        return bool(self._live)

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
