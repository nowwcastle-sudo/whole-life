# Conformance evidence — codex codex-cli 0.149.0

Collected 2026-08-22 on Windows, in the child environment built by `whole_life.runtime.childenv.build_child_env`.

Redacted by construction: no environment values, no credentials, no full authentication payload, no raw stderr, no account identifiers.

## Observations

- `codex --version` exit `0`, stdout `codex-cli 0.149.0`
- `codex login status` exit `0`, stdout empty: **True**
- decision arrives on stderr and equals the pinned `Logged in using ChatGPT`: **True**
- with `OPENAI_API_KEY` present, exit code identical: **True**
- with `OPENAI_API_KEY` present, stdout identical: **True**
- with `OPENAI_API_KEY` present, stderr identical: **True**
- therefore the status output cannot distinguish a subscription sign-in from an API-key credential path, and excluding the variable from the child environment is the only detection this build has
- `CODEX_HOME` is set explicitly rather than inherited; `--ignore-user-config` ignores `config.toml` only and authentication is still read from `CODEX_HOME`

## Collection environment

- forbidden variables present in the *parent* environment at collection time: `none`
- the child environment is built from an inherit allowlist and refuses any forbidden variable, so the above could not reach the command either way

Reproduce with `python scripts/collect-conformance-evidence.py codex`.
