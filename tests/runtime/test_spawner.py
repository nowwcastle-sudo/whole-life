"""The production spawner — normative source: spec section 4, "v0 transport".

`prompt는 command-line argument가 아니라 UTF-8 stdin으로 전달한다. executable은
시작 시 절대경로로 해석한다.`

These tests start real processes, because the claims are about how a process is
started and a mock cannot be wrong about that in the way a shell can. The
process started is the running interpreter, so nothing provider-side is touched
and no turn is spent.
"""

import asyncio
import dataclasses
import os
import sys
import unittest
from pathlib import Path

from tests.support.launch_fixtures import launch_plan, turn_request
from whole_life.runtime.spawn import SubprocessSpawner, resolve_executable
from whole_life.runtime.launch import PreStartRefusal, RefusalCode

#: Echoes its own argv and whatever arrived on stdin, so a test can see exactly
#: what the operating system handed the child.
REPORT_SOURCE = (
    "import sys, json;"
    "sys.stdout.write(json.dumps("
    '{"argv": sys.argv[1:], "stdin": sys.stdin.buffer.read().decode("utf-8")}'
    "))"
)


def report_plan(prompt="prompt text", args=()):
    return launch_plan(
        executable=Path(sys.executable),
        args=("-c", REPORT_SOURCE, *args),
        turn_request=turn_request(prompt=prompt),
    )


class SpawnedProcessTests(unittest.IsolatedAsyncioTestCase):
    async def report(self, plan):
        process = await SubprocessSpawner().spawn(plan)
        stdout, _stderr = await process.communicate()
        import json

        return json.loads(stdout.decode("utf-8"))

    async def test_the_prompt_arrives_on_stdin_and_never_in_argv(self):
        sentinel = "SENTINEL-PROMPT-TEXT"

        seen = await self.report(report_plan(prompt=sentinel))

        self.assertEqual(sentinel, seen["stdin"])
        self.assertNotIn(sentinel, " ".join(seen["argv"]))

    async def test_the_prompt_is_encoded_as_utf_8(self):
        """A Korean prompt is the ordinary case here, not an edge case."""
        prompt = "한국어 프롬프트 — em dash and 全角"

        seen = await self.report(report_plan(prompt=prompt))

        self.assertEqual(prompt, seen["stdin"])

    async def test_arguments_reach_the_child_unsplit_and_uninterpreted(self):
        """No shell: metacharacters are data, not syntax.

        Two independent checks, because the second one is the one that bites.
        The arguments must arrive intact, *and* the redirect they contain must
        not have produced a file. A shell would have ended the command at `&`,
        run `echo`, and written `out.txt` into the working directory.

        This is not hypothetical. Mutating the spawner to
        `create_subprocess_shell` during the mutation proof created exactly that
        file, containing `pwned`. The argv assertion alone caught it — but the
        artifact is the more direct evidence, and cleaning up is what keeps a
        mutation run from leaving the injection behind in the repository.
        """
        artefact = Path("no-shell-probe.txt")
        hostile = (f"a b & echo pwned > {artefact}", "$(id)", "%PATH%", "*")
        artefact.unlink(missing_ok=True)

        try:
            seen = await self.report(report_plan(args=hostile))

            self.assertEqual(list(hostile), seen["argv"])
            self.assertFalse(
                artefact.exists(), "a shell interpreted the redirect"
            )
        finally:
            artefact.unlink(missing_ok=True)

    async def test_stdin_is_closed_so_the_child_does_not_wait_forever(self):
        """The child read to EOF above; reaching this point at all proves it."""
        seen = await self.report(report_plan(prompt=""))

        self.assertEqual("", seen["stdin"])


class ExecutableResolutionTests(unittest.TestCase):
    """`executable은 시작 시 절대경로로 해석한다`, and a shim is not the target.

    On Windows a name on PATH can resolve to a PowerShell shim that re-launches
    the real program. Handing that to the spawner means the argv and the
    environment we verified are consumed by an interpreter we never inspected.
    """

    def test_a_real_executable_resolves_to_an_absolute_path(self):
        """A real file on disk, but not one this test hopes is on PATH.

        Looking `python` up through the environment made the suite depend on the
        shell it ran in — it passed here and errored in the Closer's shell. The
        interpreter running this test is a genuine absolute `.exe`, so it
        exercises every branch without asking anything of PATH.
        """
        resolved = resolve_executable("python", which=lambda _n: sys.executable)

        self.assertTrue(resolved.is_absolute())
        self.assertTrue(resolved.is_file())
        self.assertEqual(".exe", resolved.suffix.lower())

    def test_a_missing_program_is_refused(self):
        with self.assertRaises(PreStartRefusal) as caught:
            resolve_executable("no-such-program-anywhere-on-this-machine")

        self.assertEqual(RefusalCode.EXECUTABLE_UNRESOLVED, caught.exception.code)

    def test_a_powershell_shim_is_refused(self):
        with self.assertRaises(PreStartRefusal) as caught:
            resolve_executable("claude", which=lambda _n: r"C:\shims\claude.ps1")

        self.assertEqual(RefusalCode.EXECUTABLE_UNRESOLVED, caught.exception.code)

    def test_an_extensionless_shim_is_refused(self):
        with self.assertRaises(PreStartRefusal):
            resolve_executable("claude", which=lambda _n: r"C:\shims\claude")

    def test_a_command_launcher_is_refused(self):
        """Measured, not assumed — see CommandLauncherArgvTests below.

        Spec line 132 permits `.cmd` or `.exe`; line 202 requires shell=False
        with split argv. A `.cmd` cannot satisfy the second, so the set that
        satisfies both is `.exe` alone. A fail-closed subset of the approved
        wording, not a contradiction of it — the baseline is left untouched for
        its owner to reconcile.
        """
        with self.assertRaises(PreStartRefusal) as caught:
            resolve_executable("codex", which=lambda _n: r"C:\tools\codex.cmd")

        self.assertEqual(RefusalCode.EXECUTABLE_UNRESOLVED, caught.exception.code)


