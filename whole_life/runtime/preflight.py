"""Exact-version and subscription-authentication preflight. Spec section 5.

Two rules shape everything here.

Fail closed. An output we do not recognise is not evidence of success. A field
that appeared, a field that vanished, a version we have not tested — each
refuses the launch rather than assuming the old meaning still holds.

Read the decision, keep nothing else. The Claude status output carries the
operator's email and organisation alongside the four fields that decide
anything. Codex prints its decision on stderr, which section 5 forbids
persisting. So the parsers return a decision and drop the payload; there is no
code path on which the rest reaches a caller, a diagnostic or an artifact.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from whole_life.runtime.contract import Provider
from whole_life.runtime.launch import (
    PreStartRefusal,
    RefusalCode,
    SUPPORTED_VERSIONS,
    VersionConformance,
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one short status command."""

    exit_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Runs a status command. Injected so the suite never needs a real account.

    The concrete runner arrives with #14, together with executable resolution
    and the no-shell split-argv process start it shares with turn spawning.
    """

    async def run(
        self,
        executable: Path,
        args: tuple[str, ...],
        env: Mapping[str, str],
    ) -> CommandResult: ...


_VERSION_PATTERNS = {
    Provider.CLAUDE: re.compile(r"\A(\d+\.\d+\.\d+) \(Claude Code\)\Z"),
    Provider.CODEX: re.compile(r"\Acodex-cli (\d+\.\d+\.\d+)\Z"),
}

#: The complete field set of `claude auth status --json`, pinned to the tested
#: version. Anything else is a schema we have not evaluated.
CLAUDE_AUTH_FIELDS = frozenset(
    {
        "loggedIn",
        "authMethod",
        "apiProvider",
        "subscriptionType",
        "email",
        "orgId",
        "orgName",
    }
)

#: The only Codex status this build accepts, matched exactly.
CODEX_SUBSCRIPTION_STATUS = "Logged in using ChatGPT"


def conformance_for(provider: Provider, result: CommandResult) -> VersionConformance:
    """Resolve a `--version` result against the allowlist, or refuse."""
    if result.exit_code != 0:
        raise PreStartRefusal(RefusalCode.UNSUPPORTED_CLI_VERSION)

    match = _VERSION_PATTERNS[provider].match(result.stdout.strip())
    if match is None:
        raise PreStartRefusal(RefusalCode.UNSUPPORTED_CLI_VERSION)

    conformance = SUPPORTED_VERSIONS[provider].get(match.group(1))
    if conformance is None:
        raise PreStartRefusal(RefusalCode.UNSUPPORTED_CLI_VERSION)

    return conformance


def _object_without_repeats(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object, rejecting a repeated field instead of keeping the last.

    Without this, `{"loggedIn": false, "loggedIn": true}` parses to a signed-in
    decision and every later check agrees with it. The message carries no value
    from the payload.
    """
    names = [name for name, _value in pairs]
    if len(names) != len(set(names)):
        raise ValueError("repeated field in authentication status")
    return dict(pairs)


def decide_claude_auth(result: CommandResult) -> None:
    """Accept only the pinned first-party subscription state. Returns nothing.

    A schema we do not recognise is `AuthStatusUnsupported`; a schema we do
    recognise reporting a credential path we will not use is
    `SubscriptionAuthRequired`. Both refuse.
    """
    if result.exit_code != 0:
        raise PreStartRefusal(RefusalCode.AUTH_STATUS_UNSUPPORTED)

    try:
        payload = json.loads(result.stdout, object_pairs_hook=_object_without_repeats)
    except (ValueError, TypeError):
        raise PreStartRefusal(RefusalCode.AUTH_STATUS_UNSUPPORTED) from None

    if not isinstance(payload, dict) or set(payload) != CLAUDE_AUTH_FIELDS:
        raise PreStartRefusal(RefusalCode.AUTH_STATUS_UNSUPPORTED)

    if payload["loggedIn"] is not True:
        raise PreStartRefusal(RefusalCode.SUBSCRIPTION_AUTH_REQUIRED)

    if payload["authMethod"] != "claude.ai":
        raise PreStartRefusal(RefusalCode.SUBSCRIPTION_AUTH_REQUIRED)

    # Separates a first-party subscription from Bedrock, Vertex and other
    # third-party credential paths.
    if payload["apiProvider"] != "firstParty":
        raise PreStartRefusal(RefusalCode.SUBSCRIPTION_AUTH_REQUIRED)

    subscription = payload["subscriptionType"]
    if not isinstance(subscription, str) or not subscription:
        raise PreStartRefusal(RefusalCode.SUBSCRIPTION_AUTH_REQUIRED)

    # payload["email"], payload["orgId"] and payload["orgName"] are deliberately
    # never read. Nothing is returned, so nothing can leak.


def decide_codex_auth(result: CommandResult) -> None:
    """Accept only the pinned ChatGPT sign-in, matched exactly. Returns nothing.

    The decision arrives on stderr with stdout empty, so this is the one place
    stderr is read for meaning. Only the comparison result leaves the function.

    Every non-matching output is `AuthStatusUnsupported` rather than
    `SubscriptionAuthRequired`: the API-key wording has not been measured, and
    guessing at it would be inventing a fixture. Both refuse the launch, so the
    imprecision costs a clearer diagnostic, not safety.
    """
    if result.exit_code != 0:
        raise PreStartRefusal(RefusalCode.AUTH_STATUS_UNSUPPORTED)

    # Exactly empty, not "empty after stripping": the pinned shape is the empty
    # string, and a build that started writing whitespace to stdout is a build
    # whose output format we have not measured.
    if result.stdout != "":
        raise PreStartRefusal(RefusalCode.AUTH_STATUS_UNSUPPORTED)

    if result.stderr.strip() != CODEX_SUBSCRIPTION_STATUS:
        raise PreStartRefusal(RefusalCode.AUTH_STATUS_UNSUPPORTED)
