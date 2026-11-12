# VidLink provider (enc-dec.app TMDB id encryption + vidlink.pro JSON API)
import logging
from typing import List, Tuple, Dict
from urllib.parse import quote

from curl_cffi import requests

log = logging.getLogger("tmdb_tui.providers.vidlink")

VIDLINK_BASE = "https://vidlink.pro"
VIDLINK_ENC_API = "https://enc-dec.app/api/enc-vidlink"
VIDLINK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
VIDLINK_HEADERS = {"User-Agent": VIDLINK_UA, "Referer": "https://vidlink.pro"}


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    try:
        enc_resp = requests.get(f"{VIDLINK_ENC_API}?text={quote(str(tmdb_id), safe='')}", timeout=8)
        enc_resp.raise_for_status()
        encoded = enc_resp.json().get("result")
        if not encoded:
            return []
        is_tv = (media_type or "movie") == "tv"
        if is_tv:
            api_url = f"{VIDLINK_BASE}/api/b/tv/{encoded}/{season_num or 1}/{episode_num or 1}?multiLang=0"
        else:
            api_url = f"{VIDLINK_BASE}/api/b/movie/{encoded}?multiLang=0"
        resp = requests.get(api_url, headers=VIDLINK_HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        stream_data = data.get("stream") or {}
        qualities = stream_data.get("qualities") or {}
    except Exception as e:
        log.warning("VidLink fetch failed: %s", e)
        return []

    def quality_num(key) -> int:
        if not key:
            return 0
        k = str(key)
        if k.lower() == "4k":
            return 2160
        if k.lower() in ("auto", "unknown"):
            return 0
        try:
            return int(k)
        except ValueError:
            return 0

    items = sorted(qualities.items(), key=lambda kv: quality_num(kv[0]), reverse=True)
    streams: List[Tuple[str, Dict[str, str]]] = []
    for kval, entry in items:
        url = (entry or {}).get("url") if isinstance(entry, dict) else None
        if not url:
            continue
        streams.append((url, {"User-Agent": VIDLINK_UA, "Referer": "https://vidlink.pro"}))
    return streams