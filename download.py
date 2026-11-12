# Download functionality with ffmpeg
import os
import re
import time
import subprocess
import threading
import logging
import curses
from typing import List, Tuple, Dict, Optional
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests

from stub_main.config import BROWSER_HEADERS, DEFAULT_PLAYER
from stub_main.metadata import _fetch_content_length, _format_size

log = logging.getLogger("tmdb_tui.download")


class DownloadManager:
    def __init__(self, app_ref):
        """Initialize with reference to main App for callbacks."""
        self.app = app_ref
        self.download_links: List[Tuple[str, Dict[str, str], str]] = []
        self.download_selected = 0
        self.download_offset = 0
        self._link_sizes: Dict[str, int] = {}
        self._download_state: Dict = {}
        self._download_proc = None
        self.sd_cancel = False
        self.sd_episodes = []
        self.sd_index = 0

    def gather_download_links(
        self,
        tmdb_id: str,
        title: str,
        media_type: str,
        season_num: str,
        episode_num: str,
        year: str = "",
    ) -> List[Tuple[str, Dict[str, str], str]]:
        """Resolve streams from every provider (no deduplication).

        Returns a list of (url, headers, provider_name).
        """
        from .providers import PROVIDERS
        links: List[Tuple[str, Dict[str, str], str]] = []
        for provider in PROVIDERS:
            self.app.status = f"Resolving {provider.name}..."
            self.app.draw()
            self.app.stdscr.refresh()
            try:
                streams = provider.fetch_func(
                    tmdb_id, title, media_type, season_num, episode_num, year=year
                )
            except Exception as e:
                log.warning("%s failed: %s", provider.name, e)
                continue
            for url, headers in streams:
                links.append((url, headers, provider.name))
        return links

    def _prefetch_link_sizes(self, links: List[Tuple[str, Dict[str, str], str]]) -> None:
        """Best-effort parallel Content-Length probe for the resolved links."""
        with ThreadPoolExecutor(max_workers=min(8, len(links))) as ex:
            futures = {
                ex.submit(_fetch_content_length, url, headers): (url, headers)
                for url, headers, _ in links
            }
            for fut in as_completed(futures):
                try:
                    size = fut.result()
                    if size is not None:
                        url, _ = futures[fut]
                        self._link_sizes[url] = size
                except Exception:
                    pass

    def _player_backend(self, backend: Optional[str] = None) -> str:
        """Resolve the playback backend to use ('mpv' or 'vlc')."""
        if backend:
            return backend
        return getattr(self.app, "player_backend", None) or DEFAULT_PLAYER

    def _player_command(
        self,
        stream_url: str,
        stream_headers: Dict[str, str],
        start_position: float = 0,
        ipc_path: Optional[str] = None,
        backend: Optional[str] = None,
    ) -> List[str]:
        """Build the argv list for a stream using the chosen player backend."""
        backend = self._player_backend(backend)
        if backend == "vlc":
            return self._vlc_command(stream_url, stream_headers)
        return self._mpv_command(
            stream_url,
            stream_headers,
            start_position=start_position,
            ipc_path=ipc_path,
        )

    def _mpv_command(
        self,
        stream_url: str,
        stream_headers: Dict[str, str],
        start_position: float = 0,
        ipc_path: Optional[str] = None,
    ) -> List[str]:
        """Build the mpv argv list for a stream (no shell, no embedded quotes)."""
        cmd = ["mpv", "--no-ytdl"]

        if start_position and start_position > 0:
            cmd.append(f"--start={start_position}")

        if ipc_path:
            cmd.append(f"--input-ipc-server={ipc_path}")

        ua = stream_headers.get("User-Agent") or stream_headers.get("user-agent")
        if ua:
            cmd.append(f"--user-agent={ua}")

        referer = stream_headers.get("Referer") or stream_headers.get("referer")
        if referer:
            cmd.append(f"--referrer={referer}")

        extra_headers = [
            f"{k}: {v}" for k, v in stream_headers.items()
            if k.lower() not in ("user-agent", "referer")
        ]
        if extra_headers:
            cmd.append("--http-header-fields=" + ",".join(extra_headers))

        cmd.append(stream_url)
        return cmd

    def _vlc_command(self, stream_url: str, stream_headers: Dict[str, str]) -> List[str]:
        """Build the VLC argv list for a stream (no shell, no embedded quotes).

        VLC exposes no IPC/progress tracking and no direct start-position flag,
        so playback always starts from the beginning and progress is not tracked.
        """
        cmd = ["vlc", "--play-and-exit"]
        cmd.append(stream_url)
        return cmd

    def _save_playback_progress(self, item_info: dict, state: dict):
        """Persist playback progress to history (best effort, no crash)."""
        try:
            media_type = item_info.get("media_type", "movie")
            position = state.get("position_seconds", 0)
            duration = state.get("duration_seconds")
            progress = 0.0
            if duration:
                progress = round((position / duration) * 100.0, 1)
            self.app.add_history(
                item_info,
                season_number=item_info.get("season_number"),
                episode_number=item_info.get("episode_number"),
                episode_title=item_info.get("episode_title"),
                position_seconds=position,
                duration_seconds=duration,
                progress_percent=progress,
            )
        except Exception as e:
            log.warning("failed to save playback progress: %s", e)

    def _run_player_with_progress(
        self,
        stream_url: str,
        stream_headers: Dict[str, str],
        history_item: dict,
        start_seconds: float = 0,
        backend: Optional[str] = None,
    ) -> int:
        """Run the configured player backend with progress handling, restoring curses afterwards."""
        backend = self._player_backend(backend)

        if backend == "vlc":
            return self._run_vlc(stream_url, stream_headers, history_item)

        return self._run_mpv_with_progress(
            stream_url,
            stream_headers,
            history_item,
            start_seconds=start_seconds,
        )

    def _run_vlc(
        self,
        stream_url: str,
        stream_headers: Dict[str, str],
        history_item: dict,
    ) -> int:
        """Run VLC (no IPC progress tracking available; starts from the beginning)."""
        cmd = self._vlc_command(stream_url, stream_headers)

        proc = None
        try:
            import curses
            curses.endwin()
            proc = subprocess.Popen(cmd)
            proc.wait()
            curses.reset_prog_mode()
            curses.doupdate()
            self.app.stdscr.refresh()
        except FileNotFoundError:
            self.app.status = "Error: 'vlc' executable not found."
            return -1
        except Exception as e:
            log.warning("Playback error: %s", e)
            return -1
        finally:
            self.app._ipc_path = None
            self.app._playback_state = None
        return proc.returncode if proc else -1

    def _run_mpv_with_progress(
        self,
        stream_url: str,
        stream_headers: Dict[str, str],
        history_item: dict,
        start_seconds: float = 0,
    ) -> int:
        """Run mpv with IPC progress tracking, restoring curses afterwards."""
        from .mpv_ipc import _mpv_progress_poll, make_ipc_path

        ipc_path = make_ipc_path()
        cmd = self._mpv_command(
            stream_url,
            stream_headers,
            start_position=start_seconds,
            ipc_path=ipc_path,
        )

        stop_event = threading.Event()
        state = {
            "position_seconds": start_seconds,
            "duration_seconds": None,
            "progress_percent": 0.0,
        }
        poller = threading.Thread(
            target=_mpv_progress_poll,
            args=(self.app, ipc_path, stop_event, state, history_item),
            daemon=True,
        )

        proc = None
        try:
            import curses
            curses.endwin()
            proc = subprocess.Popen(cmd)
            poller.start()
            proc.wait()
            stop_event.set()
            poller.join(timeout=3)
            curses.reset_prog_mode()
            curses.doupdate()
            self.app.stdscr.refresh()
        except FileNotFoundError:
            self.app.status = "Error: 'mpv' executable not found."
            stop_event.set()
            return -1
        except Exception as e:
            log.warning("Playback error: %s", e)
            stop_event.set()
            return -1
        finally:
            self.app._ipc_path = None
            self.app._playback_state = None
        return proc.returncode if proc else -1

    def _play_via_player(
        self,
        stream_url: str,
        stream_headers: Dict[str, str],
        backend: Optional[str] = None,
    ) -> int:
        """Launch the chosen player backend, auto-resuming from the saved history position if any."""
        backend = self._player_backend(backend)
        item = self.app._current_history_item()
        existing = self.app._find_history_entry(item)
        resume = 0.0
        if backend == "mpv" and existing:
            try:
                resume = max(0.0, float(existing.get("position_seconds", 0) or 0))
                duration = existing.get("duration_seconds")
                # Don't resume a movie that was watched to the end: starting
                # at the final seconds would immediately hit EOF.
                if duration and resume >= float(duration) - 5.0:
                    resume = 0.0
            except (TypeError, ValueError):
                resume = 0.0
        if backend == "vlc":
            # VLC has no start-position flag / progress tracking; start over.
            return self._run_vlc(stream_url, stream_headers, item)
        return self._run_mpv_with_progress(
            stream_url, stream_headers, item, start_seconds=resume
        )

    def _play_via_mpv(self, stream_url: str, stream_headers: Dict[str, str]) -> int:
        """Launch mpv, auto-resuming from the saved history position if any."""
        return self._play_via_player(stream_url, stream_headers, backend="mpv")

    def _play_via_vlc(self, stream_url: str, stream_headers: Dict[str, str]) -> int:
        """Launch VLC (no resume / progress tracking)."""
        return self._play_via_player(stream_url, stream_headers, backend="vlc")

    def play_selected_download(self):
        if not self.download_links:
            self.app.status = "No stream links available."
            return
        if not (0 <= self.download_selected < len(self.download_links)):
            self.app.status = "Selected link no longer available. Pick a link and try again."
            return
        url, headers, _ = self.download_links[self.download_selected]

        self.app.status = "Playing selected link..."
        self.app.draw()
        self.app.stdscr.refresh()
        try:
            code = self._play_via_player(url, headers)
        except Exception as e:
            log.warning("Playback error: %s", e)
            self.app.status = f"Playback error: {e}"
            return
        if code == 0:
            self.app.status = "Playback finished."
        else:
            self.app.status = f"Playback ended (code {code})."
        self.app.draw()

    def download_selected_link(self):
        """Download the chosen link with ffmpeg (stream copy) into ./downloads,
        showing a curses progress screen instead of raw ffmpeg output."""
        if not self.download_links:
            self.app.status = "No stream links available."
            return
        if not (0 <= self.download_selected < len(self.download_links)):
            self.app.status = "Selected link no longer available. Pick a link and try again."
            return
        url, headers, provider = self.download_links[self.download_selected]
        title = (
            self.app.detail.get("title") or self.app.detail.get("name") or "media"
        ).strip()

        def safe_name(value):
            return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .") or "Unknown"

        safe_title = safe_name(title)
        root_dir = Path(__file__).resolve().parent / "downloads"
        ext = os.path.splitext(urlparse(url).path)[1] or ".mp4"

        if self.app.detail.get("media_type") == "tv":
            if not self.app.season_data or not self.app.episode_detail:
                self.app.status = "Season or episode information missing."
                return
            season = int(self.app.season_data.get("season_number", 1))
            episode = int(self.app.episode_detail.get("episode_number", 1))
            episode_title = safe_name(
                self.app.episode_detail.get("name") or f"Episode {episode}"
            )
            out_dir = root_dir / safe_title / f"Season [{season}]"
            filename = f"Episode [{episode}] - {episode_title}{ext}"
        else:
            out_dir = root_dir / safe_title
            filename = f"{safe_title}{ext}"

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename

        # Prevent overwriting existing downloads
        base = out_path.stem
        extension = out_path.suffix
        counter = 1
        while out_path.exists():
            out_path = out_dir / f"{base} ({counter}){extension}"
            counter += 1
        out_path = str(out_path)
        log.info("Actual download destination: %s", out_path)

        duration = self._probe_duration(url, headers)

        cmd = ["ffmpeg", "-y", "-nostdin", "-stats"]
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            cmd += ["-user_agent", ua]
        referer = headers.get("Referer") or headers.get("referer")
        if referer:
            cmd += ["-referer", referer]
        extra = [
            f"{k}: {v}" for k, v in headers.items()
            if k.lower() not in ("user-agent", "referer")
        ]
        if extra:
            cmd += ["-headers", "\r\n".join(extra) + "\r\n"]
        cmd += ["-i", url, "-c", "copy", out_path]

        self._download_state = {
            "done": False,
            "cancelled": False,
            "error": None,
            "provider": provider,
            "frame": 0,
            "time": "00:00:00.00",
            "speed": "",
            "rate_bps": 0.0,
            "_last_size": 0,
            "_last_t": time.monotonic(),
        }
        self._download_proc = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self.app.status = "Error: 'ffmpeg' executable not found."
            return
        except Exception as e:
            log.warning("Download error: %s", e)
            self.app.status = f"Download failed: {e}"
            return

        self._download_proc = proc
        reader = threading.Thread(
            target=self._ffmpeg_reader_thread, args=(proc,), daemon=True
        )
        reader.start()

        self.app.stdscr.nodelay(True)
        try:
            while not self._download_state["done"]:
                key = self.app.stdscr.getch()
                if key in (27, ord("q"), ord("Q")):
                    self._download_state["cancelled"] = True
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    self.app.status = "Cancelling download..."
                    break
                self.draw_download_progress(duration)
                self.app.stdscr.refresh()
                curses.napms(100)
        finally:
            self.app.stdscr.nodelay(False)

        reader.join(timeout=5)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            proc.wait()

        self.draw_download_progress(duration)
        self.app.stdscr.refresh()

        if self._download_state["cancelled"]:
            self.app.status = "Download cancelled."
        elif self._download_state["error"]:
            self.app.status = f"Download failed: {self._download_state['error']}"
        elif proc.returncode == 0:
            self.app.add_history(self.app.detail)
            self.app.status = f"Saved: {os.path.basename(out_path)}"
        else:
            self.app.status = f"ffmpeg ended (code {proc.returncode})."
        self._download_proc = None

    def _probe_duration(self, url: str, headers: Dict[str, str]) -> Optional[float]:
        """Best-effort duration in seconds via ffprobe, or None."""
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
        ]
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            cmd += ["-user_agent", ua]
        referer = headers.get("Referer") or headers.get("referer")
        if referer:
            cmd += ["-referer", referer]
        extra = [
            f"{k}: {v}" for k, v in headers.items()
            if k.lower() not in ("user-agent", "referer")
        ]
        if extra:
            cmd += ["-headers", "\r\n".join(extra) + "\r\n"]
        cmd += [url]
        try:
            out = subprocess.check_output(
                cmd, stderr=subprocess.DEVNULL, text=True, timeout=25
            )
            return float(out.strip())
        except Exception:
            return None

    def _ffmpeg_reader_thread(self, proc) -> None:
        """Read ffmpeg stderr in a background thread and parse progress."""
        try:
            for line in proc.stderr:
                self._parse_ffmpeg_progress_line(line)
        except Exception:
            pass
        finally:
            self._download_state["done"] = True

    def _parse_ffmpeg_progress_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        st = self._download_state
        m = re.search(r"frame=\s*(\d+)", line)
        if m:
            st["frame"] = int(m.group(1))
        m = re.search(r"time=([\d:.]+)", line)
        if m:
            st["time"] = m.group(1)
        m = re.search(r"speed=\s*([\d.]+x?)", line)
        if m:
            st["speed"] = m.group(1)
        m = re.search(r"size=\s*([\d.]+)\s*([KMGT]i?B?)", line)
        if m:
            val = float(m.group(1))
            unit = m.group(2).upper()
            mult = {
                "": 1, "K": 1024, "KiB": 1024,
                "M": 1024 ** 2, "MiB": 1024 ** 2,
                "G": 1024 ** 3, "GiB": 1024 ** 3,
            }.get(unit, 1)
            size_bytes = val * mult
            now = time.monotonic()
            last_size = st.get("_last_size", 0)
            last_t = st.get("_last_t", now)
            if now > last_t:
                st["rate_bps"] = max(0.0, (size_bytes - last_size) / (now - last_t))
            st["_last_size"] = size_bytes
            st["_last_t"] = now
        if "progress=end" in line:
            st["progress"] = "end"
            st["done"] = True
        elif "progress=continue" in line:
            st["progress"] = "continue"

    def draw_download_progress(self, duration: Optional[float] = None) -> None:
        """Draw the in-progress download screen (curses, no raw ffmpeg output)."""
        import curses
        stdscr = self.app.stdscr
        h, w = stdscr.getmaxyx()
        st = self._download_state
        title = (self.app.detail.get("title") or self.app.detail.get("name") or "media").strip()
        provider = st.get("provider", "")

        stdscr.erase()
        safe_addstr(stdscr, 1, 2, "Download".ljust(w - 4), curses.A_BOLD | curses.color_pair(1))
        safe_addstr(stdscr, 2, 2, f"File: {title[: max(0, w - 8)]}")
        safe_addstr(
            stdscr, 3, 2, f"Provider: {provider}   Output: downloads/"
        )
        row = 5
        rate_bps = st.get("rate_bps") or 0.0
        info = f"frame={st.get('frame', 0)}  time={st.get('time', '00:00:00.00')}  speed={st.get('speed', '')}  rate={rate_bps/1e6:.2f} MB/s"
        safe_addstr(stdscr, row, 2, info[: max(0, w - 4)])
        row += 1

        pct = 0.0
        if duration and st.get("time"):
            try:
                parts = st["time"].split(":")
                secs = (
                    float(parts[-1])
                    + 60 * (float(parts[-2]) if len(parts) > 1 else 0)
                    + 3600 * (float(parts[-3]) if len(parts) > 2 else 0)
                )
                pct = max(0.0, min(100.0, secs / duration * 100.0))
            except Exception:
                pct = 0.0
        bar_w = max(10, w - 20)
        filled = int(bar_w * pct / 100.0)
        bar = "#" * filled + "-" * (bar_w - filled)
        safe_addstr(stdscr, row, 2, f"[{bar}] {pct:5.1f}%")
        row += 2
        hint = "Esc/q to cancel" if not st.get("done") else "Done."
        safe_addstr(stdscr, row, 2, hint, curses.color_pair(4) | curses.A_BOLD)
        stdscr.refresh()

    def _ffmpeg_cmd(self, url: str, headers: Dict[str, str], out_path: str) -> List[str]:
        """Build the ffmpeg argv for a stream-copy download, passing headers through."""
        cmd = ["ffmpeg", "-y", "-nostdin", "-stats"]
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            cmd += ["-user_agent", ua]
        referer = headers.get("Referer") or headers.get("referer")
        if referer:
            cmd += ["-referer", referer]
        extra = [
            f"{k}: {v}" for k, v in headers.items()
            if k.lower() not in ("user-agent", "referer")
        ]
        if extra:
            cmd += ["-headers", "\r\n".join(extra) + "\r\n"]
        cmd += ["-i", url, "-c", "copy", out_path]
        return cmd

    def _download_out_path(
        self,
        title: str,
        provider: str,
        url: str,
        season_number=None,
        episode_number=None,
        episode_title=None,
    ) -> str:
        """Create an organized output path under ./downloads."""

        def safe_name(value):
            return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .") or "Unknown"

        safe_title = safe_name(title)
        root_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "downloads"
        )
        ext = os.path.splitext(urlparse(url).path)[1] or ".mp4"

        if season_number is not None and episode_number is not None:
            safe_episode_title = safe_name(
                episode_title or f"Episode {episode_number}"
            )
            out_dir = os.path.join(
                root_dir,
                safe_title,
                f"Season [{season_number}]",
            )
            filename = f"Episode [{episode_number}] - {safe_episode_title}{ext}"
        else:
            out_dir = root_dir
            filename = f"{safe_title}{ext}"

        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, filename)

        base, ext2 = os.path.splitext(out_path)
        n = 1
        while os.path.exists(out_path):
            out_path = f"{base} ({n}){ext2}"
            n += 1
        return out_path

    def _download_one_episode(self, url: str, headers: Dict[str, str], out_path: str) -> int:
        """Run ffmpeg for one link, updating _download_state and redrawing the season screen."""
        cmd = self._ffmpeg_cmd(url, headers, out_path)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1
            )
        except FileNotFoundError:
            self._download_state["error"] = "'ffmpeg' not found"
            return -1
        except Exception as e:
            self._download_state["error"] = str(e)
            return -1

        self._download_state.update({
            "done": False, "cancelled": False, "error": None,
            "frame": 0, "time": "00:00:00.00", "speed": "", "rate_bps": 0.0,
            "_last_size": 0, "_last_t": time.monotonic(),
        })
        reader = threading.Thread(target=self._ffmpeg_reader_thread, args=(proc,), daemon=True)
        reader.start()

        rc = -1
        try:
            while True:
                if self.sd_cancel:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    self._download_state["cancelled"] = True
                    break
                # nodelay is on: poll for a cancel keypress mid-download
                try:
                    key = self.app.stdscr.getch()
                except curses.error:
                    key = -1
                if key in (27, ord("q"), ord("Q")):
                    self.sd_cancel = True
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    self._download_state["cancelled"] = True
                    break
                rc = proc.poll()
                if rc is not None:
                    break
                self.app.draw_season_download()
                self.app.stdscr.refresh()
                curses.napms(100)
        finally:
            reader.join(timeout=5)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                proc.wait()
            self._download_state["done"] = True
        return proc.returncode

    def download_season(self) -> None:
        """Download every episode of the current season, one at a time."""
        if not self.app.detail or self.app.detail.get("media_type") != "tv":
            self.app.status = "Not a TV show."
            return
        if not self.app.season_data or not self.app.episodes:
            self.app.status = "No season/episodes to download."
            return
        self.sd_episodes = []
        for ep in self.app.episodes:
            self.sd_episodes.append({
                "ep": ep.get("episode_number"),
                "name": ep.get("name") or f"Episode {ep.get('episode_number')}",
                "status": "queued",
                "error": "",
                "provider": "",
            })
        self.sd_index = 0
        self.sd_cancel = False
        self.app.screen = self.app.SCREEN_SEASON_DOWNLOAD
        self.app.status = f"Download season {self.app.season_data.get('season_number', '?')}"
        self._run_season_download()

    def _run_season_download(self) -> None:
        """Drive the sequential per-episode download loop."""
        import curses
        tmdb_id = str(self.app.detail.get("id", ""))
        title = self.app.detail.get("title") or self.app.detail.get("name", "")
        year = (self.app.detail.get("release_date") or self.app.detail.get("first_air_date") or "")[:4]
        season_num = str(self.app.season_data.get("season_number", "1"))
        total = len(self.sd_episodes)

        self._download_state = {
            "done": False, "cancelled": False, "error": None,
            "frame": 0, "time": "00:00:00.00", "speed": "", "rate_bps": 0.0,
            "_last_size": 0, "_last_t": time.monotonic(),
        }

        self.app.stdscr.nodelay(True)
        try:
            while self.sd_index < total and not self.sd_cancel:
                curses.napms(50)
                ep = self.sd_episodes[self.sd_index]
                ep_num = ep["ep"]
                if ep["status"] in ("done", "failed", "cancelled"):
                    self.sd_index += 1
                    continue
                self.app.draw_season_download()
                self.app.stdscr.refresh()
                key = self.app.stdscr.getch()
                if key in (27, ord("q"), ord("Q")):
                    self.sd_cancel = True
                    break
                # Resolve links for this episode.
                ep["status"] = "resolving"
                self.app.draw_season_download()
                self.app.stdscr.refresh()
                links = []
                try:
                    links = self.gather_download_links(
                        tmdb_id, title, "tv", season_num, str(ep_num), year=year
                    )
                except Exception as e:
                    log.warning("season resolve failed E%s: %s", ep_num, e)
                    ep["status"] = "failed"
                    ep["error"] = str(e)[:50]
                    self.sd_index += 1
                    continue
                if not links:
                    ep["status"] = "failed"
                    ep["error"] = "no links resolved"
                    self.sd_index += 1
                    continue
                url, headers, provider = links[0]
                ep["provider"] = provider
                ep["status"] = "downloading"
                self._download_state["provider"] = provider
                out_path = self._download_out_path(
                    title=title,
                    provider=provider,
                    url=url,
                    season_number=season_num,
                    episode_number=ep_num,
                    episode_title=ep.get("name"),
                )
                rc = self._download_one_episode(url, headers, out_path)
                if self.sd_cancel:
                    ep["status"] = "cancelled"
                    break
                if rc == 0:
                    ep["status"] = "done"
                else:
                    ep["status"] = "failed"
                    ep["error"] = f"ffmpeg code {rc}"
                self.sd_index += 1
                curses.napms(100)
        finally:
            self.app.stdscr.nodelay(False)

        self.app.draw_season_download()
        self.app.stdscr.refresh()
        done = sum(1 for e in self.sd_episodes if e["status"] == "done")
        if self.sd_cancel:
            self.app.status = "Season download cancelled."
        elif done == total:
            self.app.add_history(self.app.detail)
            self.app.status = f"Season download complete ({done}/{total})."
        else:
            self.app.status = f"Season download finished ({done}/{total} done)."
        self.app.screen = self.app.SCREEN_EPISODES


def safe_addstr(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w:
        try:
            win.addstr(y, x, text[: max(0, w - x - 1)], attr)
        except curses.error:
            pass