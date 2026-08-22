"""The Codex CLI runtime adapter. The other of exactly two in v0."""

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
from whole_life.runtime.launch import VersionConformance
from whole_life.runtime.preflight import (
    CommandRunner,
    conformance_for,
    decide_codex_auth,
)

_LATER_SLICE = "arrives with a later ticket; this slice ends at preflight"


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
    ) -> None:
        self._executable = executable
        self._runner = runner
        self.child_env = build_child_env(
            parent_env, extra={"CODEX_HOME": str(codex_home)}
        )
        self._conformance: VersionConformance | None = None

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
            # Documented in spec section 4, not yet measured here. #17 replaces
            # these with observed values and fails closed where it cannot see.
            worker_concurrency_enforcement=EnforcementLevel.HARD,
            worker_total_start_enforcement=EnforcementLevel.COOPERATIVE,
            delegation_depth_enforcement=EnforcementLevel.COOPERATIVE,
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
