# Safety gates

## Git guardrails — push is blocked

Claude Code sessions in this repo run a PreToolUse hook
(`.claude/hooks/block-dangerous-git.sh`, registered in `.claude/settings.json`,
project scope only) that blocks `git push` (all variants), `git reset --hard`,
`git clean -f`, `git branch -D`, and `git checkout .` / `git restore .`.

> push는 차단됨 — 완결자가 명령을 준비하고 대표가 실행한다.

(Push is blocked — the Closer prepares the exact commands, copy-paste ready,
and the owner runs them.) This is deliberate: a human stands in front of
anything that leaves this machine.

`.claude/settings.local.json` and user-scoped MCP/plugin configuration are
owner-managed and outside this repository. Never edit them, and never assume a
particular local hook or memory provider is present. The shared-memory behavior
agents may rely on is defined in `AGENTS.md`; current repository and GitHub state
remain the evidence for every safety gate.

## Secret scan — reviewer (REV) gate

Before every commit, and in every review, run this against the staged content
(Git Bash):

```bash
git grep --cached -nE "(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-|-----BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|secret|token|password)[[:space:]]*[=:][[:space:]]*['\"][A-Za-z0-9])"
```

Reading the result:

- **Exit code 1 with no output = clean.** No staged line matches a token, API
  key, or private-key pattern.
- **Exit code 0 = it found something. Do not commit.** The matching lines are
  printed with path and line number; remove the secret and re-stage.

Verified in this repository on 2026-08-22 with all bootstrap files staged: the
command printed nothing and exited 1.

## Dependency audit — reviewer (REV) gate

This repository has no dependency manifest today (no `requirements.txt`, no
`pyproject.toml`) and the code is standard-library only, so there is nothing to
audit yet. The gate fires the moment the first manifest lands: the commit that
adds it — and every commit that changes it afterwards — must be audited before
it merges. Until then, "no manifest" is the clean state; it is not a pass, it
is the gate not yet firing.

When it fires, run (Git Bash):

```bash
python -m pip install pip-audit && python -m pip_audit -r requirements.txt --strict
```

(If the manifest is a `pyproject.toml` instead, install the project into a
fresh virtual environment and run `python -m pip_audit` there — pip-audit
audits a requirements file directly but audits a `pyproject.toml` through the
installed environment.)

Reading the result:

- **Exit code 0 = clean.** No dependency in the manifest matches a known
  vulnerability in the PyPI advisory or OSV databases. Note this is the
  opposite convention from the secret scan above, where exit 1 is clean —
  do not carry one gate's reading over to the other.
- **Exit code 1 = it found a known vulnerability. Do not merge.** The findings
  are printed with package, version and advisory ID; upgrade or replace the
  dependency and re-run. An exit 1 with no findings printed is the install leg
  failing — pip also exits 1 — which is the "did not run" case below, not a
  verdict: the verdict is the advisory listing, never the number alone.
- **Any other nonzero exit = the audit did not run** (network failure, broken
  manifest, missing tool). That is not clean — a gate that never fired proves
  nothing. Fix the audit and re-run.

pip-audit is deliberately not vendored into this repository — it would itself
be the first dependency. The install line above is part of the gate command so
the auditor does not depend on ambient tooling.
