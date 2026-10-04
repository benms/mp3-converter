import asyncio
import json
import shutil
import struct
import sys
import wave
from dataclasses import replace

import pytest

from app.config import Settings
from app.converter import (
    Converter,
    access_options,
    check_metadata,
    encode_mp3,
    storage_reserve,
)
from app.models import ConversionError
from app.processes import classify_failure, failure_detail, run_process
from app.urls import download_filename, normalize_url


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/watch?v=BaW_jenozKc",
        "https://www.youtube.com/watch?v=BaW_jenozKc&list=anything&t=3",
        "https://m.youtube.com/watch?v=BaW_jenozKc",
        "https://music.youtube.com/watch?v=BaW_jenozKc",
        "https://youtu.be/BaW_jenozKc?si=tracking",
        "https://www.youtube.com/shorts/BaW_jenozKc",
        "http://youtube.com/embed/BaW_jenozKc",
    ],
)
def test_video_url_normalization(url):
    assert normalize_url(url) == "https://www.youtube.com/watch?v=BaW_jenozKc"


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/playlist?list=123",
        "https://youtube.com/@channel",
        "https://youtube.com.evil.com/watch?v=BaW_jenozKc",
        "https://youtube.com@127.0.0.1/watch?v=BaW_jenozKc",
        "https://user:pass@youtube.com/watch?v=BaW_jenozKc",
        "https://youtube.com:9999/watch?v=BaW_jenozKc",
        "http://127.0.0.1/",
        "file:///etc/passwd",
        "--exec=anything",
        "https://youtube.com/watch?v=BaW_jenozKc&v=BaW_jenozKc",
        "https://youtu.be/../../etc/passwd",
        "https://youtu.be/too_short",
    ],
)
def test_reject_other_targets(url):
    with pytest.raises(ConversionError, match="valid YouTube"):
        normalize_url(url)


def test_safe_download_filenames():
    assert download_filename("../../hello\r\nworld") == "-..-helloworld.mp3"
    assert download_filename("NUL") == "audio.mp3"
    assert download_filename("Тиха музика") == "Тиха музика.mp3"


@pytest.mark.parametrize(
    "info,code",
    [
        ({"duration": 3601}, "video_too_long"),
        ({"duration": None}, "unknown_duration"),
        ({"duration": float("nan")}, "unknown_duration"),
        ({"duration": 20, "is_live": True}, "live_video"),
        ({"duration": 20, "live_status": "is_upcoming"}, "live_video"),
    ],
)
def test_duration_and_live_limits(info, code):
    with pytest.raises(ConversionError) as exc:
        check_metadata(info, Settings())
    assert exc.value.code == code


def test_max_duration_inclusive():
    assert check_metadata({"title": "test", "duration": 3600}, Settings()) == ("test", 3600)


def test_subprocess_timeout_and_output_bound():
    async def scenario():
        started = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await run_process([sys.executable, "-c", "import time; time.sleep(60)"])
        assert asyncio.get_running_loop().time() - started < 5
        with pytest.raises(ConversionError) as exc:
            await run_process([sys.executable, "-c", 'print("x" * (5 * 1024**2))'])
        assert exc.value.code == "output_limit"

    asyncio.run(scenario())


def test_failed_subprocess_does_not_expose_stderr():
    async def scenario():
        with pytest.raises(ConversionError) as exc:
            await run_process(
                [sys.executable, "-c", 'import sys; sys.stderr.write("secret-path"); sys.exit(1)']
            )
        assert "secret-path" not in exc.value.message

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "stderr,code",
    [
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "unavailable"),
        ("ERROR: [youtube] x: Video unavailable. This video has been removed", "unavailable"),
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot", "upstream_restricted"),
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "upstream_restricted"),
        ("ERROR: unable to write data: [Errno 27] File too large", "source_too_large"),
        ("av_interleaved_write_frame(): File too large", "source_too_large"),
        # Words that merely appear in unrelated output must not be misclassified.
        (
            "WARNING: bottleneck removed\nERROR: Requested format is not available",
            "conversion_failed",
        ),
        ("WARNING: 403 retries\nERROR: something else", "conversion_failed"),
    ],
)
def test_failure_classification(stderr, code):
    assert classify_failure(stderr).code == code


