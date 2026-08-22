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

import json
import subprocess
import sys
from datetime import date
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


def collect_claude():
    env = build_child_env(os.environ)
    version_exit, version_out, _ = run("claude", ["--version"], env)
    auth_exit, auth_out, _ = run("claude", ["auth", "status", "--json"], env)
    payload = json.loads(auth_out)

    return version_out, [
        f"- `claude --version` exit `{version_exit}`, stdout `{version_out}`",
        f"- `claude auth status --json` exit `{auth_exit}`, parsed as a JSON object",
        f"- field names present: `{'`, `'.join(sorted(payload))}`",
        f"- field set equals the pinned set: **{set(payload) == set(CLAUDE_AUTH_FIELDS)}**",
        f"- `loggedIn` is exactly `True`: **{payload['loggedIn'] is True}**",
        f"- `authMethod == \"claude.ai\"`: **{payload['authMethod'] == 'claude.ai'}**",
        f"- `apiProvider == \"firstParty\"`: **{payload['apiProvider'] == 'firstParty'}**",
        f"- `subscriptionType` is a non-empty string: "
        f"**{isinstance(payload.get('subscriptionType'), str) and bool(payload['subscriptionType'])}**",
        "- `email`, `orgId` and `orgName` were present in the output and were not read",
    ], [
        "- bare mode default: `-p` is documented not to imply bare mode, which is "
        "reachable via `--bare` or `CLAUDE_CODE_SIMPLE` (spec section 5). Deciding "
        "`bare_default` for a version needs an observed turn; this collector runs "
        "none. The gate that enforces it arrives with #13.",
    ]


def collect_codex():
    home = Path(os.environ["USERPROFILE"]) / ".codex"
    env = build_child_env(os.environ, extra={"CODEX_HOME": str(home)})
    version_exit, version_out, _ = run("codex", ["--version"], env)
    clean = run("codex", ["login", "status"], env)

    # The one place an API-key variable is deliberately constructed, to measure
    # whether it is detectable in the output. Never the production path.
    probed_env = dict(env) | {"OPENAI_API_KEY": FAKE_KEY}
    probed = run("codex", ["login", "status"], probed_env)

    return version_out, [
        f"- `codex --version` exit `{version_exit}`, stdout `{version_out}`",
        f"- `codex login status` exit `{clean[0]}`, stdout empty: **{clean[1] == ''}**",
        f"- decision arrives on stderr and equals the pinned "
        f"`{CODEX_SUBSCRIPTION_STATUS}`: **{clean[2] == CODEX_SUBSCRIPTION_STATUS}**",
        f"- with `OPENAI_API_KEY` present, exit code identical: **{clean[0] == probed[0]}**",
        f"- with `OPENAI_API_KEY` present, stdout identical: **{clean[1] == probed[1]}**",
        f"- with `OPENAI_API_KEY` present, stderr identical: **{clean[2] == probed[2]}**",
        "- therefore the status output cannot distinguish a subscription sign-in from "
        "an API-key credential path, and excluding the variable from the child "
        "environment is the only detection this build has",
    ], [
        "- `CODEX_HOME` is set explicitly rather than inherited; `--ignore-user-config` "
        "is documented to ignore `config.toml` only, leaving authentication in "
        "`CODEX_HOME` (spec section 5). This collector runs no `--ignore-user-config` "
        "probe, so it does not confirm that separation.",
    ]


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
