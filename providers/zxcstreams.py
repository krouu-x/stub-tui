# ZXCStreams provider (zxcstream.xyz — token-gated multi-server API)
import json
import time
import hashlib
import logging
import threading
from typing import List, Tuple, Dict, Optional
from urllib.parse import urlencode, urlparse

from curl_cffi import requests

from stub_main.config import BROWSER_HEADERS
from stub_main.tmdb import _tmdb_provider_info

log = logging.getLogger("tmdb_tui.providers.zxcstreams")

ZXC_PORTALS = ["https://zxcstream.xyz", "https://zxcprime.xyz"]
ZXC_INITIAL_BASE = "https://r1.zxcstream.xyz"
ZXC_SALT = "3435443433"
ZXC_SERVERS = ["icarus", "berkas", "orion", "athena"]
ZXC_BASE_TTL_MS = 10 * 60 * 1000
ZXC_PROBE_SUBDOMAINS = ["r1", "r2", "r3", "r4", "r5", "r6", "v4", "cdn", "api", "stream"]
ZXC_F_ID = "rgrwsdsdfgwrwrwwr"
ZXC_F_TOKEN = "xfgdfgdsffgrwgrwyjhkjt"
ZXC_TS = "rdghhdghhfssft"
ZXC_TOKEN = "ZDDVHJFGHYRHG"
ZXC_TITLE = "TUKTHFSSFGDGHJS"
ZXC_YEAR = "53653TRFG647GF"
ZXC_SEASON = "adkljfhdahfladhfjahfjlahfhfljkadfdf"
ZXC_EPISODE = "546745ygy46ytfgty"
ZXC_IMDB_ID = "564745ygtuy5yi75yuy"

ZXC_COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}
_ZXC_BASE_LOCK = threading.Lock()
_ZXC_BASE_STATE = {"base": ZXC_INITIAL_BASE, "validated_at": 0}


def _zxc_sha512_hex(data: str) -> str:
    return hashlib.sha512(data.encode("utf-8")).hexdigest()


def _zxc_verify_base(base: str) -> str:
    rt = int(time.time() * 1000)
    xt = _zxc_sha512_hex(f"{rt}:{ZXC_SALT}:550")[:64]
    body = json.dumps({ZXC_F_ID: "550", ZXC_F_TOKEN: xt, ZXC_TS: str(rt)})
    resp = requests.post(
        f"{base}/backend/token",
        headers={
            **ZXC_COMMON_HEADERS,
            "Origin": base,
            "Content-Type": "application/json",
            "Referer": f"{base}/player/movie/550",
        },
        json=body,
        timeout=10,
    )
    if resp.status_code != 200:
        raise Exception(f"verify failed: {resp.status_code}")
    data = resp.json()
    if not data.get(ZXC_TOKEN):
        raise Exception("verify failed: no token")
    return base


