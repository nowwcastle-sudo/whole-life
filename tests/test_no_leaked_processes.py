"""A leaked child or transport must fail the suite, not whisper on stderr.

`-W error::ResourceWarning` does not do this. Those warnings are raised inside
`__del__`, and an exception in a deallocator becomes `Exception ignored in ...`
printed to stderr — the interpreter swallows it, unittest never sees it, and the
run reports OK. That is exactly how a leak survived a green suite here: the exit
code said nothing was wrong because nothing *could* say so.

So the check reads stderr instead of trusting the status, and it does that from
a separate process because the warnings only appear at garbage collection, after
the tests that caused them have finished.

Acceptance criterion 6 is the reason this is worth a whole extra run: `no hidden
process work` is a claim about what is left behind, and the only honest way to
check it is to look at what was left behind.
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The runtime tests are the ones that start real processes. Not the whole
#: suite: this module lives outside `tests/runtime`, so pointing the child run
#: at that package cannot recurse into this file.
TARGET = "tests/runtime"

LEAK_MARKERS = ("ResourceWarning", "Exception ignored")


class NoLeakedProcessesTests(unittest.TestCase):
    def test_the_runtime_suite_leaks_no_process_or_transport(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-W",
                "error::ResourceWarning",
                "-m",
                "unittest",
                "discover",
                "-t",
                ".",
                "-s",
                TARGET,
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=600,
        )

        leaks = [
            line
            for line in completed.stderr.splitlines()
            if any(marker in line for marker in LEAK_MARKERS)
        ]

        self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
        self.assertEqual(
            [],
            leaks,
            "the runtime suite left a process or transport open:\n"
            + "\n".join(leaks[:12]),
        )


if __name__ == "__main__":
    unittest.main()
