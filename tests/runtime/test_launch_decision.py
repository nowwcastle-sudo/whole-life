"""The recorded launch decision — normative source: spec section 4.

Acceptance criterion 7 asks that acceptance run through the final pre-spawn gate
and record the exact launch decision, with no Broker scheduling or persistence
behaviour. So the decision is a value: computed from the plan that is about to
run, handed to whatever the caller supplies, and stored nowhere by this code.

The point of recording it is that "what we checked" and "what we started" must
be the same thing. A decision derived from anything other than the spawned plan
would be a description of an intention, not of an event.
"""

import unittest

from tests.support.launch_fixtures import RecordingSpawner, turn_request
from tests.support.preflight_fixtures import (
    CLAUDE_EXECUTABLE,
    CODEX_EXECUTABLE,
    CODEX_HOME,
    claude_runner,
    codex_runner,
)
from whole_life.runtime.claude import ClaudeRuntime
from whole_life.runtime.codex import CodexRuntime
from whole_life.runtime.delegation import REPORTED_ENFORCEMENT
from whole_life.runtime.contract import Provider, TurnMode
from whole_life.runtime.launch import RecordingJournal, launch

CLEAN_PARENT_ENV = {"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\Windows\system32"}
NATIVE_SESSION = "1f0c6d9e-8f2a-4c3b-9d61-2b7a5e4f8c10"


async def claude_adapter():
    runtime = ClaudeRuntime(
        executable=CLAUDE_EXECUTABLE,
        runner=claude_runner(),
        parent_env=CLEAN_PARENT_ENV,
    )
    await runtime.preflight()
    return runtime


async def codex_adapter():
    runtime = CodexRuntime(
        executable=CODEX_EXECUTABLE,
        runner=codex_runner(),
        parent_env=CLEAN_PARENT_ENV,
        codex_home=CODEX_HOME,
        # This suite's subject is not delegation capability, so the adapter
        # is given a row it can hold. Codex reports `unsupported` on every
        # axis until its delegation measurement is made, and a turn from an
        # unsupported runtime is refused before spawn — which is the control
        # working, not this test failing.
        delegation_enforcement=REPORTED_ENFORCEMENT[Provider.CLAUDE],
    )
    await runtime.preflight()
    return runtime


class LaunchDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def decide(self, adapter, request):
        journal = RecordingJournal()
        spawner = RecordingSpawner()

        await launch(adapter.assemble_launch_plan(request), spawner, journal=journal)

        (decision,) = journal.decisions
        (spawned,) = spawner.calls
        return decision, spawned

    async def test_the_decision_describes_the_plan_that_actually_spawned(self):
        adapter = await claude_adapter()

        decision, spawned = await self.decide(adapter, turn_request())

        self.assertEqual(spawned.provider, decision.provider)
        self.assertEqual(spawned.executable, decision.executable)
        self.assertEqual(spawned.args, decision.args)
        self.assertEqual(spawned.version_conformance.cli_version, decision.cli_version)

    async def test_a_resume_is_recorded_as_a_resume_with_its_identifier(self):
        adapter = await codex_adapter()
        request = turn_request(
            mode=TurnMode.RESUME, native_session_id=NATIVE_SESSION
        )

        decision, _spawned = await self.decide(adapter, request)

        self.assertEqual(TurnMode.RESUME, decision.mode)
        self.assertEqual(NATIVE_SESSION, decision.native_session_id)
        self.assertEqual(Provider.CODEX, decision.provider)

    async def test_a_new_turn_records_no_native_session(self):
        """A new snapshot is never recorded as though it resumed something."""
        adapter = await codex_adapter()

        decision, _spawned = await self.decide(adapter, turn_request())

        self.assertEqual(TurnMode.NEW, decision.mode)
        self.assertIsNone(decision.native_session_id)

    async def test_a_refused_launch_records_nothing(self):
        """The journal holds starts, not attempts."""
        adapter = await claude_adapter()
        journal = RecordingJournal()

        with self.assertRaises(Exception):
            await launch(
                adapter.assemble_launch_plan(
                    turn_request(mode=TurnMode.RESUME, native_session_id=None)
                ),
                RecordingSpawner(),
                journal=journal,
            )

        self.assertEqual([], journal.decisions)


class BrokerValidationTests(unittest.IsolatedAsyncioTestCase):
    """`지원되지 않아도 broker의 동일 JSON schema 검증은 생략하지 않는다`.

    The provider-side schema options are an optimisation: they ask the CLI to
    constrain its own output. Whether the installed build offers them is a
    property of that build. Broker-side validation is a property of the broker,
    and making it conditional on the former would mean the safety of a result
    depended on which CLI happened to be installed.
    """

    async def test_broker_validation_holds_without_a_provider_side_schema(self):
        for adapter in (await claude_adapter(), await codex_adapter()):
            with self.subTest(provider=adapter.provider):
                journal = RecordingJournal()

                await launch(
                    adapter.assemble_launch_plan(turn_request()),
                    RecordingSpawner(),
                    journal=journal,
                )

                (decision,) = journal.decisions
                self.assertFalse(decision.provider_schema_requested)
                self.assertTrue(decision.broker_validates_results)


class ReadOnlyInheritanceTests(unittest.IsolatedAsyncioTestCase):
    """Workers inherit the parent's boundary and cannot widen it.

    Measured, not assumed: `claude --safe-mode` reports that it disables
    CLAUDE.md, skills, plugins, hooks, MCP servers and custom agents, while
    leaving built-in tools and permissions working — which is precisely why the
    tool allowlist and permission mode carry the rest of the boundary.
    """

    async def test_codex_agents_cannot_widen_the_sandbox(self):
        adapter = await codex_adapter()

        args = adapter.assemble_launch_plan(turn_request()).args

        # A custom agent file is the documented way to ask for a wider sandbox.
        self.assertIn("--ignore-rules", args)
        self.assertIn("--ignore-user-config", args)
        self.assertEqual("read-only", args[args.index("--sandbox") + 1])

    async def test_claude_workers_inherit_the_parent_tool_allowlist(self):
        adapter = await claude_adapter()

        args = adapter.assemble_launch_plan(turn_request()).args

        allowlist = args[args.index("--tools") + 1].split(",")
        self.assertIn("Agent", allowlist)
        for widening in ("Write", "Edit", "Bash", "WebFetch", "NotebookEdit"):
            with self.subTest(tool=widening):
                self.assertNotIn(widening, allowlist)

    async def test_no_adapter_grants_a_writable_directory(self):
        for adapter in (await claude_adapter(), await codex_adapter()):
            with self.subTest(provider=adapter.provider):
                args = adapter.assemble_launch_plan(turn_request()).args

                self.assertNotIn("--add-dir", args)
                self.assertNotIn("--cd", args)
                self.assertNotIn("-C", args)


if __name__ == "__main__":
    unittest.main()
