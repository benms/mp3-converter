import asyncio
import json
import math
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.models import ConversionError, Stage
from app.processes import run_process

Update = Callable[..., None]


def directory_size(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass
    return total


def check_space(settings: Settings, reserve: int) -> int:
    """Reject work that cannot fit; return the storage currently in use."""
    used = directory_size(settings.data_dir)
    if used + reserve > settings.max_storage_bytes:
        raise ConversionError("storage_full", "Temporary storage is full. Please retry later.")
    if shutil.disk_usage(settings.data_dir).free < reserve:
        raise ConversionError("storage_full", "The conversion server is low on disk space.")
    return used


def storage_reserve(info: dict, duration: float, bitrate: int, settings: Settings) -> int:
    """Estimate a job's peak storage: its source, one transient fragment, and the MP3."""
    source = info.get("filesize") or info.get("filesize_approx")
    if not isinstance(source, (int, float)) or source <= 0:
        kbps = info.get("abr") or info.get("tbr")
        valid = isinstance(kbps, (int, float)) and kbps > 0
        source = kbps * 1000 / 8 * duration if valid else settings.max_source_bytes
    source = min(settings.max_source_bytes, int(source * 1.1))
    return source + int(duration * bitrate * 1000 / 8) + 16 * 1024**2


def downloaded_source(directory: Path) -> Path:
    sources = [p for p in directory.glob("source.*") if p.suffix not in {".part", ".ytdl"}]
    if len(sources) != 1 or sources[0].stat().st_size == 0:
        raise ConversionError(
            "source_too_large", "No audio was downloaded, or it exceeds the size limit."
        )
    return sources[0]


def check_metadata(info: dict, settings: Settings) -> tuple[str, float]:
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise ConversionError(
            "live_video", "Live streams cannot be converted. Choose a finished video."
        )
    duration = info.get("duration")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ConversionError("unknown_duration", "The video's duration could not be determined.")
    if duration > settings.max_duration:
        raise ConversionError(
            "video_too_long",
            f"Choose a video no longer than {settings.max_duration // 60} minutes.",
        )
    return str(info.get("title") or "YouTube audio")[:500], float(duration)


async def encode_mp3(
    source: Path,
    output: Path,
    bitrate: int,
    duration: float,
    settings: Settings,
    guard: Callable[[], None],
) -> None:
    output_limit = int(settings.max_duration * bitrate * 1000 / 8) + 2 * 1024**2
    await run_process(
        [
            sys.executable,
            "-m",
            "app.child",
            str(output_limit),
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-map_metadata",
            "-1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{bitrate}k",
            "-threads",
            "1",
            str(output),
        ],
        guard=guard,
    )
    async with asyncio.timeout(30):
        probe = await run_process(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name:format=duration",
                "-of",
                "json",
                str(output),
            ],
        )
    data = json.loads(probe)
    actual_duration = float(data.get("format", {}).get("duration", 0))
    codecs = [x.get("codec_name") for x in data.get("streams", [])]
    if (
        "mp3" not in codecs
        or actual_duration <= 0
        or abs(actual_duration - duration) > max(3, duration * 0.01)
    ):
        raise ConversionError("invalid_output", "The audio could not be converted completely.")


class Converter:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def convert(self, url: str, bitrate: int, directory: Path, update: Update) -> Path:
        settings = self.settings
        base = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-plugin-dirs",
            "--no-playlist",
            "--no-cache-dir",
            "--socket-timeout",
            "15",
            "--retries",
            "2",
            "--fragment-retries",
            "2",
            "--js-runtimes",
            "deno",
            "--format",
            # Some videos have no audio-only stream; FFmpeg extracts the audio either way.
            "bestaudio/best",
        ]
        update(stage=Stage.INSPECTING, progress=None)
        info = json.loads(await run_process([*base, "--dump-single-json", "--skip-download", url]))
        title, duration = check_metadata(info, settings)
        baseline = check_space(settings, storage_reserve(info, duration, bitrate, settings))
        update(title=title, duration=duration, stage=Stage.DOWNLOADING)

        def guard() -> None:
            # Only the running job writes, so usage is the baseline plus its own directory.
            if baseline + directory_size(directory) > settings.max_storage_bytes:
                raise ConversionError(
                    "storage_full", "Temporary storage is full. Please retry later."
                )

        def source_guard() -> None:
            guard()
            if directory_size(directory) > settings.max_source_bytes:
                raise ConversionError(
                    "source_too_large", "This video's audio exceeds the size limit."
                )

        def progress(line: str) -> None:
            if not line.startswith("SD:"):
                return
            try:
                item = json.loads(line[3:])
                downloaded = item.get("downloaded_bytes")
                total = item.get("total_bytes") or item.get("total_bytes_estimate")
                if isinstance(total, (int, float)) and total > settings.max_source_bytes:
                    raise ConversionError(
                        "source_too_large", "This video's audio exceeds the size limit."
                    )
                if isinstance(downloaded, (int, float)) and downloaded > settings.max_source_bytes:
                    raise ConversionError(
                        "source_too_large", "This video's audio exceeds the size limit."
                    )
                if (
                    isinstance(downloaded, (int, float))
                    and isinstance(total, (int, float))
                    and total > 0
                ):
                    update(progress=round(max(0, min(100, downloaded / total * 100)), 1))
            except (ValueError, TypeError, AttributeError):
                pass

        await run_process(
            [
                sys.executable,
                "-m",
                "app.child",
                str(settings.max_source_bytes),
                *base,
                "--newline",
                "--progress",
                "--progress-delta",
                "1",
                "--no-warnings",
                "--progress-template",
                "download:SD:%(progress)j",
                "--concurrent-fragments",
                "1",
                "--max-filesize",
                str(settings.max_source_bytes),
                "--output",
                str(directory / "source.%(ext)s"),
                url,
            ],
            on_line=progress,
            guard=source_guard,
        )
        source = downloaded_source(directory)
        update(stage=Stage.CONVERTING, progress=None)
        output = directory / "audio.mp3"
        await encode_mp3(source, output, bitrate, duration, settings, guard)
        source.unlink(missing_ok=True)
        return output


def tools_ready() -> dict[str, bool]:
    from importlib.util import find_spec

    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "deno": shutil.which("deno") is not None,
        "yt_dlp": find_spec("yt_dlp") is not None,
        "yt_dlp_ejs": find_spec("yt_dlp_ejs") is not None,
    }