def test_failure_detail_keeps_the_error_and_masks_credentials():
    stderr = (
        "WARNING: something minor\n"
        "ERROR: Unable to connect to proxy http://user:secret@proxy.example:8080\n"
    )
    detail = failure_detail(stderr)
    assert detail.startswith("ERROR: Unable to connect to proxy")
    assert "secret" not in detail and "http://***@proxy.example:8080" in detail
    assert failure_detail("") == ""


def test_access_options_use_a_writable_cookie_copy_and_proxy(tmp_path):
    secret = tmp_path / "secret-cookies.txt"
    secret.write_text("# Netscape HTTP Cookie File\n")
    job = tmp_path / "job"
    job.mkdir()
    settings = replace(Settings(), cookies_file=secret, proxy="http://u:p@proxy.example:8080")
    options = access_options(settings, job)
    assert options == [
        "--proxy",
        "http://u:p@proxy.example:8080",
        "--cookies",
        str(job / "cookies.txt"),
    ]
    assert (job / "cookies.txt").read_text() == secret.read_text()
    assert access_options(replace(settings, cookies_file=None, proxy=None), job) == []
    assert "u:p" not in repr(settings)


@pytest.mark.parametrize(
    "name,value",
    [
        ("SOUNDDROP_YTDLP_PROXY", "ftp://proxy.example"),
        ("SOUNDDROP_YTDLP_PROXY", "proxy.example:8080"),
        ("SOUNDDROP_YTDLP_COOKIES_FILE", "/does/not/exist.txt"),
    ],
)
def test_invalid_access_settings_fail_at_startup(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings()


def test_storage_reserve_uses_the_actual_video():
    settings = Settings()
    output = 600 * 192 * 125 + 16 * 1024**2
    assert storage_reserve({"filesize": 10_000_000}, 600, 192, settings) == 11_000_000 + output
    assert storage_reserve({"abr": 128}, 600, 192, settings) == 10_560_000 + output
    assert storage_reserve({}, 600, 192, settings) == settings.max_source_bytes + output


def test_guard_stops_download_on_actual_disk_growth(tmp_path, monkeypatch):
    settings = replace(Settings(), data_dir=tmp_path, max_source_bytes=50)
    directory = tmp_path / "job"
    directory.mkdir()

    async def fake_process(args, **kwargs):
        if "--dump-single-json" in args:
            return json.dumps({"title": "test", "duration": 1})
        (directory / "source.webm").write_bytes(b"x" * 51)
        kwargs["guard"]()

    monkeypatch.setattr("app.converter.run_process", fake_process)

    async def scenario():
        with pytest.raises(ConversionError) as exc:
            await Converter(settings).convert(
                "https://youtu.be/BaW_jenozKc", 192, directory, lambda **_: None
            )
        assert exc.value.code == "source_too_large"

    asyncio.run(scenario())


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg required; run container tests",
)
@pytest.mark.parametrize("bitrate", [128, 192, 320])
def test_real_mp3_encoding(tmp_path, bitrate):
    source = tmp_path / "fixture.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        audio.writeframes(struct.pack("<h", 1000) * 44100)
    output = tmp_path / "audio.mp3"

    async def scenario():
        await encode_mp3(source, output, bitrate, 1, Settings(), lambda: None)
        metadata = json.loads(
            await run_process(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration,bit_rate",
                    "-of",
                    "json",
                    str(output),
                ]
            )
        )
        assert abs(float(metadata["format"]["duration"]) - 1) < 0.15
        assert abs(int(metadata["format"]["bit_rate"]) - bitrate * 1000) < bitrate * 100

    asyncio.run(scenario())
    assert output.stat().st_size > 1000
