# JS Server Provider - Client for TMDB-Embed-API-main REST API
import os
import time
import logging
import threading
from typing import List, Tuple, Dict, Optional
from urllib.parse import urljoin

from curl_cffi import requests

from stub_main.config import BROWSER_HEADERS
from stub_main.providers.stream_utils import (
    is_seekable,
    prioritize_seekable,
    unwrap_stream_url,
)

log = logging.getLogger("tmdb_tui.providers.js_server")

# Default JS server URL - can be overridden via environment variable
JS_SERVER_URL = os.environ.get("TMDB_JS_SERVER_URL", "http://localhost:8787")

# Short-lived cache of resolved streams so navigating (links -> play -> back)
# or retrying a playback does not re-scrape every provider on the server.
STREAM_CACHE_TTL = 60.0
STREAM_CACHE_MAX = 128
_STREAM_CACHE: Dict[tuple, Tuple[float, List[Tuple[str, Dict[str, str]]]]] = {}
_STREAM_CACHE_LOCK = threading.Lock()

# Set by js_server_manager while the Node.js server is starting in the
# background, so a fetch can wait for it instead of failing instantly.
_server_booting = threading.Event()
SERVER_READY_TIMEOUT = float(os.environ.get("TMDB_JS_SERVER_READY_TIMEOUT", "20"))


def mark_server_booting(booting: bool) -> None:
    """Called by js_server_manager around the background start-up."""
    if booting:
        _server_booting.set()
    else:
        _server_booting.clear()


def _wait_for_server(client: "JSServerClient") -> bool:
    """Return True once the server is healthy, waiting out a cold start.

    The TUI no longer blocks at launch, so the first stream request may race
    the server boot. While the manager reports a boot in progress we poll
    briefly; otherwise this is just a single health check.
    """
    if client.check_health():
        return True
    deadline = time.monotonic() + max(0.0, SERVER_READY_TIMEOUT)
    while _server_booting.is_set() and time.monotonic() < deadline:
        time.sleep(0.2)
        if client.check_health():
            return True
    return client.check_health()



class JSServerClient:
    """Client for communicating with the TMDB-Embed-API-main JS server."""

    def __init__(self, base_url: str = None):
        self.base_url = base_url or JS_SERVER_URL
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "TMDB-TUI/1.0",
            "Accept": "application/json",
        })
        self._health_ok_until = 0.0

    def _make_request(
        self,
        endpoint: str,
        params: Dict = None,
        timeout: float = 30,
        attempts: int = 3,
        retry_delay: float = 0.4,
    ) -> Dict:
        """Make a request to the JS server API (with a small retry for transient blips)."""
        url = urljoin(self.base_url, endpoint)
        last_err = None
        for attempt in range(attempts):
            resp = None
            try:
                resp = self.session.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as e:
                status = getattr(resp, "status_code", None)
                if status == 503:
                    raise Exception(f"Provider disabled or unavailable: {e}") from e
                raise Exception(f"JS server error ({status}): {e}") from e
            except Exception as e:
                last_err = e
            if attempt < attempts - 1:
                time.sleep(retry_delay)
        raise Exception(
            f"JS server request failed after {attempts} attempts: {last_err}"
        )

    def get_providers(self) -> List[Dict]:
        """Get list of available providers from the JS server."""
        data = self._make_request("/api/providers")
        return data.get("providers", [])

    def get_streams(
        self,
        tmdb_id: str,
        media_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        provider: Optional[str] = None,
        fast: bool = False,
    ) -> Dict:
        """
        Get streams from the JS server.

        Args:
            tmdb_id: TMDB ID
            media_type: "movie" or "tv"
            season: Season number (for TV)
            episode: Episode number (for TV)
            provider: Specific provider name, or None for all enabled providers
            fast: Ask the server not to wait for slow providers (playback path)

        Returns:
            The full response payload. ``payload["streams"]`` holds the stream
            objects and ``payload["partial"]`` is set when the server answered
            before slower providers finished (the rest keep loading server-side).
        """
        # Convert media_type to JS server format
        js_type = "movie" if media_type == "movie" else "series"

        if provider:
            endpoint = f"/api/streams/{provider}/{js_type}/{tmdb_id}"
        else:
            endpoint = f"/api/streams/{js_type}/{tmdb_id}"

        params = {}
        if season:
            params["season"] = season
        if episode:
            params["episode"] = episode
        if fast:
            params["fast"] = "1"

        return self._make_request(endpoint, params=params)

    def check_health(self, timeout: float = 2.0, ttl: float = 10.0) -> bool:
        """Check if JS server is reachable.

        A successful probe is cached for a short while so that each stream
        fetch does not pay for an extra HTTP round-trip.
        """
        now = time.monotonic()
        if now < self._health_ok_until:
            return True
        try:
            data = self._make_request("/api/health", timeout=timeout, attempts=1)
            ok = bool(data.get("ok", False))
            if ok:
                self._health_ok_until = now + ttl
            return ok
        except Exception:
            return False


# Global client instance
_js_client: Optional[JSServerClient] = None


def _get_js_client() -> JSServerClient:
    global _js_client
    if _js_client is None:
        _js_client = JSServerClient()
    return _js_client


