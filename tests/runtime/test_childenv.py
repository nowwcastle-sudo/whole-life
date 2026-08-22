"""The sanitized child environment — normative source: spec section 5.

`codex login status` prints the same thing whether or not `OPENAI_API_KEY` is
set (measured on 0.148.0 and re-measured on 0.149.0), so keeping the variable
out of the child environment is not one detection path among several. It is the
only one. These tests are what stands behind that.
"""

import unittest

from whole_life.runtime.childenv import (
    FORBIDDEN_VARIABLES,
    build_child_env,
)
from whole_life.runtime.launch import PreStartRefusal, RefusalCode

PARENT = {
    "SYSTEMROOT": r"C:\Windows",
    "PATH": r"C:\Windows\system32",
    "LANG": "en-US.UTF-8",
    "SOME_UNRELATED_TOOL_HOME": r"D:\unrelated",
}


class ForbiddenVariableTests(unittest.TestCase):
    def test_no_forbidden_variable_is_ever_inherited(self):
        parent = dict(PARENT)
        for name in FORBIDDEN_VARIABLES:
            parent[name] = "inherited-value"

        env = build_child_env(parent)

        for name in FORBIDDEN_VARIABLES:
            with self.subTest(variable=name):
                self.assertNotIn(name, env)

    def test_the_forbidden_set_is_exactly_the_five_the_spec_names(self):
        self.assertEqual(
            {
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "OPENAI_API_KEY",
                "CLAUDE_CODE_SIMPLE",
            },
            set(FORBIDDEN_VARIABLES),
        )

    def test_every_forbidden_name_is_upper_case(self):
        """Otherwise the upper-cased membership test silently stops matching.

        `build_child_env` compares `FORBIDDEN_VARIABLES` against upper-cased
        environment names. A mixed-case entry added later would match nothing and
        be carried into the child — a fail-open direction. The module asserts this
        at import too, but `python -O` strips assertions and this must hold there.
        """
        for name in FORBIDDEN_VARIABLES:
            with self.subTest(name=name):
                self.assertEqual(name.upper(), name)

    def test_an_extra_that_sets_a_forbidden_variable_is_refused(self):
        with self.assertRaises(PreStartRefusal) as caught:
            build_child_env(PARENT, extra={"OPENAI_API_KEY": "sk-injected"})

        self.assertEqual(
            RefusalCode.CHILD_ENV_FORBIDDEN_VARIABLE, caught.exception.code
        )

    def test_a_case_variant_of_a_forbidden_name_is_refused(self):
        """Windows environment names are case-insensitive; this check was not.

        `openai_api_key` reaches the child as the same variable as
        `OPENAI_API_KEY`, so a case-sensitive comparison hands the child exactly
        the credential path the control exists to remove. Spelled out literally
        rather than derived from FORBIDDEN_VARIABLES.
        """
        for name in (
            "openai_api_key",
            "Anthropic_Api_Key",
            "anthropic_auth_token",
            "claude_code_oauth_token",
            "Claude_Code_Simple",
        ):
            with self.subTest(name=name):
                with self.assertRaises(PreStartRefusal) as caught:
                    build_child_env(PARENT, extra={name: "sk-injected"})

                self.assertEqual(
                    RefusalCode.CHILD_ENV_FORBIDDEN_VARIABLE, caught.exception.code
                )

    def test_refusal_does_not_carry_the_environment_value(self):
        with self.assertRaises(PreStartRefusal) as caught:
            build_child_env(PARENT, extra={"ANTHROPIC_API_KEY": "SENTINEL-KEY-VALUE"})

        rendered = f"{caught.exception}{caught.exception!r}{caught.exception.args}"
        self.assertNotIn("SENTINEL-KEY-VALUE", rendered)


    def test_the_five_named_variables_are_absent_however_the_constant_changes(self):
        """Spelled out literally: this must not follow FORBIDDEN_VARIABLES.

        A mutation that drops a name from the constant would make a test that
        iterates the constant stop checking for it, and pass.
        """
        parent = dict(PARENT)
        parent.update(
            {
                "ANTHROPIC_API_KEY": "v",
                "ANTHROPIC_AUTH_TOKEN": "v",
                "CLAUDE_CODE_OAUTH_TOKEN": "v",
                "OPENAI_API_KEY": "v",
                "CLAUDE_CODE_SIMPLE": "1",
            }
        )

        env = build_child_env(parent)

        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_SIMPLE", env)


class InheritanceTests(unittest.TestCase):
    def test_only_allowlisted_parent_variables_are_inherited(self):
        env = build_child_env(PARENT)

        self.assertEqual(r"C:\Windows", env["SYSTEMROOT"])
        self.assertNotIn("SOME_UNRELATED_TOOL_HOME", env)

    def test_explicit_extras_are_added(self):
        env = build_child_env(PARENT, extra={"CODEX_HOME": r"D:\codex-home"})

        self.assertEqual(r"D:\codex-home", env["CODEX_HOME"])

    def test_the_parent_environment_is_not_modified(self):
        parent = dict(PARENT)
        parent["ANTHROPIC_API_KEY"] = "still-here"

        build_child_env(parent, extra={"CODEX_HOME": r"D:\codex-home"})

        self.assertEqual("still-here", parent["ANTHROPIC_API_KEY"])
        self.assertNotIn("CODEX_HOME", parent)

    def test_the_child_environment_cannot_be_mutated_afterwards(self):
        env = build_child_env(PARENT)

        with self.assertRaises(TypeError):
            env["CLAUDE_CODE_SIMPLE"] = "1"

    def test_two_builds_from_the_same_parent_are_equal(self):
        self.assertEqual(dict(build_child_env(PARENT)), dict(build_child_env(PARENT)))


if __name__ == "__main__":
    unittest.main()
