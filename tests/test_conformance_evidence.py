"""The committed AC 7 evidence must not present unmeasured claims as observations.

The collector runs no `-p` probe, so it cannot observe Claude's bare mode
default. Printing that claim under `## Observations` — beside computed booleans,
under a fresh collection date — restamps it as evidence every time the script is
re-run for a new version. A maintainer reading it would then set
`bare_default=False` for a version nobody measured, which is exactly the value
the #13 gate will enforce.

These tests read the committed documents, not the collector, because the
documents are the artifact the acceptance criterion is about.
"""

import unittest
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "docs" / "conformance"

UNMEASURED_HEADING = "## Not measured by this script"


def evidence_documents() -> list[Path]:
    return sorted(EVIDENCE_DIR.glob("*.md"))


def section(text: str, heading: str) -> str:
    """The body of one `##` section, empty when the heading is absent."""
    _, marker, rest = text.partition(heading + "\n")
    if not marker:
        return ""
    body, _, _ = rest.partition("\n## ")
    return body


class EvidenceDocumentTests(unittest.TestCase):
    def test_there_are_evidence_documents_to_check(self):
        self.assertNotEqual([], evidence_documents())

    def test_every_document_separates_what_it_did_not_measure(self):
        for path in evidence_documents():
            with self.subTest(document=path.name):
                self.assertIn(
                    UNMEASURED_HEADING, path.read_text(encoding="utf-8")
                )

    def test_no_bare_mode_claim_is_recorded_as_an_observation(self):
        """The collector executes no turn, so it cannot observe this."""
        for path in evidence_documents():
            with self.subTest(document=path.name):
                observations = section(path.read_text(encoding="utf-8"), "## Observations")

                self.assertNotIn("bare", observations.lower())

    def test_the_unmeasured_section_says_it_carries_no_new_evidence(self):
        for path in evidence_documents():
            with self.subTest(document=path.name):
                carried = section(path.read_text(encoding="utf-8"), UNMEASURED_HEADING)

                self.assertIn("not measured by this script", carried.lower())


if __name__ == "__main__":
    unittest.main()
