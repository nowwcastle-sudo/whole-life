"""The Claude Code runtime adapter. One of exactly two in v0."""

from collections.abc import AsyncIterator, Mapping
from pathlib import Path
import dataclasses
from uuid import uuid4

from whole_life.runtime.childenv import build_child_env
from whole_life.runtime.delegation import REPORTED_ENFORCEMENT
from whole_life.runtime.contract import (
    CancelOutcome,
    EnforcementLevel,
    Provider,
    RunHandle,
    RunOutcome,
    RuntimeEvent,
    RuntimeStatus,
    TurnMode,
    TurnRequest,
)
from whole_life.runtime.launch import (
    LaunchPlan,
    PreStartRefusal,
    RefusalCode,
    VersionConformance,
    launch,
)
from whole_life.runtime.lifecycle import GRACEFUL_WAIT_SECONDS
from whole_life.runtime.normalize import normalize_claude_line
from whole_life.runtime.observe import RunObserver, close_all_runs
from whole_life.runtime.preflight import (
    CommandRunner,
    conformance_for,
    decide_claude_auth,
)

_LATER_SLICE = "arrives with a later ticket; this slice ends at preflight"

#: The final arguments for one Claude turn, as spec section 4 lists them
#: (lines 128 and 139). `--safe-mode`, never `--bare`: `--bare` replaces the
#: authentication path and would break the subscription premise.
#:
#: `--safe-mode` alone is *not* the read-only boundary. The spec says plainly
#: that it leaves authentication, model selection, built-in tools and
#: permissions working — so the tool allowlist, the permission mode and a strict
#: empty MCP configuration are what actually keep `Write`, `Edit`, `Bash` and
#: network side-effect tools off the turn.
CLAUDE_TURN_ARGS = (
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
)


