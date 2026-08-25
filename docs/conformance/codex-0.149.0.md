# Conformance evidence — codex codex-cli 0.149.0

Collected 2026-08-24 on Windows, in the child environment built by `whole_life.runtime.childenv.build_child_env`.

Redacted by construction: no environment values, no credentials, no full authentication payload, no raw stderr, no account identifiers.

## Observations

Every line below was computed from a command this run executed.

- `codex --version` exit `0`, stdout `codex-cli 0.149.0`
- `codex login status` exit `0`, stdout empty: **True**
- decision arrives on stderr and equals the pinned `Logged in using ChatGPT`: **True**
- with `OPENAI_API_KEY` present, exit code identical: **True**
- with `OPENAI_API_KEY` present, stdout identical: **True**
- with `OPENAI_API_KEY` present, stderr identical: **True**
- therefore the status output cannot distinguish a subscription sign-in from an API-key credential path, and excluding the variable from the child environment is the only detection this build has
- started from a directory that is not a git repository, with no `--skip-git-repo-check`: exit `1`, first stderr line `Not inside a trusted directory and --skip-git-repo-check was not specified.`
- therefore this build **refuses** a turn whose working directory is untrusted, before any model request — so a Broker that lets the child inherit its own directory decides whether every turn on that machine can start at all (issue #33)

## Not measured by this script

Carried from the specification, not observed here. The collection date above does not apply to this section: these claims are not measured by this script, so re-running it for a new version does not re-verify them.

- `CODEX_HOME` is set explicitly rather than inherited; `--ignore-user-config` is documented to ignore `config.toml` only, leaving authentication in `CODEX_HOME` (spec section 5). This collector runs no `--ignore-user-config` probe, so it does not confirm that separation.

## Collection environment

- forbidden variables present in the *parent* environment at collection time: `none`
- the child environment is built from an inherit allowlist and refuses any forbidden variable, so the above could not reach the command either way

Reproduce with `python scripts/collect-conformance-evidence.py codex`.
