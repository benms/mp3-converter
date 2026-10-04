import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

from app.models import ConversionError

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


def normalize_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            raise ValueError
        host = parsed.hostname
        parts = parsed.path.strip("/").split("/")
        video_id = ""
        if host in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
            video_id = parts[0]
        elif host in HOSTS:
            if parsed.path == "/watch":
                ids = parse_qs(parsed.query).get("v", [])
                if len(ids) == 1:
                    video_id = ids[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "embed"}:
                video_id = parts[1]
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError
        return f"https://www.youtube.com/watch?v={video_id}"
    except ValueError as exc:
        raise ConversionError(
            "invalid_url", "Paste a valid YouTube video link. Playlists are not supported."
        ) from exc


def download_filename(title: str) -> str:
    title = unicodedata.normalize("NFKC", title)
    title = "".join(c for c in title if not unicodedata.category(c).startswith("C"))
    title = re.sub(r'[<>:"/\\|?*]', "-", title).strip(" .")[:100].rstrip(" .")
    if not title or title.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        title = "audio"
    return f"{title}.mp3"
