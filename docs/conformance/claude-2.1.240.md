# Conformance evidence — claude 2.1.240 (Claude Code)

Collected 2026-08-23 on Windows, in the child environment built by `whole_life.runtime.childenv.build_child_env`.

Redacted by construction: no environment values, no credentials, no full authentication payload, no raw stderr, no account identifiers.

## Observations

Every line below was computed from a command this run executed.

- `claude --version` exit `0`, stdout `2.1.240 (Claude Code)`
- `claude auth status --json` exit `0`, parsed as a JSON object
- field names present: `apiProvider`, `authMethod`, `email`, `loggedIn`, `orgId`, `orgName`, `subscriptionType`
- field set equals the pinned set: **True**
- `loggedIn` is exactly `True`: **True**
- `authMethod == "claude.ai"`: **True**
- `apiProvider == "firstParty"`: **True**
- `subscriptionType` is a non-empty string: **True**
- bare mode default is **False**, measured: a minimal `-p` turn exited `0` in a child environment holding no API-key variable. Bare mode reads `ANTHROPIC_API_KEY` or a settings `apiKeyHelper` and never OAuth, so a turn that completes there was not bare (spec section 5, procedure T-2).
- executable SHA-256 before and after: `f7ee87c58d315bedbd38fc8923ce7b955dbbab2afe50d9d15c059463a70869da`

## Not measured by this script

Carried from the specification, not observed here. The collection date above does not apply to this section: these claims are not measured by this script, so re-running it for a new version does not re-verify them.

- `email`, `orgId` and `orgName` are never read by the parser. That is a property of the code, not something this run observed.

## Collection environment

- forbidden variables present in the *parent* environment at collection time: `none`
- the child environment is built from an inherit allowlist and refuses any forbidden variable, so the above could not reach the command either way

Reproduce with `python scripts/collect-conformance-evidence.py claude`.
