# URL metadata parsing — compact, readable labels for the downloads page
import os
import re
import logging
from functools import lru_cache
from typing import List, Tuple, Dict, Optional
from urllib.parse import urlparse, unquote

from stub_main.config import BROWSER_HEADERS

log = logging.getLogger("tmdb_tui.metadata")

_RESOLUTION_RE = re.compile(r"\b(\d{3,4})\s*[pP]\b")

_QUALITY_KEYWORDS = [
    (["bluray", "blu.?ray", "bdrip", "bdr", "remux"], "BluRay"),
    (["web-dl", "webrip", "web dl"], "WEB-DL"),
    (["hdtv", "hdtvrip"], "HDTV"),
    (["dvdrip", "dvd rip"], "DVDRip"),
    (["hdrip", "hd rip"], "HDRip"),
    (["brrip", "br rip"], "BRRip"),
    (["hdcam", "hd cam"], "HDCam"),
    (["camrip", "cam rip"], "CAMRip"),
    (["dvdscr", "dvd scr"], "DVDScr"),
    (["tvrip", "tv rip"], "TVRip"),
]

_CODEC_KEYWORDS = [
    (["x265", "h265", "hevc"], "H.265"),
    (["x264", "h264", "avc"], "H.264"),
    (["av1"], "AV1"),
    (["vp9"], "VP9"),
    (["xvid"], "XviD"),
    (["divx"], "DivX"),
]

_AUDIO_KEYWORDS = [
    (["truehd", "true-hd", "dolby truehd"], "TrueHD"),
    (["dts-hd", "dtshd", "dts master"], "DTS-HD"),
    (["atmos"], "Dolby Atmos"),
    (["dts"], "DTS"),
    (["ac3", "ac-3", "dolby digital"], "AC3"),
    (["aac"], "AAC"),
    (["flac"], "FLAC"),
    (["mp3"], "MP3"),
    (["lpcm"], "LPCM"),
]

_LANGUAGE_KEYWORDS = {
    "english": "English", "eng": "English",
    "hindi": "Hindi", "hin": "Hindi",
    "tamil": "Tamil", "tam": "Tamil",
    "telugu": "Telugu", "tel": "Telugu",
    "malayalam": "Malayalam", "mal": "Malayalam",
    "kannada": "Kannada", "kan": "Kannada",
    "bengali": "Bengali", "ben": "Bengali",
    "marathi": "Marathi", "mar": "Marathi",
    "punjabi": "Punjabi", "pan": "Punjabi",
    "gujarati": "Gujarati", "guj": "Gujarati",
    "spanish": "Spanish", "spa": "Spanish",
    "french": "French", "fra": "French",
    "german": "German", "deu": "German",
    "italian": "Italian", "ita": "Italian",
    "japanese": "Japanese", "jpn": "Japanese",
    "korean": "Korean", "kor": "Korean",
    "chinese": "Chinese", "chi": "Chinese",
    "russian": "Russian", "rus": "Russian",
    "arabic": "Arabic", "ara": "Arabic",
    "portuguese": "Portuguese", "por": "Portuguese",
    "turkish": "Turkish", "tur": "Turkish",
    "vietnamese": "Vietnamese", "vie": "Vietnamese",
    "thai": "Thai", "tha": "Thai",
    "indonesian": "Indonesian", "ind": "Indonesian",
    "dutch": "Dutch", "nld": "Dutch",
    "polish": "Polish", "pol": "Polish",
    "swedish": "Swedish", "swe": "Swedish",
    "norwegian": "Norwegian", "nor": "Norwegian",
    "danish": "Danish", "dan": "Danish",
    "finnish": "Finnish", "fin": "Finnish",
    "czech": "Czech", "ces": "Czech",
    "hungarian": "Hungarian", "hun": "Hungarian",
    "romanian": "Romanian", "ron": "Romanian",
    "greek": "Greek", "ell": "Greek",
    "hebrew": "Hebrew", "heb": "Hebrew",
}


