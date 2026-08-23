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
import re
import unittest
from pathlib import Path

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


class ClaudeNativeWorkerTests(unittest.TestCase):
    """The `Agent` delegation lifecycle. Spec section 4, native delegation.

    Every fixture below is the shape a live Claude Code 2.1.240 turn actually
    wrote, recorded by running the production argument vector against the
    operator's own sign-in. The synthetic fixtures this suite started with did
    not contain these lines at all, which is why a green suite coexisted with a
    normalizer that rejected most of a real delegating turn.
    """

    @staticmethod
    def task_started(**overrides):
        line = {
            "type": "system",
            "subtype": "task_started",
            "task_id": "a3755a9018fbc95a8",
            "tool_use_id": "toolu_01A1tLe2g217PHsZYZ2nSxVf",
            "description": "Return a single word",
            "subagent_type": "general-purpose",
            "is_backgrounded": False,
            "spawn_depth": 1,
            "task_type": "local_agent",
            "prompt": "Reply with exactly the word PINEAPPLE7731 and nothing else.",
            "uuid": "0a7d9c57-d042-4544-b42d-3a3930031566",
            "session_id": "beb0fa2d-8b57-4457-baca-38f66765c0ef",
        }
        line.update(overrides)
        return json.dumps(line)

    def test_a_task_start_is_a_native_worker_activity(self):
        (event,) = normalize_claude_line(self.task_started(), run_id=RUN_ID)

        self.assertEqual("runtime.activity.started", event.kind)
        self.assertEqual("native_worker", event.data["activity_kind"])

    def test_the_worker_carries_the_provider_issued_lifecycle_id(self):
        """Spec line 118. The broker never invents one, but this build publishes
        `task_id`, so leaving `native_child_id` empty would discard a real
        identifier and record less than was observed."""
        (event,) = normalize_claude_line(
            self.task_started(task_id="a46dacfacc666a194"), run_id=RUN_ID
        )

        self.assertEqual("a46dacfacc666a194", event.data["native_child_id"])

    #: Spec line 247 names the whole permitted payload for a native-worker
    #: activity. `parent_participant_id` belongs to the caller, which knows the
    #: participant; the normalizer sees one line and cannot supply it.
    WORKER_PAYLOAD_FIELDS = frozenset(
        {
            "activity_kind",
            "parent_participant_id",
            "native_child_id",
            "observability",
            "status",
        }
    )

    def test_the_worker_payload_records_how_much_can_be_seen(self):
        """Spec line 118 requires `observability` on a worker activity. The
        provider publishes a lifecycle id, a status and a summary — never the
        worker's reasoning or tool state — so what is seen is a summary."""
        (event,) = normalize_claude_line(self.task_started(), run_id=RUN_ID)

        self.assertEqual("summary_only", event.data["observability"])

    def test_the_worker_payload_is_exactly_these_keys(self):
        """Asserted as a whole set, not as the absence of one name. Spec line
        247 states a whitelist, and a test that only forbids today's unwanted
        key lets tomorrow's walk in. `parent_participant_id` is absent because
        this function sees one line and not the roster; the caller that knows
        the participant is the one that can add it, and it stays inside the
        five the specification permits."""
        (started,) = normalize_claude_line(self.task_started(), run_id=RUN_ID)
        (finished,) = normalize_claude_line(
            self.task_notification(), run_id=RUN_ID
        )

        self.assertEqual(
            {"activity_kind", "native_child_id", "observability"},
            set(started.data),
        )
        self.assertEqual(
            {"activity_kind", "native_child_id", "observability", "status"},
            set(finished.data),
        )
        self.assertLessEqual(set(started.data), self.WORKER_PAYLOAD_FIELDS)
        self.assertLessEqual(set(finished.data), self.WORKER_PAYLOAD_FIELDS)

    @staticmethod
    def task_notification(**overrides):
        line = {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "a3755a9018fbc95a8",
            "tool_use_id": "toolu_01A1tLe2g217PHsZYZ2nSxVf",
            "status": "completed",
            "output_file": r"C:\Temp\a3755a9018fbc95a8.output",
            "summary": "PINEAPPLE7731",
            "usage": {"total_tokens": 4759, "tool_uses": 0, "duration_ms": 1606},
            "uuid": "29da1725-bf25-48c8-b0f4-db9e6060af4e",
            "session_id": "beb0fa2d-8b57-4457-baca-38f66765c0ef",
        }
        line.update(overrides)
        return json.dumps(line)

    def test_a_task_notification_ends_the_same_worker(self):
        """The provider's published summary boundary. It is where a worker is
        known to have finished, and the id ties it to the start."""
        (event,) = normalize_claude_line(self.task_notification(), run_id=RUN_ID)

        self.assertEqual("runtime.activity.finished", event.kind)
        self.assertEqual("native_worker", event.data["activity_kind"])
        self.assertEqual("a3755a9018fbc95a8", event.data["native_child_id"])

    def test_the_finish_carries_the_status_the_provider_reported(self):
        """Spec line 247 permits `status`. A worker that ended is not the same
        fact as a worker that ended *well*, and the turn gate below needs the
        difference."""
        (event,) = normalize_claude_line(
            self.task_notification(status="completed"), run_id=RUN_ID
        )

        self.assertEqual("completed", event.data["status"])

    def test_no_worker_prompt_or_summary_survives_normalization(self):
        """Spec line 247 and AC5. The provider hands us the worker's prompt on
        the start line and its answer on the finish line; neither is ours to
        carry into another participant's history."""
        started = normalize_claude_line(
            self.task_started(prompt="RAWPROMPT4402", description="RAWDESC4402"),
            run_id=RUN_ID,
        )
        finished = normalize_claude_line(
            self.task_notification(summary="RAWSUMMARY4402"), run_id=RUN_ID
        )

        carried = json.dumps(
            [dict(event.data) for event in (*started, *finished)]
        )
        self.assertNotIn("RAWPROMPT4402", carried)
        self.assertNotIn("RAWDESC4402", carried)
        self.assertNotIn("RAWSUMMARY4402", carried)

    def test_intermediate_worker_lines_carry_no_canonical_event(self):
        """`task_updated` and `task_progress` sit between the start and the
        finish. Section 7 has a started and a finished event and no notion of
        an update in between — the same answer already given to Codex's
        `item.updated`. Recognised and skipped, not invented into an event."""
        updated = json.dumps(
            {
                "type": "system",
                "subtype": "task_updated",
                "task_id": "a3755a9018fbc95a8",
                "patch": {"status": "running"},
                "uuid": "dd0dd3ef-a2ab-4555-aa5a-0163c486066a",
                "session_id": "beb0fa2d-8b57-4457-baca-38f66765c0ef",
            }
        )
        progress = json.dumps(
            {
                "type": "system",
                "subtype": "task_progress",
                "task_id": "a3755a9018fbc95a8",
                "uuid": "1f6a2b19-6f57-4f4e-9a2e-0a4a1f0f2b77",
                "session_id": "beb0fa2d-8b57-4457-baca-38f66765c0ef",
            }
        )

        self.assertEqual([], normalize_claude_line(updated, run_id=RUN_ID))
        self.assertEqual([], normalize_claude_line(progress, run_id=RUN_ID))

    def test_the_subagent_announcement_is_not_a_participant_message(self):
        """A live delegating turn writes a `user` line whose blocks are prose,
        not a tool result — the provider restating the delegated task. Counting
        it as `message.committed` would file the worker's brief as the
        participant's own answer, and the worker's lifecycle is already carried
        by the task lines."""
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "RAWBRIEF4402"}],
                },
                "parent_tool_use_id": "toolu_01A1tLe2g217PHsZYZ2nSxVf",
                "subagent_type": "general-purpose",
                "task_description": "Return a single word",
                "session_id": "beb0fa2d-8b57-4457-baca-38f66765c0ef",
                "uuid": "3b9c1d2e-3f4a-4b5c-8d6e-7f8a9b0c1d2e",
            }
        )

        self.assertEqual([], normalize_claude_line(line, run_id=RUN_ID))
