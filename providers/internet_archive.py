# Internet Archive provider (archive.org)
import logging
from typing import List, Tuple, Dict
from difflib import SequenceMatcher

from curl_cffi import requests

from ..config import BROWSER_HEADERS

log = logging.getLogger("tmdb_tui.providers.internet_archive")


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    base_url = "https://archive.org"

    query_parts = [title, "mediatype:(movies)"]
    if year:
        query_parts.append(f"year:[{year} TO {year}]")
    search_q = " AND ".join(query_parts)

    params = {
        "q": search_q,
        "fl[]": ["identifier", "title", "mediatype"],
        "rows": 50,
        "page": 1,
        "output": "json",
    }

    resp = requests.get(f"{base_url}/advancedsearch.php", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    docs = data.get("response", {}).get("docs", [])
    if not docs:
        raise Exception("No Internet Archive results found")

    want = title.lower()
    best = None
    best_score = 0.0
    for doc in docs:
        doc_title = doc.get("title") or ""
        score = SequenceMatcher(None, want, doc_title.lower()).ratio()
        if score > best_score:
            best_score = score
            best = doc

    if best is None:
        best = docs[0]

    identifier = best.get("identifier", "")
    if not identifier:
        raise Exception("Internet Archive result missing identifier")

    meta_resp = requests.get(f"{base_url}/metadata/{identifier}", timeout=15)
    meta_resp.raise_for_status()
    metadata = meta_resp.json()

    files = metadata.get("files", [])
    server = metadata.get("server", "")
    dir_path = metadata.get("dir", "")

    candidate_streams = []
    for f in files:
        fmt = (f.get("format") or "").upper()
        name = f.get("name", "")
        if not name:
            continue

        length_str = f.get("length", "0")
        try:
            length_sec = float(length_str)
        except (ValueError, TypeError):
            length_sec = 0.0
        if length_sec < 10.0:
            continue

        if not (
            "MPEG" in fmt
            or fmt.startswith("H.264")
            or fmt.startswith("MATROSKA")
            or fmt.startswith("DIVX")
            or fmt.startswith("OGG VIDEO")
        ):
            continue

        stream_url = f"https://{server}{dir_path}/{name}"
        candidate_streams.append(
            (stream_url, {"User-Agent": BROWSER_HEADERS["User-Agent"]})
        )

    if not candidate_streams:
        raise Exception("No playable Internet Archive streams found")

    return candidate_streams