def fetch_streams(
    tmdb_id: str,
    title: str,
    media_type: str,
    season_num: str,
    episode_num: str,
    year: str = "",
    fast: bool = False,
) -> Tuple[List[Tuple[str, Dict[str, str]]], bool]:
    """
    Fetch streams from the JS server, reporting whether the list is complete.

    This replaces all inline Python providers by delegating to the
    TMDB-Embed-API-main Node.js server.

    Args:
        fast: Ask the server to answer as soon as a few providers are ready
            instead of waiting for all of them. Used for playback and for the
            first paint of the downloads list.

    Returns:
        ``(streams, partial)``. ``partial`` is True when the server answered
        early and more links are still being resolved in the background. Call
        again to pick up the completed list (partial responses are never
        cached).
    """
    client = _get_js_client()

    # Parse season/episode up front so the cache key is complete.
    season = int(season_num) if media_type == "tv" and season_num and season_num.isdigit() else None
    episode = int(episode_num) if media_type == "tv" and episode_num and episode_num.isdigit() else None

    cache_key = (str(tmdb_id), media_type, season, episode)
    now = time.monotonic()
    with _STREAM_CACHE_LOCK:
        hit = _STREAM_CACHE.get(cache_key)
    if hit and (now - hit[0]) < STREAM_CACHE_TTL:
        log.info("JS Server: stream cache hit for %s", cache_key)
        return [(url, dict(headers)) for url, headers in hit[1]], False

    # Check server health first (cached for a few seconds inside the client).
    # A background start-up from the TUI is waited out rather than failed.
    if not _wait_for_server(client):
        raise Exception(
            f"JS server not reachable at {client.base_url}. "
            "Start it with: cd stub_main/server && npm start"
        )

    # Get streams from all enabled providers. The server may answer early with
    # a partial payload while slower providers keep loading; that partial
    # response is usable immediately and is not cached here so the next fetch
    # can pick up the completed result.
    payload = client.get_streams(tmdb_id, media_type, season, episode, fast=fast)
    streams_data = payload.get("streams", []) or []
    partial = bool(payload.get("partial"))

    if not streams_data:
        if partial:
            return [], True
        raise Exception("No streams returned from JS server")

    # Convert to our format: (url, headers)
    # Also preserve provider name and quality from JS server
    candidate_streams: List[Tuple[str, Dict[str, str]]] = []
    for stream in streams_data:
        url = stream.get("url") or stream.get("streamUrl") or stream.get("link")
        if not url:
            continue

        # Player-page wrappers (e.g. hbplay.pages.dev/?u=<base64>) are HTML,
        # not video files: play the direct file behind them instead.
        url = unwrap_stream_url(url)

        # Extract headers if provided
        headers = stream.get("headers", {})
        # Ensure User-Agent is set
        if "User-Agent" not in headers and "user-agent" not in headers:
            headers["User-Agent"] = BROWSER_HEADERS["User-Agent"]

        # Add referer if provided (accept common key variants: "referer", "referrer")
        referer = None
        if isinstance(stream, dict):
            for key, value in stream.items():
                if isinstance(value, str) and str(key).lower() in ("referer", "referrer") and value.strip():
                    referer = value.strip()
                    break
        if referer:
            headers["Referer"] = referer

        # Preserve underlying provider name and quality from JS server
        provider_name = stream.get("provider") or stream.get("source") or "JS Server"
        if provider_name:
            headers["X-Provider"] = provider_name

        quality = stream.get("quality") or stream.get("resolution")
        if quality:
            headers["X-Quality"] = quality

        candidate_streams.append((url, headers))

    if not candidate_streams:
        if partial:
            return [], True
        raise Exception("No valid stream URLs found in JS server response")

    # Seekable sources first: the app plays the first stream that starts, and
    # non-seekable ones (Google Drive) cannot be skipped/seeked inside.
    candidate_streams = prioritize_seekable(candidate_streams)
    seekable = sum(1 for url, _ in candidate_streams if is_seekable(url))
    log.info(
        "JS Server: got %d playable stream(s) (%d seekable)%s",
        len(candidate_streams),
        seekable,
        " [partial]" if partial else "",
    )

    # Cache a complete copy briefly; callers get their own copy back so header
    # edits (e.g. X-Provider) never leak into the cache. Partial responses are
    # deliberately not cached — the server is still filling in providers, and
    # the next fetch should be able to return the complete list.
    if not partial:
        with _STREAM_CACHE_LOCK:
            if len(_STREAM_CACHE) >= STREAM_CACHE_MAX:
                _STREAM_CACHE.clear()
            _STREAM_CACHE[cache_key] = (
                time.monotonic(),
                [(url, dict(headers)) for url, headers in candidate_streams],
            )

    return candidate_streams, partial


def fetch(
    tmdb_id: str,
    title: str,
    media_type: str,
    season_num: str,
    episode_num: str,
    year: str = "",
    fast: bool = False,
) -> List[Tuple[str, Dict[str, str]]]:
    """Fetch streams from the JS server, hiding the partial flag.

    Kept for callers that only need a usable list. Use
    :func:`fetch_streams` when a partial result should be retried.
    """
    streams, _partial = fetch_streams(
        tmdb_id,
        title,
        media_type,
        season_num,
        episode_num,
        year=year,
        fast=fast,
    )
    if not streams:
        raise Exception("No streams returned from JS server")
    return streams


# Also export a function to get available providers for UI display
def get_available_providers() -> List[str]:
    """Get list of available provider names from JS server."""
    try:
        client = _get_js_client()
        providers = client.get_providers()
        return [p["name"] for p in providers if p.get("enabled", True)]
    except Exception:
        return []