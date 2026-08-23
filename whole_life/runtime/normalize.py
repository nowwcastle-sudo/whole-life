"""Provider output to canonical events. Normative source: spec section 7.

Three rules run through this module.

**Nothing the provider wrote is carried across.** A normalized event holds
allowlisted fields with checked types — never model prose, error text, worker
reasoning or raw payloads. That is why an unknown event cannot be handled by
forwarding it "just in case": there is no channel to forward it through.

**An unrecognised shape is a failure, not a shrug.** `schema 불일치는 turn
failure다`. Best-effort parsing means a turn continuing on a stream this build
does not understand, which is the fail-open direction.

**Required fields are checked even when their values are never read.** The point
is not to use the data; it is that a version whose schema has drifted must not
pass silently. Checking presence and type, and rejecting values outside a closed
enum, makes drift visible while storing nothing.

Sources, both pinned rather than "latest":

- Codex: the `rust-v0.149.0` tag of `openai/codex`, `sdk/typescript/src/events.ts`
  and `items.ts` — the exact version in the allowlist. One approved live turn
  against the installed build confirmed the envelope and the item discriminator
  (`type`, not the `item_type` an earlier version of this file invented and then
  tested against).
- Claude: the Anthropic Claude Code / Agent SDK stream-json schema and the
  documented tool-use flow, where `tool_use` in an assistant message is answered
  by `tool_result` in a *user* message.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from whole_life.runtime.outcome import TerminalEvent
from whole_life.runtime.streams import StreamFailure

#: Spec section 7, `정확히 8개`. Nothing is added for native workers: they are
#: `runtime.activity.*` with an `activity_kind`.
CANONICAL_EVENT_TYPES = (
    "session.started",
    "turn.started",
    "runtime.activity.started",
    "runtime.activity.finished",
    "message.committed",
    "turn.completed",
    "turn.failed",
    "artifact.committed",
)

#: `Usage`, verbatim from the pinned SDK. Every field is a number, and `bool` is
#: refused for them: Python makes `bool` a subclass of `int`, so `True` would
#: otherwise pass as a token count.
CODEX_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)

COMMAND_EXECUTION_STATUS = frozenset({"in_progress", "completed", "failed"})
PATCH_APPLY_STATUS = frozenset({"completed", "failed"})
PATCH_CHANGE_KIND = frozenset({"add", "delete", "update"})
MCP_TOOL_CALL_STATUS = frozenset({"in_progress", "completed", "failed"})

#: What each Codex item subtype means in section 7's vocabulary, decided one by
#: one. There is no generic fallback: mapping "everything else" to a tool
#: activity would file provider-internal bookkeeping as observed tool use, which
#: is a canonical claim nobody checked.
#:
#: `None` means the subtype is recognised and carries no canonical event — a real
#: answer, not a gap. Section 7 has eight event types and no obligation to place
#: every provider item into one.
#:
#: The live 0.149.0 turn settles `error`: it appeared in a stream that completed
#: successfully with exit 0. Raising it to a turn failure, or recording it as
#: tool activity, would contradict a measurement we hold.
CODEX_ITEM_ACTIVITY = "activity"
CODEX_ITEM_MESSAGE = "message"

CODEX_ITEM_MAPPING = {
    "agent_message": CODEX_ITEM_MESSAGE,
    "reasoning": None,
    "todo_list": None,
    "error": None,
    "command_execution": CODEX_ITEM_ACTIVITY,
    "file_change": CODEX_ITEM_ACTIVITY,
    "mcp_tool_call": CODEX_ITEM_ACTIVITY,
    "web_search": CODEX_ITEM_ACTIVITY,
}

#: Content block types an assistant message may carry. `tool_result` is
#: deliberately absent: the official role boundary puts it in a *user* message,
#: and accepting it here would bless a shape the provider does not produce.
CLAUDE_ASSISTANT_BLOCKS = frozenset(
    {"text", "thinking", "redacted_thinking", "tool_use"}
)

#: The documented `system` and `result` subtypes. Closed rather than "any
#: string": a subtype this build has not evaluated is drift, and accepting it
#: would make the claim "pinned required shape, checked in full" false.
#: `task_started` is the provider announcing a native worker. It is listed
#: here because a live 2.1.240 turn writes it, not because documentation
#: mentions it.
CLAUDE_SYSTEM_SUBTYPES = frozenset(
    {"init", "task_started", "task_updated", "task_progress", "task_notification"}
)
#: Spec line 118's one named value. What the provider publishes about a worker
#: is its lifecycle id, its status and a summary — never the worker's prompt,
#: reasoning or tool state — so a summary is the whole of what can be seen. The
#: specification names no second value and this module does not invent one.
WORKER_OBSERVABILITY = "summary_only"

CLAUDE_RESULT_SUBTYPES = frozenset(
    {"success", "error_max_turns", "error_during_execution"}
)


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """One canonical event, plus what it implies about how the turn ended.

    `terminal` is separate from `kind` because the outcome resolver asks a
    different question than the event log does — and `unknown_outcome` is a run
    state rather than an event type.
    """

    run_id: str
    kind: str
    occurred_at: datetime
    data: Mapping[str, object]
    terminal: TerminalEvent = TerminalEvent.NONE


def _reject_non_json_number(value):
    """`NaN`, `Infinity` and `-Infinity` are Python extensions, not JSON.

    `json.loads` accepts them by default, so a stream carrying them would parse
    here and fail later — or arrive at a consumer as a float no JSON reader on
    the other side can represent.
    """
    raise StreamFailure("StdoutNotJsonObject")


def _parse(line: str) -> dict:
    try:
        parsed = json.loads(line, parse_constant=_reject_non_json_number)
    except StreamFailure:
        raise
    except (json.JSONDecodeError, TypeError, ValueError):
        raise StreamFailure("StdoutNotJsonObject") from None

    if not isinstance(parsed, dict):
        raise StreamFailure("StdoutNotJsonObject")
    return parsed


def _typed(value, expected):
    """Return `value` only if it is exactly `expected`, else refuse.

    Written as an explicit check because Python's coercions are the trap:
    `bool("false")` is True and `len("not-a-list")` is 10, so a chained `.get()`
    can turn a schema mismatch into a plausible canonical event rather than an
    error. Duck typing is the wrong instinct at a boundary whose job is to be
    able to say no.
    """
    if not isinstance(value, expected):
        raise StreamFailure("StdoutSchemaMismatch")
    return value


def _number(value):
    """A JSON number. `bool` is refused despite subclassing `int`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StreamFailure("StdoutSchemaMismatch")
    return value


