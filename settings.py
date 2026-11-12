# User settings management (persisted preferences, e.g. default playback player)
import os
import json
import threading
import logging
from typing import Optional

from stub_main.config import SETTINGS_FILE, DEFAULT_PLAYER, VALID_PLAYERS

log = logging.getLogger("tmdb_tui.settings")


class SettingsManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._values = {}
        self.load()

    def load(self) -> None:
        self._values = {}
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._values = raw
        except (OSError, ValueError) as e:
            log.warning("failed to load settings: %s", e)
            self._values = {}

    def save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._values, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            log.warning("failed to save settings: %s", e)
            return False

    def get(self, key: str, default=None):
        with self._lock:
            return self._values.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._values[key] = value
            self.save()

    @staticmethod
    def _normalize(value) -> Optional[str]:
        if not isinstance(value, str):
            return None
        value = value.strip().lower()
        return value if value in VALID_PLAYERS else None

    def get_player(self) -> str:
        """Return the active playback backend: 'mpv' or 'vlc'."""
        backend = self._normalize(self.get("player_backend"))
        return backend or DEFAULT_PLAYER

    def set_player(self, backend: str) -> str:
        """Set the playback backend and persist it. Falls back to DEFAULT_PLAYER."""
        backend = self._normalize(backend) or DEFAULT_PLAYER
        self.set("player_backend", backend)
        return backend

    def cycle_player(self) -> str:
        """Switch to the other supported backend ('mpv' <-> 'vlc') and persist it."""
        current = self.get_player()
        return self.set_player("vlc" if current == "mpv" else "mpv")


settings = SettingsManager()
