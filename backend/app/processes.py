import asyncio
import contextlib
import os
import signal
from collections.abc import Callable, Sequence

from app.models import ConversionError

UNAVAILABLE = (
    "private video",
    "video unavailable",
    "this video is unavailable",
    "this video is no longer available",
    "this video has been removed",
    "this video does not exist",
)
RESTRICTED = (
    "sign in to confirm",
    "confirm your age",
    "age-restricted",
    "members-only",
    "not made this video available in your country",
    "http error 403",
    "http error 429",
    "too many requests",
)
# RLIMIT_FSIZE makes writes fail with EFBIG ("File too large"); Python ignores SIGXFSZ.
TOO_LARGE = ("file too large", "larger than max-filesize")


def classify_failure(stderr: str) -> ConversionError:
    """Map tool output to a safe, user-facing error without exposing the output itself."""
    lines = stderr.lower().splitlines()
    # yt-dlp prefixes fatal errors; FFmpeg (run with -loglevel error) prints only errors.
    message = "\n".join([x for x in lines if x.startswith("error:")] or lines)
    if any(x in message for x in TOO_LARGE):
        return ConversionError("source_too_large", "This video's audio exceeds the size limit.")
    if any(x in message for x in UNAVAILABLE):
        return ConversionError("unavailable", "This video is unavailable or private.")
    if any(x in message for x in RESTRICTED):
        return ConversionError(
            "upstream_restricted",
            "YouTube is restricting access to this video from the conversion server. "
            "Try another video or try again later.",
        )
    return ConversionError(
        "conversion_failed",
        "The video could not be processed. Try another video or retry later.",
    )


async def run_process(
    args: Sequence[str],
    *,
    on_line: Callable[[str], None] | None = None,
    guard: Callable[[], None] | None = None,
) -> str:
    """Bound output, reap the entire process group, and propagate cancellation.

    Callers bound the run time with asyncio.timeout(); cancellation still reaps the processes.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout = bytearray()
    stderr = bytearray()

    async def read(stream: asyncio.StreamReader, output: bytearray, progress: bool) -> None:
        pending = b""
        while chunk := await stream.read(65536):
            output.extend(chunk)
            if len(output) > 4 * 1024**2:
                raise ConversionError("output_limit", "The video returned too much information.")
            if progress and on_line:
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    on_line(line.decode("utf-8", errors="replace"))

    async def monitor() -> None:
        while process.returncode is None:
            if guard:
                guard()
            await asyncio.sleep(0.2)

    tasks = [
        asyncio.create_task(read(process.stdout, stdout, True)),
        asyncio.create_task(read(process.stderr, stderr, False)),
        asyncio.create_task(monitor()),
    ]
    try:
        await asyncio.gather(process.wait(), *tasks)
        if guard:
            guard()
        if process.returncode:
            raise classify_failure(stderr.decode("utf-8", errors="replace"))
        return stdout.decode("utf-8", errors="replace")
    finally:
        # Also terminate descendants if their parent has already exited.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except TimeoutError:
            pass
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Drain to EOF so the pipe transports close now rather than when garbage collected.
        with contextlib.suppress(Exception):
            async with asyncio.timeout(1):
                while await process.stdout.read(65536) or await process.stderr.read(65536):
                    pass
