# Shared helpers for normalising and ordering provider stream URLs
import base64
from typing import Dict, List, Tuple
from urllib.parse import urlparse, parse_qs, unquote

# Hosts that ignore HTTP Range requests. mpv/VLC cannot seek ("skip") inside
# these files, so they are only used as a last resort.
UNSEEKABLE_HOSTS = (
    "video-downloads.googleusercontent.com",
    "drive.google.com",
    "docs.google.com",
)

# Wrapper hosts that serve an HTML player page with the real file URL in a
# query parameter (e.g. https://hbplay.pages.dev/?u=<base64 direct url>).
_WRAPPER_HOST_SUFFIXES = ("pages.dev",)


def _b64decode(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", "replace")
    except Exception:
        try:
            return base64.b64decode(padded).decode("utf-8", "replace")
        except Exception:
            return ""


def unwrap_stream_url(url: str) -> str:
    """Return the direct file URL behind an HTML player-page wrapper.

    Some providers hand out links such as
    ``https://hbplay.pages.dev/?u=<base64 of direct url>``. mpv cannot play
    the HTML page itself, so the embedded URL is decoded here.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not any(host == s or host.endswith("." + s) for s in _WRAPPER_HOST_SUFFIXES):
            return url
        query = parse_qs(parsed.query)
        for key in ("u", "url", "link"):
            values = query.get(key)
            if not values:
                continue
            decoded = _b64decode(unquote(values[0]))
            if decoded.startswith(("http://", "https://")):
                return decoded
    except Exception:
        pass
    return url


def is_seekable(url: str) -> bool:
    """True when the host is expected to honour Range requests."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    for blocked in UNSEEKABLE_HOSTS:
        if host == blocked or host.endswith("." + blocked):
            return False
    return True


def prioritize_seekable(
    streams: List[Tuple[str, Dict[str, str]]]
) -> List[Tuple[str, Dict[str, str]]]:
    """Dedupe by URL and put seekable streams first.

    Seekable sources must come first because the app plays the first stream
    that starts successfully; leading with a non-seekable Google Drive file
    makes playback impossible to skip/seek during. Unseekable ones are kept
    as a last resort. The sort is stable, so provider ordering is preserved
    within each group.
    """
    seen = set()
    unique: List[Tuple[str, Dict[str, str]]] = []
    for url, headers in streams:
        if url in seen:
            continue
        seen.add(url)
        unique.append((url, headers))
    unique.sort(key=lambda item: 0 if is_seekable(item[0]) else 1)
    return unique
