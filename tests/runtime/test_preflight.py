"""Subscription authentication preflight — normative source: spec section 5.

Everything here is hermetic. No test depends on an authenticated account.
"""

import asyncio
import json
import unittest

from tests.support.preflight_fixtures import (
    CLAUDE_AUTH_OK,
    CLAUDE_EXECUTABLE,
    CODEX_EXECUTABLE,
    CODEX_HOME,
    PARENT_ENV,
    SENTINEL_EMAIL,
    SENTINEL_ORG_ID,
    SENTINEL_ORG_NAME,
    claude_auth_result,
    claude_runner,
    codex_auth_result,
    codex_runner,
)
from whole_life.runtime.childenv import FORBIDDEN_VARIABLES, build_child_env
from whole_life.runtime.claude import ClaudeRuntime
from whole_life.runtime.codex import CodexRuntime
from whole_life.runtime.delegation import REPORTED_ENFORCEMENT
from whole_life.runtime.contract import (
    AgentRuntime,
    EnforcementLevel,
    Provider,
    RunHandle,
)
from whole_life.runtime.launch import PreStartRefusal, RefusalCode

CLAUDE_AUTH_ARGS = ("auth", "status", "--json")
CODEX_AUTH_ARGS = ("login", "status")


def claude(runner=None, parent_env=None) -> ClaudeRuntime:
    return ClaudeRuntime(
        executable=CLAUDE_EXECUTABLE,
        runner=runner or claude_runner(),
        parent_env=PARENT_ENV if parent_env is None else parent_env,
    )


def codex(runner=None, parent_env=None) -> CodexRuntime:
    return CodexRuntime(
        executable=CODEX_EXECUTABLE,
        runner=runner or codex_runner(),
        parent_env=PARENT_ENV if parent_env is None else parent_env,
        codex_home=CODEX_HOME,
    )



def _stranger_handle(adapter):
    """A handle from nowhere — never issued by the adapter under test."""
    return RunHandle(
        run_id="never-started",
        participant_id="claude-01",
        provider=adapter.provider,
    )


class AdapterContractTests(unittest.TestCase):
    def test_exactly_two_adapters_satisfy_the_runtime_contract(self):
        self.assertIsInstance(claude(), AgentRuntime)
        self.assertIsInstance(codex(), AgentRuntime)

    def test_no_contract_operation_is_left_unimplemented(self):
        """With #16 there are no later slices — the surface is complete.

        This test previously asserted the opposite: that `cancel` and `close`
        raised `NotImplementedError`, tracking whichever operations were still
        unfinished. Nothing is now, so it asserts completeness instead. The
        underlying point is unchanged — an operation must not return something
        empty and plausible in place of doing its job.
        """
        for adapter in (claude(), codex()):
            with self.subTest(provider=adapter.provider):
                # Nothing was started, so this has nothing to stop — and must
                # say so by returning, not by raising.
                asyncio.run(adapter.close())

                # A handle this adapter never issued stays a programming error
                # rather than becoming a quiet no-op.
                with self.assertRaises(KeyError):
                    asyncio.run(adapter.cancel(_stranger_handle(adapter)))


class ChildEnvironmentReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_claude_checks_authentication_in_the_sanitized_child_env(self):
        runner = claude_runner()
        adapter = claude(runner)

        await adapter.preflight()

        self.assertEqual(dict(adapter.child_env), runner.env_for(CLAUDE_AUTH_ARGS))
        self.assertEqual(dict(build_child_env(PARENT_ENV)), dict(adapter.child_env))

    async def test_codex_checks_authentication_in_the_sanitized_child_env(self):
        runner = codex_runner()
        adapter = codex(runner)

        await adapter.preflight()

        self.assertEqual(dict(adapter.child_env), runner.env_for(CODEX_AUTH_ARGS))

    async def test_no_forbidden_variable_reaches_either_status_command(self):
        for adapter, runner, args in (
            (None, claude_runner(), CLAUDE_AUTH_ARGS),
            (None, codex_runner(), CODEX_AUTH_ARGS),
        ):
            built = claude(runner) if args == CLAUDE_AUTH_ARGS else codex(runner)
            await built.preflight()
            env = runner.env_for(args)
            for name in FORBIDDEN_VARIABLES:
                with self.subTest(command=args, variable=name):
                    self.assertNotIn(name, env)

    async def test_the_five_named_variables_reach_neither_status_command(self):
        """SP-N1. Spelled out literally, because the test above cannot be.

        The test above iterates `FORBIDDEN_VARIABLES`, so a mutation that both
        removes a name from that constant *and* bypasses the builder leaves it
        checking a set that no longer contains the name it should have caught —
        and passes. Naming the five here is what closes that composite mutation
        at the adapter level, where the child environment actually reaches a
        command.
        """
        for runner, args in (
            (claude_runner(), CLAUDE_AUTH_ARGS),
            (codex_runner(), CODEX_AUTH_ARGS),
        ):
            built = claude(runner) if args == CLAUDE_AUTH_ARGS else codex(runner)
            await built.preflight()
            env = {name.upper() for name in runner.env_for(args)}

            with self.subTest(command=args):
                self.assertNotIn("ANTHROPIC_API_KEY", env)
                self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
                self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
                self.assertNotIn("OPENAI_API_KEY", env)
                self.assertNotIn("CLAUDE_CODE_SIMPLE", env)

    async def test_codex_home_is_explicit_and_identical_for_status_and_version(self):
        runner = codex_runner()
        adapter = codex(runner)

        await adapter.preflight()

        self.assertEqual(str(CODEX_HOME), runner.env_for(CODEX_AUTH_ARGS)["CODEX_HOME"])
        self.assertEqual(
            runner.env_for(("--version",))["CODEX_HOME"],
            runner.env_for(CODEX_AUTH_ARGS)["CODEX_HOME"],
        )