def _zxc_try_portal(portal: str) -> str:
    resp = requests.get(portal, headers={"User-Agent": ZXC_COMMON_HEADERS["User-Agent"]}, timeout=10)
    parsed = urlparse(resp.url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin == f"{urlparse(portal).scheme}://{urlparse(portal).netloc}":
        raise Exception(f"portal {portal} did not redirect")
    return _zxc_verify_base(origin)


def _zxc_discover_base() -> str:
    for portal in ZXC_PORTALS:
        try:
            return _zxc_try_portal(portal)
        except Exception as e:
            log.warning("ZXCStreams portal %s failed: %s", portal, e)
    for sub in ZXC_PROBE_SUBDOMAINS:
        try:
            return _zxc_verify_base(f"https://{sub}.zxcstream.xyz")
        except Exception as e:
            log.warning("ZXCStreams subdomain %s failed: %s", sub, e)
    raise Exception("ZXCStreams base discovery failed")


def _zxc_get_base() -> str:
    now_ms = int(time.time() * 1000)
    with _ZXC_BASE_LOCK:
        if now_ms - _ZXC_BASE_STATE["validated_at"] <= ZXC_BASE_TTL_MS:
            return _ZXC_BASE_STATE["base"]
    base = _zxc_discover_base()
    with _ZXC_BASE_LOCK:
        _ZXC_BASE_STATE["base"] = base
        _ZXC_BASE_STATE["validated_at"] = int(time.time() * 1000)
    return base


def _zxc_generate_frontend_token(tmdb_id: str) -> Dict[str, str]:
    rt = int(time.time() * 1000)
    xt = _zxc_sha512_hex(f"{rt}:{ZXC_SALT}:{tmdb_id}")[:64]
    return {"xt": xt, "rt": str(rt)}


def _zxc_request_server_token(base: str, tmdb_id: str, referer: str) -> Dict[str, str]:
    token = _zxc_generate_frontend_token(tmdb_id)
    body = json.dumps({ZXC_F_ID: str(tmdb_id), ZXC_F_TOKEN: token["xt"], ZXC_TS: token["rt"]})
    resp = requests.post(
        f"{base}/backend/token",
        headers={
            **ZXC_COMMON_HEADERS,
            "Origin": base,
            "Content-Type": "application/json",
            "Referer": referer,
        },
        json=body,
        timeout=10,
    )
    if resp.status_code != 200:
        raise Exception(f"token failed {resp.status_code}")
    data = resp.json()
    server_token = data.get(ZXC_TOKEN)
    server_ts = data.get(ZXC_TS)
    if not server_token:
        raise Exception("token response missing token")
    return {"serverToken": server_token, "serverTs": server_ts, "xt": token["xt"]}


def _zxc_fetch_server(server: str, meta: Dict, type_: str, season, episode) -> List[Dict]:
    base = _zxc_get_base()
    referer = f"{base}/player/{type_}/{meta.get('tmdbId')}"
    if season is not None and episode is not None:
        referer = f"{base}/player/{type_}/{meta.get('tmdbId')}/{season}/{episode}"
    token_data = _zxc_request_server_token(base, meta.get("tmdbId"), referer)
    params = {
        ZXC_F_ID: str(meta.get("tmdbId")),
        "b": type_,
        ZXC_TS: str(token_data["serverTs"]),
        ZXC_TOKEN: token_data["serverToken"],
        ZXC_F_TOKEN: token_data["xt"],
        ZXC_TITLE: str(meta.get("title") or ""),
        ZXC_YEAR: str(meta.get("year") or ""),
        "date": str(meta.get("releaseDate") or ""),
        ZXC_IMDB_ID: str(meta.get("imdbId") or ""),
    }
    if season is not None and episode is not None:
        params[ZXC_SEASON] = str(season)
        params[ZXC_EPISODE] = str(episode)
    qs = urlencode(params)
    resp = requests.get(
        f"{base}/backend_/servers/{server}?{qs}",
        headers={**ZXC_COMMON_HEADERS, "Origin": base, "Referer": referer},
        timeout=12,
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    if not isinstance(data, dict) or data.get("success") is not True:
        return []
    links = data.get("links")
    if not isinstance(links, list):
        return []
    streams: List[Dict] = []
    for l in links:
        url = l.get("link") if isinstance(l, dict) else None
        if not url:
            continue
        streams.append({
            "server": server,
            "type": l.get("type") or ("hls" if ".m3u8" in str(url) else "mp4"),
            "resolution": l.get("resolution"),
            "size": l.get("size"),
            "url": url,
            "requestHeaders": {
                "Referer": referer,
                "Origin": base,
                "User-Agent": ZXC_COMMON_HEADERS["User-Agent"],
            },
        })
    return streams


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    info = _tmdb_provider_info(tmdb_id, media_type)
    if not info:
        return []
    type_ = "tv" if info.get("media_type") == "tv" else "movie"
    season = int(season_num) if type_ == "tv" and str(season_num).isdigit() and int(season_num) > 0 else None
    episode = int(episode_num) if type_ == "tv" and str(episode_num).isdigit() and int(episode_num) > 0 else None
    imdb_id = info.get("imdb_id") or ""
    if not imdb_id:
        return []
    meta = {
        "tmdbId": str(tmdb_id),
        "title": str(info.get("title") or title),
        "year": str(info.get("year") or year or ""),
        "releaseDate": f"{info.get('year') or year or ''}-01-01",
        "imdbId": imdb_id,
    }
    found: List[Dict] = []
    for server in ZXC_SERVERS:
        try:
            found.extend(_zxc_fetch_server(server, meta, type_, season, episode))
        except Exception as e:
            log.warning("ZXCStreams %s failed: %s", server, e)

    streams: List[Tuple[str, Dict[str, str]]] = []
    seen = set()
    for s in found:
        url = s.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        streams.append((url, dict(s.get("requestHeaders") or {"User-Agent": ZXC_COMMON_HEADERS["User-Agent"]})))
    return streams