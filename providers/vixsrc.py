# VixSrc provider (vixsrc.to — direct JSON API)
import re
import logging
from typing import List, Tuple, Dict, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from curl_cffi import requests

from stub_main.config import PAGE_TIMEOUT
from stub_main.providers.fourkhdhub import _4k_session

log = logging.getLogger("tmdb_tui.providers.vixsrc")

VIXSRC_BASE = "https://vixsrc.to"


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    session = _4k_session()
    referer = {"Referer": VIXSRC_BASE + "/"}

    if media_type == "tv":
        candidates = [
            f"{VIXSRC_BASE}/api/tv/{tmdb_id}/{season_num or '1'}/{episode_num or '1'}",
            f"{VIXSRC_BASE}/api/tv/{tmdb_id}/{season_num or '1'}",
            f"{VIXSRC_BASE}/api/movie/{tmdb_id}",
        ]
    else:
        candidates = [
            f"{VIXSRC_BASE}/api/movie/{tmdb_id}",
        ]

    streams: List[Tuple[str, Dict[str, str]]] = []
    errors = []

    for url in candidates:
        try:
            resp = session.get(url, timeout=PAGE_TIMEOUT, headers=referer)
            if resp.status_code != 200:
                errors.append(f"{url}: HTTP {resp.status_code}")
                continue

            data = None
            try:
                data = resp.json()
            except ValueError:
                pass

            def _walk(node):
                """Recursively hoist any http url that looks like media."""
                if isinstance(node, dict):
                    for k, v in node.items():
                        if isinstance(v, str) and v.startswith("http") and (
                            ".m3u8" in v or k in ("file", "url", "src", "link", "source")
                        ):
                            streams.append((v, referer))
                        else:
                            _walk(v)
                elif isinstance(node, list):
                    for v in node:
                        _walk(v)

            if data is not None:
                _walk(data)

            # VixSrc returns {"src": "/embed/...?token=..."} (a relative path), which
            # _walk() skips because it only hoists absolute http URLs. Resolve it to
            # the embed page, then build the master-playlist URL from
            # window.masterPlaylist.url + its token/expires params (+ h=1, lang=en).
            if not streams and isinstance(data, dict):
                src = data.get("src") or data.get("url")
                if isinstance(src, str) and src.startswith("/"):
                    embed_url = VIXSRC_BASE + src
                    try:
                        eref = {"Referer": VIXSRC_BASE + "/"}
                        eresp = session.get(
                            embed_url, timeout=PAGE_TIMEOUT, headers=eref
                        )
                        etext = eresp.text
                        murl = re.search(r"url:\s*'([^']+)'", etext)
                        mtok = re.search(r"'token':\s*'([^']+)'", etext)
                        mexp = re.search(r"'expires':\s*'([^']+)'", etext)
                        if murl:
                            u = urlparse(murl.group(1))
                            qs = dict(parse_qs(u.query))
                            if mtok:
                                qs["token"] = [mtok.group(1)]
                            if mexp:
                                qs["expires"] = [mexp.group(1)]
                            qs["h"] = ["1"]
                            qs["lang"] = ["en"]
                            playlist_url = urlunparse(
                                u._replace(query=urlencode(qs, doseq=True))
                            )
                            streams.append((playlist_url, referer))
                    except Exception as e:
                        errors.append(f"embed {embed_url}: {e}")

            # plain-text response: maybe it's just the m3u8 URL itself
            if not streams and ".m3u8" in resp.text:
                for m in re.finditer(r"https?://[^\s\"'<>]+?\.m3u8[^\s\"'<>]*", resp.text):
                    streams.append((m.group(0), referer))

            if streams:
                break
            errors.append(f"{url}: no stream fields in payload")
        except Exception as e:
            errors.append(f"{url}: {e}")

    if not streams:
        raise Exception("VixSrc failed: " + "; ".join(errors))

    seen, uniq = set(), []
    for u, h in streams:
        if u not in seen:
            seen.add(u)
            uniq.append((u, h))
    return uniq