class VersionAllowlistTests(unittest.IsolatedAsyncioTestCase):
    async def _refused(self, adapter) -> PreStartRefusal:
        with self.assertRaises(PreStartRefusal) as caught:
            await adapter.preflight()
        self.assertEqual("pre_start", caught.exception.phase)
        return caught.exception

    async def test_a_version_outside_the_allowlist_is_refused(self):
        """2.1.239 was the pinned version until #13 and is now refused.

        Being previously supported earns a version nothing: the allowlist holds
        the versions with local conformance evidence, and 2.1.239's evidence was
        removed when the installed CLI moved to 2.1.240.
        """
        adapter = claude(claude_runner(version="2.1.239 (Claude Code)"))

        refusal = await self._refused(adapter)

        self.assertEqual(RefusalCode.UNSUPPORTED_CLI_VERSION, refusal.code)

    async def test_a_changed_version_output_format_is_refused(self):
        adapter = claude(claude_runner(version="Claude Code v2.1.240"))

        refusal = await self._refused(adapter)

        self.assertEqual(RefusalCode.UNSUPPORTED_CLI_VERSION, refusal.code)

    async def test_a_nonzero_version_exit_is_refused(self):
        adapter = claude(claude_runner(version_exit=1))

        refusal = await self._refused(adapter)

        self.assertEqual(RefusalCode.UNSUPPORTED_CLI_VERSION, refusal.code)

    async def test_authentication_is_not_even_attempted_on_an_unknown_version(self):
        runner = claude_runner(version="9.9.9 (Claude Code)")

        with self.assertRaises(PreStartRefusal):
            await claude(runner).preflight()

        self.assertEqual([("--version",)], [args for _e, args, _v in runner.calls])


class ConformanceInvalidationTests(unittest.IsolatedAsyncioTestCase):
    """A plan may only carry the record of the *latest* successful preflight.

    `_conformance` is written on success only. Without clearing it first, a
    caller that catches a later preflight failure can still assemble a plan
    carrying the previous canonical record — and the launch gate accepts it,
    because the record is genuine. It is simply no longer true.
    """

    async def test_a_failed_later_preflight_discards_the_earlier_record(self):
        adapter = claude(claude_runner())
        await adapter.preflight()
        self.assertIsNotNone(adapter.conformance)

        adapter._runner = claude_runner(version="9.9.9 (Claude Code)")
        with self.assertRaises(PreStartRefusal):
            await adapter.preflight()

        self.assertIsNone(adapter.conformance)

    async def test_assembly_after_a_failed_preflight_is_refused(self):
        from tests.support.launch_fixtures import turn_request

        adapter = claude(claude_runner())
        await adapter.preflight()
        adapter._runner = claude_runner(version="9.9.9 (Claude Code)")
        with self.assertRaises(PreStartRefusal):
            await adapter.preflight()

        with self.assertRaises(PreStartRefusal) as caught:
            adapter.assemble_launch_plan(turn_request())

        self.assertEqual(RefusalCode.UNSUPPORTED_CLI_VERSION, caught.exception.code)


