"""Provider output normalized to the eight canonical events. Spec section 7.

`provider 고유 event는 adapter 내부에서 이 집합으로 정규화한다. 알 수 없는 provider
event는 raw payload를 저장하지 않고 allowlist metadata를 포함한 diagnostic으로
처리한다.`

The rule that shapes every test here: nothing the provider wrote is carried
through. A normalized event holds allowlisted fields with checked types. Raw
payloads, worker reasoning and stderr do not appear in one, which means an
unknown event cannot be handled by passing it along "just in case".
"""

import json
import unittest

from whole_life.runtime.normalize import (
    CANONICAL_EVENT_TYPES,
    CODEX_ITEM_MAPPING,
    normalize_claude_line,
    normalize_codex_line,
)
from whole_life.runtime.outcome import TerminalEvent
from whole_life.runtime.streams import StreamFailure

RUN_ID = "run-7"


class CanonicalSetTests(unittest.TestCase):
    def test_there_are_exactly_eight_event_types(self):
        """Spec section 7 says `정확히 8개`, and names them."""
        self.assertEqual(
            {
                "session.started",
                "turn.started",
                "runtime.activity.started",
                "runtime.activity.finished",
                "message.committed",
                "turn.completed",
                "turn.failed",
                "artifact.committed",
            },
            set(CANONICAL_EVENT_TYPES),
        )


class ClaudeNormalizationTests(unittest.TestCase):
    def test_an_assistant_message_becomes_message_committed(self):
        line = '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}'

        (event,) = normalize_claude_line(line, run_id=RUN_ID)

        self.assertEqual("message.committed", event.kind)
        self.assertEqual(RUN_ID, event.run_id)

    def test_a_successful_result_is_the_terminal_completion(self):
        line = '{"type":"result","subtype":"success","is_error":false}'

        (event,) = normalize_claude_line(line, run_id=RUN_ID)

        self.assertEqual("turn.completed", event.kind)
        self.assertIs(TerminalEvent.COMPLETED, event.terminal)

    def test_an_error_result_is_the_terminal_failure(self):
        line = '{"type":"result","subtype":"error_during_execution","is_error":true}'

        (event,) = normalize_claude_line(line, run_id=RUN_ID)

        self.assertEqual("turn.failed", event.kind)
        self.assertIs(TerminalEvent.FAILED, event.terminal)

    def test_a_tool_use_becomes_a_runtime_activity(self):
        line = '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"tu_1","name":"Read","input":{}}]}}'

        (event,) = normalize_claude_line(line, run_id=RUN_ID)

        self.assertEqual("runtime.activity.started", event.kind)

    def test_no_provider_text_survives_normalization(self):
        sentinel = "SENTINEL-MODEL-PROSE"
        line = (
            '{"type":"assistant","message":{"content":'
            f'[{{"type":"text","text":"{sentinel}"}}]}}}}'
        )

        (event,) = normalize_claude_line(line, run_id=RUN_ID)

        self.assertNotIn(sentinel, f"{event.data}{event!r}")


class ClaudeToolTurnTests(unittest.TestCase):
    """The ordinary Claude turn, which #14 makes ordinary by granting tools.

    `assistant(tool_use)` -> `user(tool_result)` -> `assistant(text)` ->
    `result`. An earlier version rejected the `user` line outright, so every
    real tool turn was a stream failure while the fixtures — which had no tool
    use in them — stayed green.
    """

    TOOL_USE = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","id":"tu_1","name":"Read","input":{"path":"a"}}]}}'
    )
    TOOL_RESULT = (
        '{"type":"user","message":{"content":'
        '[{"type":"tool_result","tool_use_id":"tu_1","content":"file body"}]}}'
    )
    TEXT = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"done"}]}}'
    )
    RESULT = '{"type":"result","subtype":"success","is_error":false}'

    def kinds(self, line):
        return [e.kind for e in normalize_claude_line(line, run_id=RUN_ID)]

    def test_the_whole_tool_turn_normalizes(self):
        self.assertEqual(["runtime.activity.started"], self.kinds(self.TOOL_USE))
        self.assertEqual(["runtime.activity.finished"], self.kinds(self.TOOL_RESULT))
        self.assertEqual(["message.committed"], self.kinds(self.TEXT))
        self.assertEqual(["turn.completed"], self.kinds(self.RESULT))

    def test_a_tool_result_body_is_never_carried(self):
        sentinel = "SENTINEL-TOOL-OUTPUT"
        line = (
            '{"type":"user","message":{"content":'
            f'[{{"type":"tool_result","tool_use_id":"tu_1","content":"{sentinel}"}}]}}}}'
        )

        events = normalize_claude_line(line, run_id=RUN_ID)

        self.assertNotIn(sentinel, str([e.data for e in events]))

    def test_a_tool_result_in_an_assistant_message_is_refused(self):
        """The official role boundary puts tool_result on the user message."""
        line = (
            '{"type":"assistant","message":{"content":'
            '[{"type":"tool_result","tool_use_id":"tu_1"}]}}'
        )

        with self.assertRaises(StreamFailure):
            normalize_claude_line(line, run_id=RUN_ID)

    def test_a_mixed_block_line_emits_both_events(self):
        """Prose and a tool call in one line are two canonical facts.

        The specification sets no limit of one event per provider line, so
        returning only the activity — as an earlier version did — silently
        dropped the message from the Journal.
        """
        line = (
            '{"type":"assistant","message":{"content":['
            '{"type":"text","text":"calling a tool"},'
            '{"type":"tool_use","id":"tu_1","name":"Read","input":{}}]}}'
        )

        self.assertEqual(
            ["message.committed", "runtime.activity.started"], self.kinds(line)
        )

    def test_thinking_alone_commits_nothing(self):
        line = (
            '{"type":"assistant","message":{"content":'
            '[{"type":"thinking","thinking":"SENTINEL-REASONING"}]}}'
        )

        self.assertEqual([], normalize_claude_line(line, run_id=RUN_ID))


