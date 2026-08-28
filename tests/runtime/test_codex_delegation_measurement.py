"""What a live Codex turn showed about native workers. Ticket #35.

`tests/recordings/codex-0.149.0-agents-enabled-turn.jsonl` is one real turn on
the pinned Codex version, captured by running the production argument vector —
which already carries `agents.enabled=true` and
`agents.max_concurrent_threads_per_session=3` — in the sanitized child
environment `build_child_env` produces, from inside a git repository so the
build's trusted-directory check passed. The prompt asked explicitly for three
concurrent subagents and told the model not to answer itself.

Redacted before committing, by value and never by silence: the one thread
identifier it carried (`thread_id`, and `sender_thread_id` on four items) was
replaced with the same all-zero placeholder the Claude recordings use. Nothing
else in the stream was an identifier — no path, no account, no authentication
output — and stderr was never captured to this file.

Why a recording and not a hand-written fixture: the enforcement row for Codex
was a reading of the stream schema, and for Claude the schema reading and the
live stream disagreed. A fixture would only contain what someone already
believed.
"""

import json
import unittest
from pathlib import Path

from whole_life.runtime.contract import EnforcementLevel, Provider
from whole_life.runtime.delegation import REPORTED_ENFORCEMENT
from whole_life.runtime.normalize import normalize_codex_line
from whole_life.runtime.streams import StreamFailure

RECORDING = (
    Path(__file__).resolve().parent.parent
    / "recordings"
    / "codex-0.149.0-agents-enabled-turn.jsonl"
)

#: The identifier that was redacted. Asserted as absent, with the placeholder
#: asserted as present — "no identifier" on its own would also pass against an
#: empty file.
REDACTED_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"

RUN_ID = "run-35"


def recorded_lines():
    return [
        line
        for line in RECORDING.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class RecordingIsRedactedTests(unittest.TestCase):
    """AC1's second half. The recording is committed, so this is about bytes."""

    def test_no_thread_identifier_survives(self):
        text = RECORDING.read_text(encoding="utf-8")

        self.assertIn(REDACTED_PLACEHOLDER, text, "nothing was redacted at all")
        for line in recorded_lines():
            parsed = json.loads(line)
            for field in ("thread_id",):
                if field in parsed:
                    self.assertEqual(REDACTED_PLACEHOLDER, parsed[field])
            item = parsed.get("item", {})
            if "sender_thread_id" in item:
                self.assertEqual(REDACTED_PLACEHOLDER, item["sender_thread_id"])

    def test_no_local_path_account_or_stderr_is_present(self):
        text = RECORDING.read_text(encoding="utf-8")

        for forbidden in (":\\", ":/", "@", "USERPROFILE", "CODEX_HOME"):
            self.assertNotIn(forbidden, text)


class WhatTheStreamShowedTests(unittest.TestCase):
    """AC2. The three axes are set from this, not from reading the schema."""

    def test_the_turn_completed(self):
        """Otherwise the measurement is of a failed turn, not of delegation."""
        kinds = [json.loads(line)["type"] for line in recorded_lines()]

        self.assertIn("turn.completed", kinds)

    def test_the_model_said_it_delegated(self):
        """The premise. Without this the recording shows a turn that was never
        asked to delegate, and the axes below would be untested."""
        texts = [
            json.loads(line).get("item", {}).get("text", "")
            for line in recorded_lines()
        ]

        self.assertTrue(
            any("subagent" in text.lower() for text in texts),
            "the model never claimed to delegate, so this recording does not "
            "exercise the question the ticket asks",
        )

    def test_no_worker_was_actually_started(self):
        """The observation the enforcement row now rests on.

        The build exposed a collaboration tool call, and the model's prose
        reported three subagents by name — but the call carried no receivers and
        no agent states. Nothing in the stream names a worker, so there is
        nothing to count.
        """
        collab = [
            json.loads(line)["item"]
            for line in recorded_lines()
            if json.loads(line).get("item", {}).get("type") == "collab_tool_call"
        ]

        self.assertTrue(collab, "the recording has no collaboration call at all")
        for item in collab:
            self.assertEqual([], item["receiver_thread_ids"])
            self.assertEqual({}, item["agents_states"])

    def test_every_axis_stays_unsupported(self):
        """AC2: an axis the stream does not expose is `unsupported`, never
        described as enforced. Measured now rather than read from the schema —
        and the row is unchanged, which is itself the result."""
        row = REPORTED_ENFORCEMENT[Provider.CODEX]

        self.assertEqual(
            {
                "worker_concurrency_enforcement": EnforcementLevel.UNSUPPORTED,
                "worker_total_start_enforcement": EnforcementLevel.UNSUPPORTED,
                "delegation_depth_enforcement": EnforcementLevel.UNSUPPORTED,
            },
            dict(row),
        )


class TheCollaborationTurnNowCompletesTests(unittest.TestCase):
    """Ticket #51. The predecessor of this class pinned the opposite — that
    every `collab_tool_call` line was refused as `UnknownProviderEvent` — and
    said a change to it had to be deliberate. This is that deliberate change.

    The production argument vector asks for the collaboration capability on
    every turn, so a turn that used it must reach its end. Replayed against
    the committed recording rather than hand-written lines: every synthetic
    line is one someone already knew about, which is how the previous defect
    of this family lived.
    """

    def test_the_whole_recording_replays_without_failing_the_run(self):
        """#51 AC1, on the recording. Ordering asserted too: the two
        collaboration calls surface as tool activity between the model's two
        messages, exactly where the provider put them."""
        kinds = []
        for line in recorded_lines():
            kinds.extend(
                event.kind for event in normalize_codex_line(line, run_id=RUN_ID)
            )

        self.assertEqual(
            [
                "turn.started",
                "message.committed",
                "runtime.activity.started",
                "runtime.activity.finished",
                "runtime.activity.started",
                "runtime.activity.finished",
                "message.committed",
                "turn.completed",
            ],
            kinds,
        )

    def test_the_collaboration_item_is_a_tool_call_not_a_worker(self):
        """#51 AC2. The stream reported no worker — the receiving-thread list
        and agent-state map are empty on every call — so the event says
        `tool_use`, carries no worker identity and no depth."""
        events = []
        for line in recorded_lines():
            if json.loads(line).get("item", {}).get("type") != "collab_tool_call":
                continue
            events.extend(normalize_codex_line(line, run_id=RUN_ID))

        self.assertEqual(4, len(events), "the recording carries four calls")
        for event in events:
            self.assertEqual("tool_use", event.data["activity_kind"])
            self.assertNotIn("native_child_id", event.data)
            self.assertIsNone(event.worker_depth)

    def test_a_fabricated_item_type_still_fails_the_same_way(self):
        """#51 AC4, as a positive control on the same recorded bytes: swap the
        one recognised discriminator for one nobody has seen and the line must
        die. A normalizer that started accepting whatever arrives would pass
        the replay above and fail here."""
        line = next(
            line
            for line in recorded_lines()
            if json.loads(line).get("item", {}).get("type") == "collab_tool_call"
        )
        fabricated = line.replace('"collab_tool_call"', '"collab_tool_call_v2"')
        assert fabricated != line

        with self.assertRaises(StreamFailure) as caught:
            normalize_codex_line(fabricated, run_id=RUN_ID)

        self.assertEqual("UnknownProviderEvent", caught.exception.diagnostic)


if __name__ == "__main__":
    unittest.main()
