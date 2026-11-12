# Peestream provider
import json
import logging
from typing import List, Tuple, Dict

from curl_cffi import requests

log = logging.getLogger("tmdb_tui.providers.peestream")


def fetch(
    tmdb_id: str,
    title: str,
    media_type: str,
    season_num: str,
    episode_num: str,
    year: str = "",
) -> List[Tuple[str, Dict[str, str]]]:

    url = "https://api.peestream.in/api/search/stream"
    params = {
        "q": tmdb_id,
        "type": media_type,
    }

    if media_type == "tv":
        params["season"] = season_num
        params["episode"] = episode_num

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    # Parse SSE events and find the final JSON payload.
    data = None
    event_name = None

    for line in response.text.splitlines():
        if line.startswith("event:"):
            event_name = line[6:].strip()

        elif line.startswith("data:") and event_name == "done":
            data = json.loads(line[5:].strip())
            break

    if data is None:
        raise Exception("Peestream returned no done event")

    candidate_streams = []

    for result in data.get("results", []):
        for stream in result.get("streams", []):
            stream_url = stream.get("url")

            if stream_url:
                candidate_streams.append(
                    (stream_url, stream.get("headers", {}))
                )

    if not candidate_streams:
        raise Exception(
            f"No Peestream results found for {tmdb_id}"
        )

    return candidate_streams