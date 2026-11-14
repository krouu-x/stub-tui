# Key handlers for the TUI
import curses
import logging
from typing import Optional

from stub_main.config import (
    SCREEN_HOME, SCREEN_RESULTS, SCREEN_DETAIL, SCREEN_EPISODES,
    SCREEN_EPISODE_DETAIL, SCREEN_TRENDING, SCREEN_HISTORY,
    SCREEN_DOWNLOADS, SCREEN_NEW, SCREEN_SEASON_DOWNLOAD,
    MODE_NORMAL, MODE_SEARCH, MODE_YEAR, MODE_ID, MODE_SEASON, MODE_EPISODE,
)

log = logging.getLogger("tmdb_tui.ui.key_handlers")


class KeyHandler:
    def __init__(self, app):
        self.app = app

    def handle_key(self, key) -> Optional[str]:
        if key == curses.KEY_RESIZE:
            curses.update_lines_cols()
            self.app.stdscr.clear()
            self.app.stdscr.refresh()
            return None
        if self.app.mode != MODE_NORMAL:
            self.handle_text_input(key)
            return None
        if key in (ord("q"), ord("Q")):
            return "quit"
        if key in (ord("m"), ord("M")):
            self.app.cycle_player_backend()
            return None
        if self.app.screen == SCREEN_HOME:
            self.handle_key_home(key)
        elif self.app.screen == SCREEN_RESULTS:
            self.handle_key_results(key)
        elif self.app.screen == SCREEN_DETAIL:
            self.handle_key_detail(key)
        elif self.app.screen == SCREEN_EPISODES:
            self.handle_key_episodes(key)
        elif self.app.screen == SCREEN_EPISODE_DETAIL:
            self.handle_key_episode_detail(key)
        elif self.app.screen == SCREEN_TRENDING:
            self.handle_key_trending(key)
        elif self.app.screen == SCREEN_HISTORY:
            self.handle_key_history(key)
        elif self.app.screen == SCREEN_DOWNLOADS:
            self.handle_key_downloads(key)
        elif self.app.screen == SCREEN_PROVIDERS:
            self.handle_key_providers(key)
        elif self.app.screen == SCREEN_NEW:
            self.handle_key_new(key)
        elif self.app.screen == SCREEN_SEASON_DOWNLOAD:
            self.handle_key_season_download(key)
        return None

    def handle_key_home(self, key):
        from stub_main.history import HistoryManager
        entries = sorted(
            self.app.history,
            key=lambda e: e.get("last_watched") or e.get("played_at") or "",
            reverse=True,
        )

        if key in (curses.KEY_DOWN, ord("j")):
            if entries:
                self.app.home_selected = min(self.app.home_selected + 1, len(entries) - 1)
        elif key in (curses.KEY_UP, ord("k")):
            if entries:
                self.app.home_selected = max(self.app.home_selected - 1, 0)
        elif key in (curses.KEY_ENTER, 10, 13, ord("p"), ord("P")):
            if entries and 0 <= self.app.home_selected < len(entries):
                self.app.resume_from_history(entries[self.app.home_selected])
        elif key == ord("/"):
            self.app.mode = MODE_SEARCH
            self.app.input_buffer = self.app.title_query
        elif key == ord("h"):
            self.app.show_history()
        elif key == ord("t"):
            self.app.load_trending()
        elif key in (ord("P"),):
            self.app.open_providers()
        elif key == 27:
            self.app.screen = SCREEN_RESULTS
            self.app.error = None
            self.app.status = "Results."

    def handle_key_results(self, key):
        if key == 9:  # Tab
            self.app.id_lookup_type = "tv" if self.app.id_lookup_type == "movie" else "movie"
            self.app.status = f"ID-lookup type set to {self.app.id_lookup_type.upper()} (used by the 'i' command)."
        elif key == ord("/"):
            self.app.mode = MODE_SEARCH
            self.app.input_buffer = self.app.title_query
        elif key == ord("y"):
            self.app.mode = MODE_YEAR
            self.app.input_buffer = self.app.year_query
        elif key == ord("i"):
            self.app.mode = MODE_ID
            self.app.input_buffer = self.app.id_query
        elif key == ord("t"):
            self.app.load_trending()
        elif key == ord("h"):
            self.app.show_history()
        elif key in (curses.KEY_DOWN, ord("j")):
            if self.app.results:
                self.app.selected = min(self.app.selected + 1, len(self.app.results) - 1)
        elif key in (curses.KEY_UP, ord("k")):
            if self.app.results:
                self.app.selected = max(self.app.selected - 1, 0)
        elif key in (curses.KEY_ENTER, 10, 13):
            self.app.open_selected_detail()
        elif key in (ord("d"), ord("D")):
            self.app.screen = SCREEN_NEW
            self.app.error = None
            self.app.status = "New screen — press Esc to go back."

    def handle_key_detail(self, key):
        if key == 27:
            self.app.detail = None
            self.app.season_data = None
            self.app.episodes = []
            self.app.episode_detail = None
            self.app.error = None
            self.app.screen = SCREEN_RESULTS
            self.app.status = "Back to results."
            return
        if key in (ord("o"), ord("O")):
            self.app.open_downloads()
            return
        provider_idx = self.app._get_provider_idx_by_key(key)
        if provider_idx is not None:
            self.app.play_stream(start_provider_idx=provider_idx)
            return
        if key in (ord("p"), ord("P")):
            self.app.play_stream(start_provider_idx=None)
            return
        if not self.app.detail or self.app.detail.get("media_type") != "tv":
            return
        seasons = self.app.get_tv_seasons()
        if key in (curses.KEY_DOWN, ord("j")):
            if seasons:
                self.app.season_selected = min(self.app.season_selected + 1, len(seasons) - 1)
        elif key in (curses.KEY_UP, ord("k")):
            if seasons:
                self.app.season_selected = max(self.app.season_selected - 1, 0)
        elif key in (curses.KEY_ENTER, 10, 13):
            if seasons:
                self.app.select_season(seasons[self.app.season_selected])
        elif key == ord("s"):
            self.app.mode = MODE_SEASON
            self.app.input_buffer = ""
        elif key in (ord("d"), ord("D")):
            self.app.download_season()
            return

    def handle_key_episodes(self, key):
        if key in (ord("o"), ord("O")):
            self.app.open_downloads()
            return
        provider_idx = self.app._get_provider_idx_by_key(key)
        if provider_idx is not None:
            self.app.play_stream(start_provider_idx=provider_idx)
            return
        elif key in (ord("p"), ord("P")):
            self.app.play_stream(start_provider_idx=None)
            return
        elif key == ord("e"):
            self.app.mode = MODE_EPISODE
            self.app.input_buffer = ""
        elif key in (curses.KEY_DOWN, ord("j")):
            if self.app.episodes:
                self.app.episode_selected = min(self.app.episode_selected + 1, len(self.app.episodes) - 1)
        elif key in (curses.KEY_UP, ord("k")):
            if self.app.episodes:
                self.app.episode_selected = max(self.app.episode_selected - 1, 0)
        elif key in (curses.KEY_ENTER, 10, 13):
            self.app.open_episode_by_index(self.app.episode_selected)
        elif key in (ord("d"), ord("D")):
            self.app.download_season()
            return
        elif key == 27:
            self.app.screen = SCREEN_DETAIL
            self.app.error = None
            self.app.status = "Back to show details."

    def handle_key_episode_detail(self, key):
        if key in (ord("o"), ord("O")):
            self.app.open_downloads()
            return
        provider_idx = self.app._get_provider_idx_by_key(key)
        if provider_idx is not None:
            self.app.play_stream(start_provider_idx=provider_idx)
            return
        elif key in (ord("p"), ord("P")):
            self.app.play_stream(start_provider_idx=None)
            return
        elif key in (ord("d"), ord("D")):
            self.app.download_season()
            return
        elif key == 27:
            self.app.screen = SCREEN_EPISODES
            self.app.error = None
            self.app.status = "Back to episode list."

    def handle_key_trending(self, key):
        if key == 27:
            self.app.screen = SCREEN_RESULTS
            self.app.error = None
            self.app.status = "Back to results."
            return
        if key in (ord("p"), ord("P")):
            if self.app.trending:
                self.app.play_stream(start_provider_idx=None)
            return
        if key in (curses.KEY_DOWN, ord("j")):
            if self.app.trending:
                self.app.trending_selected = min(
                    self.app.trending_selected + 1, len(self.app.trending) - 1
                )
        elif key in (curses.KEY_UP, ord("k")):
            if self.app.trending:
                self.app.trending_selected = max(self.app.trending_selected - 1, 0)
        elif key in (curses.KEY_ENTER, 10, 13):
            if self.app.trending:
                self.app.open_item(self.app.trending[self.app.trending_selected])

    def handle_key_history(self, key):
        if key == 27:
            self.app.screen = SCREEN_RESULTS
            self.app.error = None
            self.app.status = "Back to results."
            return
        if key in (curses.KEY_DOWN, ord("j")):
            if self.app.history:
                self.app.history_selected = min(
                    self.app.history_selected + 1, len(self.app.history) - 1
                )
        elif key in (curses.KEY_UP, ord("k")):
            if self.app.history:
                self.app.history_selected = max(self.app.history_selected - 1, 0)
        elif key in (curses.KEY_ENTER, 10, 13):
            if self.app.history:
                self.app.open_item(self.app.history[self.app.history_selected])
        elif key in (ord("c"), ord("C")):
            self.app.history = []
            self.app.history_selected = 0
            self.app.history_offset = 0
            self.app._save_history()
            self.app.status = "History cleared."

    def handle_key_downloads(self, key):
        if key == 27:
            self.app.stop_live_link_updates()
            self.app.screen = SCREEN_RESULTS
            self.app.error = None
            self.app.status = "Back to results."
            return
        if key in (ord("p"), ord("P")):
            self.app.play_selected_download()
            return
        if key in (curses.KEY_ENTER, 10, 13):
            self.app.play_selected_download()
            return
        if key in (ord("d"), ord("D")):
            self.app.download_selected_link()
            return
        if key in (curses.KEY_DOWN, ord("j")):
            if self.app.download_links:
                self.app.download_selected = min(self.app.download_selected + 1, len(self.app.download_links) - 1)
        elif key in (curses.KEY_UP, ord("k")):
            if self.app.download_links:
                self.app.download_selected = max(self.app.download_selected - 1, 0)

    def handle_key_new(self, key):
        if key == 27:
            self.app.screen = SCREEN_RESULTS
            self.app.error = None
            self.app.status = "Back to results."
            return

    def handle_key_providers(self, key):
        if key == 27:
            self.app.screen = SCREEN_HOME
            self.app.status = "Back to home."
            return
        if key in (curses.KEY_DOWN, ord("j")):
            if getattr(self.app, "provider_list", None):
                self.app.provider_selected = min(self.app.provider_selected + 1, len(self.app.provider_list) - 1)
        elif key in (curses.KEY_UP, ord("k")):
            if getattr(self.app, "provider_list", None):
                self.app.provider_selected = max(self.app.provider_selected - 1, 0)
        elif key == ord(" "):
            # Toggle provider
            if getattr(self.app, "provider_list", None):
                provider = self.app.provider_list[self.app.provider_selected]
                enabled = provider.get("enabled", True)
                self.app._toggle_provider(not enabled)
        elif key in (ord("r"), ord("R")):
            self.app._fetch_provider_list()

    def handle_key_season_download(self, key):
        if key in (27, ord("q"), ord("Q")):
            self.app.sd_cancel = True
            self.app.screen = SCREEN_EPISODES
            self.app.status = "Season download stopped."
            return

    def handle_text_input(self, key):
        if key == 27:
            self.app.mode = MODE_NORMAL
            self.app.input_buffer = ""
            return
        if key in (curses.KEY_ENTER, 10, 13):
            value = self.app.input_buffer.strip()
            mode = self.app.mode
            self.app.mode = MODE_NORMAL
            if mode == MODE_SEARCH:
                self.app.title_query = value
                self.app.do_search()
            elif mode == MODE_YEAR:
                self.app.year_query = value
                if self.app.title_query:
                    self.app.do_search()
            elif mode == MODE_ID:
                self.app.id_query = value
                self.app.do_id_lookup()
            elif mode == MODE_SEASON:
                if value:
                    self.app.load_season(value)
                else:
                    self.app.status = "Type a season number first."
            elif mode == MODE_EPISODE:
                if value:
                    self.app.open_episode_by_number(value)
                else:
                    self.app.status = "Type an episode number first."
            return
        if key in (curses.KEY_BACKSPACE, 127, 8):
            self.app.input_buffer = self.app.input_buffer[:-1]
            return
        if 32 <= key <= 126:
            self.app.input_buffer += chr(key)