class CodexNormalizationTests(unittest.TestCase):
    def test_an_agent_message_becomes_message_committed(self):
        line = '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"hi"}}'

        (event,) = normalize_codex_line(line, run_id=RUN_ID)

        self.assertEqual("message.committed", event.kind)

    def test_a_completed_turn_is_terminal_success(self):
        line = '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}'

        (event,) = normalize_codex_line(line, run_id=RUN_ID)

        self.assertIs(TerminalEvent.COMPLETED, event.terminal)

    def test_a_failed_turn_is_terminal_failure(self):
        line = '{"type":"turn.failed","error":{"message":"boom"}}'

        (event,) = normalize_codex_line(line, run_id=RUN_ID)

        self.assertIs(TerminalEvent.FAILED, event.terminal)

    def test_no_error_text_survives_normalization(self):
        sentinel = "SENTINEL-ERROR-DETAIL"
        line = f'{{"type":"turn.failed","error":{{"message":"{sentinel}"}}}}'

        (event,) = normalize_codex_line(line, run_id=RUN_ID)

        self.assertNotIn(sentinel, f"{event.data}{event!r}")

    def test_a_tool_call_is_an_activity_not_a_new_event_type(self):
        """`별도 event type을 늘리지 않는다` — tool use reuses runtime.activity.*.

        Recorded as `tool_use`, not `native_worker`. An MCP tool call is a tool
        call; `native_worker` is reserved for a provider that actually announces
        worker spawn and finish, and Codex 0.149.0 announces no such thing.
        Labelling it otherwise would invent a delegation the stream never
        reported.
        """
        line = '{"type":"item.started","item":{"id":"item_1","type":"mcp_tool_call","server":"s","tool":"t","arguments":{},"status":"completed"}}'

        (event,) = normalize_codex_line(line, run_id=RUN_ID)

        self.assertEqual("runtime.activity.started", event.kind)
        self.assertEqual("tool_use", event.data["activity_kind"])


