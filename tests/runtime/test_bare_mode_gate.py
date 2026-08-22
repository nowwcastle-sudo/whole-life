"""The bare mode gate — normative source: spec section 5, "bare mode gate".

`--bare` does not merely disable customization; it changes the authentication
path. Under it, Anthropic auth is limited to `ANTHROPIC_API_KEY` or a settings
`apiKeyHelper`, and OAuth and the keychain are never read. The moment bare mode
is on, v0's subscription premise is false.

The gate is three layers, and each is a separate control:

1. argv — the final argument vector carries no `--bare`
2. env — the final child environment carries no `CLAUDE_CODE_SIMPLE`
3. version — the pinned version's `-p` default is not bare

These are the same injections as T-2 in `docs/smoke/gate-2-usage-attribution.md`,
which that document states produce this gate's conformance fixture. Every case
here is driven through the plan the Claude adapter actually assembles, because
"the adapter does not build a bad plan" and "a bad plan is refused" are
different claims and only the second one is a gate.
"""

import dataclasses
import unittest

from tests.support.launch_fixtures import RecordingSpawner, turn_request
from tests.support.preflight_fixtures import CLAUDE_EXECUTABLE, claude_runner
from whole_life.runtime.claude import ClaudeRuntime
from whole_life.runtime.launch import (
    PreStartRefusal,
    RefusalCode,
    VersionConformance,
    launch,
)

#: Deliberately free of every forbidden variable, so that what a case injects is
#: the only thing under test.
CLEAN_PARENT_ENV = {
    "SYSTEMROOT": r"C:\Windows",
    "PATH": r"C:\Windows\system32",
}


async def assembled_plan():
    """A launch plan from the adapter's own assembly, after a passing preflight."""
    runtime = ClaudeRuntime(
        executable=CLAUDE_EXECUTABLE,
        runner=claude_runner(),
        parent_env=CLEAN_PARENT_ENV,
    )
    await runtime.preflight()
    return runtime.assemble_launch_plan(turn_request())


class AcceptedTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_assembled_plan_reaches_the_spawner(self):
        """The accepted path, so that a refusal test proves something."""
        spawner = RecordingSpawner()

        await launch(await assembled_plan(), spawner)

        (spawned,) = spawner.calls
        self.assertNotIn("--bare", spawned.args)
        self.assertNotIn("CLAUDE_CODE_SIMPLE", spawned.child_env)
        self.assertIs(False, spawned.version_conformance.bare_default)


class ReadOnlyArgvTests(unittest.IsolatedAsyncioTestCase):
    """Spec section 4 line 139 lists what a Claude turn must carry.

    `--safe-mode` alone is not the read-only boundary. The spec is explicit that
    it leaves built-in tools and permissions working, which is why the tool
    allowlist, the permission mode and the strict empty MCP configuration are
    named alongside it. A plan missing them is not a launchable plan.
    """

    async def test_the_assembled_argv_carries_every_required_control(self):
        plan = await assembled_plan()

        self.assertIn("--safe-mode", plan.args)
        self.assertIn("--strict-mcp-config", plan.args)
        self.assertIn("--permission-mode", plan.args)
        self.assertIn("dontAsk", plan.args)
        self.assertIn("--tools", plan.args)
        self.assertIn("Agent,Read,Glob,Grep", plan.args)

    async def test_the_mcp_configuration_is_present_and_empty(self):
        plan = await assembled_plan()

        index = plan.args.index("--mcp-config")
        self.assertEqual('{"mcpServers": {}}', plan.args[index + 1])

    async def test_the_argv_is_exactly_the_specified_tuple(self):
        """Spelled out here, never imported from the production constant.

        A test that asserts `plan.args == CLAUDE_TURN_ARGS` passes whatever that
        constant becomes, including a constant with the read-only controls
        deleted. The specification's tuple has to exist somewhere the production
        code cannot edit.
        """
        plan = await assembled_plan()

        self.assertEqual(
            (
                "-p",
                "--safe-mode",
                "--output-format",
                "stream-json",
                "--verbose",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers": {}}',
                "--tools",
                "Agent,Read,Glob,Grep",
                "--permission-mode",
                "dontAsk",
            ),
            plan.args,
        )

    async def test_no_write_capable_tool_is_named(self):
        plan = await assembled_plan()

        joined = " ".join(plan.args)
        for tool in ("Write", "Edit", "Bash"):
            with self.subTest(tool=tool):
                self.assertNotIn(tool, joined)


class BareModeGateTests(unittest.IsolatedAsyncioTestCase):
    async def _refuse(self, plan) -> PreStartRefusal:
        spawner = RecordingSpawner()

        with self.assertRaises(PreStartRefusal) as caught:
            await launch(plan, spawner)

        self.assertEqual([], spawner.calls)
        self.assertEqual("pre_start", caught.exception.phase)
        return caught.exception

    async def test_an_injected_bare_flag_is_refused(self):
        """T-2, row 4: argv `--bare` forced."""
        plan = dataclasses.replace(
            await assembled_plan(),
            args=("-p", "--bare", "--output-format", "stream-json"),
        )

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.BARE_MODE_ARGV, refusal.code)

    async def test_bare_anywhere_in_the_argument_vector_is_refused(self):
        """Position is not part of the control; presence is."""
        plan = dataclasses.replace(await assembled_plan(), args=("-p", "--bare"))

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.BARE_MODE_ARGV, refusal.code)

    async def test_a_version_whose_p_default_is_bare_is_refused(self):
        """Spec section 5, control 3, refused `allowlist 통과 여부와 무관하게`.

        This is why the control runs before the allowlist comparison. A release
        that makes `-p` default to bare would otherwise pass every other section
        5 check and only then demand an API key at execution — fail-open. The
        record here is allowlisted on purpose: "otherwise accepted" is the whole
        point of the acceptance criterion.
        """
        plan = dataclasses.replace(
            await assembled_plan(),
            version_conformance=VersionConformance(
                cli_version="2.1.240", allowlisted=True, bare_default=True
            ),
        )

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.BARE_MODE_DEFAULT, refusal.code)

    async def test_an_injected_claude_code_simple_is_refused(self):
        """T-2, row 3: `CLAUDE_CODE_SIMPLE=1` in the final child environment.

        `build_child_env` already refuses this, which is exactly why the gate
        must check it too — a plan assembled elsewhere, or mutated after the
        builder ran, never passes through that refusal.
        """
        plan = dataclasses.replace(
            await assembled_plan(),
            child_env={"SYSTEMROOT": r"C:\Windows", "CLAUDE_CODE_SIMPLE": "1"},
        )

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.CHILD_ENV_FORBIDDEN_VARIABLE, refusal.code)

    async def test_a_lowercase_claude_code_simple_is_refused(self):
        """Windows environment names are case-insensitive."""
        plan = dataclasses.replace(
            await assembled_plan(),
            child_env={"SYSTEMROOT": r"C:\Windows", "claude_code_simple": "1"},
        )

        refusal = await self._refuse(plan)

        self.assertEqual(RefusalCode.CHILD_ENV_FORBIDDEN_VARIABLE, refusal.code)


if __name__ == "__main__":
    unittest.main()
