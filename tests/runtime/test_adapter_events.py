"""The event boundary reached through the adapter, not around it.

Acceptance criterion 6 asks for the *assembled launch path*. Driving
`SubprocessSpawner` directly proves the observer works; it does not prove a
Broker calling `start_turn` and then `events` gets those events. That distinction
already cost a round on #14, where a helper path stood in for the public method.

So these start a real child through `start_turn` and consume through `events`.
"""

import asyncio
import sys
import unittest
from pathlib import Path

from tests.support.launch_fixtures import turn_request
from tests.support.preflight_fixtures import CODEX_HOME, codex_runner
from whole_life.runtime.codex import CodexRuntime
from whole_life.runtime.contract import RunHandle, RunStatus
from whole_life.runtime.spawn import SubprocessSpawner
from whole_life.runtime.streams import StreamFailure

CLEAN_PARENT_ENV = {"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\Windows\system32"}
COMPLETED = '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}'
MESSAGE = '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"hi"}}'


def child_script(lines, *, exit_code=0):
    body = "import sys\n"
    for line in lines:
        body += f"sys.stdout.write({line!r} + chr(10))\n"
    body += f"sys.stdout.flush()\nsys.exit({exit_code})\n"
    return body


async def adapter_running(lines, *, exit_code=0):
    """A Codex adapter whose turn really starts this interpreter."""
    runtime = CodexRuntime(
        executable=Path(sys.executable),
        runner=codex_runner(),
        parent_env=CLEAN_PARENT_ENV,
        codex_home=CODEX_HOME,
        spawner=SubprocessSpawner(),
    )
    await runtime.preflight()

    # The child is this interpreter, so the assembled provider argv would not
    # mean anything to it. Only the argv is substituted; assembly, the pre-spawn
    # gate and the spawner are the real ones.
    runtime.turn_args_override = ("-c", child_script(lines, exit_code=exit_code))
    return runtime


#: The minimal successful Codex stream, in the official SDK's shape. Written out
#: literally here rather than built from the production constants, so a change
#: to those constants cannot quietly bring this expectation along with it.
#:
#: Provenance: the official Codex SDK (`sdk/typescript/src/events.ts`) documents
#: `type` as the item discriminator, and one approved `codex exec --json` turn
#: against the installed 0.149.0 confirmed it. That run observed exactly
#: `thread.started`, `turn.started`, `item.completed`, `turn.completed`, with
#: item keys `id`, `message`, `text`, `type` and discriminator values
#: `type=agent_message` and `type=error`. Only the shape was recorded — no
#: response text, no reasoning, no raw line.
#:
#: Worth stating plainly: an earlier version of this file invented `item_type`
#: and tested against the invention, so AC 2 was green while proving nothing
#: about real Codex output. Documentation and the installed build now agree, and
#: both were checked.
OFFICIAL_SUCCESS_STREAM = (
    '{"type":"thread.started","thread_id":"th_1"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"hi"}}',
    '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}',
)


