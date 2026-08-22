"""Reading provider output within fixed bounds. Spec section 6.

Two limits, both from the specification and both deliberately not configurable:
one stdout line may be at most 1 MiB, and stderr is kept only as a 64 KiB ring.
The spec is explicit that these are conformance fixtures rather than tuning
knobs — `환경변수 설정으로 미리 일반화하지 않는다` — so they are constants here.

The bounds matter more than they look. A provider that streams without end, or
sends one enormous line, must not be able to grow this process without limit.
And when a limit is hit the answer is a refusal: truncating a JSON line is the
one response that can still parse, producing a smaller valid-looking object the
provider never sent.
"""

import asyncio
from collections.abc import AsyncIterator

#: Spec section 6. One stdout JSONL line, in bytes — counted as bytes because a
#: multi-byte character must not buy extra room.
MAX_STDOUT_LINE_BYTES = 1024 * 1024

#: Spec section 6. All of stderr that is ever held in memory.
STDERR_RING_BYTES = 64 * 1024


class StreamFailure(Exception):
    """A stream could not be read within its bounds, or was not valid UTF-8.

    Carries an allowlisted diagnostic code and nothing from the stream itself.
    The content that broke a limit is exactly the content least safe to quote:
    it is unvalidated, unbounded, and may be a participant's text.
    """

    def __init__(self, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic


class StderrRing:
    """The last `STDERR_RING_BYTES` of stderr, and nothing older.

    Kept for turning into allowlisted diagnostic codes. The raw bytes are never
    persisted, which is why this is a fixed buffer rather than a log.

    The tail is what is kept: a process explains itself as it dies, so the
    useful bytes are the last ones.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def write(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        if len(self._buffer) > STDERR_RING_BYTES:
            del self._buffer[: len(self._buffer) - STDERR_RING_BYTES]

    def snapshot(self) -> bytes:
        return bytes(self._buffer)


async def read_bounded_lines(reader: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield decoded stdout lines, refusing rather than truncating.

    `readline()` is not used: it raises `LimitOverrunError` *after* buffering
    past its limit, and recovering from it means deciding what to do with a
    partial line. Reading one byte-bounded chunk at a time keeps the refusal
    ahead of the growth.
    """
    buffer = bytearray()

    while True:
        chunk = await reader.read(8192)
        if not chunk:
            break

        buffer.extend(chunk)

        while True:
            newline = buffer.find(b"\n")
            if newline == -1:
                break
            line = bytes(buffer[: newline + 1])
            del buffer[: newline + 1]
            yield _decode(line)

        if len(buffer) > MAX_STDOUT_LINE_BYTES:
            raise StreamFailure("StdoutLineTooLarge")

    if buffer:
        if len(buffer) > MAX_STDOUT_LINE_BYTES:
            raise StreamFailure("StdoutLineTooLarge")
        yield _decode(bytes(buffer))


def _decode(line: bytes) -> str:
    text = line.rstrip(b"\r\n") if line.endswith(b"\n") else line
    if len(text) > MAX_STDOUT_LINE_BYTES:
        raise StreamFailure("StdoutLineTooLarge")
    try:
        return text.decode("utf-8")
    except UnicodeDecodeError:
        # Never "replace": a substituted character is a silent edit of provider
        # output, and the turn would continue on altered evidence.
        raise StreamFailure("StdoutNotUtf8") from None
