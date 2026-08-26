"""The final pre-spawn boundary.

An adapter assembles everything it is about to run into one ``LaunchPlan``,
and ``launch()`` is the only way that plan becomes a process. Building safe
arguments and checking the arguments that were actually built are two
different controls; this module is the second one.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

import re

from whole_life.runtime.delegation import (
    DELEGATION_AXES,
    REPORTED_ENFORCEMENT,
)
from whole_life.runtime.contract import (
    EnforcementLevel,
    Provider,
    TurnMode,
    TurnRequest,
)

#: What a provider-issued session identifier may contain. Deliberately narrow:
#: the identifiers both CLIs issue are UUIDs, and nothing wider has a reason to
#: reach an argument vector.
NATIVE_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class RefusalCode(StrEnum):
    """Why a launch was refused before any process started."""

    RESUME_WITHOUT_NATIVE_SESSION = "ResumeWithoutNativeSession"
    NEW_TURN_WITH_NATIVE_SESSION = "NewTurnWithNativeSession"
    TURN_REQUEST_INVALID = "TurnRequestInvalid"
    CHILD_ENV_FORBIDDEN_VARIABLE = "ChildEnvForbiddenVariable"
    UNSUPPORTED_CLI_VERSION = "UnsupportedCliVersion"
    BARE_MODE_ARGV = "BareModeArgv"
    BARE_MODE_DEFAULT = "BareModeDefault"
    CONCURRENT_RESUME_REJECTED = "ConcurrentResumeRejected"
    EXECUTABLE_UNRESOLVED = "ExecutableUnresolved"
    AUTH_STATUS_UNSUPPORTED = "AuthStatusUnsupported"
    SUBSCRIPTION_AUTH_REQUIRED = "SubscriptionAuthRequired"
    DELEGATION_UNSUPPORTED = "DelegationUnsupported"
    WORKING_DIRECTORY_UNDECIDED = "WorkingDirectoryUndecided"


class PreStartRefusal(Exception):
    """A launch refused before spawn. Section 7 records these as phase=pre_start.

    The code is the whole diagnostic. Nothing from the plan is carried — no
    environment values, no authentication output, no prompt text — so there is
    no path by which a refusal message can leak them.
    """

    phase = "pre_start"

    def __init__(self, code: RefusalCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class VersionConformance:
    """What executing one pinned CLI version actually showed.

    ``bare_default`` is measured, never inferred from documentation wording or
    from version ordering.
    """

    cli_version: str
    allowlisted: bool
    bare_default: bool


#: Exact versions with local conformance evidence, and nothing else. Extending
#: this is a deliberate compatibility decision that re-runs the conformance
#: fixtures on the new version — not a version-ordering comparison.
#:
#: It lives beside the dataclass because the gate below compares against it.
#: A conformance record is a *claim*; only a record equal to the canonical one
#: for that provider and version is evidence.
SUPPORTED_VERSIONS: Mapping[Provider, Mapping[str, VersionConformance]] = {
    Provider.CLAUDE: {
        "2.1.240": VersionConformance(
            cli_version="2.1.240", allowlisted=True, bare_default=False
        )
    },
    Provider.CODEX: {
        "0.149.0": VersionConformance(
            cli_version="0.149.0", allowlisted=True, bare_default=False
        )
    },
}


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """Everything one provider process is about to be started with.

    ``args`` are the final split arguments after the executable, so there is no
    question of whether an argv[0] is included.
    """

    provider: Provider
    executable: Path
    args: tuple[str, ...]
    child_env: Mapping[str, str]
    version_conformance: VersionConformance
    turn_request: TurnRequest
    #: Where the child process runs. `None` means nobody decided, which is
    #: refused rather than allowed to become "inherit whatever the Broker was
    #: launched from" — the pinned Codex CLI declines to start outside a
    #: trusted directory, so an inherited value turns an operator's choice of
    #: launch directory into a provider that appears to be down.
    working_directory: Path | None = None
    #: What preflight reported this runtime can hold, per limit. `None` means
    #: no preflight said — which the gate treats the same as `unsupported`,
    #: because "nobody reported" and "reported as not held" are the same
    #: amount of evidence.
    delegation_enforcement: Mapping[str, EnforcementLevel] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "child_env", MappingProxyType(dict(self.child_env)))


class SpawnedProcess(Protocol):
    """The part of a running child process the adapters use.

    ``asyncio.subprocess.Process`` satisfies this; so does a test double, which
    is what lets the boundary be tested without starting anything.
    """

    pid: int
    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None
    stderr: asyncio.StreamReader | None

    async def wait(self) -> int: ...

    def kill(self) -> None: ...


class ProcessSpawner(Protocol):
    """Injected at the boundary so a refused plan can be proven not to spawn."""

    async def spawn(self, plan: LaunchPlan) -> SpawnedProcess: ...


def enforce_launch_safety(plan: LaunchPlan) -> None:
    """Refuse an unsafe plan. Raises PreStartRefusal; returns None when safe.

    Each control is a separate statement on purpose: removing one must make
    exactly one test fail.
    """
    # Spec section 5, bare mode gate. Three separate controls, and the version
    # one runs first because the spec refuses it `allowlist 통과 여부와 무관하게`
    # — a release whose `-p` defaults to bare cannot use a subscription sign-in
    # no matter how clean argv and the environment are.
    if plan.version_conformance.bare_default:
        raise PreStartRefusal(RefusalCode.BARE_MODE_DEFAULT)

    if "--bare" in plan.args:
        raise PreStartRefusal(RefusalCode.BARE_MODE_ARGV)

    # Same condition and same diagnostic as `build_child_env`. Checked again
    # because a plan can be assembled, or mutated, without passing through it.
    if "CLAUDE_CODE_SIMPLE" in {name.upper() for name in plan.child_env}:
        raise PreStartRefusal(RefusalCode.CHILD_ENV_FORBIDDEN_VARIABLE)

    canonical = SUPPORTED_VERSIONS[plan.provider].get(
        plan.version_conformance.cli_version
    )
    if canonical != plan.version_conformance:
        raise PreStartRefusal(RefusalCode.UNSUPPORTED_CLI_VERSION)

    # Spec line 121. Every v0 profile grants native delegation, so a runtime
    # that cannot show it holds the limits does not quietly become a
    # single-agent turn: it does not start.
    #
    # Resolved from the measurement table and compared whole, the same way
    # the version record is resolved above. The row travelling on the plan is
    # a report; a report that disagrees with the measurement is a claim, and
    # #12 already closed the shape where a caller-supplied flag was accepted
    # as its own evidence. Comparing the whole mapping also refuses a row
    # that simply omits an axis — an omission passes any test written as
    # "no axis says unsupported", because an empty mapping says nothing.
    # Four separate statements, like the controls above: removing one has to
    # make exactly one test fail, and a reader stepping through a refusal has
    # to be able to see which condition stopped it.
    measured = REPORTED_ENFORCEMENT.get(plan.provider)
    if measured is None:
        raise PreStartRefusal(RefusalCode.DELEGATION_UNSUPPORTED)

    if set(measured) != DELEGATION_AXES:
        raise PreStartRefusal(RefusalCode.DELEGATION_UNSUPPORTED)

    if EnforcementLevel.UNSUPPORTED in measured.values():
        raise PreStartRefusal(RefusalCode.DELEGATION_UNSUPPORTED)

    if plan.delegation_enforcement != measured:
        raise PreStartRefusal(RefusalCode.DELEGATION_UNSUPPORTED)

    request = plan.turn_request

    if request.mode is TurnMode.RESUME and not (request.native_session_id or "").strip():
        raise PreStartRefusal(RefusalCode.RESUME_WITHOUT_NATIVE_SESSION)

    # The only caller-supplied value that reaches argv. Everything else there is
    # a fixed literal. On Windows an argument carrying quotes or `%` can survive
    # into a command processor's parse, so the payload is removed by construction
    # rather than by trusting whatever the target turns out to be.
    native_session_id = request.native_session_id
    if native_session_id is not None and not NATIVE_SESSION_ID.fullmatch(
        native_session_id
    ):
        raise PreStartRefusal(RefusalCode.TURN_REQUEST_INVALID)

    if request.mode is TurnMode.NEW and request.native_session_id is not None:
        raise PreStartRefusal(RefusalCode.NEW_TURN_WITH_NATIVE_SESSION)

    if not request.participant_id.strip() or not request.round_id.strip():
        raise PreStartRefusal(RefusalCode.TURN_REQUEST_INVALID)

    budget = request.delegation_budget
    if min(
        budget.max_concurrent_workers,
        budget.max_total_worker_starts,
        budget.max_depth,
    ) < 0:
        raise PreStartRefusal(RefusalCode.TURN_REQUEST_INVALID)

    limits = request.result_limits
    if min(limits.max_result_bytes, limits.max_capsule_bytes) <= 0:
        raise PreStartRefusal(RefusalCode.TURN_REQUEST_INVALID)


@dataclass(frozen=True, slots=True)
class LaunchDecision:
    """Exactly what was started, derived from the plan that started it.

    Recorded so that "what the gate checked" and "what ran" are one statement
    rather than two that might drift. Nothing here is scheduling or storage —
    the value is handed to the caller's journal and this module keeps none of it.
    """

    provider: Provider
    executable: Path
    args: tuple[str, ...]
    #: The directory the child ran in — the plan's resolved value, not its
    #: maybe-unset intent. Typed without `None` on purpose: a plan whose
    #: directory is undecided is refused before spawn, and the journal holds
    #: starts, so no recorded decision can carry one. Without this field two
    #: runs differing only in their input snapshot root journal identically,
    #: even though the child could read different data.
    working_directory: Path
    mode: TurnMode
    native_session_id: str | None
    cli_version: str
    #: Whether the CLI was asked to constrain its own output. False until a
    #: preflight measures that the installed build supports it.
    provider_schema_requested: bool
    #: Always true. Broker-side validation is a property of the broker, not of
    #: whichever CLI happens to be installed, so it is never conditional on the
    #: field above.
    broker_validates_results: bool


def decide_launch(plan: LaunchPlan) -> LaunchDecision:
    """Describe the plan that is about to be spawned."""
    request = plan.turn_request
    return LaunchDecision(
        provider=plan.provider,
        executable=plan.executable,
        args=plan.args,
        working_directory=plan.working_directory,
        mode=request.mode,
        native_session_id=request.native_session_id,
        cli_version=plan.version_conformance.cli_version,
        provider_schema_requested=PROVIDER_SCHEMA_FLAGS.intersection(plan.args) != set(),
        broker_validates_results=True,
    )


#: The provider-side "constrain your own output" options. Absent until a
#: preflight measures support; their absence changes nothing about validation.
PROVIDER_SCHEMA_FLAGS = frozenset({"--output-schema", "--json-schema"})


class RecordingJournal:
    """A journal that keeps decisions in memory. The caller owns it."""

    def __init__(self) -> None:
        self.decisions: list[LaunchDecision] = []

    def record(self, decision: LaunchDecision) -> None:
        self.decisions.append(decision)


class ActiveNativeSessions:
    """Which provider/native-session pairs currently have a run in flight.

    A native session is conversation state the provider owns. Two turns
    resuming it at once interleave writes into that state, and afterwards
    neither transcript is a faithful record of what the model saw — and the
    damage is in the provider's store, not somewhere we can repair. So the
    second start is refused before spawn.

    Ownership is the caller's on purpose: nothing here is global, so tests and
    separate brokers do not share a registry by accident.
    """

    def __init__(self) -> None:
        self._active: set[tuple[Provider, str]] = set()

    @staticmethod
    def _key(plan: LaunchPlan) -> tuple[Provider, str] | None:
        """The pair, or None when this turn resumes nothing.

        Scoped by provider: the same identifier under two providers names two
        unrelated sessions.
        """
        native_session_id = plan.turn_request.native_session_id
        if not (native_session_id or "").strip():
            return None
        return (plan.provider, native_session_id)

    def reserve(self, plan: LaunchPlan) -> None:
        key = self._key(plan)
        if key is None:
            return
        if key in self._active:
            raise PreStartRefusal(RefusalCode.CONCURRENT_RESUME_REJECTED)
        self._active.add(key)

    def release(self, plan: LaunchPlan) -> None:
        key = self._key(plan)
        if key is not None:
            self._active.discard(key)


async def launch(
    plan: LaunchPlan,
    spawner: ProcessSpawner,
    *,
    sessions: ActiveNativeSessions | None = None,
    journal: "RecordingJournal | None" = None,
) -> SpawnedProcess:
    """Start the planned process. The production path for both adapters.

    The safety boundary runs here, immediately before the spawner, so that
    checking and invoking cannot drift apart.

    The reservation is taken *after* the safety gate, so a plan the gate refuses
    never holds a session it was never going to run. If the spawner itself
    fails, the reservation is handed back — nothing is left running to hold it.

    That last clause used to read "a process that did not start is not a run in
    flight", which was the one thing this path could not promise: the spawner
    creates the child before it writes the prompt, so a failure during the
    handover left a process running that no caller had ever received. Releasing
    here is correct because the spawner now guarantees the weaker and true
    statement — it either returns a handle or leaves nothing alive.
    """
    enforce_launch_safety(plan)

    if sessions is not None:
        sessions.reserve(plan)

    try:
        process = await spawner.spawn(plan)
    except BaseException:
        if sessions is not None:
            sessions.release(plan)
        raise

    # After the spawn, so the journal holds starts rather than attempts.
    if journal is not None:
        journal.record(decide_launch(plan))

    return process
