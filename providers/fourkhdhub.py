# 4KHDHub provider
import os
import re
import time
import base64
import json
import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional, Callable
from urllib.parse import urlparse, parse_qs, quote, urlencode, urlunparse, unquote

from curl_cffi import requests
from bs4 import BeautifulSoup

from stub_main.config import (
    BROWSER_HEADERS,
    PAGE_TIMEOUT,
    MIRROR_WORKERS,
    MAX_MIRRORS_PER_RELEASE,
    SEARCH_TTL,
    SEARCH_CACHE_MAX,
)
from stub_main.providers.stream_utils import prioritize_seekable, unwrap_stream_url

log = logging.getLogger("tmdb_tui.providers.fourkhdhub")

FOURKHDHUB_DOMAINS = [
    "https://4khdhub.link",
    "https://4khdhub.dad",
    "https://4khdhub.one",
    "https://4khub.com",
]

_SEARCH_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
_SEARCH_CACHE_LOCK = threading.Lock()
_TLS = threading.local()


def _4k_session() -> requests.Session:
    """One impersonated session per calling thread."""
    s = getattr(_TLS, "session", None)
    if s is None:
        s = requests.Session(impersonate="chrome120")
        s.headers.update(BROWSER_HEADERS)
        _TLS.session = s
    return s


def _norm_title(t: str) -> str:
    """Lowercase, strip (year)/Season N/punctuation for comparison."""
    t = t.lower()
    t = re.sub(r"\((?:19|20)\d{2}\)", " ", t)
    t = re.sub(r"\bseason\s*\d+\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _4khdhub_search(query: str) -> List[Dict]:
    key = _norm_title(query)
    now = time.time()

    with _SEARCH_CACHE_LOCK:
        hit = _SEARCH_CACHE.get(key)
        if hit and now - hit[0] < SEARCH_TTL:
            log.info("search cache hit: %r", query)
            return hit[1]

    session = _4k_session()
    last_err = None
    items: List[Dict] = []
    seen_paths = set()

    for domain in FOURKHDHUB_DOMAINS:
        url = f"{domain}/?s={quote(query)}"
        try:
            resp = session.get(url, timeout=PAGE_TIMEOUT)
            if resp.status_code != 200:
                log.warning("search %s -> HTTP %s", domain, resp.status_code)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("a.movie-card") or soup.select(
                ".post-title a, .entry-title a, article a, .movie-item a"
            )

            for card in cards:
                href = card.get("href")
                if not href:
                    continue

                title_el = card.select_one(".movie-card-title, .title, h2, h3")
                title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)
                if not title or len(title) < 2:
                    continue

                meta_el = card.select_one(".movie-card-meta, .meta")
                meta = meta_el.get_text(strip=True) if meta_el else title
                year_match = re.search(r"\b(19|20)\d{2}\b", meta)
                year = year_match.group(0) if year_match else None

                media_type = (
                    "Series"
                    if any(k in href.lower() for k in ["-series-", "/series/", "season"])
                    else "Movie"
                )
                path = urlparse(href).path
                if path in seen_paths:
                    continue
                seen_paths.add(path)

                items.append({
                    "id": path,
                    "title": title,
                    "media_type": media_type,
                    "year": year,
                    "base_domain": domain,
                })

            if items:
                log.info("search %r: %d hits via %s", query, len(items), domain)
                break

        except Exception as e:
            last_err = e
            log.warning("search %s failed: %s", domain, e)
            continue

    if not items:
        raise Exception(
            f"No 4KHDHub search results (mirrors down or blocked). Last error: {last_err}"
        )

    with _SEARCH_CACHE_LOCK:
        if len(_SEARCH_CACHE) >= SEARCH_CACHE_MAX:
            _SEARCH_CACHE.clear()
        _SEARCH_CACHE[key] = (time.time(), items)
    return items


