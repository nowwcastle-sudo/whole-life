"""The AgentRuntime contract — normative source: spec section 4."""

import dataclasses
import unittest

from whole_life.runtime.contract import (
    AgentRuntime,
    BudgetProfile,
    DelegationBudget,
    ResultLimits,
    TurnMode,
    TurnRequest,
)


class _RuntimeWithoutClose:
    """Every AgentRuntime operation except close()."""

    async def preflight(self): ...

    async def start_turn(self, request): ...

    def events(self, run): ...

    async def cancel(self, run): ...

    async def wait(self, run): ...


class _CompleteRuntime(_RuntimeWithoutClose):
    async def close(self): ...


def _turn_request() -> TurnRequest:
    return TurnRequest(
        participant_id="claude-01",
        round_id="round-1",
        mode=TurnMode.NEW,
        prompt="task",
        budget_profile=BudgetProfile.BALANCED,
        delegation_budget=DelegationBudget(
            max_concurrent_workers=1,
            max_total_worker_starts=1,
            max_depth=1,
        ),
        result_limits=ResultLimits(max_result_bytes=24576, max_capsule_bytes=8192),
    )


class AgentRuntimeContractTests(unittest.TestCase):
    def test_runtime_missing_an_operation_is_not_an_agent_runtime(self):
        self.assertNotIsInstance(_RuntimeWithoutClose(), AgentRuntime)

    def test_runtime_with_every_operation_is_an_agent_runtime(self):
        self.assertIsInstance(_CompleteRuntime(), AgentRuntime)

    def test_turn_mode_is_exactly_new_and_resume(self):
        self.assertEqual({"new", "resume"}, {mode.value for mode in TurnMode})


class TurnRequestTests(unittest.TestCase):
    def test_turn_request_is_immutable(self):
        request = _turn_request()

        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.participant_id = "codex-01"


if __name__ == "__main__":
    unittest.main()