def _enum(value, allowed):
    """A closed enum. An unlisted value is drift, not a variation."""
    if _typed(value, str) not in allowed:
        raise StreamFailure("StdoutSchemaMismatch")
    return value


def _require(mapping, field, checker):
    if field not in mapping:
        raise StreamFailure("StdoutSchemaMismatch")
    return checker(mapping[field])


def _event(run_id, kind, terminal=TerminalEvent.NONE, **data) -> NormalizedEvent:
    return NormalizedEvent(
        run_id=run_id,
        kind=kind,
        occurred_at=datetime.now(UTC),
        data=dict(data),
        terminal=terminal,
    )


def _worker_event(parsed: dict, run_id: str, kind: str, **extra) -> NormalizedEvent:
    """One native-worker lifecycle event, carrying only spec line 247's fields.

    Built here rather than at each call site so that the payload allowlist is
    one statement. The provider's line also holds the worker's prompt, its
    description and its answer; none of them is read.
    """
    task_id = _require(parsed, "task_id", lambda v: _typed(v, str))
    return _event(
        run_id,
        kind,
        activity_kind="native_worker",
        native_child_id=task_id,
        observability=WORKER_OBSERVABILITY,
        **extra,
    )


def _validate_codex_item(item: dict, item_type: str) -> None:
    """Every required field of one item subtype, per the pinned SDK.

    None of these values is stored. They are checked so a build whose schema has
    moved cannot slip past as a green run.
    """
    _require(item, "id", lambda v: _typed(v, str))

    if item_type in ("agent_message", "reasoning"):
        _require(item, "text", lambda v: _typed(v, str))
    elif item_type == "error":
        _require(item, "message", lambda v: _typed(v, str))
    elif item_type == "web_search":
        _require(item, "query", lambda v: _typed(v, str))
    elif item_type == "todo_list":
        for entry in _require(item, "items", lambda v: _typed(v, list)):
            _typed(entry, dict)
            _require(entry, "text", lambda v: _typed(v, str))
            _require(entry, "completed", lambda v: _typed(v, bool))
    elif item_type == "command_execution":
        _require(item, "command", lambda v: _typed(v, str))
        _require(item, "aggregated_output", lambda v: _typed(v, str))
        _require(item, "status", lambda v: _enum(v, COMMAND_EXECUTION_STATUS))
        if "exit_code" in item:
            _number(item["exit_code"])
    elif item_type == "file_change":
        for change in _require(item, "changes", lambda v: _typed(v, list)):
            _typed(change, dict)
            _require(change, "path", lambda v: _typed(v, str))
            _require(change, "kind", lambda v: _enum(v, PATCH_CHANGE_KIND))
        _require(item, "status", lambda v: _enum(v, PATCH_APPLY_STATUS))
    elif item_type == "mcp_tool_call":
        _require(item, "server", lambda v: _typed(v, str))
        _require(item, "tool", lambda v: _typed(v, str))
        if "arguments" not in item:
            raise StreamFailure("StdoutSchemaMismatch")
        _require(item, "status", lambda v: _enum(v, MCP_TOOL_CALL_STATUS))
        # Optional in the pinned definition, but shape-checked when present:
        # "we do not store it" is not a reason to let drift through.
        if "result" in item and item["result"] is not None:
            result = _typed(item["result"], dict)
            _require(result, "content", lambda v: _typed(v, list))
            if "structured_content" not in result:
                raise StreamFailure("StdoutSchemaMismatch")
        if "error" in item and item["error"] is not None:
            _require(_typed(item["error"], dict), "message", lambda v: _typed(v, str))


