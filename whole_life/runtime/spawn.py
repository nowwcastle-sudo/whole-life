"""Starting the planned process. Normative source: spec section 4, transport.

Two rules from the specification shape this module.

The prompt travels as UTF-8 stdin bytes, never as an argument. A command line is
readable by anything that can enumerate processes, and a participant's prompt is
the operator's content.

The executable is resolved to an absolute path before anything is started, and a
shim is not an acceptable target. On Windows a name on PATH can resolve to a
PowerShell wrapper that re-launches the real program; handing that to the
spawner means the argument vector and the child environment this code verified
are consumed by an interpreter nobody inspected.
"""

import asyncio
import shutil
from pathlib import Path

from whole_life.runtime.launch import LaunchPlan, PreStartRefusal, RefusalCode
from whole_life.runtime.lifecycle import (
    LifecycleFailure,
    terminate_process_only,
    terminate_process_tree,
)

#: Only a real image. Spec line 135 permits `.cmd` or `.exe` and line 205
#: requires shell=False with split argv — and a `.cmd` cannot deliver the
#: second. Measured on this machine: passing `%PATH%` to a `.cmd` under
#: `create_subprocess_exec` yields the expanded value, split on spaces into
#: many arguments. The set satisfying both requirements is therefore `.exe`
#: alone, which is a fail-closed subset of the approved wording rather than a
#: contradiction of it.
#:
#: Consequence worth stating: `codex` on PATH resolves to an npm `.cmd` shim,
#: so the broker must be configured with the vendored `codex.exe` that shim
#: dispatches to. Refusing here is what makes that a startup error instead of a
#: silently mangled argument vector.
DIRECTLY_EXECUTABLE_SUFFIXES = frozenset({".exe"})


def assert_directly_executable(candidate: Path) -> None:
    """Refuse anything that is not an absolute real image.

    Called by both the name resolver and the spawner. A guard that only the
    resolver calls is a guard the spawner does not have — and the spawner is
    where a plan assembled elsewhere arrives.
    """
    if not candidate.is_absolute():
        raise PreStartRefusal(RefusalCode.EXECUTABLE_UNRESOLVED)
    if candidate.suffix.lower() not in DIRECTLY_EXECUTABLE_SUFFIXES:
        raise PreStartRefusal(RefusalCode.EXECUTABLE_UNRESOLVED)


def assert_working_directory(candidate: Path | None) -> None:
    """Refuse a working directory that is undecided, not absolute, or missing.

    `None` is the case this exists for. Passing it through would mean the child
    inherits the Broker's own directory, which is wherever an operator happened
    to start it — and the pinned Codex CLI refuses to run outside a trusted
    directory, so that inheritance turns into a provider that looks down.

    A path that is not absolute is refused for the same reason even though it
    may name a directory that exists today: the operating system resolves it
    against the Broker's own current directory at spawn time, so the plan is
    not what decides where the child runs — the Broker's location does.
    Accepting one would hand the launch-directory inheritance above a way back
    in, and the plan — the one place that names where the child runs — would
    no longer pin down where it landed. On Windows this covers more than the
    plain "relative" reading: `pathlib` counts drive-less rooted paths like
    `/Windows` and drive-relative paths like `C:foo` as not absolute, and both
    still resolve against the current drive or directory.

    A directory that does not exist is refused here too, so it arrives as a
    pre-start refusal like every other decision checked on this boundary rather
    than as an operating-system error from the spawn itself.
    """
    if candidate is None:
        raise PreStartRefusal(RefusalCode.WORKING_DIRECTORY_UNDECIDED)
    if not candidate.is_absolute():
        raise PreStartRefusal(RefusalCode.WORKING_DIRECTORY_UNDECIDED)
    if not candidate.is_dir():
        raise PreStartRefusal(RefusalCode.WORKING_DIRECTORY_UNDECIDED)


