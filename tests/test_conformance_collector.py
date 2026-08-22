"""The collector must refuse to describe a measurement it did not make.

`collect_codex` concluded, in a fixed sentence, that the status output cannot
distinguish a subscription sign-in from an API-key credential path. That is the
sentence AC 8 rests on, and it was printed unconditionally — including when the
three equality measurements above it came out False. A future Codex build whose
output *did* differ would produce three `**False**` lines followed by a
conclusion those lines refute, freshly dated, beside computed values.

The fix is not to invert the sentence when the premises fail. An inverted
sentence is a new claim nobody reviewed, which reproduces the original defect
with the sign flipped. A measurement that contradicts the design premise is an
event for a person, so collection stops and writes nothing.

Every case here is synthetic. No CLI is executed.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "collect-conformance-evidence.py"

_spec = importlib.util.spec_from_file_location("_collector_under_test", SCRIPT)
collector = importlib.util.module_from_spec(_spec)
sys.modules["_collector_under_test"] = collector
_spec.loader.exec_module(collector)

PINNED = "Logged in using ChatGPT"


def result(exit_code=0, stdout="", stderr=PINNED):
    return exit_code, stdout, stderr


class CodexMeasurementTests(unittest.TestCase):
    def test_identical_outputs_yield_the_conclusion(self):
        measured, _carried = collector.codex_measurement_lines(
            0, "codex-cli 0.149.0", result(), result()
        )

        self.assertTrue(any("therefore" in line for line in measured))

    def test_a_differing_exit_code_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            collector.codex_measurement_lines(
                0, "codex-cli 0.149.0", result(), result(exit_code=1)
            )

    def test_a_differing_stdout_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            collector.codex_measurement_lines(
                0, "codex-cli 0.149.0", result(), result(stdout="leaked")
            )

    def test_a_differing_stderr_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            collector.codex_measurement_lines(
                0,
                "codex-cli 0.149.0",
                result(),
                result(stderr="Logged in using an API key"),
            )

    def test_the_failure_carries_no_command_output(self):
        """The refusal names the condition, never what the command printed."""
        with self.assertRaises(collector.CollectionFailed) as caught:
            collector.codex_measurement_lines(
                0, "codex-cli 0.149.0", result(), result(stdout="SENTINEL-STDOUT")
            )

        rendered = f"{caught.exception}{caught.exception!r}{caught.exception.args}"
        self.assertNotIn("SENTINEL-STDOUT", rendered)


class ClaudeMeasurementTests(unittest.TestCase):
    def test_the_unread_identifier_note_is_not_presented_as_measured(self):
        """`were not read` is a statement about this code, not an observation.

        The collector never checks it; it is true because of how the parser is
        written. It belongs with the carried claims, not under a heading that
        says every line was computed from a command this run executed.
        """
        payload = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": "REPLAYED",
            "email": "REPLAYED",
            "orgId": "REPLAYED",
            "orgName": "REPLAYED",
        }

        measured, carried = collector.claude_measurement_lines(
            0, "2.1.240 (Claude Code)", 0, payload, collector.BareProbe(0, False)
        )

        # Keyed on "never read" rather than the full sentence: the claim's
        # placement is the invariant, its wording is not. `orgId` cannot serve
        # as the marker because the measured field-name line legitimately
        # contains it.
        self.assertFalse(any("never read" in line for line in measured))
        self.assertTrue(any("never read" in line for line in carried))


class BareDefaultProbeTests(unittest.TestCase):
    """`bare_default` is decided by execution, not by documentation wording.

    The sanitized child environment contains no API key variable by
    construction. So a plain `-p` turn that succeeds in it authenticated through
    the subscription, which means `-p` did not imply bare mode. Had bare been
    the default, the same turn must have failed to authenticate.
    """

    PAYLOAD = {
        "loggedIn": True,
        "authMethod": "claude.ai",
        "apiProvider": "firstParty",
        "subscriptionType": "REPLAYED",
        "email": "REPLAYED",
        "orgId": "REPLAYED",
        "orgName": "REPLAYED",
    }

    def lines(self, probe):
        return collector.claude_measurement_lines(
            0, "2.1.240 (Claude Code)", 0, self.PAYLOAD, probe
        )

    def test_a_successful_turn_is_recorded_as_a_measured_observation(self):
        measured, carried = self.lines(collector.BareProbe(0, False))

        self.assertTrue(any("bare mode default" in line for line in measured))
        self.assertFalse(any("bare mode default" in line for line in carried))

    def test_a_failed_turn_stops_the_collection(self):
        """Not evidence of bare mode — evidence of something unexplained."""
        with self.assertRaises(collector.CollectionFailed):
            self.lines(collector.BareProbe(1, False))

    def test_a_timed_out_turn_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            self.lines(collector.BareProbe(None, True))


class AuthDecisionGuardTests(unittest.TestCase):
    """A document must not record a failing decision as if it were evidence.

    The turn below can succeed while the account is in a state this build does
    not support. Recording `**False**` beside a passing probe would produce a
    conformance file for a configuration nobody approved.
    """

    OK = {
        "loggedIn": True,
        "authMethod": "claude.ai",
        "apiProvider": "firstParty",
        "subscriptionType": "REPLAYED",
        "email": "REPLAYED",
        "orgId": "REPLAYED",
        "orgName": "REPLAYED",
    }

    def lines(self, payload, version="2.1.240 (Claude Code)", auth_exit=0):
        return collector.claude_measurement_lines(
            0, version, auth_exit, payload, collector.BareProbe(0, False)
        )

    def test_a_signed_out_account_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            self.lines({**self.OK, "loggedIn": False})

    def test_a_third_party_api_provider_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            self.lines({**self.OK, "apiProvider": "bedrock"})

    def test_an_unrecognised_field_set_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            self.lines({**self.OK, "surprise": "field"})

    def test_a_nonzero_auth_exit_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            self.lines(self.OK, auth_exit=1)


class BinaryIdentityTests(unittest.TestCase):
    """The version, the auth check and the turn are evidence about one build.

    They are three separate executions. If the executable is replaced between
    them — an installer running in the background is enough — the document would
    attribute one binary's version to another binary's behaviour.
    """

    def test_an_unchanged_digest_is_accepted(self):
        collector.assert_same_binary("abc", "abc")

    def test_a_changed_digest_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            collector.assert_same_binary("abc", "def")


if __name__ == "__main__":
    unittest.main()