@lru_cache(maxsize=1024)
def _extract_url_metadata(url: str) -> dict:
    """Extract readable metadata tags from a stream URL.

    Only what is encoded in the URL/filename is used — no network
    inspection — so language/quality are best-effort guesses from the
    filename. Returns a dict with filename, title and a list of
    (category, label) tags.
    """
    path = urlparse(url).path
    filename = os.path.basename(path) or path
    filename = filename.split("?")[0].split("#")[0]
    decoded = unquote(filename)
    text = decoded.lower()

    tags: List[Tuple[str, str]] = []

    # --- Resolution ---
    resolution = None
    m = _RESOLUTION_RE.search(text)
    if m:
        resolution = f"{m.group(1)}p"
    else:
        for token, label in (("4k", "4K"), ("2160", "2160p"), ("1080", "1080p"),
                             ("720", "720p"), ("480", "480p"), ("2k", "2K"), ("8k", "8K")):
            if token in text:
                resolution = label
                break
    if resolution:
        tags.append(("resolution", resolution))

    # --- Quality / source ---
    quality = None
    for keywords, label in _QUALITY_KEYWORDS:
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
                quality = label
                break
        if quality:
            break
    if quality:
        tags.append(("quality", quality))

    # --- Language (best-effort from filename only; keep all matches) ---
    languages = []
    for kw, label in _LANGUAGE_KEYWORDS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", text) and label not in languages:
            languages.append(label)
    for label in languages:
        tags.append(("language", label))

    # --- Video codec ---
    codec = None
    for keywords, label in _CODEC_KEYWORDS:
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
                codec = label
                break
        if codec:
            break
    if codec:
        tags.append(("codec", codec))

    # --- Audio ---
    audio = None
    for keywords, label in _AUDIO_KEYWORDS:
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
                audio = label
                break
        if audio:
            break
    if audio:
        tags.append(("audio", audio))

    # --- Container ---
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if ext in {"mkv", "mp4", "avi", "mov", "webm", "ts", "m2ts", "wmv"}:
        tags.append(("container", ext.upper()))

    # --- HDR ---
    hdr = None
    for kw, label in (("hdr10+", "HDR10+"), ("hdr10", "HDR10"),
                      ("dolby vision", "Dolby Vision"), ("dv ", "Dolby Vision")):
        if kw in text:
            hdr = label
            break
    if hdr:
        tags.append(("hdr", hdr))

    # --- Title guess (filename with noise tokens stripped) ---
    title = os.path.splitext(decoded)[0]
    noise = (
        r"\b\d{3,4}p\b", r"\b4k\b", r"\b2k\b", r"\b8k\b",
        r"\bbluray\b", r"\bblu.?ray\b", r"\bweb-dl\b", r"\bwebrip\b",
        r"\bhdtv\b", r"\bdvdrip\b", r"\bhdrip\b", r"\bbrrip\b",
        r"\bx264\b", r"\bx265\b", r"\bh264\b", r"\bh265\b", r"\bhevc\b",
        r"\bav1\b", r"\bvp9\b", r"\bdts\b", r"\bac3\b", r"\baac\b",
        r"\bflac\b", r"\btruehd\b", r"\batmos\b", r"\bhdr10\b",
    )
    for pat in noise:
        title = re.sub(pat, " ", title, flags=re.IGNORECASE)
    title = re.sub(r"[\(\[\{].*?[\)\}\]]", " ", title)
    title = re.sub(r"[._\-]+", " ", title)
    title = " ".join(title.split()).strip(" -_.,")
    if not title:
        title = filename

    return {
        "filename": filename,
        "title": title,
        "tags": tags,
    }


def _fallback_label(url: str) -> str:
    """Label for URLs whose filename carries no usable metadata (e.g. opaque
    IDs/hashes), so the links page stays readable instead of showing hashes."""
    path = urlparse(url).path
    filename = os.path.basename(path) or ""
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if re.fullmatch(r"[a-z0-9]{6,}", filename or ""):
        return f"stream [{ext.upper()}]" if ext else "stream link"
    return filename or "stream link"


@lru_cache(maxsize=2048)
def _url_label(url: str, max_tags: int = 6) -> str:
    """Build a compact, readable label for a stream URL.

    Each link stays its own row (no grouping). Tags are plain text so they
    render on any console. Falls back to a short descriptor when nothing
    can be extracted from the filename.
    """
    meta = _extract_url_metadata(url)
    by_cat: Dict[str, List[str]] = {}
    for category, label in meta["tags"]:
        by_cat.setdefault(category, []).append(label)

    parts: List[str] = []
    for cat in ("resolution", "quality", "language", "codec", "audio", "container", "hdr"):
        for label in by_cat.get(cat, []):
            parts.append(label)
            if len(parts) >= max_tags:
                break
        if len(parts) >= max_tags:
            break

    if not parts:
        return _fallback_label(url)
    return "  ".join(parts)


def _format_size(num_bytes: Optional[int]) -> str:
    """Human-readable byte count."""
    if num_bytes is None:
        return ""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    for unit in ("KB", "MB", "GB", "TB"):
        num_bytes /= 1024.0
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
    return f"{num_bytes:.2f} PB"


def _fetch_content_length(url: str, headers: Dict[str, str], timeout: int = 8) -> Optional[int]:
    """Best-effort Content-Length via HEAD, with a ranged-GET fallback.

    Returns bytes or None when the size cannot be determined (no crash).
    """
    from curl_cffi import requests
    try:
        resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        cl = resp.headers.get("Content-Length") or resp.headers.get("content-length")
        if cl:
            return int(cl)
    except Exception:
        pass
    try:
        hdr = dict(headers)
        hdr["Range"] = "bytes=0-0"
        resp = requests.get(url, headers=hdr, timeout=timeout, stream=True)
        cl = resp.headers.get("Content-Length") or resp.headers.get("content-length")
        try:
            resp.close()
        except Exception:
            pass
        if cl:
            return int(cl)
    except Exception:
        pass
    return None