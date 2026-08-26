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


class TheStreamStillFailsClosedTests(unittest.TestCase):
    """What the measurement also showed, pinned so it cannot change silently.

    `collab_tool_call` is not in the normalizer's closed item enum, so a turn
    that uses it fails rather than being partly understood. That is section 7's
    rule working — an unknown event is not guessed at — and it is recorded here
    because the production argument vector carries `agents.enabled=true` on
    every turn, so this is reachable rather than hypothetical.

    Whether the item should be recognised is a different question and a
    different ticket. This test says what today does, so that a change to it
    has to be deliberate.
    """

    def test_the_collaboration_item_is_refused_rather_than_guessed(self):
        refused = []
        for line in recorded_lines():
            if json.loads(line).get("item", {}).get("type") != "collab_tool_call":
                continue
            with self.assertRaises(StreamFailure) as caught:
                normalize_codex_line(line, run_id=RUN_ID)
            refused.append(caught.exception.diagnostic)

        self.assertEqual(["UnknownProviderEvent"] * 4, refused)

    def test_the_rest_of_the_stream_normalizes(self):
        """The control. If everything raised, the assertion above would hold
        for a normalizer that simply rejects every line."""
        understood = []
        for line in recorded_lines():
            if json.loads(line).get("item", {}).get("type") == "collab_tool_call":
                continue
            understood.extend(
                event.kind for event in normalize_codex_line(line, run_id=RUN_ID)
            )

        self.assertEqual(
            ["turn.started", "message.committed", "message.committed",
             "turn.completed"],
            understood,
        )


if __name__ == "__main__":
    unittest.main()
