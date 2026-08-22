"""Test doubles for authentication preflight.

Every fixture is synthetic. The identifier fields carry sentinels precisely so
that a test can prove they did not survive parsing.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from whole_life.runtime.preflight import CommandResult

CLAUDE_EXECUTABLE = Path(r"C:\tools\claude\claude.cmd")
CODEX_EXECUTABLE = Path(r"C:\tools\codex\codex.cmd")
CODEX_HOME = Path(r"D:\codex-home")

SENTINEL_EMAIL = "SENTINEL-EMAIL-ADDRESS"
SENTINEL_ORG_ID = "SENTINEL-ORG-ID"
SENTINEL_ORG_NAME = "SENTINEL-ORG-NAME"

#: Exactly the field set Claude Code 2.1.240 returns, observed locally.
CLAUDE_AUTH_OK = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "subscriptionType": "max",
    "email": SENTINEL_EMAIL,
    "orgId": SENTINEL_ORG_ID,
    "orgName": SENTINEL_ORG_NAME,
}

#: Codex prints its decision on stderr and leaves stdout empty.
CODEX_LOGGED_IN_STDERR = "Logged in using ChatGPT"

PARENT_ENV = {
    "SYSTEMROOT": r"C:\Windows",
    "PATH": r"C:\Windows\system32",
    "LANG": "en-US.UTF-8",
    # Present in the parent on purpose: the child environment must not carry it,
    # and for Codex this is the only way the wrong credential path is detectable.
    "OPENAI_API_KEY": "sk-PARENT-KEY-MUST-NOT-PROPAGATE",
    "ANTHROPIC_API_KEY": "sk-PARENT-ANTHROPIC-MUST-NOT-PROPAGATE",
}


def claude_auth_result(payload=None, *, exit_code=0, stdout=None) -> CommandResult:
    if stdout is None:
        stdout = json.dumps(CLAUDE_AUTH_OK if payload is None else payload)
    return CommandResult(exit_code=exit_code, stdout=stdout, stderr="")


def codex_auth_result(
    stderr=CODEX_LOGGED_IN_STDERR, *, exit_code=0, stdout=""
) -> CommandResult:
    return CommandResult(exit_code=exit_code, stdout=stdout, stderr=stderr)


class ScriptedRunner:
    """A CommandRunner that replays scripted results and records every call."""

    def __init__(self, results: Mapping[tuple[str, ...], CommandResult]) -> None:
        self._results = dict(results)
        self.calls: list[tuple[Path, tuple[str, ...], Mapping[str, str]]] = []

    async def run(self, executable, args, env) -> CommandResult:
        self.calls.append((executable, tuple(args), dict(env)))
        try:
            return self._results[tuple(args)]
        except KeyError:  # pragma: no cover - a test asked for an unscripted call
            raise AssertionError(f"unscripted command: {args}") from None

    def env_for(self, args: tuple[str, ...]) -> Mapping[str, str]:
        for _executable, called_args, env in self.calls:
            if called_args == args:
                return env
        raise AssertionError(f"never called: {args}")


def claude_runner(
    *, version="2.1.240 (Claude Code)", auth=None, version_exit=0
) -> ScriptedRunner:
    return ScriptedRunner(
        {
            ("--version",): CommandResult(version_exit, version, ""),
            ("auth", "status", "--json"): auth or claude_auth_result(),
        }
    )


def codex_runner(
    *, version="codex-cli 0.149.0", auth=None, version_exit=0
) -> ScriptedRunner:
    return ScriptedRunner(
        {
            ("--version",): CommandResult(version_exit, version, ""),
            ("login", "status"): auth or codex_auth_result(),
        }
    )
