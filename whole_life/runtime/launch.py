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

from whole_life.runtime.contract import Provider, TurnMode, TurnRequest


class RefusalCode(StrEnum):
    """Why a launch was refused before any process started."""

    RESUME_WITHOUT_NATIVE_SESSION = "ResumeWithoutNativeSession"
    NEW_TURN_WITH_NATIVE_SESSION = "NewTurnWithNativeSession"
    TURN_REQUEST_INVALID = "TurnRequestInvalid"


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
    request = plan.turn_request

    if request.mode is TurnMode.RESUME and not (request.native_session_id or "").strip():
        raise PreStartRefusal(RefusalCode.RESUME_WITHOUT_NATIVE_SESSION)

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


async def launch(plan: LaunchPlan, spawner: ProcessSpawner) -> SpawnedProcess:
    """Start the planned process. The production path for both adapters.

    The safety boundary runs here, immediately before the spawner, so that
    checking and invoking cannot drift apart.
    """
    enforce_launch_safety(plan)
    return await spawner.spawn(plan)
