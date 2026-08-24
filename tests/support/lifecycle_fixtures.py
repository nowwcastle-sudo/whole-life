"""Environments in which the Windows tree killer cannot be located.

`system_taskkill` is called with no argument, so it reads *this* process's
environment rather than the child's. These context managers therefore change the
broker's own environment, and everything else on the path — spawn, observe,
cancel — runs for real.

Both are deliberately narrow. `reap_tree` and `force_stop` read `SYSTEMROOT`
too, so a patch still in force during cleanup would kill the cleanup and leave
the probe child behind. Enter them around the call under test, not around the
whole test.
"""

import contextlib
import os
import tempfile
from unittest import mock


@contextlib.contextmanager
def without_windows_roots():
    """Neither root variable is set, so there is nowhere to look."""
    with mock.patch.dict(os.environ):
        os.environ.pop("SYSTEMROOT", None)
        os.environ.pop("WINDIR", None)
        yield


@contextlib.contextmanager
def with_a_root_that_has_no_killer():
    """The root is set and the executable is not under it.

    The second of the two ways `system_taskkill` refuses, and a different
    precondition from the first: the environment is intact, so anything that
    only clears the variables never reaches this branch. A stripped or
    redirected Windows directory produces it.
    """
    with mock.patch.dict(os.environ), tempfile.TemporaryDirectory() as empty:
        os.environ["SYSTEMROOT"] = empty
        os.environ.pop("WINDIR", None)
        yield


#: Both ways the tree killer can refuse: a subTest name, the environment, and
#: the words that refusal uses. The wording is carried here rather than asserted
#: loosely so a test cannot pass on the *other* branch's message — the two
#: refusals are the thing being told apart.
KILLER_UNAVAILABLE = (
    ("no root variable", without_windows_roots, "SYSTEMROOT is not set"),
    ("root without taskkill", with_a_root_that_has_no_killer, "taskkill.exe was not found"),
)