class OfficialStreamTests(unittest.IsolatedAsyncioTestCase):
    """AC 2 against the documented shape, through the assembled path."""

    async def test_the_minimal_official_stream_normalizes_and_succeeds(self):
        adapter = await adapter_running(OFFICIAL_SUCCESS_STREAM)

        handle = await adapter.start_turn(turn_request())
        kinds = [event.kind async for event in adapter.events(handle)]
        outcome = await adapter.wait(handle)

        # `thread.started` is recognised and yields no canonical event: section 7
        # reserves `session.started` for Whole Life's own roster and profile,
        # which no provider can supply.
        self.assertEqual(["turn.started", "message.committed", "turn.completed"], kinds)
        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_a_top_level_error_is_a_terminal_failure(self):
        adapter = await adapter_running(('{"type":"error","message":"boom"}',))

        handle = await adapter.start_turn(turn_request())
        kinds = [event.kind async for event in adapter.events(handle)]
        outcome = await adapter.wait(handle)

        self.assertEqual(["turn.failed"], kinds)
        self.assertEqual(RunStatus.FAILED, outcome.status)

    async def test_an_error_item_does_not_spoil_a_successful_turn(self):
        """Measured: the live 0.149.0 turn carried `type=error` and still
        completed with exit 0.

        So an error *item* is not a turn failure, and it is not tool activity
        either. Promoting it to either would contradict an observation we
        actually have — the run must stay `completed` with no event invented for
        it.
        """
        stream = (
            '{"type":"thread.started","thread_id":"th_1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i1","type":"error","message":"x"}}',
            '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"hi"}}',
            '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}',
        )
        adapter = await adapter_running(stream)

        handle = await adapter.start_turn(turn_request())
        kinds = [event.kind async for event in adapter.events(handle)]
        outcome = await adapter.wait(handle)

        self.assertEqual(["turn.started", "message.committed", "turn.completed"], kinds)
        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_reasoning_never_reaches_a_canonical_event(self):
        """Section 7 keeps worker reasoning out of the Journal entirely."""
        stream = (
            '{"type":"item.completed","item":{"id":"i1","type":"reasoning","text":"SENTINEL-REASONING"}}',
            '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}',
        )
        adapter = await adapter_running(stream)

        handle = await adapter.start_turn(turn_request())
        events = [event async for event in adapter.events(handle)]

        self.assertEqual(["turn.completed"], [event.kind for event in events])
        self.assertNotIn("SENTINEL-REASONING", str([event.data for event in events]))

    async def test_an_item_update_carries_no_canonical_event(self):
        """Section 7 has activity *started* and *finished*, and nothing between."""
        stream = (
            '{"type":"item.updated","item":{"id":"item_1","type":"command_execution","command":"ls","aggregated_output":"","status":"completed"}}',
            '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0}}',
        )
        adapter = await adapter_running(stream)

        handle = await adapter.start_turn(turn_request())
        kinds = [event.kind async for event in adapter.events(handle)]

        self.assertEqual(["turn.completed"], kinds)


class AdapterEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_events_flow_from_start_turn_through_the_adapter(self):
        adapter = await adapter_running([MESSAGE, COMPLETED])

        handle = await adapter.start_turn(turn_request())
        kinds = [event.kind async for event in adapter.events(handle)]

        self.assertEqual(["message.committed", "turn.completed"], kinds)

    async def test_the_outcome_is_available_after_the_events(self):
        adapter = await adapter_running([MESSAGE, COMPLETED])

        handle = await adapter.start_turn(turn_request())
        [event async for event in adapter.events(handle)]

        outcome = await adapter.wait(handle)
        self.assertEqual(RunStatus.COMPLETED, outcome.status)

    async def test_a_mid_stream_exit_is_unknown_not_success(self):
        adapter = await adapter_running([MESSAGE])

        handle = await adapter.start_turn(turn_request())
        [event async for event in adapter.events(handle)]

        outcome = await adapter.wait(handle)
        self.assertEqual(RunStatus.UNKNOWN_OUTCOME, outcome.status)

    async def test_a_corrupt_stream_fails_the_turn(self):
        adapter = await adapter_running(['{"type":"mystery"}'])

        handle = await adapter.start_turn(turn_request())

        with self.assertRaises(StreamFailure):
            [event async for event in adapter.events(handle)]

    async def test_an_unknown_handle_is_rejected(self):
        """No turn is started here, because none is needed.

        An earlier version started a real one and never consumed its events, so
        the observer's cleanup never ran and the transport was left open — the
        `unclosed transport ... pid=N running` warnings the Closer reproduced.
        The leak was this test's, but it does point at a real gap: a run that is
        started and never observed is currently cleaned up by nobody. That is
        `close()`, which belongs to #16, and is carried forward as such.
        """
        adapter = await adapter_running([COMPLETED])
        stranger = RunHandle(
            run_id="never-started",
            participant_id="claude-01",
            provider=adapter.provider,
        )

        with self.assertRaises(KeyError):
            [event async for event in adapter.events(stranger)]


if __name__ == "__main__":
    unittest.main()
