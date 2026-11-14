# Main App class - ties all components together
import os
import sys
import time
import inspect
import curses
import locale
import logging
import threading
import tempfile
import uuid
import subprocess
import json
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any

from stub_main.config import (
    MIN_WIDTH, MIN_HEIGHT,
    SCREEN_HOME, SCREEN_RESULTS, SCREEN_DETAIL, SCREEN_EPISODES,
    SCREEN_EPISODE_DETAIL, SCREEN_TRENDING, SCREEN_HISTORY,
    SCREEN_DOWNLOADS, SCREEN_NEW, SCREEN_SEASON_DOWNLOAD,
    MODE_NORMAL, MODE_SEARCH, MODE_YEAR, MODE_ID, MODE_SEASON, MODE_EPISODE,
    HISTORY_FILE, HISTORY_MAX, BROWSER_HEADERS,
)
from stub_main.tmdb import TMDBClient
from stub_main.history import HistoryManager
from stub_main.settings import SettingsManager
from stub_main.download import DownloadManager
from stub_main.metadata import _url_label, _format_size
from stub_main.providers import PROVIDERS
from stub_main.js_server_manager import server_state
from .drawing import (
    safe_addstr, draw_too_small, draw_home, draw_results, draw_detail,
    draw_episode_list, draw_episode_detail, draw_trending, draw_history,
    draw_downloads, draw_new_screen, draw_season_download,
    draw_header_bar, draw_mode_prompt, draw_status_line,
)
from .key_handlers import KeyHandler

log = logging.getLogger("tmdb_tui.ui.app")


