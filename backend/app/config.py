import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

PREFIX = "SOUNDDROP_"


def env(name: str, default: str) -> str:
    return os.getenv(PREFIX + name, default)


def number(name: str, default: int) -> int:
    value = int(env(name, str(default)))
    if value < 1:
        raise ValueError(f"{PREFIX}{name} must be a positive integer")
    return value


def origins() -> list[str]:
    values = [
        x.strip().rstrip("/")
        for x in env("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if x.strip()
    ]
    for value in values:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or "*" in value
        ):
            raise ValueError(f"{PREFIX}ALLOWED_ORIGINS must contain exact HTTP(S) origins")
    return values


def cookies_file() -> Path | None:
    value = env("YTDLP_COOKIES_FILE", "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ValueError(f"{PREFIX}YTDLP_COOKIES_FILE is missing or unreadable: {value}")
    return path


def proxy() -> str | None:
    value = env("YTDLP_PROXY", "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError(f"{PREFIX}YTDLP_PROXY must be an http(s):// or socks5(h):// URL")
    return value


@dataclass(frozen=True)
class Settings:
    allowed_origins: list[str] = field(default_factory=origins)
    data_dir: Path = field(default_factory=lambda: Path(env("DATA_DIR", "/tmp/sounddrop")))
    max_duration: int = field(default_factory=lambda: number("MAX_DURATION_SECONDS", 3600))
    max_source_bytes: int = field(default_factory=lambda: number("MAX_SOURCE_MB", 256) * 1024**2)
    max_storage_bytes: int = field(default_factory=lambda: number("MAX_STORAGE_MB", 1024) * 1024**2)
    timeout: int = field(default_factory=lambda: number("JOB_TIMEOUT_SECONDS", 1200))
    ttl: int = field(default_factory=lambda: number("FILE_TTL_SECONDS", 3600))
    max_queue: int = field(default_factory=lambda: number("MAX_QUEUE_SIZE", 10))
    hourly_limit: int = field(default_factory=lambda: number("RATE_LIMIT_PER_HOUR", 10))
    # Optional ways past YouTube's blocking of cloud server IPs (see README).
    cookies_file: Path | None = field(default_factory=cookies_file)
    proxy: str | None = field(default_factory=proxy, repr=False)
