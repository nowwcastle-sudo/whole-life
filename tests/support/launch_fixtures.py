"""Shared test doubles for the pre-spawn seam.

The recording spawner is the acceptance seam for this slice and the ones that
follow: every pre-start refusal must leave ``calls`` empty.
"""

from pathlib import Path

from whole_life.runtime.delegation import REPORTED_ENFORCEMENT
from whole_life.runtime.contract import (
    BudgetProfile,
    DelegationBudget,
    Provider,
    ResultLimits,
    TurnMode,
    TurnRequest,
)
from whole_life.runtime.launch import LaunchPlan, VersionConformance


class FakeProcess:
    """A SpawnedProcess that never runs anything."""

    def __init__(self) -> None:
        self.pid = 4321
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.killed = False

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class RecordingSpawner:
    """Records every spawn attempt and starts no process."""

    def __init__(self) -> None:
        self.calls: list[LaunchPlan] = []
        self.process = FakeProcess()

    async def spawn(self, plan: LaunchPlan) -> FakeProcess:
        self.calls.append(plan)
        return self.process


def turn_request(**overrides) -> TurnRequest:
    fields = {
        "participant_id": "claude-01",
        "round_id": "round-1",
        "mode": TurnMode.NEW,
        "prompt": "compare the two designs",
        "budget_profile": BudgetProfile.BALANCED,
        "delegation_budget": DelegationBudget(
            max_concurrent_workers=1,
            max_total_worker_starts=1,
            max_depth=1,
        ),
        "result_limits": ResultLimits(max_result_bytes=24576, max_capsule_bytes=8192),
        "native_session_id": None,
    }
    fields.update(overrides)
    return TurnRequest(**fields)


def launch_plan(**overrides) -> LaunchPlan:
    fields = {
        "provider": Provider.CLAUDE,
        "executable": Path("C:/tools/claude/claude.cmd"),
        "args": ("-p", "--safe-mode", "--output-format", "stream-json"),
        "child_env": {"SYSTEMROOT": r"C:\Windows", "LANG": "en-US.UTF-8"},
        "version_conformance": VersionConformance(
            cli_version="2.1.240",
            allowlisted=True,
            bare_default=False,
        ),
        "turn_request": turn_request(),
        # A plan carrying no delegation report is refused by the gate,
        # which is that control working. Fixtures carry the row a real
        # Claude preflight reports, so a test has to opt into a refusal.
        "delegation_enforcement": REPORTED_ENFORCEMENT[Provider.CLAUDE],
    }
    fields.update(overrides)
    return LaunchPlan(**fields)
