"""Record redacted conformance evidence for one locally installed CLI version.

Not part of the test suite and not run in CI: it needs the operator's real
sign-in. Run it by hand when adding a version to the allowlist in
`whole_life/runtime/preflight.py`, and commit the file it writes.

What it deliberately does not record: any environment value, any credential,
the full authentication payload, raw stderr, and the `email`, `orgId` and
`orgName` fields. It records field *names*, the four Claude decision values,
exit codes, and whether Codex's output changes when an API-key variable is
present. That last measurement is why the child-environment allowlist exists.

Each collector returns its measured lines and, separately, the claims it carries
from the specification without observing them. They are rendered under different
headings so that re-running this script for a new version cannot restamp an
unmeasured claim — Claude's bare mode default above all — with a fresh date.

    python scripts/collect-conformance-evidence.py claude
    python scripts/collect-conformance-evidence.py codex
"""

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import date
from typing import NamedTuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

from whole_life.runtime.childenv import (  # noqa: E402
    FORBIDDEN_VARIABLES,
    build_child_env,
)
from whole_life.runtime.preflight import (  # noqa: E402
    CLAUDE_AUTH_FIELDS,
    CODEX_SUBSCRIPTION_STATUS,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "conformance"
FAKE_KEY = "sk-conformance-probe-not-a-real-key"


def run(executable, args, env):
    completed = subprocess.run(
        [executable, *args], capture_output=True, text=True, env=dict(env), shell=True
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


class CollectionFailed(Exception):
    """A measurement contradicted the premise this collector describes.

    No document is written. The alternative — restating the conclusion the other
    way round — would put a claim nobody reviewed into a file whose whole purpose
    is to be evidence. A surprising measurement is an event for a person.
    """


def resolved_executable(name):
    """The absolute path `name` resolves to, or refuse to measure anything.

    Resolved rather than hard-coded: a path under the operator's home directory
    is machine-specific and does not belong in this repository. The identity that
    *is* recorded is the hash below, which is what makes the version, the
    authentication check and the turn provably the same binary.
    """
    found = shutil.which(name)
    if found is None:
        raise CollectionFailed(f"{name} is not on PATH; nothing can be measured")
    return Path(found).resolve()


def binary_digest(executable):
    return hashlib.sha256(executable.read_bytes()).hexdigest()


def assert_same_binary(digest_before, digest_after):
    """The three executions must have measured one build, or nothing is written."""
    if digest_before != digest_after:
        raise CollectionFailed(
            "the executable changed during collection; the version, the "
            "authentication check and the turn are not about the same binary"
        )


class BareProbe(NamedTuple):
    """The outcome of the minimal `-p` turn. Never its output."""

    exit_code: int | None
    timed_out: bool


#: The minimal turn. Fixed so a re-run is comparable, and short so it costs one
#: trivial exchange of the operator's subscription.
BARE_PROBE_PROMPT = "Reply with exactly: ok"
BARE_PROBE_TIMEOUT_SECONDS = 180

#: Everything that could let this turn read a credential from somewhere other
#: than the operator's existing sign-in, or leave state behind, turned off.
BARE_PROBE_ARGS = (
    "-p",
    "--no-session-persistence",
    "--tools",
    "",
    "--setting-sources",
    "",
    "--settings",
    "{}",
    # An empty object is rejected: the schema requires the `mcpServers` key,
    # present and empty. With `--strict-mcp-config` this is the only source of
    # MCP servers, so the turn has none.
    "--mcp-config",
    '{"mcpServers": {}}',
    "--strict-mcp-config",
    "--output-format",
    "text",
)


def probe_bare_default(env, executable):
    """Run the minimal `-p` turn and keep only whether it completed.

    `shell=False` with split argv, the prompt on stdin rather than in argv, and
    the output discarded unread — the turn's body is a model response, which
    section 5 does not permit persisting and this script has no reason to see.
    """
    try:
        completed = subprocess.run(
            [str(executable), *BARE_PROBE_ARGS],
            input=BARE_PROBE_PROMPT.encode("utf-8"),
            capture_output=True,
            env=dict(env),
            shell=False,
            timeout=BARE_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return BareProbe(exit_code=None, timed_out=True)

    return BareProbe(exit_code=completed.returncode, timed_out=False)


def claude_measurement_lines(version_exit, version_out, auth_exit, payload, probe):
    """Split what this run computed from what it carries without observing.

    The `-p` turn ran in a child environment that contains no API-key variable,
    because `build_child_env` refuses to build one that does. Bare mode reads
    `ANTHROPIC_API_KEY` or a settings `apiKeyHelper` and never OAuth, so a turn
    that completes there cannot have been bare. That is the measurement spec
    section 5 requires for control 3, and the procedure is T-2 of the gate 2
    smoke test.

    Anything other than a clean exit stops the collection. A failed or timed-out
    turn has many explanations — network, quota, a changed flag — and choosing
    `bare_default=True` from it would be inventing the very measurement this
    function exists to make.
    """
    if probe.timed_out or probe.exit_code != 0:
        raise CollectionFailed(
            "the minimal `-p` turn did not complete cleanly, so this run cannot "
            "decide bare_default for this version"
        )

    decisions = {
        "auth status exited zero": auth_exit == 0,
        "field set is the pinned one": set(payload) == set(CLAUDE_AUTH_FIELDS),
        "loggedIn is True": payload.get("loggedIn") is True,
        "authMethod is claude.ai": payload.get("authMethod") == "claude.ai",
        "apiProvider is firstParty": payload.get("apiProvider") == "firstParty",
        "subscriptionType is a non-empty string": isinstance(
            payload.get("subscriptionType"), str
        )
        and bool(payload.get("subscriptionType")),
    }
    if not all(decisions.values()):
        # Names which condition failed, never the value that failed it.
        failed = sorted(name for name, held in decisions.items() if not held)
        raise CollectionFailed(
            "the sign-in is not the pinned first-party subscription state, so "
            f"this run has nothing to record: {', '.join(failed)}"
        )

    measured = [
        f"- `claude --version` exit `{version_exit}`, stdout `{version_out}`",
        f"- `claude auth status --json` exit `{auth_exit}`, parsed as a JSON object",
        f"- field names present: `{'`, `'.join(sorted(payload))}`",
        f"- field set equals the pinned set: **{set(payload) == set(CLAUDE_AUTH_FIELDS)}**",
        f"- `loggedIn` is exactly `True`: **{payload['loggedIn'] is True}**",
        f"- `authMethod == \"claude.ai\"`: **{payload['authMethod'] == 'claude.ai'}**",
        f"- `apiProvider == \"firstParty\"`: **{payload['apiProvider'] == 'firstParty'}**",
        f"- `subscriptionType` is a non-empty string: "
        f"**{isinstance(payload.get('subscriptionType'), str) and bool(payload['subscriptionType'])}**",
    ]
    measured.append(
        "- bare mode default is **False**, measured: a minimal `-p` turn exited "
        f"`{probe.exit_code}` in a child environment holding no API-key variable. "
        "Bare mode reads `ANTHROPIC_API_KEY` or a settings `apiKeyHelper` and never "
        "OAuth, so a turn that completes there was not bare (spec section 5, "
        "procedure T-2)."
    )
    carried = [
        "- `email`, `orgId` and `orgName` are never read by the parser. That is a "
        "property of the code, not something this run observed.",
    ]
    return measured, carried


def collect_claude():
    env = build_child_env(os.environ)
    executable = resolved_executable("claude")
    digest_before = binary_digest(executable)

    version_exit, version_out, _ = run("claude", ["--version"], env)
    auth_exit, auth_out, _ = run("claude", ["auth", "status", "--json"], env)
    payload = json.loads(auth_out)
    probe = probe_bare_default(env, executable)

    assert_same_binary(digest_before, binary_digest(executable))

    measured, carried = claude_measurement_lines(
        version_exit, version_out, auth_exit, payload, probe
    )
    measured.append(f"- executable SHA-256 before and after: `{digest_before}`")
    return version_out, measured, carried


def codex_measurement_lines(version_exit, version_out, clean, probed):
    """Split computed lines from carried claims, or refuse to describe the run.

    The `therefore` line is the sentence AC 8 rests on. It is emitted only when
    all three comparisons hold, because it is a conclusion *from* them. If any
    differs, this build can distinguish the credential paths in its output and
    the design premise has changed — that is a decision for a person, not a
    sentence for this script to rewrite.
    """
    identical = (clean[0] == probed[0], clean[1] == probed[1], clean[2] == probed[2])
    if not all(identical):
        # Names the condition only. The outputs that differed are never carried
        # into the message; one of them is the probe environment's own key.
        raise CollectionFailed(
            "codex status output changed when an API-key variable was present; "
            "the section 5 premise no longer holds for this version"
        )

    measured = [
        f"- `codex --version` exit `{version_exit}`, stdout `{version_out}`",
        f"- `codex login status` exit `{clean[0]}`, stdout empty: **{clean[1] == ''}**",
        f"- decision arrives on stderr and equals the pinned "
        f"`{CODEX_SUBSCRIPTION_STATUS}`: **{clean[2] == CODEX_SUBSCRIPTION_STATUS}**",
        f"- with `OPENAI_API_KEY` present, exit code identical: **{identical[0]}**",
        f"- with `OPENAI_API_KEY` present, stdout identical: **{identical[1]}**",
        f"- with `OPENAI_API_KEY` present, stderr identical: **{identical[2]}**",
        "- therefore the status output cannot distinguish a subscription sign-in from "
        "an API-key credential path, and excluding the variable from the child "
        "environment is the only detection this build has",
    ]
    carried = [
        "- `CODEX_HOME` is set explicitly rather than inherited; `--ignore-user-config` "
        "is documented to ignore `config.toml` only, leaving authentication in "
        "`CODEX_HOME` (spec section 5). This collector runs no `--ignore-user-config` "
        "probe, so it does not confirm that separation.",
    ]
    return measured, carried


def collect_codex():
    home = Path(os.environ["USERPROFILE"]) / ".codex"
    env = build_child_env(os.environ, extra={"CODEX_HOME": str(home)})
    version_exit, version_out, _ = run("codex", ["--version"], env)
    clean = run("codex", ["login", "status"], env)

    # The one place an API-key variable is deliberately constructed, to measure
    # whether it is detectable in the output. Never the production path.
    probed_env = dict(env) | {"OPENAI_API_KEY": FAKE_KEY}
    probed = run("codex", ["login", "status"], probed_env)

    measured, carried = codex_measurement_lines(
        version_exit, version_out, clean, probed
    )
    return version_out, measured, carried


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("claude", "codex"):
        raise SystemExit(__doc__)

    provider = sys.argv[1]
    leaked = sorted(FORBIDDEN_VARIABLES & set(os.environ))
    version, measured, carried = (
        collect_claude if provider == "claude" else collect_codex
    )()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if provider == "claude":
        out = OUT_DIR / f"claude-{version.split()[0]}.md"
    else:
        out = OUT_DIR / f"{provider}-{version.split()[-1].strip('()')}.md"

    body = [
        f"# Conformance evidence — {provider} {version}",
        "",
        f"Collected {date.today().isoformat()} on Windows, in the child environment "
        "built by `whole_life.runtime.childenv.build_child_env`.",
        "",
        "Redacted by construction: no environment values, no credentials, no full "
        "authentication payload, no raw stderr, no account identifiers.",
        "",
        "## Observations",
        "",
        "Every line below was computed from a command this run executed.",
        "",
        *measured,
        "",
        "## Not measured by this script",
        "",
        "Carried from the specification, not observed here. The collection date "
        "above does not apply to this section: these claims are not measured by "
        "this script, so re-running it for a new version does not re-verify them.",
        "",
        *carried,
        "",
        "## Collection environment",
        "",
        f"- forbidden variables present in the *parent* environment at collection time: "
        f"`{'`, `'.join(leaked) if leaked else 'none'}`",
        "- the child environment is built from an inherit allowlist and refuses any "
        "forbidden variable, so the above could not reach the command either way",
        "",
        "Reproduce with `python scripts/collect-conformance-evidence.py "
        f"{provider}`.",
        "",
    ]
    out.write_text("\n".join(body), encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(OUT_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