def resolve_executable(name, *, which=shutil.which) -> Path:
    """The absolute path `name` runs, or refuse before anything starts.

    `which` is injectable so the refusal paths can be tested without installing
    a shim on the machine running the suite.
    """
    found = which(name)
    if found is None:
        raise PreStartRefusal(RefusalCode.EXECUTABLE_UNRESOLVED)

    resolved = Path(found).resolve()
    assert_directly_executable(resolved)
    return resolved


async def hand_over_prompt(writer, prompt: bytes) -> None:
    """Give the child its prompt and end the pipe.

    Written and closed here rather than left to the caller: a child that reads
    instructions from stdin waits forever if the pipe never ends, and that hang
    would look like a slow model rather than a bug.

    A child that has stopped reading is an ordinary provider outcome rather than
    a transport fault — a binary that rejects a flag or fails to authenticate
    exits immediately, and spec section 11 already resolves a provider that ends
    early from its exit code and stderr. Raising here instead would discard the
    handle for a process that is already running.

    The two errors below are how Windows reports a read end that is gone, and
    which one arrives is not stable: the same scenario produced `BrokenPipeError`
    on one machine and `ConnectionResetError` on another. Nothing wider is
    tolerated — both are `OSError` subclasses, and catching the base class would
    turn a real I/O fault into a child that silently never got its prompt.
    """
    try:
        writer.write(prompt)
        await writer.drain()
        writer.close()
    except (BrokenPipeError, ConnectionResetError):
        pass


class SubprocessSpawner:
    """Starts the planned process and hands the prompt over on stdin.

    `create_subprocess_exec`, never `create_subprocess_shell`: the arguments are
    already split, and a shell would reinterpret every metacharacter in a
    participant's text as syntax.
    """

    async def spawn(self, plan: LaunchPlan) -> asyncio.subprocess.Process:
        # Checked here, not only where a name was resolved: this is the last
        # point before a process exists, and the plan may have been assembled
        # by something that never went through the resolver.
        assert_directly_executable(plan.executable)
        assert_working_directory(plan.working_directory)

        process = await asyncio.create_subprocess_exec(
            str(plan.executable),
            *plan.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(plan.child_env),
            cwd=str(plan.working_directory),
        )

        # From here on a process exists, so every way out of this function has
        # to leave either a handle with the caller or nothing running at all.
        try:
            await hand_over_prompt(
                process.stdin, plan.turn_request.prompt.encode("utf-8")
            )
        except BaseException as original:
            # Two ways in. One is live today: an error the tolerance above
            # does not accept — an `OSError` that is not a reader that left —
            # arrives here on every turn that hits it.
            #
            # The other is cancellation while this write is still blocked, and
            # nothing reaches it yet: `TurnDeadline` has no consumer, and no
            # caller wraps `start_turn` in a task it could cancel. That window
            # opens when a broker starts cancelling turns. Guarding it now is
            # not speculative work — without it the child outlives a spawn that
            # never returned, which is the one thing this function must not do.
            #
            # The tree, not the process: Windows does not cascade a kill, and a
            # provider that starts a helper before reading its prompt already
            # has a descendant by the time this window opens. The result is
            # deliberately not inspected — the exception on its way out is the
            # finding, and replacing it with a lifecycle error would lose it.
            #
            # Which is also why the escalation cannot be left to raise. Inside
            # an `except`, anything it raises leaves in its place, and the
            # caller is handed a complaint about cleanup for a spawn it never
            # learned had been cancelled. The child is still ended as far as we
            # can reach without the helper: our own process, though not the
            # descendants only `taskkill /T` gets to.
            try:
                await terminate_process_tree(process)
            except LifecycleFailure as cleanup:
                await terminate_process_only(process)
                # A note rather than a replacement or a chain: the exception on
                # its way out keeps its type and its place, and the one fact
                # that explains why descendants may still be running rides
                # with it instead of being dropped.
                original.add_note(f"process tree not terminated: {cleanup}")
            raise

        return process