class CodexSubtypeMappingTests(unittest.TestCase):
    """One decision per subtype, spelled out here rather than imported.

    Taking the expectation from `CODEX_ITEM_MAPPING` would make this agree with
    whatever that table becomes — including a table that quietly reintroduces a
    catch-all. The mapping is a set of claims about meaning, so the claims are
    written down independently.

    The item bodies are the pinned `rust-v0.149.0` shapes, complete with every
    required field. Writing them out is the point: a fixture missing a required
    field would exercise the validator instead of the mapping.
    """

    VALID_ITEMS = {
        "agent_message": '{"id":"i","type":"agent_message","text":"hi"}',
        "reasoning": '{"id":"i","type":"reasoning","text":"why"}',
        "error": '{"id":"i","type":"error","message":"m"}',
        "web_search": '{"id":"i","type":"web_search","query":"q"}',
        "todo_list": (
            '{"id":"i","type":"todo_list","items":'
            '[{"text":"t","completed":false}]}'
        ),
        "command_execution": (
            '{"id":"i","type":"command_execution","command":"ls",'
            '"aggregated_output":"","status":"completed"}'
        ),
        "file_change": (
            '{"id":"i","type":"file_change","changes":'
            '[{"path":"p","kind":"add"}],"status":"completed"}'
        ),
        "mcp_tool_call": (
            '{"id":"i","type":"mcp_tool_call","server":"s","tool":"t",'
            '"arguments":{},"status":"completed"}'
        ),
    }

    def kinds(self, item_type):
        results = {}
        for envelope in ("item.started", "item.completed"):
            line = f'{{"type":"{envelope}","item":{self.VALID_ITEMS[item_type]}}}'
            events = normalize_codex_line(line, run_id=RUN_ID)
            results[envelope] = events[0].kind if events else None
        return results

    def test_every_mapped_subtype_has_a_valid_fixture(self):
        """A missing fixture would silently narrow the coverage below."""
        self.assertEqual(set(CODEX_ITEM_MAPPING), set(self.VALID_ITEMS))

    def test_an_agent_message_commits_only_when_complete(self):
        self.assertEqual(
            {"item.started": None, "item.completed": "message.committed"},
            self.kinds("agent_message"),
        )

    def test_provider_internal_subtypes_carry_no_canonical_event(self):
        """Reasoning above all: section 7 keeps worker reasoning out entirely."""
        for item_type in ("reasoning", "todo_list", "error"):
            with self.subTest(item_type=item_type):
                self.assertEqual(
                    {"item.started": None, "item.completed": None},
                    self.kinds(item_type),
                )

    def test_tool_subtypes_map_to_activity_lifecycle(self):
        for item_type in (
            "command_execution",
            "file_change",
            "mcp_tool_call",
            "web_search",
        ):
            with self.subTest(item_type=item_type):
                self.assertEqual(
                    {
                        "item.started": "runtime.activity.started",
                        "item.completed": "runtime.activity.finished",
                    },
                    self.kinds(item_type),
                )

    def test_nothing_is_ever_labelled_a_native_worker(self):
        """Codex 0.149.0 announces no worker spawn, so none may be recorded."""
        for item_type, body in self.VALID_ITEMS.items():
            for envelope in ("item.started", "item.completed"):
                with self.subTest(item_type=item_type, envelope=envelope):
                    line = f'{{"type":"{envelope}","item":{body}}}'
                    for event in normalize_codex_line(line, run_id=RUN_ID):
                        self.assertNotEqual(
                            "native_worker", event.data.get("activity_kind")
                        )

    def test_an_item_update_never_produces_an_event(self):
        for item_type, body in self.VALID_ITEMS.items():
            with self.subTest(item_type=item_type):
                line = f'{{"type":"item.updated","item":{body}}}'

                self.assertEqual([], normalize_codex_line(line, run_id=RUN_ID))


class FailClosedTests(unittest.TestCase):
    """`malformed JSON, truncated UTF-8, schema 불일치는 turn failure다.`"""

    def test_malformed_json_fails(self):
        for line in ('{"type":', "not json", "", "[]", "null"):
            with self.subTest(line=line):
                with self.assertRaises(StreamFailure) as caught:
                    normalize_claude_line(line, run_id=RUN_ID)

                self.assertEqual("StdoutNotJsonObject", caught.exception.diagnostic)

    def test_an_unknown_event_type_fails_rather_than_passing_through(self):
        line = '{"type":"some_future_event","payload":{"a":1}}'

        with self.assertRaises(StreamFailure) as caught:
            normalize_claude_line(line, run_id=RUN_ID)

        self.assertEqual("UnknownProviderEvent", caught.exception.diagnostic)

    def test_python_only_json_numbers_are_refused(self):
        """`json.loads` accepts NaN and Infinity. JSON does not have them.

        Letting them through would hand a consumer a float no JSON reader on
        the other side can represent.
        """
        for line in (
            '{"type":"turn.completed","usage":NaN}',
            '{"type":"turn.completed","usage":Infinity}',
            '{"type":"turn.completed","usage":-Infinity}',
        ):
            with self.subTest(line=line):
                with self.assertRaises(StreamFailure) as caught:
                    normalize_codex_line(line, run_id=RUN_ID)

                self.assertEqual("StdoutNotJsonObject", caught.exception.diagnostic)

    def test_a_missing_type_fails(self):
        with self.assertRaises(StreamFailure):
            normalize_codex_line('{"item":{"item_type":"agent_message"}}', run_id=RUN_ID)

    def test_the_failure_never_quotes_the_payload(self):
        sentinel = "SENTINEL-UNKNOWN-PAYLOAD"
        line = f'{{"type":"mystery","detail":"{sentinel}"}}'

        with self.assertRaises(StreamFailure) as caught:
            normalize_codex_line(line, run_id=RUN_ID)

        rendered = f"{caught.exception}{caught.exception!r}{caught.exception.args}"
        self.assertNotIn(sentinel, rendered)


