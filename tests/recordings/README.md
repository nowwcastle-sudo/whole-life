# Recorded provider turns

Real provider turns captured once and replayed by the test suite. They are evidence,
not fixtures.

## `codex-0.149.0-agents-enabled-turn.jsonl` — how it was captured

Recorded 2026-08-24 for ticket #35, which required the Codex enforcement row to come
from an observation rather than from reading the stream schema.

- **Argument vector:** `CODEX_TURN_ARGS` verbatim — `exec --json --sandbox read-only
  --ignore-user-config --ignore-rules -c agents.enabled=true
  -c agents.max_concurrent_threads_per_session=3`. The subagent workflow the ticket
  asks for is already in the production vector; nothing was added for the recording.
- **Environment:** `build_child_env(os.environ, extra={"CODEX_HOME": …})`, the same
  sanitized child environment the adapter builds — 15 variables.
- **Working directory:** inside a git worktree, so the build's trusted-directory check
  passed without `--skip-git-repo-check` (that flag lands with #33).
- **Prompt:** asked for three independent subagents launched concurrently, one per
  question, and told the model not to answer them itself.
- **Result:** exit `0`. Stderr was 29 bytes and was never written to this file.

**Redacted by value, not by silence.** One identifier appeared — the thread UUID, as
`thread_id` on `thread.started` and `sender_thread_id` on four items, five occurrences
of one value. All five were replaced with `00000000-0000-0000-0000-000000000000`, the
placeholder the Claude recordings use. A field-by-field walk of the whole stream found
nothing else to redact: no path, no account identifier, no authentication output. What
remains is item ids, tool names, statuses, token counts, and the model's own prose.

**Reproducing it costs a billed turn.** There is no script here that does it; the steps
above are the record. The redaction is asserted by
`tests/runtime/test_codex_delegation_measurement.py`, which also pins what the stream
showed — including that the collaboration call carried no receivers and no agent
states, which is why every Codex enforcement axis stays `unsupported`. The delegation limits live in the normative table of
[`docs/spec/whole-life-v0.md`](../../docs/spec/whole-life-v0.md), which reads:

```
Claude는 concurrency `cooperative`·total starts `cooperative`·depth `cooperative`다.
depth는 2026-08-23 실측으로 정정했다
```

The measurement it was corrected by is in this directory.
`claude-2.1.240-nested-delegation-turn.jsonl` runs a worker that launches another
worker, and reports:

```json
"max_depth": 2, "completed": 2, "failed": 0,
"refused": {"budget": 0, "concurrency_limit": 0, "depth_limit": 0}
```

Nothing was refused two levels deep, so depth is not provider-enforced — which is why
the table says `cooperative` and not `hard`. The normalizer is tested by replaying
these turns rather than by hand-written JSON that could agree with the code and
disagree with the provider.

The conformance documents under `docs/conformance/` do not carry these limits and are
not meant to. `#12` AC7 scopes that evidence to what came *"from the sanitized child
environment"*, meaning what `scripts/collect-conformance-evidence.py` executed itself.
Its only turn is the bare-mode probe:

```python
BARE_PROBE_ARGS = (
    "-p",
    "--no-session-persistence",
    "--tools",
    "",
```

With the tool list emptied there is no `Agent` tool, so that turn cannot delegate and
therefore cannot observe a delegation limit.

An earlier version of this paragraph cited that folder as the place the limits are
reported. It was wrong, and the link resolved anyway: `scripts/check-markdown-links.sh`
tests `[ ! -e "$resolved" ]`, so it proves a path exists and says nothing about whether
the sentence around it is true. Quoting the source, as above, is what makes a citation
checkable — a paraphrase cannot be grepped.

## What is redacted, and what is deliberately not

Measured across all three files:

| field | value in the recordings | redacted |
|---|---|---|
| `session_id` | `00000000-0000-0000-0000-000000000000` | yes |
| `uuid` | `00000000-0000-0000-0000-000000000000` | yes |
| `request_id` | `req_redacted` | yes |
| `parent_tool_use_id` | 2 distinct provider values | **no** |
| `task_id` | 3 distinct provider values | **no** |

The last two are left as the provider issued them. Two reasons, in order of weight.

### 1. Redacting them would kill a live assertion

`tests/runtime/test_normalize.py::test_the_worker_identity_comes_from_the_provider`
reads the `task_id` values out of the recording and asserts that the
`native_child_id` on each emitted event is *that* value. Spec line 118 forbids the
broker from inventing a worker identity, and this is the check that the broker did
not.

That assertion only has force while the values are distinct. Replace them with one
constant and "we carried the provider's identifier" and "we substituted our own"
produce identical output. This is measured, not argued — four combinations:

| recording | normalizer | result |
|---|---|---|
| as recorded | carries `task_id` | pass |
| as recorded | hardcoded constant instead | **fail** — the check is alive |
| `task_id` → one constant | carries `task_id` | pass |
| `task_id` → one constant | hardcoded constant instead | **pass** — the check is dead |

Reproduce by replacing `"task_id": "<hex>"` with a single constant throughout
`claude-2.1.240-delegating-turn.jsonl`, then applying
`native_child_id=task_id` → `native_child_id="<that constant>"` in
`whole_life/runtime/normalize.py`, and running that one test across the four
combinations. Restore both afterwards and confirm by control string, not by a green
suite: `grep -c task_redacted` on both files should be `0`.

### 2. They are not credentials and not account identifiers

A tool-use id and a task id are per-turn handles scoped to a session whose id is
already zeroed here. They name nothing about the operator, carry no entitlement, and
cannot be replayed against the provider. The redaction rule that governs this
directory — no environment values, no credentials, no authentication payload, no raw
stderr, no `email` / `orgId` / `orgName` — does not reach them.

## If you are about to propose redacting them

This has been proposed and withdrawn once, for the reason in §1. Redaction is still
the right instinct for anything that identifies a person or grants access; it is the
wrong move here specifically because the evidence value *is* the distinctness. If the
assertion in §1 is ever rewritten so that it no longer depends on these values, this
paragraph stops applying — say so in the same change.
