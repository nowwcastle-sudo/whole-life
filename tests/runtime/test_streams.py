"""Bounded stream observation — normative source: spec section 6.

`stdout JSONL 한 줄의 v0 상한은 1 MiB다. 초과, malformed JSON, truncated UTF-8,
schema 불일치는 turn failure다.`
`stderr는 memory의 64 KiB ring buffer까지만 유지하며 allowlist diagnostic code로
변환한다. raw 내용은 영속화하지 않는다.`

The bounds are the point. A provider that streams without end, or emits one
enormous line, must not be able to grow this process without limit — and the
failure when it tries has to be a refusal, never a truncated line that parses
into something plausible.
"""

import asyncio
import json
import unittest

from whole_life.runtime.streams import (
    MAX_STDOUT_LINE_BYTES,
    STDERR_RING_BYTES,
    StderrRing,
    StreamFailure,
    read_bounded_lines,
)


async def feed(chunks):
    """A StreamReader carrying exactly `chunks`, then EOF."""
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


class BoundedLineTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, chunks):
        return [line async for line in read_bounded_lines(await feed(chunks))]

    async def test_ordinary_lines_arrive_whole(self):
        lines = await self.collect([b'{"a":1}\n{"b":2}\n'])

        self.assertEqual(['{"a":1}', '{"b":2}'], lines)

    async def test_a_line_at_the_limit_is_accepted(self):
        payload = b"x" * (MAX_STDOUT_LINE_BYTES - 2)
        lines = await self.collect([b'"' + payload + b'"\n'])

        self.assertEqual(MAX_STDOUT_LINE_BYTES, len(lines[0]))

    async def test_a_line_beyond_the_limit_fails_rather_than_truncating(self):
        """Truncation is the dangerous option: it can still parse.

        A cut JSON line may deserialize into a smaller, valid-looking object,
        and the turn would proceed on evidence the provider never sent.
        """
        oversized = b"y" * (MAX_STDOUT_LINE_BYTES + 1) + b"\n"

        with self.assertRaises(StreamFailure) as caught:
            await self.collect([oversized])

        self.assertEqual("StdoutLineTooLarge", caught.exception.diagnostic)

    async def test_the_limit_counts_bytes_not_characters(self):
        """A multi-byte character must not buy extra room."""
        wide = "가".encode("utf-8")  # three bytes each
        oversized = wide * (MAX_STDOUT_LINE_BYTES // 3 + 1) + b"\n"

        with self.assertRaises(StreamFailure):
            await self.collect([oversized])

    async def test_truncated_utf_8_fails(self):
        """The first two bytes of a three-byte character, then end of stream."""
        with self.assertRaises(StreamFailure) as caught:
            await self.collect(["가".encode("utf-8")[:2] + b"\n"])

        self.assertEqual("StdoutNotUtf8", caught.exception.diagnostic)

    async def test_a_final_line_without_a_newline_is_still_delivered(self):
        lines = await self.collect([b'{"a":1}'])

        self.assertEqual(['{"a":1}'], lines)

    async def test_growth_is_refused_before_the_stream_ends(self):
        """The unbounded-memory case, and it must fail *while still reading*.

        A provider that never sends a newline would otherwise grow the buffer
        without limit. Feeding the same bytes and then EOF does not test this:
        the end-of-stream check would catch it anyway. So this stream is never
        closed — the reader has to refuse on its own, mid-flight.

        Without the in-loop bound the reader simply waits for more, which is
        precisely the unbounded wait the bound exists to prevent; the timeout
        below is what that failure looks like.
        """
        reader = asyncio.StreamReader()
        reader.feed_data(b"n" * (MAX_STDOUT_LINE_BYTES + 4096))
        # Deliberately no feed_eof(): the provider is still "sending".

        async def drain():
            return [line async for line in read_bounded_lines(reader)]

        with self.assertRaises(StreamFailure) as caught:
            await asyncio.wait_for(drain(), timeout=5)

        self.assertEqual("StdoutLineTooLarge", caught.exception.diagnostic)

    async def test_the_failure_carries_no_stream_content(self):
        sentinel = b"SENTINEL-STREAM-CONTENT"
        oversized = sentinel + b"z" * MAX_STDOUT_LINE_BYTES + b"\n"

        with self.assertRaises(StreamFailure) as caught:
            await self.collect([oversized])

        rendered = f"{caught.exception}{caught.exception!r}{caught.exception.args}"
        self.assertNotIn("SENTINEL-STREAM-CONTENT", rendered)


class StderrRingTests(unittest.TestCase):
    """`raw 내용은 영속화하지 않는다` — the ring exists to be bounded, not read."""

    def test_it_never_exceeds_the_bound(self):
        ring = StderrRing()

        for _ in range(200):
            ring.write(b"z" * 1024)

        self.assertLessEqual(len(ring.snapshot()), STDERR_RING_BYTES)

    def test_it_keeps_the_most_recent_bytes(self):
        """Diagnostics live at the end of a stream, not the beginning."""
        ring = StderrRing()

        ring.write(b"a" * STDERR_RING_BYTES)
        ring.write(b"TAIL")

        self.assertTrue(ring.snapshot().endswith(b"TAIL"))

    def test_a_single_oversized_write_is_also_bounded(self):
        ring = StderrRing()

        ring.write(b"q" * (STDERR_RING_BYTES * 3))

        self.assertEqual(STDERR_RING_BYTES, len(ring.snapshot()))

    def test_the_bound_is_the_specified_sixty_four_kibibytes(self):
        self.assertEqual(64 * 1024, STDERR_RING_BYTES)

    def test_the_stdout_bound_is_the_specified_one_mebibyte(self):
        self.assertEqual(1024 * 1024, MAX_STDOUT_LINE_BYTES)


if __name__ == "__main__":
    unittest.main()