class RequiredFieldTests(unittest.TestCase):
    """A subtype is only recognised when the fields its claim rests on are there.

    Knowing the discriminator is not enough. `message.committed` asserts the
    provider sent a message, and a block with no `type` asserts content this
    build can read — both are canonical claims, and a shape missing the field
    that backs them is schema drift, not a minor omission.

    Skipped envelopes are checked too: silently skipping a malformed
    `thread.started` would hide exactly the drift the skip is meant to tolerate.
    """

    CASES = (
        # (normalizer, line)
        ("claude", '{"type":"assistant","message":{"content":[{}]}}'),
        ("claude", '{"type":"assistant","message":{"content":[{"type":7}]}}'),
        ("claude", '{"type":"assistant","message":{"content":[{"type":"future_block"}]}}'),
        ("codex", '{"type":"item.completed","item":{"type":"agent_message"}}'),
        ("codex", '{"type":"item.completed","item":{"id":"i","type":"agent_message"}}'),
        ("codex", '{"type":"thread.started"}'),
        ("codex", '{"type":"thread.started","thread_id":7}'),
        ("codex", '{"type":"item.updated","item":{}}'),
    )

    def test_every_incomplete_shape_is_refused(self):
        for provider, line in self.CASES:
            normalize = (
                normalize_claude_line if provider == "claude" else normalize_codex_line
            )
            with self.subTest(provider=provider, line=line):
                with self.assertRaises(StreamFailure):
                    normalize(line, run_id=RUN_ID)

    def test_an_unknown_content_block_is_not_treated_as_prose(self):
        """Counting it as text would commit a message this build cannot read."""
        with self.assertRaises(StreamFailure) as caught:
            normalize_claude_line(
                '{"type":"assistant","message":{"content":[{"type":"future_block"}]}}',
                run_id=RUN_ID,
            )

        self.assertEqual("UnknownProviderEvent", caught.exception.diagnostic)


class ShapeAndTypeTests(unittest.TestCase):
    """`schema 불일치는 turn failure다` — including shapes that merely *look* usable.

    Chained `.get()` calls fail in two directions and both are worse than a
    refusal. A null where an object belongs raises an internal error that is not
    an allowlisted diagnostic. A string where a list belongs is worse still:
    `len("not-a-list")` is 10, so the line normalizes into a canonical event
    carrying a block count the provider never sent.

    Every case here must be a `StreamFailure`, and none may quote the payload.
    """

    CLAUDE_BAD = (
        '{"type":"assistant","message":null}',
        '{"type":"assistant"}',
        '{"type":"assistant","message":{"content":"not-a-list"}}',
        '{"type":"assistant","message":{"content":[1,2]}}',
        '{"type":"assistant","message":"text"}',
        '{"type":"result","subtype":"success","is_error":"false"}',
        '{"type":"result"}',
        '{"type":"result","is_error":1}',
        '{"type":123}',
    )

    CODEX_BAD = (
        '{"type":"item.completed","item":null}',
        '{"type":"item.completed"}',
        '{"type":"item.completed","item":"agent_message"}',
        '{"type":"item.completed","item":{"type":7}}',
        '{"type":"item.completed","item":{}}',
        '{"type":null}',
    )

    def test_claude_rejects_every_malformed_shape(self):
        for line in self.CLAUDE_BAD:
            with self.subTest(line=line):
                with self.assertRaises(StreamFailure) as caught:
                    normalize_claude_line(line, run_id=RUN_ID)

                self.assertIn(
                    caught.exception.diagnostic,
                    ("StdoutSchemaMismatch", "UnknownProviderEvent"),
                )

    def test_codex_rejects_every_malformed_shape(self):
        for line in self.CODEX_BAD:
            with self.subTest(line=line):
                with self.assertRaises(StreamFailure) as caught:
                    normalize_codex_line(line, run_id=RUN_ID)

                self.assertIn(
                    caught.exception.diagnostic,
                    ("StdoutSchemaMismatch", "UnknownProviderEvent"),
                )

    def test_a_string_is_never_counted_as_a_block_list(self):
        """`len("not-a-list") == 10` is the specific accident being closed."""
        with self.assertRaises(StreamFailure):
            normalize_claude_line(
                '{"type":"assistant","message":{"content":"not-a-list"}}',
                run_id=RUN_ID,
            )

    def test_a_string_is_never_read_as_a_boolean(self):
        """`bool("false")` is True, so a typed check is the only honest one."""
        with self.assertRaises(StreamFailure):
            normalize_claude_line(
                '{"type":"result","is_error":"false"}', run_id=RUN_ID
            )

    def test_an_unknown_item_subtype_is_refused_not_defaulted(self):
        with self.assertRaises(StreamFailure):
            normalize_codex_line(
                '{"type":"item.completed","item":{"id":"i","type":"future_thing"}}',
                run_id=RUN_ID,
            )

    def test_no_rejection_quotes_the_payload(self):
        sentinel = "SENTINEL-SHAPE-PAYLOAD"
        line = f'{{"type":"assistant","message":{{"content":"{sentinel}"}}}}'

        with self.assertRaises(StreamFailure) as caught:
            normalize_claude_line(line, run_id=RUN_ID)

        rendered = f"{caught.exception}{caught.exception!r}{caught.exception.args}"
        self.assertNotIn(sentinel, rendered)


