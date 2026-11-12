# VaPlayer provider (streamdata.vaplayer.ru)
import logging
from typing import List, Tuple, Dict, Optional
from urllib.parse import urlencode

from curl_cffi import requests

from stub_main.config import BROWSER_HEADERS
from stub_main.tmdb import _get_tmdb_client, _tmdb_provider_info, _SEARCH_CACHE_LOCK, _TMDB_INFO_CACHE

log = logging.getLogger("tmdb_tui.providers.vaplayer")

VAPPLAYER_API = "https://streamdata.vaplayer.ru/api.php"
VAPPLAYER_ORIGIN = "https://nextgencloudfabric.com"
VAPLAYER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _vaplayer_fetch_streams(
    imdb_id: str, is_tv: bool, season: Optional[int], episode: Optional[int]
) -> Tuple[List[str], Dict[str, str]]:
    params = {"imdb": imdb_id, "type": "tv" if is_tv else "movie"}
    if is_tv and season and episode:
        params["season"] = str(season)
        params["episode"] = str(episode)
    url = f"{VAPPLAYER_API}?{urlencode(params)}"
    if is_tv and season and episode:
        referer = f"{VAPPLAYER_ORIGIN}/embed/tv/{imdb_id}/{season}/{episode}"
    else:
        referer = f"{VAPPLAYER_ORIGIN}/embed/movie/{imdb_id}"
    headers = {
        "User-Agent": VAPLAYER_UA,
        "Referer": referer,
        "Origin": VAPPLAYER_ORIGIN,
    }
    resp = requests.get(url, headers=headers, timeout=12)
    if resp.status_code != 200:
        raise Exception(f"VaPlayer API HTTP {resp.status_code}")
    data = resp.json()
    status = data.get("status_code") if isinstance(data, dict) else None
    if status is not None and str(status) != "200":
        raise Exception(f"VaPlayer API error: {data.get('error') if isinstance(data, dict) else status}")
    stream_urls = ((data or {}).get("data") or {}).get("stream_urls") or []
    return [u for u in (stream_urls or []) if isinstance(u, str) and u], headers


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    info = _tmdb_provider_info(tmdb_id, media_type)
    if not info:
        return []
    imdb_id = info.get("imdb_id") or ""
    if not imdb_id:
        return []
    is_tv = info.get("media_type") == "tv"
    season = int(season_num) if is_tv and str(season_num).isdigit() and int(season_num) > 0 else None
    episode = int(episode_num) if is_tv and str(episode_num).isdigit() and int(episode_num) > 0 else None
    try:
        stream_urls, headers = _vaplayer_fetch_streams(imdb_id, is_tv, season, episode)
    except Exception as e:
        log.warning("VaPlayer fetch failed: %s", e)
        return []
    return [(u, dict(headers)) for u in stream_urls]