class CommandLauncherArgvTests(unittest.IsolatedAsyncioTestCase):
    """Why `.cmd` is refused, demonstrated against a real launcher.

    Windows runs a `.cmd` through the command processor even under
    `create_subprocess_exec`. The processor re-parses the substituted argument
    text, so `%NAME%` becomes the variable's value — and is then split on spaces
    into several arguments. A short vector arrives much longer.

    Not cosmetic: a participant's prompt or a path containing `%` would be
    silently rewritten with the contents of the environment, which is precisely
    what the sanitized child environment exists to control.
    """

    async def test_a_launcher_expands_and_splits_arguments(self):
        """Hermetic: absolute interpreter path, and a probe variable of its own.

        An earlier version invoked a bare `python` inside the batch and sent
        stderr to DEVNULL. On a machine without `python` on PATH the launcher
        failed, stdout came back empty, and the JSON parse blew up — a failure
        that looks nothing like the argv corruption the test is about. Worse, a
        launcher that simply never ran would otherwise read as evidence.

        So the exit status and stdout are checked *first*: only once the
        launcher demonstrably ran does its argv mean anything.
        """
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "shim.cmd"
            launcher.write_bytes(
                b"@echo off\r\n"
                b'"' + str(Path(sys.executable)).encode("utf-8") + b'"'
                b' -c "import sys,json;print(json.dumps(sys.argv[1:]))" %*\r\n'
            )
            # The probe is a variable this test defines, not an ambient one.
            # It used to be `%PATH%`, which made the outcome depend on the
            # caller's environment: Windows deletes a variable set to the empty
            # string, `cmd` leaves `%UNDEFINED%` as literal text, and so the
            # expansion this test watches for simply did not happen. Under a
            # clean-clone run with `PATH=""` that reported the launcher as
            # *safe* — a false failure from an inert probe, in a test whose
            # docstring claims to be hermetic.
            passed = ["plain", "%WL_ARGV_PROBE%"]

            process = await asyncio.create_subprocess_exec(
                str(launcher),
                *passed,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "WL_ARGV_PROBE": "expanded"},
            )
            stdout, stderr = await process.communicate()

        self.assertEqual(
            0, process.returncode, f"launcher did not run: {stderr.decode(errors='replace')[:200]}"
        )
        self.assertTrue(stdout.strip(), "launcher produced no output to compare")

        received = json.loads(stdout.decode(errors="replace"))
        self.assertNotEqual(
            passed, received, "a launcher preserving argv would be usable"
        )
        self.assertNotIn("%WL_ARGV_PROBE%", received)


class SpawnerEnforcesResolutionTests(unittest.IsolatedAsyncioTestCase):
    """The guard has to be wired, not merely defined.

    `resolve_executable` existed, was tested, and was called by nothing. A
    validator nobody invokes is documentation. The spawner is the last place
    the target can be checked, so it checks.
    """

    async def test_a_launcher_in_the_plan_is_refused_at_spawn(self):
        """The path is asserted absolute first, so the *suffix* guard is what fires.

        A previous version of this line carried a real tab where `\\t` was
        intended, leaving a relative path. The test still passed — through the
        absolute-path branch — while proving nothing about `.cmd`. A guard test
        that can pass for the wrong reason is not a guard test.
        """
        launcher = Path("C:/tools/codex.cmd")
        self.assertTrue(launcher.is_absolute())

        plan = dataclasses.replace(report_plan(), executable=launcher)

        with self.assertRaises(PreStartRefusal) as caught:
            await SubprocessSpawner().spawn(plan)

        self.assertEqual(RefusalCode.EXECUTABLE_UNRESOLVED, caught.exception.code)

    async def test_a_relative_executable_is_refused_at_spawn(self):
        plan = dataclasses.replace(report_plan(), executable=Path("python.exe"))

        with self.assertRaises(PreStartRefusal):
            await SubprocessSpawner().spawn(plan)


if __name__ == "__main__":
    unittest.main()
