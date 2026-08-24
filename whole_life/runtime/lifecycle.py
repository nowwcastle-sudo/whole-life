"""Ending a run, and everything it started. Normative source: spec section 6.

`정상 cancel은 stdin close 또는 provider가 지원하는 graceful signal을 먼저 보낸다.
5초 뒤에도 살아 있으면 Windows process tree를 종료하고, 최대 10초 동안 `wait`한다.`

Two things here are load-bearing rather than incidental.

*Tree*, because on Windows ending the process we started does not end what it
started. A CLI that launched a worker leaves that worker holding the
subscription request open, and a turn we believe we stopped goes on being
billed against the account. `/T` is the difference between stopping a turn and
merely losing sight of it.

*Located, not looked up*, because `taskkill` is the tool whose result is the
evidence that the request stopped. Finding it on PATH would reintroduce the
hazard section 4 closes for the CLI itself — a shim answering in its place, and
a success we cannot believe.
"""

import asyncio
import os
from pathlib import Path

#: Spec 207. Conformance fixtures, not tuning knobs (spec 212): a longer
#: graceful window is a longer wait before a runaway turn actually stops.
GRACEFUL_WAIT_SECONDS = 5
FORCED_WAIT_SECONDS = 10

#: Spec 208. The broker's timer owns this; the runtime exposes it so both sides
#: name the same number.
HARD_TIMEOUT_SECONDS = 20 * 60


class LifecycleFailure(RuntimeError):
    """A shutdown step could not be completed, so nothing may be claimed."""


def system_taskkill(environ=None) -> Path:
    """The Windows `taskkill` under SYSTEMROOT, or refuse."""
    environ = os.environ if environ is None else environ
    root = environ.get("SYSTEMROOT") or environ.get("WINDIR")
    if not root:
        raise LifecycleFailure(
            "SYSTEMROOT is not set, so the process killer cannot be located"
        )

    killer = Path(root) / "System32" / "taskkill.exe"
    if not killer.is_file():
        raise LifecycleFailure(f"{killer.name} was not found under SYSTEMROOT")
    return killer


async def terminate_process_only(
    process, *, forced_wait: float = FORCED_WAIT_SECONDS
) -> bool:
    """End this process alone, for when the tree killer cannot be located.

    Strictly weaker than `terminate_process_tree`: Windows does not cascade a
    kill, so descendants are left running. It is still the part we can do
    without `taskkill`, and doing nothing because we cannot do everything would
    leave the whole tree alive rather than only what we never had a handle on.

    Reaping is the reason this is a function rather than a bare `kill()`. A
    killed process whose exit has not been collected still reports
    `returncode is None`, so `close()` counts it as a live child and the
    promise that nothing is left cannot be kept. The transport is closed first
    for the reason measured in #15: asyncio releases the exit waiter only once
    every pipe reports disconnected, so a run nobody was reading parks here
    forever otherwise.
    """
    if process.returncode is not None:
        return True

    try:
        process.kill()
    except ProcessLookupError:
        return True

    transport = getattr(process, "_transport", None)
    if transport is not None:
        transport.close()

    try:
        await asyncio.wait_for(process.wait(), timeout=forced_wait)
    except (asyncio.TimeoutError, TimeoutError):
        return False

    return True


async def terminate_process_tree(
    process, *, forced_wait: float = FORCED_WAIT_SECONDS
) -> bool:
    """End the process and its descendants, and report whether it ended.

    Returns True only when the process is confirmed reaped. Issuing the kill is
    not the outcome: a nonzero `taskkill`, or a process still alive after the
    bounded wait, both mean descendants may still be running. That is reported
    as False rather than raised, because the caller's next move is to record
    `unknown_outcome` — the ambiguity is the finding, not an exception.
    """
    if process.returncode is not None:
        return True

    killer = system_taskkill()
    killed = await asyncio.create_subprocess_exec(
        str(killer),
        "/F",
        "/T",
        "/PID",
        str(process.pid),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await killed.wait()

    # Closed before the wait, not after. Measured in #15: asyncio only releases
    # a process's exit waiter once *every* pipe reports disconnected, so a run
    # nobody was reading — no drain task, pipes still connected — parks here
    # forever even though the tree is already dead. Safe to close now precisely
    # because the kill has been issued: the thing `close()` would do to a live
    # process has already been done to this one.
    transport = getattr(process, "_transport", None)
    if transport is not None:
        transport.close()

    try:
        await asyncio.wait_for(process.wait(), timeout=forced_wait)
    except (asyncio.TimeoutError, TimeoutError):
        return False

    return True


class TurnDeadline:
    """The participant turn's wall-clock deadline. Spec 208.

    `timeout은 provider 응답이 계속 streaming 중이어도 연장하지 않는다.`

    Fixed at the turn's start and never moved. The alternative anyone reaches
    for — an idle timer reset whenever output arrives — is exactly what that
    sentence forbids, and it fails in the case that matters most: a run that
    streams steadily forever never trips an idle timer, so a runaway turn bills
    the subscription indefinitely while looking perfectly healthy.

    `observed_activity` exists so a caller can report streaming *without* being
    able to extend anything. Having the method and having it do nothing to the
    deadline is the point: there is no code path that moves it.

    The clock is a parameter rather than read here, so callers use one monotonic
    source and tests are arithmetic rather than twenty minutes long.
    """

    __slots__ = ("expires_at", "started_at", "_last_activity_at")

    def __init__(self, *, started_at: float) -> None:
        self.started_at = started_at
        self.expires_at = started_at + HARD_TIMEOUT_SECONDS
        self._last_activity_at: float | None = None

    def observed_activity(self, *, at: float) -> None:
        """Note that the provider is still streaming. Changes no deadline."""
        self._last_activity_at = at

    def expired(self, *, at: float) -> bool:
        return at >= self.expires_at

    def remaining(self, *, at: float) -> float:
        return max(0, self.expires_at - at)