def _4khdhub_pick_match(
    results: List[Dict], title: str, year: str, media_type: str
) -> Tuple[Dict, float]:
    """Rank candidates by title similarity, media type, and release year."""
    want = _norm_title(title)
    target_type = "Series" if media_type == "tv" else "Movie"

    def score(r: Dict) -> float:
        s = SequenceMatcher(None, want, _norm_title(r.get("title", ""))).ratio() * 10.0
        s += 3.0 if r.get("media_type") == target_type else -2.0
        ry, wy = r.get("year"), (year or None)
        if wy and ry:
            s += 4.0 if ry == wy else -3.0
        return s

    ranked = sorted(results, key=score, reverse=True)
    best = ranked[0]
    best_score = score(best)
    if len(ranked) > 1 and score(ranked[1]) >= best_score - 0.5:
        log.warning(
            "ambiguous match for %r: %r (%.1f) vs %r (%.1f)",
            title, ranked[0]["title"], best_score, ranked[1]["title"], score(ranked[1]),
        )
    return best, best_score


def _4khdhub_get_releases(
    domain: str, id_path: str, season: int = 0, episode: int = 0
) -> List[Dict]:
    url = f"{domain}{id_path}"
    session = _4k_session()
    resp = session.get(url, timeout=PAGE_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    if season > 0:
        items = soup.select("#episodes .episode-download-item")
        title_sel = ".episode-file-title"
    else:
        items = soup.select(".download-item")
        title_sel = ".file-title"

    releases = []
    for item in items:
        title_el = item.select_one(title_sel)
        filename = title_el.get_text(strip=True) if title_el else ""
        if not filename or filename.lower().endswith(".zip"):
            continue

        if season > 0:
            m = re.search(r"S(\d+)E(\d+)", filename, re.IGNORECASE)
            if not m:
                continue
            if int(m.group(1)) != season or int(m.group(2)) != episode:
                continue

        mirrors = []
        for link in item.select("a[href]"):
            href = link.get("href", "")
            if not href.startswith("https://") or "logout" in href:
                continue
            label = link.get_text(strip=True) or "Source"
            mirrors.append({"url": href, "label": label})

        if mirrors:
            releases.append({"filename": filename, "mirrors": mirrors})

    return releases


def _4khdhub_decode_greenmotors(html: str) -> Optional[str]:
    match = re.search(r"s\(\s*'o'\s*,\s*'([^']+)'\s*", html)
    if not match:
        return None

    payload = match.group(1)
    try:
        step1 = base64.b64decode(payload).decode("utf-8")
        step2 = base64.b64decode(step1).decode("utf-8")
        step3 = step2.translate(str.maketrans(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm"
        ))
        step4 = base64.b64decode(step3).decode("utf-8")
        data = json.loads(step4)

        target_b64 = data.get("o") or data.get("url") or data.get("link")
        if target_b64:
            return base64.b64decode(target_b64).decode("utf-8")
    except Exception:
        pass
    return None


def _4khdhub_resolve_mirror(mirror_url: str, session: requests.Session) -> Optional[str]:
    try:
        if any(d in mirror_url for d in ["greenmotors", "greenmountmotors"]):
            resp = session.get(mirror_url, timeout=10)
            target = _4khdhub_decode_greenmotors(resp.text)
            if target:
                return _4khdhub_resolve_mirror(target, session)
            return None

        elif "hubdrive." in mirror_url:
            resp = session.get(mirror_url, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if "hubcloud." in href:
                    return _4khdhub_resolve_mirror(href, session)

        elif "hubcloud." in mirror_url:
            resp = session.get(mirror_url, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")

            resolver_url = None
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/download/" in href or "download" in a.get("id", "") or "btn" in " ".join(a.get("class", [])):
                    if href.startswith("https://"):
                        resolver_url = href
                        break

            if not resolver_url:
                return None

            resp2 = session.get(resolver_url, timeout=10)
            soup2 = BeautifulSoup(resp2.text, "html.parser")

            for a in soup2.select('a[href*="pages.dev"]'):
                parsed = urlparse(a["href"])
                qs = parse_qs(parsed.query)
                if "u" in qs:
                    try:
                        decoded = base64.b64decode(qs["u"][0]).decode("utf-8")
                        if decoded.startswith("https://"):
                            return decoded.replace("pixeldrain.dev", "pixeldrain.com")
                    except Exception:
                        pass

            text = resp2.text
            m = re.search(r"https?://(?:pixeldrain\.(?:dev|com)/(?:u|api/file)/)[^\s\"'<>]+", text)
            if m:
                pd_url = m.group(0).replace("pixeldrain.dev", "pixeldrain.com")
                if "/u/" in pd_url:
                    pd_url = pd_url.replace("/u/", "/api/file/") + "?download"
                elif "/api/file/" in pd_url and "?download" not in pd_url:
                    pd_url += "?download"
                return pd_url

            return None
        else:
            return mirror_url
    except Exception:
        return None


def _4khdhub_preflight(url: str, session: requests.Session) -> Optional[str]:
    if not url or not url.startswith("https://"):
        return None

    parsed = urlparse(url)
    if parsed.hostname in ("localhost", "127.0.0.1") or url.endswith(".zip"):
        return None

    try:
        resp = session.get(url, timeout=10, stream=True)
        try:
            if resp.status_code < 400:
                ct = resp.headers.get("Content-Type", "").lower()
                if "text/html" in ct or "text/plain" in ct:
                    return None
                return resp.url
            return None
        finally:
            resp.close()
    except Exception:
        return None


def _4khdhub_try_mirror(mirror_url: str) -> Optional[str]:
    """Resolve + preflight one mirror. Runs in a worker thread; never raises."""
    try:
        resolved = _4khdhub_resolve_mirror(mirror_url, _4k_session())
        if not resolved:
            log.info("resolve failed: %s", mirror_url)
            return None
        resolved = unwrap_stream_url(resolved)
        playable = _4khdhub_preflight(resolved, _4k_session())
        if playable:
            log.info("playable: %s", playable)
        return playable
    except Exception as e:
        log.warning("mirror %s failed: %s", mirror_url, e)
        return None


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    started = time.perf_counter()
    log.info("fetch title=%r type=%s year=%s", title, media_type, year)

    results = _4khdhub_search(title)
    match, score = _4khdhub_pick_match(results, title, year, media_type)
    log.info("selected %r (%r) score=%.1f", match["title"], match["base_domain"], score)

    season = int(season_num) if media_type == "tv" and season_num else 0
    episode = int(episode_num) if media_type == "tv" and episode_num else 0

    releases = _4khdhub_get_releases(match["base_domain"], match["id"], season, episode)
    if not releases:
        raise Exception("No 4KHDHub releases found")
    log.info("%d releases for S%sE%s", len(releases), season or "-", episode or "-")

    jobs, seen = [], set()
    for release in releases:
        for mirror in release["mirrors"][:MAX_MIRRORS_PER_RELEASE]:
            if mirror["url"] not in seen:
                seen.add(mirror["url"])
                jobs.append(mirror["url"])

    candidate_streams: List[Tuple[str, Dict[str, str]]] = []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(MIRROR_WORKERS, len(jobs))) as ex:
            futures = {ex.submit(_4khdhub_try_mirror, u): u for u in jobs}
            for fut in as_completed(futures):
                playable = fut.result()
                if playable:
                    candidate_streams.append((
                        playable,
                        {"User-Agent": BROWSER_HEADERS["User-Agent"]},
                    ))

    elapsed = time.perf_counter() - started
    log.info("fetch done: %d playable stream(s) in %.2fs", len(candidate_streams), elapsed)

    if not candidate_streams:
        raise Exception("No playable 4KHDHub streams resolved (see tmdb_tui.log)")
    # Prefer seekable hosts (pixeldrain / S3 / worker) over Google Drive links,
    # which ignore HTTP Range requests and therefore cannot be skipped inside.
    return prioritize_seekable(candidate_streams)