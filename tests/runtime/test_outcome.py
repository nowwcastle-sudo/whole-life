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
