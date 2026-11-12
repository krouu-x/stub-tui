# DahmerMovies provider (a.111477.xyz directory scraping + worker proxy)
import re
import logging
from typing import List, Tuple, Dict
from urllib.parse import quote, unquote

from curl_cffi import requests

from stub_main.config import BROWSER_HEADERS
from stub_main.tmdb import _tmdb_provider_info

log = logging.getLogger("tmdb_tui.providers.dahmermovies")

DAHMER_MOVIES_API = "https://a.111477.xyz"
DAHMER_WORKER_API = "https://p.111477.xyz/bulk?u="
DAHMER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": f"{DAHMER_MOVIES_API}/",
}
DAHMER_FETCH_TIMEOUT = 10


def _dahmer_parse_links(html: str) -> List[Dict]:
    links: List[Dict] = []
    for m in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", html, re.IGNORECASE):
        row = m.group(1)
        link_m = re.search(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>([^<]*)</a>', row, re.IGNORECASE)
        if not link_m:
            continue
        href = link_m.group(1)
        text = link_m.group(2).strip()
        size_m = re.search(r"<td[^>]*>(\d+(?:\.\d+)?\s?[KMGT]B)</td>", row, re.IGNORECASE)
        size = size_m.group(1).strip() if size_m else "N/A"
        if not text or href == "../":
            continue
        if not re.search(r"\.(mkv|mp4|avi|webm|m3u8)$", text, re.IGNORECASE):
            continue
        links.append({"text": text, "href": href, "size": size})
    return links


def _dahmer_fetch_dir(title: str, year: str, season: int):
    clean_title = re.sub(r":", "", title)
    variants: List[str] = []
    if season:
        variants = [
            f"/tvs/{quote(clean_title, safe='')}/Season%20{int(season):02d}/",
            f"/tvs/{quote(clean_title, safe='')}/Season%20{int(season)}/",
        ]
    else:
        variants = [f"/movies/{quote(f'{clean_title} ({year})', safe='')}/"]
    for v in variants:
        url = f"{DAHMER_MOVIES_API}{v}"
        try:
            resp = requests.get(url, headers=DAHMER_HEADERS, timeout=DAHMER_FETCH_TIMEOUT)
        except Exception:
            continue
        if resp.status_code == 200:
            return url, resp.text
    return None


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    info = _tmdb_provider_info(tmdb_id, media_type)
    if not info:
        return []
    title = info.get("title") or title
    year = info.get("year") or year or ""
    is_tv = info.get("media_type") == "tv"
    season = int(season_num) if is_tv and str(season_num).isdigit() and int(season_num) > 0 else 0
    episode = int(episode_num) if is_tv and str(episode_num).isdigit() and int(episode_num) > 0 else 0

    found = _dahmer_fetch_dir(title, year, season)
    if not found:
        return []
    dir_url, html = found
    paths = _dahmer_parse_links(html)
    if is_tv and episode:
        ep_str = f"{episode:02d}"
        se_str = f"{season:02d}"
        filtered = [
            p for p in paths
            if f"s{se_str}e{ep_str}" in p["text"].lower() or f"e{ep_str}" in p["text"].lower()
        ]
        if filtered:
            paths = filtered
    paths = sorted(paths, key=lambda p: 0 if re.search(r"2160p|1080p|720p|4[Kk]", p["text"], re.IGNORECASE) else 1)

    streams: List[Tuple[str, Dict[str, str]]] = []
    for p in paths[:5]:
        href = p["href"]
        if href.startswith("http://") or href.startswith("https://"):
            direct = href
        elif href.startswith("/"):
            direct = DAHMER_MOVIES_API.rstrip("/") + href
        else:
            direct = dir_url.rstrip("/") + "/" + href.lstrip("/")
        direct = unquote(direct)

        file_name = p["text"]
        is_multi = re.search(r"(HIN|TAM|TEL|Multi|Dual|DUB|Multi-Audio|MULTI)", file_name, re.IGNORECASE)
        has_eng = re.search(r"(Eng|English)", file_name, re.IGNORECASE)
        is_english_title = all(
            (c.isalnum() or c.isspace() or c in " ?!:-") for c in title
        )
        language = "Original"
        if is_multi:
            language = "Multi Audio"
        elif is_english_title and has_eng:
            language = "English"

        fm = re.search(r"(mkv|mp4|avi|webm|m3u8)$", file_name, re.IGNORECASE)
        file_format = fm.group(1).upper() if fm else "LINK"
        rm = re.search(r"(2160p|1080p|720p|4[Kk])", file_name)
        resolution = rm.group(0) if rm else "1080p"
        info_title = re.sub(r"(mkv|mp4|avi|webm|m3u8)$", "", file_name, flags=re.IGNORECASE)
        info_title = "".join(" " if (not c.isalnum() and not c.isspace()) else c for c in info_title)
        info_title = re.sub(r" +", " ", info_title).strip()

        stream_url = f"{DAHMER_WORKER_API}{quote(direct, safe=':/?#[]@!$&\'()*+,;=-._~')}"
        hdrs = {
            "User-Agent": DAHMER_HEADERS["User-Agent"],
            "Referer": f"{DAHMER_MOVIES_API}/",
            "Accept": "*/*",
            "Range": "bytes=0-",
        }
        streams.append((stream_url, hdrs))
    return streams