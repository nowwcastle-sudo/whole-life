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
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.runtime.test_lifecycle import still_running
from tests.support.launch_fixtures import launch_plan, turn_request
from whole_life.runtime.lifecycle import system_taskkill
from whole_life.runtime.spawn import (
    SubprocessSpawner,
    hand_over_prompt,
    resolve_executable,
)
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


#: Larger than any pipe buffer, so the write cannot finish while the child is
#: not reading. That is what makes the tests below deterministic rather than
#: lucky: the drain can only unblock when the read end goes away.
UNREADABLE_PROMPT = "X" * (1024 * 1024)

#: A child that stops reading stdin but does not exit — the variant that leaks.
#: `os.close(0)` is what breaks the pipe; the sleep is what keeps it running.
CLOSES_STDIN_THEN_SLEEPS = "import os, time; os.close(0); time.sleep(30)"


def closes_stdin_then_sleeps():
    return launch_plan(
        executable=Path(sys.executable),
        args=("-c", CLOSES_STDIN_THEN_SLEEPS),
        turn_request=turn_request(prompt=UNREADABLE_PROMPT),
    )


async def end(process):
    """Stop a still-running probe child so it cannot outlive its test."""
    if process.returncode is None:
        process.kill()
    await process.communicate()

#: Like the pid recorder, but with a descendant of its own. `taskkill /T` is
#: what reaches the second process — killing the child alone leaves the
#: grandchild running, which is the whole reason cancellation here has to end a
#: tree rather than a process.
RECORDS_A_GRANDCHILD_THEN_SLEEPS = (
    "import os, pathlib, subprocess, sys, time;"
    "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)']);"
    "part = pathlib.Path(r'{marker}.part');"
    "part.write_text(str(grandchild.pid));"
    "os.replace(str(part), r'{marker}');"
    "time.sleep(300)"
)

#: A child that never touches stdin at all, so the write above blocks instead of
#: breaking. It records its own pid the moment it is running, which is what lets
#: a test establish that the child reached the state under test before acting on
#: it. The rename is atomic so a half-written marker is never read as a pid.
RECORDS_ITS_PID_THEN_SLEEPS = (
    "import os, time, pathlib;"
    "part = pathlib.Path(r'{marker}.part');"
    "part.write_text(str(os.getpid()));"
    "os.replace(str(part), r'{marker}');"
    "time.sleep(300)"
)


def force_stop(pid):
    """Last-resort cleanup so a failing test cannot leave a probe behind."""
    subprocess.run(
        [str(system_taskkill()), "/F", "/T", "/PID", str(pid)], capture_output=True
    )


