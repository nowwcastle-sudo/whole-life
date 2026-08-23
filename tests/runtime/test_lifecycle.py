"""Ending a run and everything it started — normative source: spec section 6.

`정상 cancel은 stdin close 또는 provider가 지원하는 graceful signal을 먼저 보낸다.
5초 뒤에도 살아 있으면 Windows process tree를 종료하고, 최대 10초 동안 `wait`한다.`

The word that carries the weight is *tree*. Killing the process we started is
not enough on Windows: a CLI that launched a worker leaves that worker holding
the subscription request open, and the turn we believe we stopped goes on being
billed. So these tests start a real grandchild and check the grandchild.
"""

import asyncio
import os
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

from whole_life.runtime.lifecycle import (
    FORCED_WAIT_SECONDS,
    GRACEFUL_WAIT_SECONDS,
    HARD_TIMEOUT_SECONDS,
    LifecycleFailure,
    TurnDeadline,
    system_taskkill,
    terminate_process_tree,
)

#: Prints the grandchild's pid, then outlives any graceful request.
SPAWNS_A_GRANDCHILD = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def still_running(pid):
    """Ask Windows directly, rather than inferring from our own bookkeeping.

    Bytes, not text. `tasklist` writes in the console codepage — cp949 on this
    machine — so decoding as UTF-8 raises inside the reader thread and leaves
    `stdout` as `None` with a returncode of 0. Written as `completed.stdout or
    ""` that would be a check that always reports "not running" and can never
    fail: the grandchild would be declared dead without anyone looking.
    """
    root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    tasklist = Path(root) / "System32" / "tasklist.exe"
    completed = subprocess.run(
        [str(tasklist), "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        shell=False,
    )
    assert completed.stdout is not None, "tasklist produced no output to read"
    return str(pid).encode("ascii") in completed.stdout



def reap_tree(process, *pids):
    """Never leave a fixture's own tree behind, whatever the test did."""
    for pid in (process.pid, *pids):
        subprocess.run(
            [str(system_taskkill()), "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    transport = getattr(process, "_transport", None)
    if transport is not None:
        transport.close()


class NormativeConstantsTests(unittest.TestCase):
    """The numbers are conformance fixtures, not tuning knobs (spec 212)."""

    def test_the_escalation_delays_are_the_specified_ones(self):
        self.assertEqual(5, GRACEFUL_WAIT_SECONDS)
        self.assertEqual(10, FORCED_WAIT_SECONDS)

    def test_the_hard_timeout_is_twenty_minutes(self):
        self.assertEqual(20 * 60, HARD_TIMEOUT_SECONDS)


class KillerResolutionTests(unittest.TestCase):
    """The killer is located, never looked up on PATH.

    This is the tool whose result is the evidence that a billed request
    stopped. Resolving it through PATH would reintroduce exactly the hazard
    section 4 closes for the CLI itself — a shim could answer instead.
    """

    def test_the_killer_is_an_absolute_path_under_system32(self):
        killer = system_taskkill()

        self.assertTrue(killer.is_absolute())
        self.assertTrue(killer.is_file())
        self.assertEqual("system32", killer.parent.name.lower())

    def test_without_systemroot_it_refuses_rather_than_guessing(self):
        with self.assertRaises(LifecycleFailure):
            system_taskkill(environ={})

    def test_a_missing_killer_is_refused(self):
        with self.assertRaises(LifecycleFailure):
            system_taskkill(environ={"SYSTEMROOT": r"C:\no-such-windows-root"})


class ProcessTreeTerminationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_grandchild_dies_with_the_child(self):
        """`kill()` alone would leave the grandchild holding the request open."""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            SPAWNS_A_GRANDCHILD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        grandchild = int((await process.stdout.readline()).strip())
        self.addCleanup(reap_tree, process, grandchild)

        self.assertTrue(still_running(grandchild), "the fixture never started")

        ended = await terminate_process_tree(process)

        self.assertTrue(ended)
        self.assertIsNotNone(process.returncode)
        self.assertFalse(still_running(grandchild))

    async def test_terminating_an_already_dead_process_is_not_an_error(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "pass", stdout=asyncio.subprocess.PIPE
        )
        await process.wait()

        self.assertTrue(await terminate_process_tree(process))




class TurnDeadlineTests(unittest.TestCase):
    """`timeout은 provider 응답이 계속 streaming 중이어도 연장하지 않는다.` Spec 208.

    A deadline computed from the turn's start is the whole mechanism. The
    tempting alternative — an idle timer reset whenever something arrives — is
    what the sentence forbids, and it fails in the exact case that matters: a
    run that streams steadily forever never trips an idle timer, so a runaway
    turn bills the subscription indefinitely while looking healthy.

    The clock is injected so this is arithmetic rather than a twenty-minute
    test.
    """

    def test_the_deadline_is_fixed_at_the_turn_start(self):
        deadline = TurnDeadline(started_at=100.0)

        self.assertEqual(100.0 + HARD_TIMEOUT_SECONDS, deadline.expires_at)

    def test_activity_does_not_move_the_deadline(self):
        deadline = TurnDeadline(started_at=100.0)

        for arrival in range(1, 60):
            deadline.observed_activity(at=100.0 + arrival)

        self.assertEqual(100.0 + HARD_TIMEOUT_SECONDS, deadline.expires_at)

    def test_a_turn_streaming_past_the_deadline_has_still_expired(self):
        """Streaming right up to the deadline does not buy another second."""
        deadline = TurnDeadline(started_at=0.0)
        deadline.observed_activity(at=HARD_TIMEOUT_SECONDS - 1)

        self.assertTrue(deadline.expired(at=HARD_TIMEOUT_SECONDS))

    def test_before_the_deadline_it_has_not_expired(self):
        deadline = TurnDeadline(started_at=0.0)

        self.assertFalse(deadline.expired(at=HARD_TIMEOUT_SECONDS - 1))

    def test_the_remaining_time_shrinks_with_the_clock(self):
        deadline = TurnDeadline(started_at=0.0)

        self.assertEqual(HARD_TIMEOUT_SECONDS - 30, deadline.remaining(at=30))

    def test_the_remaining_time_never_goes_negative(self):
        deadline = TurnDeadline(started_at=0.0)

        self.assertEqual(0, deadline.remaining(at=HARD_TIMEOUT_SECONDS + 500))

class _RecordingProcess:
    """Records the order of transport close and wait, with no race to win."""

    def __init__(self):
        self.journal = []
        self.returncode = None
        self.pid = 0
        self._transport = _RecordingTransport(self)

    async def wait(self):
        self.journal.append("wait")
        self.returncode = 1
        return 1


class _RecordingTransport:
    def __init__(self, process):
        self._process = process

    def close(self):
        self._process.journal.append("close")


class TerminationWiringTests(unittest.IsolatedAsyncioTestCase):
    """The located killer must be the one actually used, and the pipes must be
    closed before the wait rather than after.

    Both were found by the mutation proof rather than by review. Replacing the
    located `taskkill` with a bare name that PATH resolves failed no test — the
    resolver had a test of its own, but nothing tied it to the place it is
    used, which is the same "defined but never wired" gap #14 hit. And dropping
    the pre-wait close left the suite green, because whether the wait parks
    depends on a race.
    """

    async def test_the_located_killer_is_the_one_used(self):
        """With no SYSTEMROOT there is no killer, so this must refuse.

        A version that shelled out to a bare `taskkill` would sail past this on
        any machine where PATH happens to resolve it — which is every machine
        it will ever run on, so nothing would ever catch it.
        """
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)"
        )
        self.addCleanup(reap_tree, process)

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LifecycleFailure):
                await terminate_process_tree(process)

    async def test_the_transport_is_closed_before_the_wait(self):
        """Closing after the wait is too late: by then it is already parked."""
        process = _RecordingProcess()

        await terminate_process_tree(process, forced_wait=1)

        self.assertEqual(["close", "wait"], process.journal)


if __name__ == "__main__":
    unittest.main()
