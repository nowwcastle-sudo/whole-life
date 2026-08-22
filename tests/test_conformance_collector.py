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
import json
import tempfile
import subprocess
import sys
import unittest
from unittest import mock
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


class SameExecutableTests(unittest.TestCase):
    """All three Claude executions must be the binary whose hash is recorded.

    Hashing a resolved path proves nothing about a command invoked by bare name:
    that name is re-resolved through PATH by the shell, and could reach a
    different file. `assert_same_binary` would still pass, because the file it
    hashed never changed — it simply was not the file that ran.
    """

    def test_version_auth_and_the_turn_all_receive_the_resolved_path(self):
        resolved = Path("C:/resolved/claude.exe")
        seen = []

        def fake_run(executable, args, env):
            seen.append(executable)
            if args == ["--version"]:
                return 0, "2.1.240 (Claude Code)", ""
            return 0, json.dumps(AuthDecisionGuardTests.OK), ""

        def fake_probe(env, executable):
            seen.append(str(executable))
            return collector.BareProbe(0, False)

        with (
            mock.patch.object(collector, "resolved_executable", return_value=resolved),
            mock.patch.object(collector, "binary_digest", return_value="deadbeef"),
            mock.patch.object(collector, "run", fake_run),
            mock.patch.object(collector, "probe_bare_default", fake_probe),
        ):
            collector.collect_claude()

        self.assertEqual([str(resolved)] * 3, seen)


class AuthBeforeTurnTests(unittest.TestCase):
    """An unapproved account must not have a turn issued against it.

    The authentication decisions gate whether this is the operator's first-party
    subscription. Running the turn first and judging afterwards spends a real
    request on an account we then refuse to describe, and inverts the T-1/T-2
    ordering the smoke procedure specifies.
    """

    def collect_with(self, payload):
        probed = []

        def fake_run(executable, args, env):
            if args == ["--version"]:
                return 0, "2.1.240 (Claude Code)", ""
            return 0, json.dumps(payload), ""

        def fake_probe(env, executable):
            probed.append(True)
            return collector.BareProbe(0, False)

        with (
            mock.patch.object(collector, "resolved_executable",
                              return_value=Path("C:/resolved/claude.exe")),
            mock.patch.object(collector, "binary_digest", return_value="deadbeef"),
            mock.patch.object(collector, "run", fake_run),
            mock.patch.object(collector, "probe_bare_default", fake_probe),
        ):
            try:
                collector.collect_claude()
            except collector.CollectionFailed:
                pass
        return probed

    def test_a_signed_out_account_never_reaches_the_turn(self):
        probed = self.collect_with({**AuthDecisionGuardTests.OK, "loggedIn": False})

        self.assertEqual([], probed)

    def test_an_api_key_account_never_reaches_the_turn(self):
        probed = self.collect_with({**AuthDecisionGuardTests.OK, "authMethod": "apiKey"})

        self.assertEqual([], probed)

    def test_an_approved_subscription_does_reach_the_turn(self):
        probed = self.collect_with(AuthDecisionGuardTests.OK)

        self.assertEqual([True], probed)


