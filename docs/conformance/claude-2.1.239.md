# Conformance evidence — claude 2.1.239 (Claude Code)

Collected 2026-08-22 on Windows, in the child environment built by `whole_life.runtime.childenv.build_child_env`.

Redacted by construction: no environment values, no credentials, no full authentication payload, no raw stderr, no account identifiers.

## Observations

- `claude --version` exit `0`, stdout `2.1.239 (Claude Code)`
- `claude auth status --json` exit `0`, parsed as a JSON object
- field names present: `apiProvider`, `authMethod`, `email`, `loggedIn`, `orgId`, `orgName`, `subscriptionType`
- field set equals the pinned set: **True**
- `loggedIn` is exactly `True`: **True**
- `authMethod == "claude.ai"`: **True**
- `apiProvider == "firstParty"`: **True**
- `subscriptionType` is a non-empty string: **True**
- `email`, `orgId` and `orgName` were present in the output and were not read
- bare mode default: `-p` does not imply bare mode on this version; bare mode is reachable only via `--bare` or `CLAUDE_CODE_SIMPLE` (spec section 5). The gate that enforces this arrives with #13.

## Collection environment

- forbidden variables present in the *parent* environment at collection time: `none`
- the child environment is built from an inherit allowlist and refuses any forbidden variable, so the above could not reach the command either way

Reproduce with `python scripts/collect-conformance-evidence.py claude`.