async def settled(pid, expected, *, timeout=5.0):
    """Wait for a pid to reach `expected` liveness, then report what it is.

    The clock is `monotonic`, not a count of the sleeps: each `is_running` call
    starts a process of its own, so a loop that adds up its own delays ran for
    thirty seconds while believing it had waited ten. That is long enough for a
    probe child to reach the end of its own sleep, and a child that left on
    time reads exactly like a child that was terminated — this test passed that
    way before the clock was fixed. The probe now sleeps far longer than any
    wait here, so reaching `False` can only mean something stopped it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if still_running(pid) == expected:
            return expected
        await asyncio.sleep(0.05)
    return still_running(pid)


class PromptHandoverTests(unittest.IsolatedAsyncioTestCase):
    """Handing the prompt over must not turn a provider outcome into a crash.

    `spawn` creates the child first, then writes the prompt and drains. If the
    read end of that pipe is already gone the drain raises, and nothing catches
    it — so the handle never leaves this function and nobody owns the child.

    A provider that stops reading stdin is an ordinary outcome, not a transport
    error: spec section 11 already resolves `provider 중간 종료` and a terminal
    event that never arrives. Its exit code and stderr are the signal.
    """

    async def test_a_child_that_exited_before_the_prompt_landed_is_still_a_run(self):
        """Break this catches: the drain error escaping instead of the handle.

        A provider binary that dies immediately — a rejected flag, a failed
        auth — is exactly this case, so it needs no speculation about provider
        internals to matter.
        """
        plan = launch_plan(
            executable=Path(sys.executable),
            args=("-c", "import os; os._exit(3)"),
            turn_request=turn_request(prompt=UNREADABLE_PROMPT),
        )

        process = await SubprocessSpawner().spawn(plan)

        await process.communicate()
        self.assertEqual(3, process.returncode)

    async def test_a_child_that_stopped_reading_but_is_still_running_is_a_run(self):
        """The other half: the read end closed while the child kept going.

        This is the variant that leaks. `spawn` raising here means the process
        exists and no caller ever received the handle, so nothing can cancel or
        close it — outside what the run lifecycle is able to guarantee, because
        no run was ever created.

        The still-alive assertion is also the evidence that the child reached
        the state under test: the 1 MiB write cannot complete while anything is
        reading, so returning at all proves the child closed its stdin, and a
        `None` returncode proves it did so without exiting.
        """
        process = await SubprocessSpawner().spawn(closes_stdin_then_sleeps())

        try:
            self.assertIsNone(process.returncode)
        finally:
            await end(process)

    async def test_the_child_reaches_end_of_input_with_no_help_from_the_caller(self):
        """Break this catches: dropping the close now that it lives elsewhere.

        The suite already had a test for this, and it could not fail. It ended
        the child with `communicate()`, which closes stdin itself, so the
        spawner's own close was never what produced the EOF — removing that
        line kept the whole suite green. Waiting on the process directly is the
        difference: nothing here closes anything, so a child that exits at all
        can only have been given its end of input by the spawner.

        In production nobody calls `communicate` either. A provider reading its
        prompt to EOF would simply never start work.
        """
        plan = launch_plan(
            executable=Path(sys.executable),
            args=("-c", "import sys; sys.stdin.buffer.read()"),
            turn_request=turn_request(prompt="short enough to fit the pipe"),
        )

        process = await SubprocessSpawner().spawn(plan)

        self.assertEqual(0, await asyncio.wait_for(process.wait(), timeout=20))

    async def test_a_cancelled_handover_does_not_leave_the_child_running(self):
        """Break this catches: the child surviving a spawn that never returned.

        Tolerating a broken pipe leaves exactly one way out of this function
        with a process already started and no handle delivered — cancellation
        while the write is still blocked. A turn cancelled during its own spawn
        does that, and `launch_process` re-raises without terminating anything,
        so the child runs on with nobody holding it.

        `CancelledError` derives from `BaseException`, so the broken-pipe
        handler does not cover this; it needs its own.
        """
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pid"
            plan = launch_plan(
                executable=Path(sys.executable),
                args=("-c", RECORDS_ITS_PID_THEN_SLEEPS.format(marker=marker)),
                turn_request=turn_request(prompt=UNREADABLE_PROMPT),
            )
            spawning = asyncio.ensure_future(SubprocessSpawner().spawn(plan))
            pid = await self.recorded_pid(marker)

            spawning.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await spawning

            try:
                self.assertFalse(
                    await settled(pid, False), "the child outlived its spawn"
                )
            finally:
                force_stop(pid)

    async def test_a_cancelled_handover_does_not_leave_a_descendant_running(self):
        """Break this catches: ending the child but not what it already started.

        Windows does not cascade a kill, so terminating the direct child leaves
        its descendants running — and a provider that starts a helper before it
        reads its prompt has one by the time this window opens. The tree is what
        has to end, which is the same conclusion #16 reached for cancelling a
        run that was already under way.
        """
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pid"
            plan = launch_plan(
                executable=Path(sys.executable),
                args=("-c", RECORDS_A_GRANDCHILD_THEN_SLEEPS.format(marker=marker)),
                turn_request=turn_request(prompt=UNREADABLE_PROMPT),
            )
            spawning = asyncio.ensure_future(SubprocessSpawner().spawn(plan))
            grandchild = await self.recorded_pid(marker)
            self.assertTrue(still_running(grandchild), "the fixture never started")

            spawning.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await spawning

            try:
                self.assertFalse(
                    await settled(grandchild, False),
                    "the grandchild outlived its spawn",
                )
            finally:
                force_stop(grandchild)

    async def recorded_pid(self, marker, *, timeout=10.0):
        """The child's own pid, once it has demonstrably started.

        Asserting on a child that never got going is how a probe reports a
        process as safely gone when it was simply never there.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker.exists():
                return int(marker.read_text())
            await asyncio.sleep(0.05)
        self.fail("the probe child never recorded a pid")


class FailingWriter:
    """A stream writer whose reader is gone, or worse."""

    def __init__(self, error):
        self.error = error
        self.written = b""
        self.closed = False

    def write(self, data):
        self.written += data

    async def drain(self):
        raise self.error

    def close(self):
        self.closed = True


class PromptToleranceTests(unittest.IsolatedAsyncioTestCase):
    """Which write failures mean "the reader is gone", pinned by type.

    Both types occur on Windows for the same scenario and a test cannot choose
    which one arrives: handing a prompt to a child that had already exited
    raised `BrokenPipeError` on this machine and `ConnectionResetError` on
    another. Covering the set here is what makes a narrowed handler fail a
    test rather than one machine's CI.
    """

    async def test_a_reader_that_is_gone_ends_the_handover_quietly(self):
        for error in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error=error.__name__):
                writer = FailingWriter(error("the read end is gone"))

                await hand_over_prompt(writer, b"prompt")

    async def test_any_other_write_failure_still_reaches_the_caller(self):
        """The pair above are `OSError` subclasses, so widening to `OSError`
        would look like a tidy simplification and would swallow a genuine I/O
        fault — leaving a child that never received its prompt looking like a
        provider that chose to say nothing.
        """
        writer = FailingWriter(OSError("the pipe is fine, the disk is not"))

        with self.assertRaises(OSError):
            await hand_over_prompt(writer, b"prompt")


if __name__ == "__main__":
    unittest.main()
