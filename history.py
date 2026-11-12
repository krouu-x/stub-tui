# History management
import os
import json
import time
import re
import threading
import logging
from typing import Optional, Dict, Tuple, List

from stub_main.config import HISTORY_FILE, HISTORY_MAX

log = logging.getLogger("tmdb_tui.history")


class HistoryManager:
    def __init__(self):
        self.history: List[Dict] = []
        self._history_lock = threading.Lock()
        self._load_history()

    @staticmethod
    def _history_key(entry: dict) -> tuple:
        """Dedup key for a history entry: movies by id+type, TV by id+type+season+episode."""
        tmdb_id = str(entry.get("tmdb_id", ""))
        media_type = entry.get("media_type", "movie")
        if media_type == "tv":
            season = entry.get("season_number")
            episode = entry.get("episode_number")
            return (tmdb_id, media_type, season, episode)
        return (tmdb_id, media_type)

    def _history_identity(self, entry: dict):
        """Identify a movie or a specific TV episode, with title fallback for missing tmdb_id."""
        tmdb_id = str(entry.get("tmdb_id") or entry.get("id") or "")
        media_type = entry.get("media_type", "movie")
        if not tmdb_id:
            fallback = re.sub(
                r"\s+", " ", str(entry.get("title") or "").strip().casefold()
            )
            tmdb_id = "title:" + fallback
        if media_type == "tv":
            return (
                tmdb_id,
                media_type,
                str(entry.get("season_number", "")),
                str(entry.get("episode_number", "")),
            )
        return (tmdb_id, media_type, "", "")

    def _current_history_item(self, item=None, detail=None, season_data=None, episode_detail=None):
        """Build a history entry dict from the current detail/season/episode state."""
        source = item or detail or {}
        entry = {
            "tmdb_id": str(source.get("id") or source.get("tmdb_id") or ""),
            "title": source.get("title") or source.get("name") or "?",
            "media_type": source.get("media_type", "movie"),
            "year": (
                source.get("release_date")
                or source.get("first_air_date")
                or source.get("year")
                or ""
            )[:4],
            "position_seconds": 0.0,
            "duration_seconds": 0.0,
            "progress_percent": 0.0,
            "played_at": time.strftime("%Y-%m-%d %H:%M"),
            "last_watched": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if entry["media_type"] == "tv":
            if season_data:
                entry["season_number"] = int(
                    season_data.get("season_number", 1)
                )
            if episode_detail:
                entry["episode_number"] = int(
                    episode_detail.get("episode_number", 1)
                )
                entry["episode_title"] = (
                    episode_detail.get("name") or ""
                )
        return entry

    def _find_history_entry(self, item):
        """Find an existing history entry matching the given item's identity."""
        key = self._history_identity(item)
        for entry in self.history:
            if self._history_identity(entry) == key:
                return entry
        # Fallback: match by title + media_type (+ season/episode for TV)
        item_title = str(item.get("title") or "").strip().casefold()
        item_type = item.get("media_type", "movie")
        item_season = item.get("season_number")
        item_episode = item.get("episode_number")
        for entry in self.history:
            entry_title = str(entry.get("title") or "").strip().casefold()
            entry_type = entry.get("media_type", "movie")
            if not item_title or not entry_title:
                continue
            if item_title != entry_title or item_type != entry_type:
                continue
            if item_type == "tv":
                if entry.get("season_number") == item_season and entry.get("episode_number") == item_episode:
                    return entry
            else:
                return entry
        return None

    @staticmethod
    def _normalize_history_entry(entry: dict) -> dict:
        """Backfill missing fields on old-format history entries so nothing crashes."""
        if not isinstance(entry, dict):
            return {}
        entry.setdefault("tmdb_id", str(entry.get("id", "")))
        entry.setdefault("title", entry.get("title") or entry.get("name") or "?")
        entry.setdefault("media_type", entry.get("media_type", "movie"))
        entry.setdefault("year", "")
        entry.setdefault("season_number", None)
        entry.setdefault("episode_number", None)
        entry.setdefault("episode_title", None)
        entry.setdefault("position_seconds", 0)
        entry.setdefault("duration_seconds", None)
        entry.setdefault("progress_percent", 0)
        entry.setdefault("played_at", entry.get("played_at") or entry.get("last_watched") or "")
        entry.setdefault("last_watched", entry.get("played_at") or "")
        return entry

    def _load_history(self):
        self.history = []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.history = []
            return
        if not isinstance(raw, list):
            self.history = []
            return
        self.history = [self._normalize_history_entry(e) for e in raw]

    def _save_history(self):
        try:
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.history[-HISTORY_MAX:], f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.warning("failed to save history: %s", e)

    def add_history(
        self,
        item: dict,
        season_number=None,
        episode_number=None,
        episode_title=None,
        position_seconds=0,
        duration_seconds=None,
        progress_percent=0,
    ):
        """Record or update a played item in history.

        TV episodes are deduplicated by (tmdb_id, media_type, season, episode)
        so each episode of a show gets its own entry.
        """
        now = time.strftime("%Y-%m-%d %H:%M")
        media_type = item.get("media_type", "movie")
        entry = {
            "tmdb_id": str(item.get("tmdb_id") or item.get("id", "")),
            "title": item.get("title") or item.get("name") or "?",
            "media_type": media_type,
            "year": (item.get("release_date") or item.get("first_air_date") or "")[:4],
            "season_number": season_number,
            "episode_number": episode_number,
            "episode_title": episode_title,
            "position_seconds": position_seconds,
            "duration_seconds": duration_seconds,
            "progress_percent": progress_percent,
            "played_at": now,
            "last_watched": now,
        }
        with self._history_lock:
            key = self._history_key(entry)
            self.history = [h for h in self.history if self._history_key(h) != key]
            self.history.insert(0, entry)
            self._save_history()

    def update_history_progress(
        self,
        tmdb_id: str,
        media_type: str,
        position_seconds: float,
        duration_seconds: Optional[float] = None,
        season_number=None,
        episode_number=None,
    ):
        """Update playback progress for an existing history entry (best effort, no crash)."""
        try:
            key = (str(tmdb_id), media_type)
            if media_type == "tv" and season_number is not None:
                key = (str(tmdb_id), media_type, season_number, episode_number)
            with self._history_lock:
                for entry in self.history:
                    if self._history_key(entry) == key:
                        entry["position_seconds"] = position_seconds
                        if duration_seconds is not None:
                            entry["duration_seconds"] = duration_seconds
                        if duration_seconds:
                            entry["progress_percent"] = round(
                                (position_seconds / duration_seconds) * 100.0, 1
                            )
                        entry["last_watched"] = time.strftime("%Y-%m-%d %H:%M")
                        self._save_history()
                        return
        except Exception as e:
            log.warning("failed to update history progress: %s", e)

    def clear(self):
        """Clear all history."""
        with self._history_lock:
            self.history = []
            self._save_history()

    def get_sorted_entries(self) -> List[Dict]:
        """Get entries sorted by last_watched (most recent first)."""
        with self._history_lock:
            return sorted(
                self.history,
                key=lambda e: e.get("last_watched") or e.get("played_at") or "",
                reverse=True,
            )