class ClaudeLiveStreamLineTests(unittest.TestCase):
    """A recorded live turn, replayed. Ticket #32.

    `tests/recordings/claude-2.1.240-live-turn.jsonl` is one real turn on the
    pinned Claude Code version, captured by running the production argument
    vector against the operator's own sign-in. It is a recording rather than a
    hand-written fixture because hand-written fixtures are what let this
    defect live: every synthetic line in this suite was one someone already
    knew about, so the suite stayed green while no real turn could run.

    Redacted before committing, by value and never by silence: `session_id`,
    `uuid` and `request_id` were replaced with fixed placeholders, and the
    `system/init` fields that enumerate the operator's machine — `cwd`,
    `messaging_socket_path`, installed plugins, skills, agents, MCP servers,
    slash commands, capabilities — were removed, along with the provider's
    signature over the thinking block. Nothing the normalizer reads was
    touched, and no stderr was ever captured to this file.
    """

    RECORDING = (
        Path(__file__).resolve().parent.parent
        / "recordings"
        / "claude-2.1.240-live-turn.jsonl"
    )

    @classmethod
    def setUpClass(cls):
        cls.lines = [
            line
            for line in cls.RECORDING.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def of_kind(self, kind, subtype=None):
        for line in self.lines:
            parsed = json.loads(line)
            if parsed.get("type") == kind and (
                subtype is None or parsed.get("subtype") == subtype
            ):
                return line
        raise AssertionError(f"the recording has no {kind}/{subtype} line")

    def test_the_recording_holds_both_lines_this_ticket_is_about(self):
        """Otherwise the replay below could pass on a recording that never
        contained the problem — a green test proving nothing, which is the
        exact failure this ticket exists to correct."""
        self.of_kind("rate_limit_event")
        self.of_kind("system", "thinking_tokens")

    def test_a_recorded_live_turn_normalizes_to_completion(self):
        events = []
        for line in self.lines:
            events.extend(normalize_claude_line(line, run_id=RUN_ID))

        self.assertEqual(TerminalEvent.COMPLETED, events[-1].terminal)
        self.assertEqual("turn.completed", events[-1].kind)

    def test_neither_notice_line_carries_a_canonical_event(self):
        """Section 7 fixes the canonical set at eight. A notice the provider
        writes about its own quota or its own token estimate has no place in
        that set, and adding a ninth would be a specification change rather
        than a defect fix."""
        self.assertEqual(
            [], normalize_claude_line(self.of_kind("rate_limit_event"), run_id=RUN_ID)
        )
        self.assertEqual(
            [],
            normalize_claude_line(
                self.of_kind("system", "thinking_tokens"), run_id=RUN_ID
            ),
        )

    def test_no_notice_content_survives_normalization(self):
        """Limit counts, reset times and token estimates are the provider's
        own accounting. None of it is turn history."""
        notices = (
            self.of_kind("rate_limit_event"),
            self.of_kind("system", "thinking_tokens"),
        )
        values = []
        for line in notices:
            parsed = json.loads(line)
            values.append(str(parsed.get("estimated_tokens")))
            values.append(str((parsed.get("rate_limit_info") or {}).get("resetsAt")))

        carried = json.dumps(
            [
                dict(event.data)
                for line in notices
                for event in normalize_claude_line(line, run_id=RUN_ID)
            ]
        )
        for value in values:
            if value != "None":
                self.assertNotIn(value, carried)

    def test_the_recording_carries_no_local_identifier(self):
        """The redaction is part of the evidence, so it is checked rather than
        described. A future recording dropped in beside this one is held to the
        same rule."""
        blob = self.RECORDING.read_text(encoding="utf-8")

        self.assertNotIn(":\\", blob)
        self.assertNotIn("signature", blob)
        self.assertNotIn("/Users/", blob)
        self.assertNotIn("/home/", blob)
        for parsed in (json.loads(line) for line in self.lines):
            self.assertEqual(
                "00000000-0000-0000-0000-000000000000", parsed["session_id"]
            )

    def test_an_unknown_top_level_type_still_fails_closed(self):
        """This change widens an allowlist. It does not remove one."""
        line = json.dumps({"type": "something_new", "session_id": "s"})

        with self.assertRaises(StreamFailure) as caught:
            normalize_claude_line(line, run_id=RUN_ID)

        self.assertEqual("UnknownProviderEvent", caught.exception.diagnostic)

    def test_an_unknown_system_subtype_still_fails_closed(self):
        line = json.dumps(
            {"type": "system", "subtype": "something_new", "session_id": "s"}
        )

        with self.assertRaises(StreamFailure) as caught:
            normalize_claude_line(line, run_id=RUN_ID)

        self.assertEqual("StdoutSchemaMismatch", caught.exception.diagnostic)


class ClaudeRecordedDelegationTests(unittest.TestCase):
    """Two recorded live turns that actually delegated. Ticket #17.

    Same standard as the #32 recording and for the same reason: a fixture
    written by hand contains only the lines its author already knew about.
    `claude-2.1.240-delegating-turn.jsonl` is one turn that spawned a single
    worker; `claude-2.1.240-nested-delegation-turn.jsonl` is one where the
    worker spawned a worker of its own.

    Redacted the same way, plus the delegation-specific fields: the worker's
    `prompt`, `description`, `task_description`, `summary` and `output_file`
    are removed, because specification section 4 keeps a worker's brief and
    its raw output inside the provider boundary. What is deliberately left in
    is the sentinel word the worker was asked to return, so a test can show
    that even an answer sitting in the recording does not reach a normalized
    event.
    """

    RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"
    DELEGATING = RECORDINGS / "claude-2.1.240-delegating-turn.jsonl"
    NESTED = RECORDINGS / "claude-2.1.240-nested-delegation-turn.jsonl"

    #: The word the worker was told to return, present in the recording.
    WORKER_ANSWER = "PINEAPPLE7731"

    @staticmethod
    def replay(path):
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.extend(normalize_claude_line(line, run_id=RUN_ID))
        return events

    def test_a_recorded_delegating_turn_normalizes_to_completion(self):
        events = self.replay(self.DELEGATING)

        self.assertEqual(TerminalEvent.COMPLETED, events[-1].terminal)

    def test_the_recorded_turn_shows_one_worker_starting_and_finishing(self):
        """The count is the whole point of this slice: a budget cannot be held
        against activity nobody counted."""
        events = self.replay(self.DELEGATING)
        workers = [
            event
            for event in events
            if event.data.get("activity_kind") == "native_worker"
        ]

        self.assertEqual(
            ["runtime.activity.started", "runtime.activity.finished"],
            [event.kind for event in workers],
        )

    def test_the_worker_identity_comes_from_the_provider(self):
        """Spec line 118 forbids inventing one. This build issues `task_id`,
        and the identifier on the event is that value rather than anything
        this module made up."""
        recorded = [
            json.loads(line)
            for line in self.DELEGATING.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        announced = [
            entry["task_id"]
            for entry in recorded
            if entry.get("subtype") in ("task_started", "task_notification")
        ]

        carried = [
            event.data["native_child_id"]
            for event in self.replay(self.DELEGATING)
            if event.data.get("activity_kind") == "native_worker"
        ]

        self.assertEqual(announced, carried)
        self.assertEqual(1, len(set(carried)))

    def test_no_worker_output_survives_the_recorded_turn(self):
        """The answer is in the recording. It is not in any event."""
        self.assertIn(
            self.WORKER_ANSWER, self.DELEGATING.read_text(encoding="utf-8")
        )

        carried = json.dumps(
            [dict(event.data) for event in self.replay(self.DELEGATING)]
        )

        self.assertNotIn(self.WORKER_ANSWER, carried)

    def test_a_recorded_nested_delegation_normalizes_to_completion(self):
        """The provider did not refuse it — measured, not assumed — so the
        stream still has to be readable end to end."""
        events = self.replay(self.NESTED)

        self.assertEqual(TerminalEvent.COMPLETED, events[-1].terminal)

    def test_the_nested_recording_holds_two_workers(self):
        workers = [
            event
            for event in self.replay(self.NESTED)
            if event.data.get("activity_kind") == "native_worker"
        ]

        self.assertEqual(4, len(workers))
        self.assertEqual(
            2, len({event.data["native_child_id"] for event in workers})
        )

    def test_worker_depth_reaches_the_caller_without_entering_the_payload(self):
        """Spec line 247 fixes the payload of a native-worker activity, and
        depth is not in it — so depth cannot be recorded as Journal content.
        But delegation depth is a limit somebody has to enforce, and on this
        build the provider does not: the nested recording completed with a
        worker at depth 2 and `refused.depth_limit` at zero. The number
        therefore has to reach the enforcing caller by another route, the way
        `terminal` already does for the outcome resolver."""
        starts = [
            event
            for event in self.replay(self.NESTED)
            if event.kind == "runtime.activity.started"
            and event.data.get("activity_kind") == "native_worker"
        ]

        self.assertEqual([1, 2], [event.worker_depth for event in starts])
        for event in starts:
            self.assertNotIn("spawn_depth", event.data)

    def test_every_recorded_worker_event_keeps_the_exact_payload(self):
        """The same whole-set check, applied to lines the provider really
        wrote rather than to fixtures written here."""
        for path in (self.DELEGATING, self.NESTED):
            for event in self.replay(path):
                if event.data.get("activity_kind") != "native_worker":
                    continue
                with self.subTest(recording=path.name, kind=event.kind):
                    expected = {
                        "activity_kind",
                        "native_child_id",
                        "observability",
                    }
                    if event.kind == "runtime.activity.finished":
                        expected.add("status")
                    self.assertEqual(expected, set(event.data))

    def test_the_delegation_recordings_carry_no_local_identifier(self):
        for path in (self.DELEGATING, self.NESTED):
            with self.subTest(recording=path.name):
                blob = path.read_text(encoding="utf-8")

                # A drive letter, not merely a colon before a backslash: an
                # escaped newline in recorded prose matches the loose form
                # and says nothing about paths.
                self.assertIsNone(re.search(r"[A-Za-z]:\\\\", blob))
                self.assertNotIn("/Users/", blob)
                self.assertNotIn("signature", blob)
                self.assertNotIn("\"prompt\"", blob)

if __name__ == "__main__":
    unittest.main()
