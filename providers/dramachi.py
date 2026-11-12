# Dramachi provider
import re
import time
import logging
from typing import List, Dict, Tuple
from urllib.parse import quote

from curl_cffi import requests

from ..config import BROWSER_HEADERS

log = logging.getLogger("tmdb_tui.providers.dramachi")


def fetch(
    tmdb_id: str, title: str, media_type: str,
    season_num: str, episode_num: str, year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:
    base_url = "https://api.nodeobjects.com/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    queries_to_try = [title]
    no_year = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()
    if no_year != title:
        queries_to_try.append(no_year)
    cleaned = re.sub(r"[:\-]", " ", title).strip()
    if cleaned != title:
        queries_to_try.append(cleaned)
    if ":" in title:
        main_title = title.split(":")[0].strip()
        if main_title != title:
            queries_to_try.append(main_title)

    items = []
    for query in queries_to_try:
        encoded_q = quote(query, safe="")
        search_url = f"{base_url}?interface=search&q={encoded_q}&filter=all&page=1"
        resp = requests.get(search_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") or []
        if items:
            break

    if not items:
        raise Exception(f"No Dramachi search results found for '{title}'")

    is_movie_target = (media_type == "movie")
    match = None
    for item in items:
        content = (item.get("content") or "").lower()
        is_movie_result = content in ["movies", "movie"]
        if is_movie_result == is_movie_target:
            match = item
            break

    if not match:
        match = items[0]

    title_id = str(match.get("id", "")).strip()
    if not title_id:
        raise Exception("Dramachi match missing ID")

    rip_to_query = "hd Rip"
    if media_type == "tv" and season_num:
        try:
            season_int = int(season_num)
            season_key = f"Season {season_int:02d}"
        except ValueError:
            season_key = "Season 01"

        details_url = f"{base_url}?interface=title_v2&id={quote(title_id)}"
        det_resp = requests.get(details_url, headers=headers, timeout=15)
        det_resp.raise_for_status()
        det_data = det_resp.json()

        seasons = det_data.get("seasons") or {}
        target_rip = None

        if season_key in seasons:
            versions = seasons[season_key].get("versions") or []
            if versions:
                target_rip = versions[0].get("rip")

        if not target_rip:
            for s_name, s_data in seasons.items():
                versions = s_data.get("versions") or []
                if versions:
                    target_rip = versions[0].get("rip")
                    break

        if not target_rip:
            target_rip = season_key
        rip_to_query = target_rip

    eplist_url = f"{base_url}?interface=eplist&season={quote(rip_to_query)}&id={quote(title_id)}"
    ep_resp = requests.get(eplist_url, headers=headers, timeout=15)
    ep_resp.raise_for_status()
    ep_data = ep_resp.json()
    episodes = ep_data.get("episode_list") or []

    if not episodes:
        raise Exception(f"No episodes found for Dramachi RIP: {rip_to_query}")

    def parse_ep_num(f_title: str) -> int:
        m = re.search(r"E(\d+)", f_title, re.IGNORECASE)
        if m:
            return int(m.group(1))
        tokens = f_title.split()
        if tokens:
            try:
                return int(tokens[-1])
            except ValueError:
                pass
        return 9999

    episodes.sort(key=lambda ep: parse_ep_num(ep.get("f_title", "")))

    target_ep = int(episode_num) if episode_num else 0
    if target_ep > 0:
        matched_episodes = [ep for ep in episodes if parse_ep_num(ep.get("f_title", "")) == target_ep]
        if not matched_episodes and target_ep <= len(episodes):
            matched_episodes = [episodes[target_ep - 1]]
    else:
        matched_episodes = episodes

    if not matched_episodes:
        raise Exception(f"Episode {episode_num} not found in Dramachi")

    candidate_streams = []
    for ep in matched_episodes:
        fid = ep.get("fid")
        disk = ep.get("disk")
        if not fid or not disk:
            continue

        file_url = f"{base_url}?interface=getFile&fid={quote(str(fid))}&findex={quote(str(disk))}"
        file_resp = requests.get(file_url, headers=headers, timeout=15)
        file_resp.raise_for_status()
        file_data = file_resp.json()

        host_info = file_data.get("hostInfo")
        file_info_list = file_data.get("fileInfo") or []

        if not host_info or not file_info_list:
            continue

        host = str(host_info.get("host", "")).strip("/")
        raw_url = file_info_list[0].get("url", "")

        if host and raw_url:
            stream_url = f"https://{host}/cdn/{raw_url.lstrip('/')}"
            hdrs = {
                "Referer": "https://dramachi.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            }
            candidate_streams.append((stream_url, hdrs))

    if not candidate_streams:
        raise Exception("No playable streams resolved from Dramachi")

    return candidate_streams