# Recorded provider turns

Real `claude` turns captured once and replayed by the test suite. They are evidence,
not fixtures: the delegation limits reported in
[`docs/conformance/claude-2.1.240.md`](../../docs/conformance/claude-2.1.240.md) are
`cooperative` rather than `hard` because these recordings show the provider refusing
nothing, and the normalizer is tested by replaying them rather than by hand-written
JSON that could agree with the code and disagree with the provider.

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
