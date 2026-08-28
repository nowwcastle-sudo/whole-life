"""Terminal outcome — normative source: spec sections 6 and 7.

`종료 결과를 확정할 수 없으면 성공이나 재시도 가능으로 추정하지 않고
`unknown_outcome`으로 둔다.`

Two independent facts decide how a run ended: what the provider said on its
stream, and what the operating system said about the process. Success requires
both to agree. Everything else is failure or, where the evidence simply is not
there, `unknown_outcome` — which is deliberately *not* a retryable state,
because a turn that may have partially executed against a native session cannot
be safely repeated.
"""

import unittest

from whole_life.runtime.contract import RunStatus
from whole_life.runtime.outcome import TerminalEvent, resolve_outcome


class SuccessRequiresAgreementTests(unittest.TestCase):
    def test_terminal_success_with_exit_zero_is_the_only_success(self):
        outcome = resolve_outcome(TerminalEvent.COMPLETED, exit_code=0)

        self.assertEqual(RunStatus.COMPLETED, outcome.status)
        self.assertEqual(0, outcome.exit_code)

    def test_terminal_success_with_a_nonzero_exit_is_not_success(self):
        """The provider announced completion and then the process failed.

        Reporting success here would hand downstream a result the process
        itself disowned.
        """
        outcome = resolve_outcome(TerminalEvent.COMPLETED, exit_code=1)

        self.assertEqual(RunStatus.FAILED, outcome.status)
        self.assertEqual(1, outcome.exit_code)

    def test_terminal_failure_is_failure_whatever_the_exit_code(self):
        for exit_code in (0, 1, 137):
            with self.subTest(exit_code=exit_code):
                outcome = resolve_outcome(TerminalEvent.FAILED, exit_code=exit_code)

                self.assertEqual(RunStatus.FAILED, outcome.status)


class MissingEvidenceTests(unittest.TestCase):
    def test_exit_zero_without_a_terminal_event_is_unknown(self):
        """The process left quietly. That is not the same as succeeding."""
        outcome = resolve_outcome(TerminalEvent.NONE, exit_code=0)

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)

    def test_a_nonzero_exit_without_a_terminal_event_is_failure(self):
        """Here the evidence is not missing — the process reported failure."""
        outcome = resolve_outcome(TerminalEvent.NONE, exit_code=2)

        self.assertEqual(RunStatus.FAILED, outcome.status)

    def test_an_unobserved_exit_is_never_success(self):
        for terminal in TerminalEvent:
            with self.subTest(terminal=terminal):
                outcome = resolve_outcome(terminal, exit_code=None)

                self.assertNotEqual(RunStatus.COMPLETED, outcome.status)

    def test_an_unobserved_exit_after_terminal_success_is_unknown(self):
        """Half the evidence is not most of the way to success."""
        outcome = resolve_outcome(TerminalEvent.COMPLETED, exit_code=None)

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)


class DiagnosticTests(unittest.TestCase):
    def test_the_diagnostic_is_an_allowlisted_code(self):
        outcome = resolve_outcome(TerminalEvent.NONE, exit_code=0)

        self.assertEqual("TerminalEventMissing", outcome.diagnostic)

    def test_a_successful_outcome_carries_no_diagnostic(self):
        outcome = resolve_outcome(TerminalEvent.COMPLETED, exit_code=0)

        self.assertIsNone(outcome.diagnostic)

    def test_disagreement_is_named_as_such(self):
        outcome = resolve_outcome(TerminalEvent.COMPLETED, exit_code=1)

        self.assertEqual("TerminalExitDisagreement", outcome.diagnostic)


if __name__ == "__main__":
    unittest.main()


class CancellationPrecedenceTests(unittest.TestCase):
    """Whichever came first wins — spec 271, 282, 487, 488.

    User cancellation and the broker's hard timeout are the same rule wearing
    two names. If a terminal result was committed before the cancellation, that
    result stands and the cancellation does not overwrite it. If the
    cancellation came first, the turn is `unknown_outcome` permanently: a clean
    exit or a terminal event arriving afterwards does not upgrade it.

    Spec 282 is explicit that a late clean exit changes nothing, which is the
    case that makes this more than bookkeeping — the process really can end
    tidily after the deadline, and reporting that as success would claim a turn
    completed when the broker had already stopped waiting for it.
    """

    def test_a_cancellation_before_any_terminal_is_unknown(self):
        outcome = resolve_outcome(
            TerminalEvent.NONE, exit_code=0, cancelled_before_terminal=True
        )

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)

    def test_a_late_clean_exit_does_not_upgrade_a_cancelled_run(self):
        """The exact case spec 282 names: tidy ending, deadline already passed."""
        outcome = resolve_outcome(
            TerminalEvent.COMPLETED, exit_code=0, cancelled_before_terminal=True
        )

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)

    def test_a_terminal_committed_before_the_cancellation_still_stands(self):
        outcome = resolve_outcome(
            TerminalEvent.COMPLETED, exit_code=0, cancelled_before_terminal=False
        )

        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    def test_a_committed_failure_is_not_rewritten_as_unknown(self):
        """A failure the provider reported before the cancel is still a failure."""
        outcome = resolve_outcome(
            TerminalEvent.FAILED, exit_code=1, cancelled_before_terminal=False
        )

        self.assertEqual(RunStatus.FAILED, outcome.status)

    def test_the_cancelled_outcome_is_never_retryable(self):
        """`unknown_outcome` carries a diagnostic naming why it cannot retry."""
        outcome = resolve_outcome(
            TerminalEvent.NONE, exit_code=None, cancelled_before_terminal=True
        )

        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)
        self.assertEqual("CancelledBeforeTerminal", outcome.diagnostic)


class CancelledAfterTerminalTests(unittest.TestCase):
    """Our own kill is not evidence that the provider failed — spec 282, 487.

    Once a terminal result is committed, stopping the process afterwards is a
    decision we made. The nonzero exit that follows is the sound of our own
    `taskkill`, so reading it as a disagreement would rewrite a turn the
    provider genuinely completed into a failure — and would do it to every
    post-terminal cancellation, including the ordinary broker shutdown.

    That is the one place where the two-witness rule from section 6 stops
    applying: the second witness is no longer independent.
    """

    def test_a_kill_after_a_committed_completion_stays_completed(self):
        outcome = resolve_outcome(
            TerminalEvent.COMPLETED, exit_code=1, cancelled_after_terminal=True
        )

        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    def test_a_kill_after_a_committed_failure_stays_a_failure(self):
        outcome = resolve_outcome(
            TerminalEvent.FAILED, exit_code=1, cancelled_after_terminal=True
        )

        self.assertEqual(RunStatus.FAILED, outcome.status)

    def test_without_a_cancellation_a_nonzero_exit_still_disagrees(self):
        """The #15 rule is untouched where we did not cause the exit."""
        outcome = resolve_outcome(TerminalEvent.COMPLETED, exit_code=1)

        self.assertEqual(RunStatus.FAILED, outcome.status)
        self.assertEqual("TerminalExitDisagreement", outcome.diagnostic)

    # There is deliberately no test for "cancelled after terminal" combined
    # with no terminal event. That state is a contradiction the observer cannot
    # produce — it decides which flag to set by reading the terminal it already
    # holds — and writing an expectation for it would be specifying behaviour
    # for an unreachable input. The reachable pair is covered end to end in
    # tests/runtime/test_cancel.py, against real processes.
