"""The Claude Code runtime adapter. One of exactly two in v0."""

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from whole_life.runtime.childenv import build_child_env
from whole_life.runtime.contract import (
    CancelOutcome,
    EnforcementLevel,
    Provider,
    RunHandle,
    RunOutcome,
    RuntimeEvent,
    RuntimeStatus,
    TurnRequest,
)
from whole_life.runtime.launch import (
    LaunchPlan,
    PreStartRefusal,
    RefusalCode,
    VersionConformance,
)
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
    ) -> None:
        self._executable = executable
        self._runner = runner
        #: Built once. The status command below and the eventual launch plan use
        #: this exact mapping, so they cannot diverge.
        self.child_env = build_child_env(parent_env)
        self._conformance: VersionConformance | None = None

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
            # Documented in spec section 4, not yet measured here. #17 replaces
            # these with observed values and fails closed where it cannot see.
            worker_concurrency_enforcement=EnforcementLevel.COOPERATIVE,
            worker_total_start_enforcement=EnforcementLevel.COOPERATIVE,
            delegation_depth_enforcement=EnforcementLevel.HARD,
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

        return LaunchPlan(
            provider=Provider.CLAUDE,
            executable=self._executable,
            args=CLAUDE_TURN_ARGS,
            child_env=self.child_env,
            version_conformance=self._conformance,
            turn_request=request,
        )

    async def start_turn(self, request: TurnRequest) -> RunHandle:
        raise NotImplementedError(f"start_turn {_LATER_SLICE}")

    def events(self, run: RunHandle) -> AsyncIterator[RuntimeEvent]:
        raise NotImplementedError(f"events {_LATER_SLICE}")

    async def cancel(self, run: RunHandle) -> CancelOutcome:
        raise NotImplementedError(f"cancel {_LATER_SLICE}")

    async def wait(self, run: RunHandle) -> RunOutcome:
        raise NotImplementedError(f"wait {_LATER_SLICE}")

    async def close(self) -> None:
        raise NotImplementedError(f"close {_LATER_SLICE}")
