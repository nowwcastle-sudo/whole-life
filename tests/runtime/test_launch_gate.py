"""The final pre-spawn boundary — normative source: spec sections 4 and 7.

The section 5 bare mode gate runs at this same boundary but arrives with #13.
"""

import unittest
from pathlib import Path

from tests.support.launch_fixtures import RecordingSpawner, launch_plan, turn_request
from whole_life.runtime.contract import (
    DelegationBudget,
    ResultLimits,
    TurnMode,
)
from whole_life.runtime.launch import PreStartRefusal, RefusalCode, launch


class AcceptedLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_plan_reaches_the_spawner_once_and_intact(self):
        plan = launch_plan()
        spawner = RecordingSpawner()

        process = await launch(plan, spawner)

        self.assertIs(spawner.process, process)
        (spawned,) = spawner.calls
        self.assertEqual(Path("C:/tools/claude/claude.cmd"), spawned.executable)
        self.assertEqual(
            ("-p", "--safe-mode", "--output-format", "stream-json"), spawned.args
        )
        self.assertEqual(r"C:\Windows", spawned.child_env["SYSTEMROOT"])
        self.assertEqual("2.1.239", spawned.version_conformance.cli_version)
        self.assertEqual("claude-01", spawned.turn_request.participant_id)


class PreStartRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def _refuse(self, plan) -> PreStartRefusal:
        """Launch a plan that must be refused, and prove nothing was spawned."""
        spawner = RecordingSpawner()

        with self.assertRaises(PreStartRefusal) as caught:
            await launch(plan, spawner)

        self.assertEqual([], spawner.calls)
        self.assertEqual("pre_start", caught.exception.phase)
        return caught.exception

    async def test_resume_without_a_native_session_is_refused(self):
        plan = launch_plan(
            turn_request=turn_request(mode=TurnMode.RESUME, native_session_id=None)
        )

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.RESUME_WITHOUT_NATIVE_SESSION, refusal.code)

    async def test_new_turn_carrying_a_native_session_is_refused(self):
        plan = launch_plan(
            turn_request=turn_request(mode=TurnMode.NEW, native_session_id="sess-7")
        )

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.NEW_TURN_WITH_NATIVE_SESSION, refusal.code)

    async def test_blank_participant_id_is_refused(self):
        plan = launch_plan(turn_request=turn_request(participant_id="  "))

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.TURN_REQUEST_INVALID, refusal.code)

    async def test_blank_round_id_is_refused(self):
        plan = launch_plan(turn_request=turn_request(round_id=""))

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.TURN_REQUEST_INVALID, refusal.code)

    async def test_negative_delegation_budget_is_refused(self):
        plan = launch_plan(
            turn_request=turn_request(
                delegation_budget=DelegationBudget(
                    max_concurrent_workers=1,
                    max_total_worker_starts=-1,
                    max_depth=1,
                )
            )
        )

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.TURN_REQUEST_INVALID, refusal.code)

    async def test_result_limit_of_zero_bytes_is_refused(self):
        plan = launch_plan(
            turn_request=turn_request(
                result_limits=ResultLimits(max_result_bytes=0, max_capsule_bytes=8192)
            )
        )

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.TURN_REQUEST_INVALID, refusal.code)

    async def test_refusal_reports_a_code_and_nothing_from_the_plan(self):
        env_sentinel = "SENTINEL-ENV-VALUE"
        prompt_sentinel = "SENTINEL-PROMPT-TEXT"
        plan = launch_plan(
            child_env={"SYSTEMROOT": r"C:\Windows", "CODEX_HOME": env_sentinel},
            turn_request=turn_request(participant_id="", prompt=prompt_sentinel),
        )

        refusal = await self._refuse(plan)

        rendered = f"{refusal}{refusal!r}{refusal.args}"
        self.assertNotIn(env_sentinel, rendered)
        self.assertNotIn(prompt_sentinel, rendered)
        self.assertIn(RefusalCode.TURN_REQUEST_INVALID.value, rendered)


class LaunchPlanTests(unittest.TestCase):
    def test_child_env_cannot_be_mutated_after_assembly(self):
        env = {"SYSTEMROOT": r"C:\Windows"}
        plan = launch_plan(child_env=env)

        with self.assertRaises(TypeError):
            plan.child_env["CLAUDE_CODE_SIMPLE"] = "1"

        env["CLAUDE_CODE_SIMPLE"] = "1"
        self.assertNotIn("CLAUDE_CODE_SIMPLE", plan.child_env)


if __name__ == "__main__":
    unittest.main()
