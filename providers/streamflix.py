# StreamFlix provider (api.streamflix.app + Firebase episode data)
import time
import logging
from typing import List, Tuple, Dict

from curl_cffi import requests

log = logging.getLogger("tmdb_tui.providers.streamflix")

STREAMFLIX_API = "https://api.streamflix.app"
STREAMFLIX_FIREBASE_BASE = "https://chilflix-410be-default-rtdb.asia-southeast1.firebasedatabase.app"
STREAMFLIX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)
STREAMFLIX_HEADERS = {
    "User-Agent": STREAMFLIX_UA,
    "Accept": "application/json, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
_STREAMFLIX_DATA_TTL = 30 * 60
_STREAMFLIX_CONFIG_TTL = 5 * 60
_STREAMFLIX_EPISODES_TTL = 60 * 60
_STREAMFLIX_DATA_CACHE = {"ts": 0.0, "items": []}
_STREAMFLIX_CONFIG_CACHE = {"ts": 0.0, "config": {}}
_STREAMFLIX_EPISODES_CACHE = {}


def _streamflix_get_data() -> List[Dict]:
    if (time.time() - _STREAMFLIX_DATA_CACHE["ts"]) < _STREAMFLIX_DATA_TTL:
        return _STREAMFLIX_DATA_CACHE["items"]
    resp = requests.get(f"{STREAMFLIX_API}/data.json", headers=STREAMFLIX_HEADERS, timeout=20)
    resp.raise_for_status()
    items = (resp.json().get("data") or [])
    _STREAMFLIX_DATA_CACHE["ts"] = time.time()
    _STREAMFLIX_DATA_CACHE["items"] = items
    return items


def _streamflix_get_bases() -> List[str]:
    if (time.time() - _STREAMFLIX_CONFIG_CACHE["ts"]) < _STREAMFLIX_CONFIG_TTL:
        config = _STREAMFLIX_CONFIG_CACHE["config"]
    else:
        resp = requests.get(
            f"{STREAMFLIX_API}/config/config-streamflixapp.json",
            headers=STREAMFLIX_HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        config = resp.json()
        _STREAMFLIX_CONFIG_CACHE["ts"] = time.time()
        _STREAMFLIX_CONFIG_CACHE["config"] = config
    bases: List[str] = []
    for u in (config.get("download") or []):
        if u and u not in bases:
            bases.append(u)
    return bases


def _streamflix_get_episodes(movie_key: str, season: int) -> Dict[int, Dict]:
    ck = f"{movie_key}:{season}"
    cached = _STREAMFLIX_EPISODES_CACHE.get(ck)
    if cached and (time.time() - cached[1]) < _STREAMFLIX_EPISODES_TTL:
        return cached[0]
    url = f"{STREAMFLIX_FIREBASE_BASE}/Data/{movie_key}/seasons/{season}/episodes.json"
    resp = requests.get(url, headers=STREAMFLIX_HEADERS, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"StreamFlix episodes HTTP {resp.status_code}")
    raw = resp.json() or {}
    episodes: Dict[int, Dict] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                episodes[int(k)] = v
            except (ValueError, TypeError):
                continue
    _STREAMFLIX_EPISODES_CACHE[ck] = (episodes, time.time())
    return episodes


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    is_tv = (media_type or "movie") == "tv"
    items = _streamflix_get_data()
    match = None
    for it in items:
        if str(it.get("tmdb")) == str(tmdb_id):
            match = it
            break
    if not match:
        return []
    bases = _streamflix_get_bases()
    if not bases:
        return []

    streams: List[Tuple[str, Dict[str, str]]] = []
    if not is_tv:
        movielink = match.get("movielink")
        if not movielink:
            return []
        for base in bases:
            streams.append((f"{base}{movielink}", {"User-Agent": STREAMFLIX_UA}))
        return streams

    season = int(season_num) if str(season_num).isdigit() and int(season_num) > 0 else 1
    episode = int(episode_num) if str(episode_num).isdigit() and int(episode_num) > 0 else 1
    episodes = _streamflix_get_episodes(str(match.get("moviekey") or ""), season)
    ep = episodes.get(episode - 1) or episodes.get(episode)
    if not ep or not ep.get("link"):
        return []
    for base in bases:
        streams.append((f"{base}{ep.get('link')}", {"User-Agent": STREAMFLIX_UA}))
    return streams