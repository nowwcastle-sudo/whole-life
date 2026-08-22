"""How a run ended. Normative source: spec sections 6 and 7.

Two witnesses decide this: the provider's terminal event on the stream, and the
process exit status from the operating system. They answer different questions —
"did the model finish its work" and "did the program end cleanly" — and success
requires both.

The interesting state is `unknown_outcome`. It exists because the honest answer
to "did this turn happen?" is sometimes that we do not know, and because that
answer must not decay into either optimism or an automatic retry: the turn may
have partially executed against a native session the provider owns, and
repeating it would compound rather than repair.
"""

from enum import StrEnum

from whole_life.runtime.contract import RunOutcome, RunStatus


class TerminalEvent(StrEnum):
    """What the provider's stream said about how the turn ended.

    `NONE` is not an error in itself. A stream can end without a terminal event
    for ordinary reasons — the process was killed, the pipe broke — and what it
    means depends on the exit status it is paired with.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    NONE = "none"


def resolve_outcome(terminal: TerminalEvent, *, exit_code: int | None) -> RunOutcome:
    """Combine the two witnesses into one status.

    Ordered so the failure evidence is consulted before the success evidence.
    A provider that reported failure is failure regardless of how the process
    exited, and a process that exited nonzero is failure regardless of what the
    provider announced beforehand — announcing completion and then exiting
    nonzero disowns the announcement.
    """
    if terminal is TerminalEvent.FAILED:
        return RunOutcome(
            status=RunStatus.FAILED,
            exit_code=exit_code,
            diagnostic="ProviderReportedFailure",
        )

    if exit_code is None:
        # The process outcome was never observed. Half of the evidence is not
        # most of the way to success.
        return RunOutcome(
            status=RunStatus.UNKNOWN_OUTCOME,
            exit_code=None,
            diagnostic="ProcessExitUnobserved",
        )

    if exit_code != 0:
        return RunOutcome(
            status=RunStatus.FAILED,
            exit_code=exit_code,
            diagnostic=(
                "TerminalExitDisagreement"
                if terminal is TerminalEvent.COMPLETED
                else "NonZeroExit"
            ),
        )

    if terminal is TerminalEvent.NONE:
        # A clean exit with nothing said. Quiet is not the same as finished.
        return RunOutcome(
            status=RunStatus.UNKNOWN_OUTCOME,
            exit_code=0,
            diagnostic="TerminalEventMissing",
        )

    return RunOutcome(status=RunStatus.COMPLETED, exit_code=0, diagnostic=None)
