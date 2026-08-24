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
