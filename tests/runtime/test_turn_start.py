"""Safe turn start — normative source: spec section 4, "v0 transport" and
"read-only 실행 경계".

A new snapshot and a resumed native session are different operations, not one
operation with a flag. The provider's own resume verb is used, the prompt never
appears in argv, and the read-only controls are part of the argument vector
rather than something the runtime is trusted to default to.

Every argument list here is spelled out literally. Importing the production
constant would make these tests agree with whatever it becomes, including a
constant with the sandbox removed.

Flag placement was measured against the installed CLIs, not inferred: `codex
exec resume` rejects `--sandbox` (`exit 2`), so the sandbox is set at the `exec`
level ahead of the `resume` subcommand, where it parses for both forms.
"""

import dataclasses
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
from whole_life.runtime.launch import (
    ActiveNativeSessions,
    PreStartRefusal,
    RefusalCode,
    launch,
)

CLEAN_PARENT_ENV = {
    "SYSTEMROOT": r"C:\Windows",
    "PATH": r"C:\Windows\system32",
}

NATIVE_SESSION = "1f0c6d9e-8f2a-4c3b-9d61-2b7a5e4f8c10"

CODEX_SAFE_OPTIONS = (
    "--json",
    "--sandbox",
    "read-only",
    "--ignore-user-config",
    "--ignore-rules",
    "-c",
    "agents.enabled=true",
    "-c",
    "agents.max_concurrent_threads_per_session=3",
)


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


def resume_request():
    return turn_request(mode=TurnMode.RESUME, native_session_id=NATIVE_SESSION)


class CodexTurnArgsTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_turn_uses_exec_with_every_safe_option(self):
        adapter = await codex_adapter()

        plan = adapter.assemble_launch_plan(turn_request())

        self.assertEqual(("exec", *CODEX_SAFE_OPTIONS), plan.args)

    async def test_a_resume_uses_the_providers_own_resume_verb(self):
        adapter = await codex_adapter()

        plan = adapter.assemble_launch_plan(resume_request())

        self.assertEqual(
            ("exec", *CODEX_SAFE_OPTIONS, "resume", NATIVE_SESSION), plan.args
        )

    async def test_the_sandbox_precedes_the_resume_verb(self):
        """Measured: `codex exec resume --sandbox` is a parse error."""
        adapter = await codex_adapter()

        args = adapter.assemble_launch_plan(resume_request()).args

        self.assertLess(args.index("--sandbox"), args.index("resume"))

    async def test_no_writable_directory_is_granted(self):
        adapter = await codex_adapter()

        for request in (turn_request(), resume_request()):
            with self.subTest(mode=request.mode):
                args = adapter.assemble_launch_plan(request).args

                self.assertNotIn("--add-dir", args)
                self.assertNotIn("workspace-write", args)
                self.assertNotIn("danger-full-access", args)
                self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)


class ClaudeTurnArgsTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_turn_carries_no_resume_flag(self):
        adapter = await claude_adapter()

        args = adapter.assemble_launch_plan(turn_request()).args

        self.assertNotIn("--resume", args)

    async def test_a_resume_appends_the_native_session_identifier(self):
        adapter = await claude_adapter()

        args = adapter.assemble_launch_plan(resume_request()).args

        self.assertEqual(("--resume", NATIVE_SESSION), args[-2:])

    async def test_the_read_only_controls_survive_a_resume(self):
        """Resume is not a way around the section 4 boundary."""
        adapter = await claude_adapter()

        args = adapter.assemble_launch_plan(resume_request()).args

        self.assertIn("--safe-mode", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("dontAsk", args)
        self.assertIn("Agent,Read,Glob,Grep", args)


class ConcurrentResumeTests(unittest.IsolatedAsyncioTestCase):
    """One native session, one run at a time.

    A native session is provider-side conversation state. Two turns resuming it
    at once interleave writes into that state, and neither turn's transcript is
    then a faithful record of what the model saw. This is refused before spawn
    rather than detected afterwards, because afterwards the damage is in the
    provider's store, not ours.
    """

    async def test_a_second_resume_of_the_same_session_is_refused(self):
        adapter = await codex_adapter()
        plan = adapter.assemble_launch_plan(resume_request())
        sessions = ActiveNativeSessions()
        spawner = RecordingSpawner()

        await launch(plan, spawner, sessions=sessions)

        with self.assertRaises(PreStartRefusal) as caught:
            await launch(plan, spawner, sessions=sessions)

        self.assertEqual(RefusalCode.CONCURRENT_RESUME_REJECTED, caught.exception.code)
        self.assertEqual(1, len(spawner.calls))

    async def test_the_session_is_free_again_once_the_run_is_released(self):
        adapter = await codex_adapter()
        plan = adapter.assemble_launch_plan(resume_request())
        sessions = ActiveNativeSessions()
        spawner = RecordingSpawner()

        await launch(plan, spawner, sessions=sessions)
        sessions.release(plan)
        await launch(plan, spawner, sessions=sessions)

        self.assertEqual(2, len(spawner.calls))

    async def test_the_same_identifier_on_another_provider_is_not_the_same_session(self):
        """Session identifiers are provider-scoped; a collision is not a clash."""
        codex_plan = (await codex_adapter()).assemble_launch_plan(resume_request())
        claude_plan = (await claude_adapter()).assemble_launch_plan(resume_request())
        sessions = ActiveNativeSessions()
        spawner = RecordingSpawner()

        await launch(codex_plan, spawner, sessions=sessions)
        await launch(claude_plan, spawner, sessions=sessions)

        self.assertEqual(2, len(spawner.calls))

    async def test_new_turns_never_collide(self):
        """A new turn has no native session yet, so there is nothing to reserve."""
        adapter = await codex_adapter()
        sessions = ActiveNativeSessions()
        spawner = RecordingSpawner()

        await launch(adapter.assemble_launch_plan(turn_request()), spawner, sessions=sessions)
        await launch(adapter.assemble_launch_plan(turn_request()), spawner, sessions=sessions)

        self.assertEqual(2, len(spawner.calls))

    async def test_a_failed_spawn_hands_the_session_back(self):
        """A process that never started is not a run in flight.

        Without this the first crash strands the session permanently: every
        later resume is refused as busy by a run that does not exist.
        """

        class FailingSpawner:
            async def spawn(self, plan):
                raise OSError("the executable could not be started")

        adapter = await codex_adapter()
        plan = adapter.assemble_launch_plan(resume_request())
        sessions = ActiveNativeSessions()

        with self.assertRaises(OSError):
            await launch(plan, FailingSpawner(), sessions=sessions)

        spawner = RecordingSpawner()
        await launch(plan, spawner, sessions=sessions)

        self.assertEqual(1, len(spawner.calls))

    async def test_a_refused_plan_does_not_hold_the_session(self):
        """A launch refused by the safety gate must not leave the pair reserved."""
        adapter = await codex_adapter()
        plan = dataclasses.replace(
            adapter.assemble_launch_plan(resume_request()),
            args=("exec", "--bare"),
        )
        sessions = ActiveNativeSessions()

        with self.assertRaises(PreStartRefusal):
            await launch(plan, RecordingSpawner(), sessions=sessions)

        healthy = adapter.assemble_launch_plan(resume_request())
        spawner = RecordingSpawner()
        await launch(healthy, spawner, sessions=sessions)

        self.assertEqual(1, len(spawner.calls))


class NativeSessionIdentifierTests(unittest.IsolatedAsyncioTestCase):
    """The one caller-supplied value that reaches argv must be constrained.

    Everything else in the argument vector is a fixed literal. The native
    session identifier comes from outside, and on Windows an argument carrying
    quotes or `%` can survive into a command processor's parse — measured by the
    reviewer as a real file-creating injection through a `.cmd` target. Bounding
    the charset removes the payload regardless of what the target turns out to
    be.
    """

    async def test_a_well_formed_identifier_is_accepted(self):
        adapter = await codex_adapter()

        plan = adapter.assemble_launch_plan(resume_request())

        self.assertIn(NATIVE_SESSION, plan.args)

    async def test_hostile_identifiers_are_refused_before_spawn(self):
        adapter = await codex_adapter()
        spawner = RecordingSpawner()

        for hostile in (
            'a" & echo pwned > owned.txt & "',
            "%PATH%",
            "id with spaces",
            "semi;colon",
            "back`tick",
            "a" * 200,
        ):
            with self.subTest(identifier=hostile):
                request = turn_request(
                    mode=TurnMode.RESUME, native_session_id=hostile
                )

                with self.assertRaises(PreStartRefusal) as caught:
                    await launch(
                        adapter.assemble_launch_plan(request), spawner
                    )

                self.assertEqual(
                    RefusalCode.TURN_REQUEST_INVALID, caught.exception.code
                )

        self.assertEqual([], spawner.calls)


class PromptHandlingTests(unittest.IsolatedAsyncioTestCase):
    """The prompt is stdin bytes, never an argument.

    Command lines are visible to any process that can list them, and are the
    usual place a secret or a participant's text leaks on Windows.
    """

    async def test_no_adapter_places_the_prompt_in_argv(self):
        sentinel = "SENTINEL-PROMPT-TEXT"

        for adapter in (await claude_adapter(), await codex_adapter()):
            for mode in (turn_request(prompt=sentinel), resume_request()):
                with self.subTest(provider=adapter.provider, mode=mode.mode):
                    args = adapter.assemble_launch_plan(mode).args

                    self.assertNotIn(sentinel, " ".join(args))


if __name__ == "__main__":
    unittest.main()
