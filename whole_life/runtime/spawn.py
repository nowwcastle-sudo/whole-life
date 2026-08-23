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
from whole_life.runtime.lifecycle import terminate_process_tree

#: Only a real image. Spec line 132 permits `.cmd` or `.exe` and line 202
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

        process = await asyncio.create_subprocess_exec(
            str(plan.executable),
            *plan.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(plan.child_env),
        )

        # From here on a process exists, so every way out of this function has
        # to leave either a handle with the caller or nothing running at all.
        try:
            await hand_over_prompt(
                process.stdin, plan.turn_request.prompt.encode("utf-8")
            )
        except BaseException:
            # Cancellation is the reachable case: a turn cancelled while this
            # write is still blocked would otherwise leave the child with no
            # owner, before it ever became a run that could be closed.
            #
            # The tree, not the process: Windows does not cascade a kill, and a
            # provider that starts a helper before reading its prompt already
            # has a descendant by the time this window opens. The result is
            # deliberately not inspected — the exception on its way out is the
            # finding, and replacing it with a lifecycle error would lose it.
            await terminate_process_tree(process)
            raise

        return process
