"""Every Python source in the repository must compile without warnings.

An invalid escape sequence such as ``"C:\\Windows"`` written as ``"C:\\Windows"``
with a single backslash is a SyntaxWarning today and a SyntaxError in a coming
release. It is silent under a default test run, so it is asserted here instead.
"""

import pathlib
import unittest
import warnings

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class SourceHygieneTests(unittest.TestCase):
    def test_every_python_source_compiles_without_warnings(self):
        offences = []

        for path in sorted(REPO_ROOT.rglob("*.py")):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    compile(path.read_text(encoding="utf-8"), str(path), "exec")
                except SyntaxError as error:  # pragma: no cover - reported below
                    offences.append(f"{path.relative_to(REPO_ROOT)}: {error}")
                    continue
            for warning in caught:
                offences.append(
                    f"{path.relative_to(REPO_ROOT)}:{warning.lineno}: "
                    f"{warning.category.__name__}: {warning.message}"
                )

        self.assertEqual([], offences)


if __name__ == "__main__":
    unittest.main()
