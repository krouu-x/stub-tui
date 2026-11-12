# TMDB API client and provider info cache
import copy
import time
import threading
import logging
from typing import Optional, Dict, Tuple

from curl_cffi import requests

from stub_main.config import TMDB_BASE_URL, HEADERS, SEARCH_CACHE_MAX, _SEARCH_CACHE_LOCK

log = logging.getLogger("tmdb_tui.tmdb")

# Global caches
_TMDB_CLIENT: Optional["TMDBClient"] = None
_TMDB_INFO_CACHE: Dict[str, Tuple[float, Optional[Dict]]] = {}

# Short-lived response cache for TMDB GETs (search/detail/season/trending).
# Repeated navigation (open -> back -> open, resume from history, repeated
# searches) then costs no network round-trip. Values are deep-copied in and
# out so callers can freely mutate the returned dicts.
JSON_CACHE_TTL = 300.0
JSON_CACHE_MAX = 256
_JSON_CACHE: Dict[tuple, Tuple[float, dict]] = {}


def _json_cache_key(url: str, params: Optional[Dict]) -> tuple:
    if params:
        try:
            return (url, tuple(sorted(params.items())))
        except TypeError:
            return (url, str(params))
    return (url,)


class TMDBClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _get_json(self, url, params=None, use_cache: bool = True):
        key = _json_cache_key(url, params)
        if use_cache:
            now = time.monotonic()
            with _SEARCH_CACHE_LOCK:
                hit = _JSON_CACHE.get(key)
            if hit and (now - hit[0]) < JSON_CACHE_TTL:
                return copy.deepcopy(hit[1]), None

        try:
            resp = self.session.get(url, params=params, timeout=10)
        except requests.exceptions.Timeout:
            return None, "Request timed out."
        except requests.exceptions.RequestException as e:
            return None, f"Network error: {e}"

        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            return None, "Malformed JSON response."

        if use_cache:
            with _SEARCH_CACHE_LOCK:
                if len(_JSON_CACHE) >= JSON_CACHE_MAX:
                    _JSON_CACHE.clear()
                _JSON_CACHE[key] = (time.monotonic(), copy.deepcopy(data))
        return data, None

    def search_multi(self, title: str, year: str = ""):
        data, err = self._get_json(f"{TMDB_BASE_URL}/search/multi", params={"query": title})
        if err:
            return [], err

        results = [r for r in data.get("results", []) if r.get("media_type") in ("movie", "tv")]
        if year:
            results = [
                r for r in results
                if (r.get("release_date") or r.get("first_air_date") or "")[:4] == year
            ]
        return results, None

    def get_by_id(self, media_type: str, tmdb_id):
        endpoint = "movie" if media_type == "movie" else "tv"
        return self._get_json(f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}")

    def get_external_ids(self, media_type: str, tmdb_id):
        endpoint = "movie" if media_type == "movie" else "tv"
        return self._get_json(f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}/external_ids")

    def get_season(self, tv_id, season_number):
        return self._get_json(f"{TMDB_BASE_URL}/tv/{tv_id}/season/{season_number}")

    def get_trending(self):
        """Fetch trending movies and TV for the week (both requests in parallel)."""
        from concurrent.futures import ThreadPoolExecutor

        def fetch(media_type):
            return media_type, self._get_json(f"{TMDB_BASE_URL}/trending/{media_type}/week")

        items = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(fetch, ("movie", "tv")))
        for media_type, (data, err) in results:
            if err:
                continue
            for r in data.get("results", []):
                r["media_type"] = media_type
                items.append(r)
        return items, None


def _get_tmdb_client() -> "TMDBClient":
    global _TMDB_CLIENT
    if _TMDB_CLIENT is None:
        _TMDB_CLIENT = TMDBClient()
    return _TMDB_CLIENT


def _tmdb_provider_info(tmdb_id: str, media_type: str = "movie") -> Optional[Dict]:
    """Cached TMDB lookup (title/year/imdb) used by stream providers for matching."""
    mt = media_type if media_type in ("movie", "tv") else "movie"
    key = f"{mt}:{tmdb_id}"
    now = time.time()
    with _SEARCH_CACHE_LOCK:
        hit = _TMDB_INFO_CACHE.get(key)
    cached_at = hit[0] if hit else 0.0
    if hit and (now - cached_at) < 3600.0:
        return hit[1]

    client = _get_tmdb_client()
    data, err = client._get_json(f"{TMDB_BASE_URL}/{mt}/{tmdb_id}")
    info: Optional[Dict] = None
    if not err and data:
        info = {
            "tmdb_id": str(tmdb_id),
            "title": data.get("title") or data.get("name") or "",
            "media_type": mt,
            "year": (data.get("release_date") or data.get("first_air_date") or "")[:4],
            "imdb_id": "",
        }
        ext_data, ext_err = client.get_external_ids(mt, tmdb_id)
        if not ext_err and ext_data:
            info["imdb_id"] = ext_data.get("imdb_id") or ""

    with _SEARCH_CACHE_LOCK:
        if len(_TMDB_INFO_CACHE) >= 128:
            _TMDB_INFO_CACHE.clear()
        _TMDB_INFO_CACHE[key] = (time.time(), info)
    return info