class ClaudeRuntime:
    """Drives the official Claude Code CLI using the operator's own sign-in.

    Nothing here reads, copies or stores a credential. The CLI is asked whether
    it is already signed in, in the same sanitized environment the real run will
    get, and the answer is reduced to yes or no.
    """

    provider = Provider.CLAUDE

    def __init__(
        self,
        *,
        executable: Path,
        runner: CommandRunner,
        parent_env: Mapping[str, str],
        spawner=None,
        sessions=None,
        journal=None,
        delegation_enforcement=None,
    ) -> None:
        self._executable = executable
        self._runner = runner
        #: Built once. The status command below and the eventual launch plan use
        #: this exact mapping, so they cannot diverge.
        self.child_env = build_child_env(parent_env)
        self._conformance: VersionConformance | None = None
        #: What this runtime reports it can hold, per limit. Injected like
        #: the runner and the spawner are, and defaulting to what the
        #: measurement table says — so production never chooses it, and a
        #: test whose subject is not delegation can say so out loud rather
        #: than be refused by a control it is not exercising.
        self._delegation_enforcement = (
            delegation_enforcement
            if delegation_enforcement is not None
            else REPORTED_ENFORCEMENT[Provider.CLAUDE]
        )
        #: Injected so a turn can be started without a real process, and so the
        #: registry and journal are owned by the caller rather than this module.
        self._spawner = spawner
        self._sessions = sessions
        self._journal = journal
        #: Observers for runs this adapter started, keyed by run id. Held so
        #: `events` and `wait` describe the process `start_turn` actually
        #: spawned rather than one reconstructed from a handle.
        self._runs: dict[str, RunObserver] = {}
        #: Test seam: substitute the argument vector while keeping assembly,
        #: the pre-spawn gate and the spawner real.
        self.turn_args_override = None

    @property
    def conformance(self) -> VersionConformance | None:
        """The pinned-version record, once preflight has established it."""
        return self._conformance

    async def preflight(self) -> RuntimeStatus:
        # Cleared first, not merely overwritten on success. A caller that
        # catches a failure here must not be left holding an adapter that can
        # still assemble a plan from the previous run's record.
        self._conformance = None

        version = await self._runner.run(
            self._executable, ("--version",), self.child_env
        )
        conformance = conformance_for(Provider.CLAUDE, version)

        auth = await self._runner.run(
            self._executable, ("auth", "status", "--json"), self.child_env
        )
        decide_claude_auth(auth)

        self._conformance = conformance
        return RuntimeStatus(
            provider=Provider.CLAUDE,
            cli_version=conformance.cli_version,
            **self._delegation_enforcement,
        )

    def assemble_launch_plan(self, request: TurnRequest) -> LaunchPlan:
        """Everything one turn is about to be started with, as one value.

        Separated from `start_turn` so that the safety boundary can be exercised
        against the plan this adapter really builds. `start_turn` arrives with
        #14 and starts by calling this.

        `--bare` is never placed here. `--safe-mode` is what disables
        customization; the two are not interchangeable, because `--bare` also
        replaces the authentication path (spec section 5).
        """
        if self._conformance is None:
            raise PreStartRefusal(RefusalCode.UNSUPPORTED_CLI_VERSION)

        args = CLAUDE_TURN_ARGS
        if request.mode is TurnMode.RESUME:
            # The provider's own resume operation. The read-only controls above
            # are not conditional on mode: resuming is not a way around them.
            args += ("--resume", request.native_session_id)

        return LaunchPlan(
            provider=Provider.CLAUDE,
            executable=self._executable,
            args=args,
            child_env=self.child_env,
            version_conformance=self._conformance,
            turn_request=request,
            delegation_enforcement=self._delegation_enforcement,
        )

    async def start_turn(self, request: TurnRequest) -> RunHandle:
        """Assemble, check, and start — one path, no way around the gate.

        The Broker calls this. Everything the gate verifies is verified about
        the plan that is actually handed to the spawner, because it is the same
        value: assembled once here and passed straight through.

        The run's lifecycle — events, wait, cancel, close — arrives with the
        later tickets. What this method owns is that a turn cannot begin except
        through the pre-spawn boundary.
        """
        plan = self.assemble_launch_plan(request)
        if self.turn_args_override is not None:
            plan = dataclasses.replace(plan, args=self.turn_args_override)

        process = await launch(
            plan,
            self._spawner,
            sessions=self._sessions,
            journal=self._journal,
        )
        run_id = str(uuid4())
        self._runs[run_id] = RunObserver(
            process, normalize=normalize_claude_line, run_id=run_id
        )
        return RunHandle(
            run_id=run_id,
            participant_id=request.participant_id,
            provider=self.provider,
            native_session_id=request.native_session_id,
        )

    def events(self, run: RunHandle) -> AsyncIterator[RuntimeEvent]:
        """Canonical events for a run this adapter started.

        A handle for a run this adapter did not start is a programming error,
        not a quiet empty stream.
        """
        return self._runs[run.run_id].events()

    async def cancel(
        self, run: RunHandle, *, graceful_wait: float = GRACEFUL_WAIT_SECONDS
    ) -> CancelOutcome:
        """End one run. A handle this adapter never issued is an error."""
        return await self._runs[run.run_id].cancel(graceful_wait=graceful_wait)

    def active_child_count(self) -> int:
        """Child processes this runtime started that are not yet reaped."""
        return sum(1 for observer in self._runs.values() if observer.has_child())

    def drain_task_count(self) -> int:
        """Stdout/stderr drain tasks still running across every run."""
        return sum(
            observer.pending_drain_tasks() for observer in self._runs.values()
        )

    async def wait(self, run: RunHandle) -> RunOutcome:
        """The resolved outcome. Meaningful once `events` has finished."""
        return await self._runs[run.run_id].outcome()

    async def close(
        self, *, graceful_wait: float = GRACEFUL_WAIT_SECONDS
    ) -> None:
        """Cancel every active run and return only once nothing is left.

        Idempotent: a second call finds nothing to do, which matters because
        shutdown paths are exactly where a close gets called twice.
        """
        await close_all_runs(
            list(self._runs.values()), graceful_wait=graceful_wait
        )