class ProbeProcessTests(unittest.TestCase):
    """A hung turn is not over until the whole tree is gone and reaped.

    Calling a killer is not the same as terminating. If `taskkill` cannot run,
    or exits nonzero, or the process never actually goes away, then a model
    request may still be running and still billing while this collector reports
    a tidy timeout. So termination is verified, and anything unverified raises
    rather than returning a clean result.
    """

    def spawn(self, *, returncode=0, timeout=False, wait_hangs=False):
        proc = mock.Mock()
        proc.pid = 4321
        proc.returncode = returncode
        proc.communicate.side_effect = (
            subprocess.TimeoutExpired(cmd="claude", timeout=1) if timeout else None
        )
        if not timeout:
            proc.communicate.return_value = (None, None)
        if wait_hangs:
            proc.wait.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=1)
        else:
            proc.wait.return_value = 1
        return proc

    def run_probe(self, proc, *, kill_exit=0, killer_present=True):
        killer = Path("C:/Windows/System32/taskkill.exe")
        with (
            mock.patch.object(subprocess, "Popen", return_value=proc) as popen,
            mock.patch.object(subprocess, "run") as kill,
        ):
            kill.return_value = mock.Mock(returncode=kill_exit)
            patcher = mock.patch.object(
                collector,
                "system_taskkill",
                side_effect=(
                    None if killer_present
                    else collector.CollectionFailed("taskkill unavailable")
                ),
                return_value=killer,
            )
            with patcher:
                result = collector.probe_bare_default(
                    {}, Path("C:/resolved/claude.exe")
                )
        return result, popen, kill

    def test_a_clean_turn_reports_its_exit_code(self):
        result, _popen, kill = self.run_probe(self.spawn(returncode=0))

        self.assertEqual(collector.BareProbe(0, False), result)
        kill.assert_not_called()

    def test_a_failed_turn_reports_its_exit_code_without_killing(self):
        result, _popen, kill = self.run_probe(self.spawn(returncode=1))

        self.assertEqual(collector.BareProbe(1, False), result)
        kill.assert_not_called()

    def test_a_hung_turn_is_reported_only_after_the_tree_is_gone(self):
        proc = self.spawn(timeout=True)

        result, _popen, kill = self.run_probe(proc)

        self.assertEqual(collector.BareProbe(None, True), result)
        argv = [str(a) for a in kill.call_args.args[0]]
        self.assertIn("/T", argv)
        self.assertIn("4321", argv)
        proc.wait.assert_called()

    def test_a_failed_kill_stops_the_collection(self):
        """A nonzero taskkill means descendants may still be running."""
        with self.assertRaises(collector.CollectionFailed):
            self.run_probe(self.spawn(timeout=True), kill_exit=1)

    def test_an_unavailable_killer_stops_the_collection(self):
        with self.assertRaises(collector.CollectionFailed):
            self.run_probe(self.spawn(timeout=True), killer_present=False)

    def test_a_process_that_survives_the_kill_stops_the_collection(self):
        """Reaping is the proof. Without it the kill is only a request."""
        with self.assertRaises(collector.CollectionFailed):
            self.run_probe(self.spawn(timeout=True, wait_hangs=True))

    def test_both_streams_are_discarded_by_the_operating_system(self):
        _result, popen, _kill = self.run_probe(self.spawn())

        kwargs = popen.call_args.kwargs
        self.assertIs(subprocess.DEVNULL, kwargs["stdout"])
        self.assertIs(subprocess.DEVNULL, kwargs["stderr"])
        self.assertNotIn("capture_output", kwargs)
        self.assertIs(False, kwargs["shell"])


class SystemTaskkillTests(unittest.TestCase):
    """The killer is located under SYSTEMROOT, never through PATH.

    PATH is exactly the re-resolution hazard already fixed for the CLI itself.
    The tool that proves termination must not be the one thing still picked up
    by name.
    """

    def test_it_resolves_under_system_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            system32 = Path(tmp) / "System32"
            system32.mkdir()
            (system32 / "taskkill.exe").write_bytes(b"MZ")
            with mock.patch.dict(collector.os.environ, {"SYSTEMROOT": tmp}, clear=False):
                resolved = collector.system_taskkill()

        self.assertEqual(system32 / "taskkill.exe", resolved)
        self.assertTrue(resolved.is_absolute())

    def test_a_missing_killer_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(collector.os.environ, {"SYSTEMROOT": tmp}, clear=False):
                with self.assertRaises(collector.CollectionFailed):
                    collector.system_taskkill()


class ResolvedTargetTests(unittest.TestCase):
    """Evidence is only collected against a real PE executable.

    A `.cmd` launcher dispatches to an implementation this collector never sees,
    so hashing the launcher certifies nothing about what actually ran — the
    implementation behind it could be replaced between the version check, the
    auth check and the turn while the wrapper's bytes stay identical. v0 does
    not chase the delegated target; it refuses.
    """

    def resolve(self, name, contents):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(contents)
            with mock.patch.object(collector.shutil, "which", return_value=str(path)):
                return collector.resolved_executable("claude")

    def test_a_pe_executable_is_accepted(self):
        resolved = self.resolve("claude.exe", bytes((0x4D, 0x5A, 0x90, 0)) + b"padding")

        self.assertEqual("claude.exe", resolved.name)

    def test_a_command_launcher_is_refused(self):
        with self.assertRaises(collector.CollectionFailed):
            self.resolve("claude.cmd", b"@echo off" + bytes((13, 10)) + b"node claude.js %*")

    def test_a_file_named_exe_without_a_pe_header_is_refused(self):
        with self.assertRaises(collector.CollectionFailed):
            self.resolve("claude.exe", b"#!/usr/bin/env node")


if __name__ == "__main__":
    unittest.main()