class ClosedValueTests(unittest.TestCase):
    """Values outside the documented sets are refused, not carried through.

    These pin checks that were added during review and then went unproven. The
    mutation proof found the enum guard surviving: deleting it left all 241
    tests green, so the code was present and nothing held it there. Written
    after the fact rather than driven by a failing test — which is exactly how
    that happens.

    Item shapes are the pinned `rust-v0.149.0` ones and only the value under
    test is out of range, so a refusal here cannot come from an unrelated gap.
    """

    CODEX_OUT_OF_RANGE = (
        # command_execution.status: in_progress | completed | failed
        '{"type":"item.completed","item":{"id":"i","type":"command_execution",'
        '"command":"ls","aggregated_output":"","status":"cancelled"}}',
        # file_change.status: completed | failed
        '{"type":"item.completed","item":{"id":"i","type":"file_change",'
        '"changes":[{"path":"p","kind":"add"}],"status":"in_progress"}}',
        # file_change.changes[].kind: add | delete | update
        '{"type":"item.completed","item":{"id":"i","type":"file_change",'
        '"changes":[{"path":"p","kind":"rename"}],"status":"completed"}}',
        # mcp_tool_call.status: in_progress | completed | failed
        '{"type":"item.completed","item":{"id":"i","type":"mcp_tool_call",'
        '"server":"s","tool":"t","arguments":{},"status":"queued"}}',
    )

    CLAUDE_OUT_OF_RANGE = (
        '{"type":"result","subtype":"error_other","is_error":true}',
        '{"type":"system","subtype":"compact"}',
    )

    def test_a_codex_status_outside_its_set_is_refused(self):
        for line in self.CODEX_OUT_OF_RANGE:
            with self.subTest(line=line):
                with self.assertRaises(StreamFailure) as caught:
                    normalize_codex_line(line, run_id=RUN_ID)

                self.assertEqual("StdoutSchemaMismatch", caught.exception.diagnostic)

    def test_a_claude_subtype_outside_its_set_is_refused(self):
        for line in self.CLAUDE_OUT_OF_RANGE:
            with self.subTest(line=line):
                with self.assertRaises(StreamFailure):
                    normalize_claude_line(line, run_id=RUN_ID)


class UsageAccountingTests(unittest.TestCase):
    """`turn.completed` carries usage, so usage has to be real numbers.

    Two accidents are closed here. `isinstance(True, int)` is `True` in Python,
    so a boolean survives an unguarded numeric check and `True` silently becomes
    a token count of 1. And a missing field would leave the accounting short
    with nothing saying so.
    """

    FIELDS = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )

    def line(self, usage):
        return json.dumps({"type": "turn.completed", "usage": usage})

    def valid(self):
        return {field: 1 for field in self.FIELDS}

    def test_a_complete_usage_block_is_accepted(self):
        """The fixture must be valid, or the refusals below prove nothing."""
        (event,) = normalize_codex_line(self.line(self.valid()), run_id=RUN_ID)

        self.assertEqual("turn.completed", event.kind)

    def test_a_boolean_is_not_a_token_count(self):
        for field in self.FIELDS:
            with self.subTest(field=field):
                usage = self.valid()
                usage[field] = True

                with self.assertRaises(StreamFailure) as caught:
                    normalize_codex_line(self.line(usage), run_id=RUN_ID)

                self.assertEqual("StdoutSchemaMismatch", caught.exception.diagnostic)

    def test_every_usage_field_is_required(self):
        for field in self.FIELDS:
            with self.subTest(field=field):
                usage = self.valid()
                del usage[field]

                with self.assertRaises(StreamFailure) as caught:
                    normalize_codex_line(self.line(usage), run_id=RUN_ID)

                self.assertEqual("StdoutSchemaMismatch", caught.exception.diagnostic)


if __name__ == "__main__":
    unittest.main()