class ClaudeAuthTests(unittest.IsolatedAsyncioTestCase):
    async def _refused(self, payload=None, **kwargs) -> PreStartRefusal:
        runner = claude_runner(auth=claude_auth_result(payload, **kwargs))
        with self.assertRaises(PreStartRefusal) as caught:
            await claude(runner).preflight()
        return caught.exception

    async def test_the_pinned_subscription_state_is_accepted(self):
        status = await claude().preflight()

        self.assertEqual(Provider.CLAUDE, status.provider)
        self.assertEqual("2.1.240", status.cli_version)
        # What each limit is reported as belongs to the delegation tests, and
        # is asserted there for every axis rather than one axis here. This
        # test is about the authentication decision.
        self.assertIsInstance(
            status.delegation_depth_enforcement, EnforcementLevel
        )

    async def test_a_signed_out_account_is_refused(self):
        refusal = await self._refused({**CLAUDE_AUTH_OK, "loggedIn": False})

        self.assertEqual(RefusalCode.SUBSCRIPTION_AUTH_REQUIRED, refusal.code)

    async def test_an_api_key_auth_method_is_refused(self):
        refusal = await self._refused({**CLAUDE_AUTH_OK, "authMethod": "apiKey"})

        self.assertEqual(RefusalCode.SUBSCRIPTION_AUTH_REQUIRED, refusal.code)

    async def test_a_third_party_api_provider_is_refused(self):
        refusal = await self._refused({**CLAUDE_AUTH_OK, "apiProvider": "bedrock"})

        self.assertEqual(RefusalCode.SUBSCRIPTION_AUTH_REQUIRED, refusal.code)

    async def test_an_empty_subscription_type_is_refused(self):
        refusal = await self._refused({**CLAUDE_AUTH_OK, "subscriptionType": ""})

        self.assertEqual(RefusalCode.SUBSCRIPTION_AUTH_REQUIRED, refusal.code)

    async def test_an_added_field_is_refused_as_an_unknown_schema(self):
        refusal = await self._refused({**CLAUDE_AUTH_OK, "newField": "surprise"})

        self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)

    async def test_a_removed_field_is_refused_as_an_unknown_schema(self):
        payload = {k: v for k, v in CLAUDE_AUTH_OK.items() if k != "apiProvider"}

        refusal = await self._refused(payload)

        self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)

    async def test_a_repeated_authentication_field_is_refused(self):
        """`json.loads` keeps the last duplicate silently.

        A payload saying `loggedIn: false` and then `loggedIn: true` parses to
        a signed-in decision with the pinned field set intact, so every later
        check agrees. An ambiguous payload is a schema we have not evaluated.
        """
        body = json.dumps(CLAUDE_AUTH_OK)
        repeated = '{"loggedIn": false, ' + body[1:]

        refusal = await self._refused(stdout=repeated)

        self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)

    async def test_unparseable_output_is_refused(self):
        refusal = await self._refused(stdout="not json at all")

        self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)

    async def test_a_nonzero_status_exit_is_refused(self):
        refusal = await self._refused(exit_code=1)

        self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)


class AccountIdentifierTests(unittest.IsolatedAsyncioTestCase):
    IDENTIFIERS = (SENTINEL_EMAIL, SENTINEL_ORG_ID, SENTINEL_ORG_NAME)

    async def test_identifiers_do_not_survive_a_successful_preflight(self):
        status = await claude().preflight()

        rendered = f"{status}{status!r}"
        for identifier in self.IDENTIFIERS:
            with self.subTest(identifier=identifier):
                self.assertNotIn(identifier, rendered)

    async def test_identifiers_do_not_survive_a_refusal(self):
        runner = claude_runner(
            auth=claude_auth_result({**CLAUDE_AUTH_OK, "loggedIn": False})
        )

        with self.assertRaises(PreStartRefusal) as caught:
            await claude(runner).preflight()

        rendered = f"{caught.exception}{caught.exception!r}{caught.exception.args}"
        for identifier in self.IDENTIFIERS:
            with self.subTest(identifier=identifier):
                self.assertNotIn(identifier, rendered)


class CodexAuthTests(unittest.IsolatedAsyncioTestCase):
    async def _refused(self, **kwargs) -> PreStartRefusal:
        runner = codex_runner(auth=codex_auth_result(**kwargs))
        with self.assertRaises(PreStartRefusal) as caught:
            await codex(runner).preflight()
        return caught.exception

    async def test_the_pinned_chatgpt_login_is_accepted(self):
        status = await codex().preflight()

        self.assertEqual(Provider.CODEX, status.provider)
        self.assertEqual("0.149.0", status.cli_version)
        self.assertIsInstance(
            status.worker_concurrency_enforcement, EnforcementLevel
        )

    async def test_an_api_key_login_is_refused(self):
        refusal = await self._refused(stderr="Logged in using an API key")

        self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)

    async def test_a_signed_out_state_is_refused(self):
        refusal = await self._refused(stderr="Not logged in")

        self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)

    async def test_a_status_that_also_writes_stdout_is_refused(self):
        """The measured fixture has stdout exactly empty; anything else is a
        changed build.

        Matching stderr alone accepts a status whose shape we never measured —
        including one that says the pinned line and then contradicts it on
        stdout. Whitespace counts: the pinned shape is the empty string, not
        "nothing worth reading".
        """
        for stdout in ("Logged in using an API key", " \r\n", "\n"):
            with self.subTest(stdout=stdout):
                refusal = await self._refused(stdout=stdout)

                self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)

    async def test_a_nonzero_status_exit_is_refused(self):
        refusal = await self._refused(exit_code=1)

        self.assertEqual(RefusalCode.AUTH_STATUS_UNSUPPORTED, refusal.code)

    async def test_raw_status_output_does_not_survive_a_refusal(self):
        status_text = "Not logged in SENTINEL-STDERR-TEXT"

        refusal = await self._refused(stderr=status_text)

        rendered = f"{refusal}{refusal!r}{refusal.args}"
        self.assertNotIn("SENTINEL-STDERR-TEXT", rendered)


if __name__ == "__main__":
    unittest.main()