class App:
    # Main-loop getch timeout (ms). Keeps the UI refreshed for background work.
    POLL_INTERVAL_MS = 200

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.client = TMDBClient()
        self.id_lookup_type = "movie"

        self.title_query = ""
        self.year_query = ""
        self.id_query = ""

        self.screen = SCREEN_HOME
        self.mode = MODE_NORMAL
        self.input_buffer = ""

        self.results = []
        self.selected = 0
        self.results_offset = 0
        self.detail = None

        self.trending = []
        self.trending_selected = 0
        self.trending_offset = 0

        # History manager
        self.history_manager = HistoryManager()
        self.history = self.history_manager.history
        self.history_selected = 0
        self.history_offset = 0

        # User settings (default playback player, etc.)
        self.settings = SettingsManager()

        # Download manager
        self.download_manager = DownloadManager(self)
        self.download_links = self.download_manager.download_links
        self.download_selected = self.download_manager.download_selected
        self.download_offset = self.download_manager.download_offset
        self._link_sizes = self.download_manager._link_sizes

        self.season_selected = 0
        self.season_offset = 0
        self.season_data = None
        self.episodes = []
        self.episode_selected = 0
        self.episode_offset = 0
        self.episode_detail = None

        self.home_selected = 0
        self.home_offset = 0

        self._playback_state = None
        self._ipc_path = None
        self._history_lock = threading.Lock()

        self.status = "Home — j/k select, Enter resume, / search, h history, t trending, q quit."
        self.error = None

        # Last seen background JS-server state (shown in the status line).
        self._server_state_seen = None

        # Key handler
        self.key_handler = KeyHandler(self)

        curses.curs_set(0)
        curses.start_colors_compat = curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)

        stdscr.nodelay(False)
        stdscr.keypad(True)

    # Delegate methods to history manager
    def _load_history(self):
        self.history_manager._load_history()
        self.history = self.history_manager.history

    def _save_history(self):
        self.history_manager._save_history()

    def add_history(self, *args, **kwargs):
        self.history_manager.add_history(*args, **kwargs)
        self.history = self.history_manager.history

    def update_history_progress(self, *args, **kwargs):
        self.history_manager.update_history_progress(*args, **kwargs)

    def _history_key(self, entry):
        return self.history_manager._history_key(entry)

    def _history_identity(self, entry):
        return self.history_manager._history_identity(entry)

    def _current_history_item(self, item=None):
        return self.history_manager._current_history_item(
            item=item,
            detail=self.detail,
            season_data=self.season_data,
            episode_detail=self.episode_detail
        )

    @property
    def player_backend(self) -> str:
        """Active playback backend: 'mpv' or 'vlc' (user-selectable default player)."""
        return self.settings.get_player()

    def cycle_player_backend(self) -> str:
        """Switch the default playback backend to the other supported player."""
        backend = self.settings.cycle_player()
        self.status = f"Default player set to {backend.upper()}."
        return backend

    def _find_history_entry(self, item):
        return self.history_manager._find_history_entry(item)

    def _normalize_history_entry(self, entry):
        return self.history_manager._normalize_history_entry(entry)

    # Download state is owned by the DownloadManager -- expose it on the App
    # through forwarding properties so there is a SINGLE source of truth. Without
    # this, the App copy and the DownloadManager copy can drift apart (e.g. the
    # key handler updates only the App copy, so the play/download action then
    # resolves the wrong / stale link).
    @property
    def download_links(self):
        return self.download_manager.download_links

    @download_links.setter
    def download_links(self, value):
        self.download_manager.download_links = value

    @property
    def download_selected(self) -> int:
        return self.download_manager.download_selected

    @download_selected.setter
    def download_selected(self, value: int) -> None:
        self.download_manager.download_selected = value

    @property
    def download_offset(self) -> int:
        return self.download_manager.download_offset

    @download_offset.setter
    def download_offset(self, value: int) -> None:
        self.download_manager.download_offset = value

    @property
    def _link_sizes(self) -> Dict[str, int]:
        return self.download_manager._link_sizes

    @_link_sizes.setter
    def _link_sizes(self, value: Dict[str, int]) -> None:
        self.download_manager._link_sizes = value

    @property
    def links_partial(self) -> bool:
        """True while more links are still being resolved for the list."""
        return self.download_manager.links_partial

    # Delegate methods to download manager
    def gather_download_links(self, *args, **kwargs):
        return self.download_manager.gather_download_links(*args, **kwargs)

    def resolve_download_links(self, *args, **kwargs):
        return self.download_manager.resolve_download_links(*args, **kwargs)

    def start_live_link_updates(self, *args, **kwargs):
        return self.download_manager.start_live_link_updates(*args, **kwargs)

    def stop_live_link_updates(self):
        return self.download_manager.stop_live_link_updates()

    def _prefetch_link_sizes(self, links):
        self.download_manager._prefetch_link_sizes(links)

    def _player_backend(self, *args, **kwargs) -> str:
        return self.download_manager._player_backend(*args, **kwargs)

    def _player_command(self, *args, **kwargs):
        return self.download_manager._player_command(*args, **kwargs)

    def _save_playback_progress(self, *args, **kwargs):
        self.download_manager._save_playback_progress(*args, **kwargs)

    def _run_player_with_progress(self, *args, **kwargs):
        return self.download_manager._run_player_with_progress(*args, **kwargs)

    def _play_via_player(self, *args, **kwargs):
        return self.download_manager._play_via_player(*args, **kwargs)

    def play_selected_download(self):
        self.download_manager.play_selected_download()

    def download_selected_link(self):
        self.download_manager.download_selected_link()

    def _probe_duration(self, *args, **kwargs):
        return self.download_manager._probe_duration(*args, **kwargs)

    def _ffmpeg_reader_thread(self, *args, **kwargs):
        self.download_manager._ffmpeg_reader_thread(*args, **kwargs)

    def _parse_ffmpeg_progress_line(self, *args, **kwargs):
        self.download_manager._parse_ffmpeg_progress_line(*args, **kwargs)

    def draw_download_progress(self, *args, **kwargs):
        self.download_manager.draw_download_progress(*args, **kwargs)

    def _ffmpeg_cmd(self, *args, **kwargs):
        return self.download_manager._ffmpeg_cmd(*args, **kwargs)

    def _download_out_path(self, *args, **kwargs):
        return self.download_manager._download_out_path(*args, **kwargs)

    def _download_one_episode(self, *args, **kwargs):
        return self.download_manager._download_one_episode(*args, **kwargs)

    def download_season(self):
        self.download_manager.download_season()

    def _run_season_download(self):
        self.download_manager._run_season_download()

    def draw_season_download(self, *args, **kwargs):
        self.download_manager.app = self  # Ensure reference is up to date
        self.download_manager.draw_season_download(*args, **kwargs)

    # Static methods
    @staticmethod
    def adjust_scroll(selected, offset, height):
        if height <= 0:
            return 0
        if selected < offset:
            offset = selected
        elif selected >= offset + height:
            offset = selected - height + 1
        return max(0, offset)

    @staticmethod
    def validate_season_number(value: str):
        if not value.isdigit():
            return None, f"'{value}' is not a valid season number."
        return int(value), None

    @staticmethod
    def validate_episode_number(value: str):
        if not value.isdigit():
            return None, f"'{value}' is not a valid episode number."
        num = int(value)
        if num <= 0:
            return None, "Episode number must be a positive integer."
        return num, None

    @staticmethod
    def _media_tag(mtype):
        return "[TV]  " if mtype == "tv" else "[MOVIE]"

    @staticmethod
    def _format_clock(seconds):
        try:
            seconds = max(0, int(float(seconds or 0)))
        except (TypeError, ValueError):
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    # TMDB methods
    def load_trending(self):
        self.status = "Loading trending..."
        self.draw()
        items, err = self.client.get_trending()
        self.error = err
        if err:
            self.status = "Failed to load trending."
            return
        self.trending = items
        self.trending_selected = 0
        self.trending_offset = 0
        self.screen = SCREEN_TRENDING
        self.status = f"Trending ({len(self.trending)} items)"

    # Provider management
    def open_providers(self):
        """Open the provider management screen."""
        self.screen = SCREEN_PROVIDERS
        self.provider_selected = 0
        self.provider_offset = 0
        self._fetch_provider_list()

    def _fetch_provider_list(self):
        """Fetch provider list from the JS server."""
        self.status = "Loading provider list..."
        self.draw()
        try:
            resp = self.client.session.get(f"{self.client.base_url}/api/providers", timeout=10)
            if resp.ok:
                data = resp.json()
                self.provider_list = data.get("providers", [])
                self.status = f"Providers loaded ({len(self.provider_list)})"
            else:
                self.provider_list = []
                self.status = "Failed to load providers"
        except Exception as e:
            self.provider_list = []
            self.status = f"Provider list error: {e}"
        self.draw()

    def _toggle_provider(self, enable: bool):
        """Enable or disable the selected provider."""
        if not self.provider_list:
            return
        provider = self.provider_list[self.provider_selected]
        name = provider.get("name", "").lower()
        if not name:
            return
        self.status = f"{'Enabling' if enable else 'Disabling'} {name}..."
        self.draw()
        try:
            flag = f"enable{name.capitalize()}Provider"
            patch = {flag: enable}
            resp = self.client.session.post(
                f"{self.client.base_url}/api/config",
                json=patch,
                timeout=10
            )
            if resp.ok:
                self._fetch_provider_list()
                self.status = f"{name} {'enabled' if enable else 'disabled'}"
            else:
                self.status = f"Failed: {resp.status_code}"
        except Exception as e:
            self.status = f"Error: {e}"
        self.draw()
        if not self.detail or self.detail.get("media_type") != "tv":
            return []
        return sorted(self.detail.get("seasons", []), key=lambda s: s.get("season_number", 0))

    def find_episode_by_number(self, num: int):
        for idx, ep in enumerate(self.episodes):
            if ep.get("episode_number") == num:
                return idx
        return None

    def _get_provider_idx_by_key(self, key: int) -> Optional[int]:
        char = chr(key)
        for idx, provider in enumerate(PROVIDERS):
            if provider.key == char:
                return idx
        return None

    def do_search(self):
        if not self.title_query:
            self.status = "Type a title first (/)."
            return
        self.status = f"Searching movies + TV for '{self.title_query}'..."
        self.draw()
        results, err = self.client.search_multi(self.title_query, self.year_query)
        self.error = err
        if err:
            self.status = "Search failed."
            return
        self.results = results
        self.selected = 0
        self.results_offset = 0
        self.detail = None
        self.screen = SCREEN_RESULTS
        self.status = (
            f"{len(results)} result(s) for '{self.title_query}'"
            + (f" ({self.year_query})" if self.year_query else "")
        )

    def do_id_lookup(self):
        if not self.id_query:
            self.status = "Type an ID first (i)."
            return
        self.status = f"Fetching {self.id_lookup_type} id={self.id_query}..."
        self.draw()
        data, err = self.client.get_by_id(self.id_lookup_type, self.id_query)
        self.error = err
        if err:
            self.status = "Lookup failed."
            return
        data["media_type"] = self.id_lookup_type
        self.results = []
        self.selected = 0
        self._enter_detail(data)
        self.status = f"Loaded id={self.id_query}"

    def open_selected_detail(self):
        if not self.results:
            return
        item = self.results[self.selected]
        media_type = item.get("media_type", "movie")
        self.status = f"Loading details for id={item.get('id')}..."
        self.draw()
        data, err = self.client.get_by_id(media_type, item.get("id"))
        self.error = err
        if err:
            self.status = f"Failed to load details: {err}"
            return
        data["media_type"] = media_type
        self._enter_detail(data)
        self.status = "Details loaded."

    def _enter_detail(self, data):
        self.detail = data
        self.season_selected = 0
        self.season_offset = 0
        self.season_data = None
        self.episodes = []
        self.episode_detail = None
        self.screen = SCREEN_DETAIL

    def load_season(self, season_number_str: str):
        if self.detail is None or self.detail.get("media_type") != "tv":
            return
        num, err = self.validate_season_number(season_number_str)
        if err:
            self.status = err
            return
        seasons = self.get_tv_seasons()
        if num not in [s.get("season_number") for s in seasons]:
            self.status = f"Season {num} does not exist for this show."
            return
        tv_id = self.detail.get("id")
        self.status = f"Loading season {num}..."
        self.draw()
        data, err = self.client.get_season(tv_id, num)
        self.error = err
        if err:
            self.status = f"Failed to load Season {num}: {err}"
            return
        self.season_data = data
        self.episodes = data.get("episodes", [])
        self.episode_selected = 0
        self.episode_offset = 0
        self.episode_detail = None
        self.screen = SCREEN_EPISODES
        for idx, s in enumerate(seasons):
            if s.get("season_number") == num:
                self.season_selected = idx
                break
        self.status = f"Season {num}: {len(self.episodes)} episode(s)."

    def select_season(self, season: dict):
        self.load_season(str(season.get("season_number", "")))

    def open_episode_by_index(self, idx: int):
        if not (0 <= idx < len(self.episodes)):
            self.status = "No such episode in this season."
            return
        self.episode_selected = idx
        self.episode_detail = self.episodes[idx]
        self.screen = SCREEN_EPISODE_DETAIL
        self.status = "Esc to go back, p to play."

    def open_episode_by_number(self, value: str):
        num, err = self.validate_episode_number(value)
        if err:
            self.status = err
            return
        idx = self.find_episode_by_number(num)
        if idx is None:
            season_num = self.season_data.get("season_number", "?") if self.season_data else "?"
            self.status = f"Episode {num} not found in Season {season_num}."
            return
        self.open_episode_by_index(idx)

    def open_item(self, item):
        media_type = item.get("media_type", "movie")
        item_id = item.get("id") or item.get("tmdb_id")
        self.status = f"Loading details for id={item_id}..."
        self.draw()
        data, err = self.client.get_by_id(media_type, item_id)
        self.error = err
        if err:
            self.status = f"Failed to load details: {err}"
            return
        data["media_type"] = media_type
        self._enter_detail(data)
        self.status = "Details loaded."

    def show_history(self):
        self._load_history()
        self.history_selected = 0
        self.history_offset = 0
        self.screen = SCREEN_HISTORY
        self.status = f"Playback history ({len(self.history)} items)"

    def resume_from_history(self, entry):
        """Resume playback from a history entry."""
        tmdb_id = str(entry.get("tmdb_id") or entry.get("id") or "")
        media_type = entry.get("media_type", "movie")
        if not tmdb_id:
            self.status = "Invalid history entry: missing TMDB ID"
            return

        # Load detail
        self.status = f"Loading details for {media_type} id={tmdb_id}..."
        self.draw()
        data, err = self.client.get_by_id(media_type, tmdb_id)
        if err:
            self.status = f"Failed to load details: {err}"
            return
        data["media_type"] = media_type
        self._enter_detail(data)

        # If TV, load season and episode
        if media_type == "tv":
            season_num = entry.get("season_number")
            episode_num = entry.get("episode_number")
            if season_num is not None:
                self.load_season(str(season_num))
                if episode_num is not None and self.episodes:
                    # Find episode by number
                    for idx, ep in enumerate(self.episodes):
                        if ep.get("episode_number") == episode_num:
                            self.episode_selected = idx
                            self.episode_detail = ep
                            self.screen = SCREEN_EPISODE_DETAIL
                            break

        # Now play
        self.play_stream()

    def open_downloads(self):
        if not self.detail:
            self.status = "No media selected."
            return

        # Cancel any refresh from a previous visit before replacing the list.
        self.stop_live_link_updates()

        media_type = self.detail.get("media_type", "movie")
        tmdb_id = str(self.detail.get("id", ""))
        title = self.detail.get("title") or self.detail.get("name", "")
        year = (self.detail.get("release_date") or self.detail.get("first_air_date") or "")[:4]

        season_num = "1"
        episode_num = "1"
        if media_type == "tv":
            if not self.season_data or not self.episode_detail:
                self.status = "Please select a season and episode first."
                return
            season_num = str(self.season_data.get("season_number", "1"))
            episode_num = str(self.episode_detail.get("episode_number", "1"))

        self.status = "Resolving links from all providers..."
        self.draw()

        # Ask for a fast, partial answer so the list paints immediately. The
        # server keeps resolving providers in the background and the live
        # updater folds their links in as they arrive.
        try:
            links, partial = self.resolve_download_links(
                tmdb_id,
                title,
                media_type,
                season_num,
                episode_num,
                year=year,
                fast=True,
            )
        except Exception as e:
            log.warning("Link resolution failed: %s", e)
            links, partial = [], False

        self.download_links = links
        self.download_selected = 0
        self.download_offset = 0
        self._link_sizes = {}
        # Always transition to the downloads screen so the user sees the
        # (possibly still growing) list and a clear status, rather than
        # staying on the detail screen where Enter/p/d mean something else.
        self.screen = SCREEN_DOWNLOADS

        if not links and not partial:
            self.status = "No stream links found from any provider."
            self.draw()
            return

        if partial:
            self.status = self.download_manager._loading_hint(len(links))
            self.start_live_link_updates(
                tmdb_id, title, media_type, season_num, episode_num, year=year
            )
        else:
            self.status = self._downloads_hint(len(links))
        self.draw()
        if links:
            self._prefetch_link_sizes_async(links)

    @staticmethod
    def _downloads_hint(count: int) -> str:
        return DownloadManager._downloads_hint(count)

    def _prefetch_link_sizes_async(self, links):
        """Probe link sizes in the background so the UI stays responsive."""
        link_list = list(links)

        def done():
            # Only refresh the status if the user is still looking at this
            # list (curses must only be touched from the main thread).
            if self.screen == SCREEN_DOWNLOADS and not self.links_partial:
                self.status = self._downloads_hint(len(self.download_links))

        self.download_manager.prefetch_link_sizes_async(link_list, on_done=done)

    def play_stream(self, start_provider_idx=None):
        if not self.detail:
            self.status = "No media selected."
            return

        media_type = self.detail.get("media_type", "movie")
        tmdb_id = str(self.detail.get("id", ""))
        title = self.detail.get("title") or self.detail.get("name", "")
        year = (self.detail.get("release_date") or self.detail.get("first_air_date") or "")[:4]

        season_num = "1"
        episode_num = "1"
        if media_type == "tv":
            if not self.season_data or not self.episode_detail:
                self.status = "Please select a season and episode first."
                return
            season_num = str(self.season_data.get("season_number", "1"))
            episode_num = str(self.episode_detail.get("episode_number", "1"))

        if not PROVIDERS:
            self.status = "No stream providers available."
            self.draw()
            self.stdscr.refresh()
            return

        start_idx = start_provider_idx if start_provider_idx is not None else 0
        start_idx = max(0, min(start_idx, len(PROVIDERS) - 1))

        for idx in range(start_idx, len(PROVIDERS)):
            provider = PROVIDERS[idx]
            if server_state().get("state") == "starting":
                self.status = "Waiting for JS server to start..."
            else:
                self.status = f"Fetching streams from {provider.name}..."
            self.draw()
            self.stdscr.refresh()

            # Playback only needs one working link, so ask providers that
            # support it to return early instead of waiting for every source.
            fetch_kwargs = {"year": year}
            try:
                if "fast" in inspect.signature(provider.fetch_func).parameters:
                    fetch_kwargs["fast"] = True
            except (TypeError, ValueError):
                pass
            try:
                candidate_streams = provider.fetch_func(
                    tmdb_id, title, media_type, season_num, episode_num, **fetch_kwargs
                )
            except Exception as e:
                log.warning("%s failed: %s", provider.name, e)
                self.status = f"{provider.name} failed, falling back to next provider..."
                self.draw()
                self.stdscr.refresh()
                continue

            provider_success = False
            for i, (stream_url, stream_headers) in enumerate(candidate_streams):
                try:
                    self.status = f"Playing via {provider.name} via {self.player_backend.upper()} (Stream {i + 1})..."
                    self.draw()
                    self.stdscr.refresh()

                    code = self._play_via_player(stream_url, stream_headers)

                    if code == 0:
                        provider_success = True
                        break
                    else:
                        log.warning("Stream %d failed (player code %d)", i + 1, code)

                except FileNotFoundError:
                    self.status = f"Error: '{self.player_backend}' executable not found."
                    provider_success = True
                    break
                except Exception as e:
                    log.warning("Playback error: %s", e)

            if provider_success:
                self.status = "Playback finished."
                note = self.download_manager.take_playback_note()
                if note:
                    self.status = f"{self.status} {note}"
                self.draw()
                self.stdscr.refresh()
                return

            self.status = f"All streams in {provider.name} failed. Trying next provider..."
            self.draw()
            self.stdscr.refresh()

        self.status = "All providers failed to find or play streams."
        note = self.download_manager.take_playback_note()
        if note:
            self.status = f"{self.status} {note}"
        self.draw()
        self.stdscr.refresh()

    # Drawing
    def draw(self):
        stdscr = self.stdscr
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        if h < MIN_HEIGHT or w < MIN_WIDTH:
            draw_too_small(stdscr, h, w)
            stdscr.refresh()
            return

        draw_header_bar(self, stdscr, h, w)

        list_top = 3
        list_bottom = h - 3
        list_height = max(0, list_bottom - list_top)

        if self.screen == SCREEN_HOME:
            draw_home(self, stdscr, list_top, list_height, w)
        elif self.screen == SCREEN_RESULTS:
            draw_results(self, stdscr, list_top, list_height, w)
        elif self.screen == SCREEN_DETAIL:
            draw_detail(self, stdscr, list_top, list_bottom, list_height, w)
        elif self.screen == SCREEN_EPISODES:
            draw_episode_list(self, stdscr, list_top, list_height, w)
        elif self.screen == SCREEN_EPISODE_DETAIL:
            draw_episode_detail(self, stdscr, list_top, list_bottom, w)
        elif self.screen == SCREEN_TRENDING:
            draw_trending(self, stdscr, list_top, list_height, w)
        elif self.screen == SCREEN_HISTORY:
            draw_history(self, stdscr, list_top, list_height, w)
        elif self.screen == SCREEN_DOWNLOADS:
            draw_downloads(self, stdscr, list_top, list_height, w)
        elif self.screen == SCREEN_PROVIDERS:
            draw_providers(self, stdscr, list_top, list_height, w)
        elif self.screen == SCREEN_NEW:
            draw_new_screen(self, stdscr, list_top, list_height, w)
        elif self.screen == SCREEN_SEASON_DOWNLOAD:
            draw_season_download(self, stdscr, list_top, list_height, w)

        draw_mode_prompt(self, stdscr, h, w)
        draw_status_line(self, stdscr, h, w)
        stdscr.refresh()

    def help_text(self):
        text = self._base_help_text()
        if not text:
            return ""
        return f"{text}   m {self.player_backend.upper()} (press m to switch player)"

    def _base_help_text(self):
        provider_help = "  ".join([f"{p.key} {p.name}" for p in PROVIDERS]) + "  p Play (Auto)"
        if self.screen == SCREEN_HOME:
            return ("j/k Select   Enter Resume   / Search   h History   t Trending   P Providers   q Quit")
        if self.screen == SCREEN_RESULTS:
            return (" / Search   y Year   i ID lookup   t Trending   h History   "
                    "Tab Movie/TV ID type   j/k Move   Enter Open   q Quit")
        if self.screen == SCREEN_DETAIL:
            if self.detail and self.detail.get("media_type") == "tv":
                return f"{provider_help}   o Links   j/k Select season   Enter Open   s Type season #   d Season   Esc Back"
            return f"{provider_help}   o Links   Esc Back"
        if self.screen == SCREEN_EPISODES:
            return f"{provider_help}   o Links   j/k Select ep   Enter Open   e Jump   d Season   Esc Back"
        if self.screen == SCREEN_EPISODE_DETAIL:
            return f"{provider_help}   o Links   d Season   Esc Back"
        if self.screen == SCREEN_TRENDING:
            return f"{provider_help}   j/k Move   Enter Open   p Play (Auto)   Esc Back"
        if self.screen == SCREEN_HISTORY:
            return f"{provider_help}   j/k Move   Enter Open   c Clear   Esc Back"
        if self.screen == SCREEN_DOWNLOADS:
            return f"{provider_help}   j/k Move   p Play   d Download   Esc Back"
        if self.screen == SCREEN_PROVIDERS:
            return "j/k Move   Space Toggle   r Refresh   Esc Back"
        return ""

    # Key handling delegation
    def handle_key(self, key):
        return self.key_handler.handle_key(key)

    def _poll_server_state(self) -> bool:
        """Reflect background JS-server progress in the status line.

        Returns True when the visible status changed, so the caller can
        repaint. The server boots on a worker thread; this never blocks.
        """
        state = server_state().get("state")
        if state == self._server_state_seen:
            return False
        self._server_state_seen = state

        if state == "starting":
            if self.screen == SCREEN_HOME and self.mode == MODE_NORMAL:
                self.status = "Starting JS server in the background..."
        elif state == "ready":
            if self.status.startswith("Starting JS server"):
                self.status = "JS server ready."
        elif state == "failed":
            self.status = "JS server failed to start — streaming unavailable."
        return True

    def run(self):
        self.draw()
        while True:
            # A short getch timeout keeps the loop ticking so background work
            # (link-size probing) becomes visible without a keypress.
            self.stdscr.timeout(self.POLL_INTERVAL_MS)
            key = self.stdscr.getch()
            if key == -1:  # no input within the poll interval
                # Only the downloads screen and JS-server state change without
                # a keypress, so don't burn CPU repainting idle screens.
                server_changed = self._poll_server_state()
                if server_changed or self.screen == SCREEN_DOWNLOADS:
                    self.draw()
                continue
            result = self.handle_key(key)
            if result == "quit":
                break
            self.draw()


def main(stdscr):
    app = App(stdscr)
    app.run()


if __name__ == "__main__":
    # Cross-platform log directory
    log_dir = os.path.join(os.path.expanduser("~"), ".stub-tui", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "tmdb_tui.log")

    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    locale.setlocale(locale.LC_ALL, "")
    curses.wrapper(main)