"""The Codex CLI runtime adapter. The other of exactly two in v0."""

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
from whole_life.runtime.normalize import normalize_codex_line
from whole_life.runtime.observe import RunObserver, close_all_runs
from whole_life.runtime.preflight import (
    CommandRunner,
    conformance_for,
    decide_codex_auth,
)

_LATER_SLICE = "arrives with a later ticket; this slice ends at preflight"

#: Spec section 4: the machine-readable output, the read-only sandbox, and the
#: refusal to load the operator's own configuration or rules.
#:
#: Every one of these sits at the `exec` level, ahead of any subcommand, and
#: that placement is measured rather than stylistic: `codex exec resume
#: --sandbox read-only` is a parse error (exit 2), while the same flag before
#: `resume` parses for both forms. Putting the sandbox after the verb would
#: mean a resumed turn silently ran without one.
CODEX_TURN_ARGS = (
    "exec",
    "--json",
    "--sandbox",
    "read-only",
    "--ignore-user-config",
    "--ignore-rules",
    # Issue #33. Measured, not read: `docs/conformance/codex-0.149.0.md`
    # records this build refusing to start outside a git repository, before any
    # model request, naming this flag as what it wanted. The Broker's working
    # directory is deliberately a neutral one rather than a repository, so the
    # trust prompt has to be answered here.
    #
    # It grants nothing. The alternative — pointing the working directory at a
    # real repository — would make that repository the agent's working root and
    # so widen what a participant can read, which is the boundary AC4 holds.
    "--skip-git-repo-check",
    "-c",
    "agents.enabled=true",
    "-c",
    "agents.max_concurrent_threads_per_session=3",
)


class CodexRuntime:
    """Drives the official Codex CLI using the operator's own ChatGPT sign-in.

    `CODEX_HOME` is required rather than inherited. `--ignore-user-config` only
    ignores `config.toml`; authentication is still read from `CODEX_HOME`, so if
    that value drifted between the status check and the real run the two would
    consult different credential stores.

    `codex login status` prints identically whether or not `OPENAI_API_KEY` is
    set — measured on 0.148.0 and again here on 0.149.0. There is therefore no
    output to inspect for the wrong credential path, and keeping the variable
    out of the child environment is the only defence.
    """

    provider = Provider.CODEX

    def __init__(
        self,
        *,
        executable: Path,
        runner: CommandRunner,
        parent_env: Mapping[str, str],
        codex_home: Path,
        working_directory: Path,
        spawner=None,
        sessions=None,
        journal=None,
    ) -> None:
        self._executable = executable
        self._runner = runner
        self.child_env = build_child_env(
            parent_env, extra={"CODEX_HOME": str(codex_home)}
        )
        self._conformance: VersionConformance | None = None
        #: Decided by the caller, never inherited. Spec issue #33: the
        #: Broker's own directory is wherever an operator started it, and
        #: a provider that refuses to run outside a trusted directory
        #: would fail every turn for a reason that reads as an outage.
        self._working_directory = working_directory
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
        version = await self._runner.run(
            self._executable, ("--version",), self.child_env
        )
        conformance = conformance_for(Provider.CODEX, version)

        auth = await self._runner.run(
            self._executable, ("login", "status"), self.child_env
        )
        decide_codex_auth(auth)

        self._conformance = conformance
        return RuntimeStatus(
            provider=Provider.CODEX,
            cli_version=conformance.cli_version,
            **REPORTED_ENFORCEMENT[Provider.CODEX],
        )

    def assemble_launch_plan(self, request: TurnRequest) -> LaunchPlan:
        """Everything one turn is about to be started with, as one value.

        The prompt is not here. It reaches the process as UTF-8 stdin bytes,
        because `codex exec` reads instructions from stdin when no prompt
        argument is given — and a command line is readable by anything that can
        list processes.
        """
        if self._conformance is None:
            raise PreStartRefusal(RefusalCode.UNSUPPORTED_CLI_VERSION)

        args = CODEX_TURN_ARGS
        if request.mode is TurnMode.RESUME:
            args += ("resume", request.native_session_id)

        return LaunchPlan(
            provider=Provider.CODEX,
            executable=self._executable,
            args=args,
            child_env=self.child_env,
            version_conformance=self._conformance,
            turn_request=request,
            working_directory=self._working_directory,
            delegation_enforcement=REPORTED_ENFORCEMENT[Provider.CODEX],
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
            process,
            normalize=normalize_codex_line,
            run_id=run_id,
            # The budget the Broker sent with this turn. Without it the
            # run counts nothing, and the limits would be a thing this
            # module defines and never applies.
            delegation_budget=request.delegation_budget,
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
