"""The ``AgentRuntime`` contract the Broker talks to.

Normative source: ``docs/spec/whole-life-v0.md`` section 4. The protocol below
is that section's signature; the value types are the vocabulary it needs. No
operation is optional and none may be answered with a no-op — a runtime that
cannot do something refuses instead of pretending.
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable


class Provider(StrEnum):
    """The two providers of v0. Matches the ``source`` field of section 7."""

    CLAUDE = "claude"
    CODEX = "codex"


class TurnMode(StrEnum):
    """Starting fresh, or resuming a provider-issued native session.

    Putting an earlier snapshot into a new session is ``NEW``, not ``RESUME``.
    """

    NEW = "new"
    RESUME = "resume"


class BudgetProfile(StrEnum):
    """The named budget that bounds a session. Section 8."""

    ECONOMY = "economy"
    BALANCED = "balanced"
    DEEP = "deep"


class EnforcementLevel(StrEnum):
    """How honestly a provider can hold a native-worker limit. Section 4.

    ``UNSUPPORTED`` is reported, never silently downgraded to a claim of
    enforcement.
    """

    HARD = "hard"
    COOPERATIVE = "cooperative"
    UNSUPPORTED = "unsupported"


class RunStatus(StrEnum):
    """Terminal state of one native process execution. Section 7.

    ``UNKNOWN_OUTCOME`` is a run state, not an event type, and never becomes
    success or an automatic retry.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


class CancelOutcome(StrEnum):
    """How a cancellation actually ended. Section 6."""

    GRACEFUL = "graceful"
    FORCED = "forced"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DelegationBudget:
    """What one turn may spend on native workers. Section 4."""

    max_concurrent_workers: int
    max_total_worker_starts: int
    max_depth: int


@dataclass(frozen=True, slots=True)
class ResultLimits:
    """Profile byte ceilings the adapter enforces on a turn result. Section 8.

    Bytes are UTF-8 bytes, never called tokens.
    """

    max_result_bytes: int
    max_capsule_bytes: int


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """One immutable request to execute a participant turn.

    The Broker builds this. The adapter validates it and never rewrites it.
    ``native_session_id`` is present only for ``TurnMode.RESUME``.
    """

    participant_id: str
    round_id: str
    mode: TurnMode
    prompt: str
    budget_profile: BudgetProfile
    delegation_budget: DelegationBudget
    result_limits: ResultLimits
    native_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """What preflight found. Section 4.

    The three enforcement fields are reported as measured, so that an
    unobservable limit fails closed instead of reading as enforced.
    """

    provider: Provider
    cli_version: str
    worker_concurrency_enforcement: EnforcementLevel
    worker_total_start_enforcement: EnforcementLevel
    delegation_depth_enforcement: EnforcementLevel


@dataclass(frozen=True, slots=True)
class RunHandle:
    """One native process execution that actually started."""

    run_id: str
    participant_id: str
    provider: Provider
    native_session_id: str | None = None


@dataclass(frozen=True)
class RuntimeEvent:
    """A provider event normalized by the adapter.

    ``kind`` and ``data`` carry only allowlisted, schema-checked content; raw
    provider payloads are not passed through.
    """

    run_id: str
    kind: str
    occurred_at: datetime
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """How one run ended. ``diagnostic`` is an allowlisted code, never raw stderr."""

    status: RunStatus
    exit_code: int | None = None
    diagnostic: str | None = None


@runtime_checkable
class AgentRuntime(Protocol):
    """The only provider seam. Section 4."""

    async def preflight(self) -> RuntimeStatus: ...

    async def start_turn(self, request: TurnRequest) -> RunHandle: ...

    def events(self, run: RunHandle) -> AsyncIterator[RuntimeEvent]: ...

    async def cancel(self, run: RunHandle) -> CancelOutcome: ...

    async def wait(self, run: RunHandle) -> RunOutcome: ...

    async def close(self) -> None: ...