def normalize_codex_line(line: str, *, run_id: str) -> list[NormalizedEvent]:
    """One Codex `--json` JSONL line, or `None` when it carries no canonical event.

    `thread.started` is deliberately not mapped to `session.started`. They sound
    alike and are not the same: section 7 requires a `session.started` payload
    carrying the immutable participant roster and budget profile, which is Whole
    Life's own concept and nothing a provider can supply. Forcing the provider
    envelope into that slot would fabricate a roster.

    `item.updated` carries no canonical meaning in v0 either: section 7 has
    `runtime.activity.started` and `.finished` and no notion of an update in
    between. Recognised and skipped rather than invented into an event.
    """
    parsed = _parse(line)
    kind = _typed(parsed.get("type"), str)

    if kind == "thread.started":
        # Skipped, but still checked: an envelope missing its required field is
        # drift, and skipping it silently would hide exactly that.
        _require(parsed, "thread_id", lambda v: _typed(v, str))
        return []

    if kind == "turn.started":
        return [_event(run_id, "turn.started")]

    if kind == "turn.completed":
        usage = _require(parsed, "usage", lambda v: _typed(v, dict))
        for field in CODEX_USAGE_FIELDS:
            _require(usage, field, _number)
        return [_event(run_id, "turn.completed", terminal=TerminalEvent.COMPLETED)]

    if kind == "turn.failed":
        error = _require(parsed, "error", lambda v: _typed(v, dict))
        # Checked for shape, never read for content.
        _require(error, "message", lambda v: _typed(v, str))
        return [_event(run_id, "turn.failed", terminal=TerminalEvent.FAILED)]

    if kind == "error":
        _require(parsed, "message", lambda v: _typed(v, str))
        return [_event(run_id, "turn.failed", terminal=TerminalEvent.FAILED)]

    if kind in ("item.started", "item.updated", "item.completed"):
        item = _require(parsed, "item", lambda v: _typed(v, dict))
        item_type = _require(item, "type", lambda v: _typed(v, str))
        if item_type not in CODEX_ITEM_MAPPING:
            raise StreamFailure("UnknownProviderEvent")

        _validate_codex_item(item, item_type)

        if kind == "item.updated":
            return []

        meaning = CODEX_ITEM_MAPPING[item_type]
        if meaning is None:
            return []

        if meaning is CODEX_ITEM_MESSAGE:
            # A message exists once it is complete. Its beginning is not a
            # committed message, and there is no canonical event for "started
            # writing".
            if kind == "item.completed":
                return [_event(run_id, "message.committed")]
            return []

        # `activity_kind=native_worker` is reserved for a provider that actually
        # announces worker spawn and finish. Codex 0.149.0 does not, and an MCP
        # tool call is a tool call — calling it a delegated worker would invent
        # a delegation the stream never reported.
        return [
            _event(
                run_id,
                "runtime.activity.started"
                if kind == "item.started"
                else "runtime.activity.finished",
                activity_kind="tool_use",
            )
        ]

    raise StreamFailure("UnknownProviderEvent")


def _validate_claude_block(block: dict) -> str:
    block_type = _require(block, "type", lambda v: _typed(v, str))
    if block_type not in CLAUDE_ASSISTANT_BLOCKS:
        raise StreamFailure("UnknownProviderEvent")

    if block_type == "text":
        _require(block, "text", lambda v: _typed(v, str))
    elif block_type == "thinking":
        _require(block, "thinking", lambda v: _typed(v, str))
    elif block_type == "redacted_thinking":
        _require(block, "data", lambda v: _typed(v, str))
    elif block_type == "tool_use":
        _require(block, "id", lambda v: _typed(v, str))
        _require(block, "name", lambda v: _typed(v, str))
        _require(block, "input", lambda v: _typed(v, dict))
    return block_type


