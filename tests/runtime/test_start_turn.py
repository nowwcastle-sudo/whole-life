"""`start_turn` is the path, not a helper that resembles it.

A test that calls `launch(adapter.assemble_launch_plan(...), spawner)` proves
the pieces fit. It does not prove the adapter's own public method joins them —
and `AgentRuntime.start_turn` is what a Broker will call. Those are two claims,
and only the second one is the acceptance criterion.
"""

import unittest

from tests.support.launch_fixtures import (
    codex_delegation_measured,
    RecordingSpawner,
    turn_request,
)
from tests.support.preflight_fixtures import (
    WORKING_DIRECTORY,
    CLAUDE_EXECUTABLE,
    CODEX_EXECUTABLE,
    CODEX_HOME,
    claude_runner,
    codex_runner,
)
from whole_life.runtime.claude import ClaudeRuntime
from whole_life.runtime.codex import CodexRuntime
from whole_life.runtime.delegation import REPORTED_ENFORCEMENT
from whole_life.runtime.contract import Provider, RunHandle, TurnMode
from whole_life.runtime.launch import (
    ActiveNativeSessions,
    PreStartRefusal,
    RecordingJournal,
    RefusalCode,
)


#: This module's subject is not delegation capability. See the helper.
_CODEX_MEASURED = None


def setUpModule():
    global _CODEX_MEASURED
    _CODEX_MEASURED = codex_delegation_measured()
    _CODEX_MEASURED.start()


def tearDownModule():
    _CODEX_MEASURED.stop()


CLEAN_PARENT_ENV = {"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\Windows\system32"}
NATIVE_SESSION = "1f0c6d9e-8f2a-4c3b-9d61-2b7a5e4f8c10"


def wiring():
    return {
        "spawner": RecordingSpawner(),
        "sessions": ActiveNativeSessions(),
        "journal": RecordingJournal(),
    }


async def claude_adapter(**overrides):
    parts = wiring() | overrides
    runtime = ClaudeRuntime(
        executable=CLAUDE_EXECUTABLE,
        runner=claude_runner(),
        working_directory=WORKING_DIRECTORY,
        parent_env=CLEAN_PARENT_ENV,
        **parts,
    )
    await runtime.preflight()
    return runtime, parts


async def codex_adapter(**overrides):
    parts = wiring() | overrides
    runtime = CodexRuntime(
        executable=CODEX_EXECUTABLE,
        runner=codex_runner(),
        working_directory=WORKING_DIRECTORY,
        parent_env=CLEAN_PARENT_ENV,
        codex_home=CODEX_HOME,
        **parts,
    )
    await runtime.preflight()
    return runtime, parts


class StartTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_starting_a_turn_spawns_the_assembled_plan(self):
        for factory in (claude_adapter, codex_adapter):
            adapter, parts = await factory()
            with self.subTest(provider=adapter.provider):
                handle = await adapter.start_turn(turn_request())

                (spawned,) = parts["spawner"].calls
                self.assertIsInstance(handle, RunHandle)
                self.assertEqual(adapter.provider, spawned.provider)
                self.assertEqual(
                    adapter.assemble_launch_plan(turn_request()).args, spawned.args
                )

    async def test_starting_a_turn_records_the_launch_decision(self):
        adapter, parts = await claude_adapter()

        await adapter.start_turn(turn_request())

        (decision,) = parts["journal"].decisions
        (spawned,) = parts["spawner"].calls
        self.assertEqual(spawned.args, decision.args)
        self.assertEqual(Provider.CLAUDE, decision.provider)

    async def test_the_final_gate_runs_on_the_adapter_path(self):
        """A plan the section 5 gate refuses must not spawn from here either."""
        adapter, parts = await claude_adapter()

        with self.assertRaises(PreStartRefusal) as caught:
            await adapter.start_turn(
                turn_request(mode=TurnMode.RESUME, native_session_id="   ")
            )

        self.assertEqual(RefusalCode.RESUME_WITHOUT_NATIVE_SESSION, caught.exception.code)
        self.assertEqual([], parts["spawner"].calls)
        self.assertEqual([], parts["journal"].decisions)

    async def test_a_second_resume_through_the_adapter_is_refused(self):
        """Both adapters, because each wires its own registry through."""
        request = turn_request(mode=TurnMode.RESUME, native_session_id=NATIVE_SESSION)

        for factory in (claude_adapter, codex_adapter):
            adapter, parts = await factory()
            with self.subTest(provider=adapter.provider):
                await adapter.start_turn(request)

                with self.assertRaises(PreStartRefusal) as caught:
                    await adapter.start_turn(request)

                self.assertEqual(RefusalCode.CONCURRENT_RESUME_REJECTED, caught.exception.code)
                self.assertEqual(1, len(parts["spawner"].calls))

    async def test_the_handle_carries_the_native_session_for_a_resume(self):
        for factory in (claude_adapter, codex_adapter):
            adapter, _parts = await factory()
            with self.subTest(provider=adapter.provider):
                handle = await adapter.start_turn(
                    turn_request(
                        mode=TurnMode.RESUME, native_session_id=NATIVE_SESSION
                    )
                )
                self.assertEqual(NATIVE_SESSION, handle.native_session_id)
                self.assertEqual("claude-01", handle.participant_id)

    async def test_each_run_receives_a_distinct_identifier(self):
        adapter, _parts = await codex_adapter()

        first = await adapter.start_turn(turn_request())
        second = await adapter.start_turn(turn_request())

        self.assertNotEqual(first.run_id, second.run_id)


if __name__ == "__main__":
    unittest.main()