def normalize_claude_line(line: str, *, run_id: str) -> list[NormalizedEvent]:
    """One Claude `stream-json` line, or `None` when it carries no canonical event.

    A normal tool turn is `assistant(tool_use)` → `user(tool_result)` →
    `assistant(text)` → `result`. The `user` message is a first-class part of
    that flow, and rejecting it — as an earlier version did — made every real
    tool turn a stream failure while the fixtures, which contained no tool use,
    stayed green. #14 grants `Agent,Read,Glob,Grep`, so tool turns are the
    ordinary case, not an exotic one.
    """
    parsed = _parse(line)
    kind = _typed(parsed.get("type"), str)

    if kind == "system":
        subtype = _require(
            parsed, "subtype", lambda v: _enum(v, CLAUDE_SYSTEM_SUBTYPES)
        )
        _require(parsed, "session_id", lambda v: _typed(v, str))
        if subtype in ("task_updated", "task_progress"):
            # Between the start and the finish. Section 7 has a started and a
            # finished event and no notion of an update in between — the same
            # answer already given to Codex's `item.updated`.
            return []

        if subtype == "task_started":
            return [_worker_event(parsed, run_id, "runtime.activity.started")]

        if subtype == "task_notification":
            # Checked as a string rather than against a closed set. Only
            # `completed` has been observed on 2.1.240; listing the failure
            # wordings this build uses would be inventing a schema, and a
            # worker that ended badly must not become a stream failure.
            status = _require(parsed, "status", lambda v: _typed(v, str))
            return [
                _worker_event(
                    parsed, run_id, "runtime.activity.finished", status=status
                )
            ]
        return [_event(run_id, "turn.started")]

    if kind == "assistant":
        message = _require(parsed, "message", lambda v: _typed(v, dict))
        blocks = _require(message, "content", lambda v: _typed(v, list))
        block_types = [_validate_claude_block(_typed(b, dict)) for b in blocks]

        # One line can carry both prose and a tool call, and the specification
        # sets no limit of one canonical event per provider line. Returning only
        # the activity — as an earlier version did — silently dropped the
        # message from the Journal. Both are emitted, in block order.
        events = []
        if "text" in block_types:
            events.append(
                _event(run_id, "message.committed", block_count=len(blocks))
            )
        if "tool_use" in block_types:
            events.append(
                _event(run_id, "runtime.activity.started", activity_kind="tool_use")
            )
        # Thinking alone commits nothing. Section 7 keeps reasoning out.
        return events

    if kind == "user":
        message = _require(parsed, "message", lambda v: _typed(v, dict))
        blocks = _require(message, "content", lambda v: _typed(v, list))
        carries_tool_result = False
        for block in blocks:
            _typed(block, dict)
            block_type = _require(block, "type", lambda v: _typed(v, str))
            if block_type == "text":
                # A live delegating turn writes a `user` line of prose: the
                # provider restating the delegated task. It is not the
                # participant's message and the worker's lifecycle is already
                # carried by the `task_*` lines, so it commits nothing.
                _require(block, "text", lambda v: _typed(v, str))
                continue
            if block_type != "tool_result":
                raise StreamFailure("UnknownProviderEvent")
            carries_tool_result = True
            _require(block, "tool_use_id", lambda v: _typed(v, str))
            # Only `tool_use_id` is required. The official ToolResultBlock makes
            # `content` `str | list[dict] | None` and `is_error` `bool | None`,
            # so demanding them would be a stricter contract than the source
            # states — inventing a requirement is the same error as inventing a
            # field. Checked when present, never read: the body is raw tool
            # output.
            content = block.get("content")
            if content is not None and not isinstance(content, (str, list)):
                raise StreamFailure("StdoutSchemaMismatch")
            if isinstance(content, list):
                for entry in content:
                    _require(_typed(entry, dict), "type", lambda v: _typed(v, str))
            if block.get("is_error") is not None:
                _typed(block["is_error"], bool)

        if not carries_tool_result:
            return []
        return [_event(run_id, "runtime.activity.finished", activity_kind="tool_use")]

    if kind == "result":
        _require(parsed, "subtype", lambda v: _enum(v, CLAUDE_RESULT_SUBTYPES))
        failed = _require(parsed, "is_error", lambda v: _typed(v, bool))
        return [
            _event(
                run_id,
                "turn.failed" if failed else "turn.completed",
                terminal=TerminalEvent.FAILED if failed else TerminalEvent.COMPLETED,
            )
        ]

    raise StreamFailure("UnknownProviderEvent")
