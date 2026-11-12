# mpv IPC helpers (playback position tracking)
import os
import sys
import json
import time
import socket
import logging
import tempfile
import threading
import uuid
from typing import Optional

log = logging.getLogger("tmdb_tui.mpv_ipc")

_mpv_ipc_seq = 0

# How long to wait for a single mpv reply before giving up on that query.
_REPLY_TIMEOUT = 5.0


def make_ipc_path() -> str:
    """Return a unique endpoint for mpv's --input-ipc-server.

    Windows mpv exposes IPC as a *named pipe*, so a regular temp-file path
    never connects (which used to leave playback progress stuck at 0.00%).
    """
    name = f"tmdb_tui_mpv_{uuid.uuid4().hex}"
    if sys.platform == "win32":
        return rf"\\.\pipe\{name}"
    return os.path.join(tempfile.gettempdir(), name)


def _normalize_ipc_path(ipc_path: str) -> str:
    r"""Map a legacy temp-file path to the named pipe mpv actually creates.

    mpv on Windows prefixes any non-pipe path with ``\\.\pipe\``, so a path
    produced by older versions of this app still connects.
    """
    if sys.platform != "win32":
        return ipc_path
    if ipc_path.startswith("\\\\.\\pipe\\"):
        return ipc_path
    return rf"\\.\pipe\{ipc_path}"


class MpvIPCConnection:
    """Line-oriented JSON IPC connection to mpv.

    Deliberately single-threaded: on Windows the pipe handle is backed by one
    CRT file descriptor, and a blocking ``read`` on that descriptor also
    blocks ``write`` calls on it from other threads. A background reader
    thread therefore made every command stall until playback ended. Instead
    the descriptor is put in non-blocking mode so one thread can write a
    command and poll for its reply with a timeout.

    mpv interleaves unsolicited *event* frames (``audio-reconfig``,
    ``end-file``, ...) with command replies; they are buffered and skipped.
    """

    def __init__(self, fd: Optional[int] = None, sock: Optional[socket.socket] = None):
        self._fd = fd
        self._sock = sock
        self._buffer = b""
        self._alive = True
        # Set when mpv reports end-file/shutdown over IPC.
        self.eof_seen = False
        if fd is not None:
            try:
                os.set_blocking(fd, False)
            except Exception:
                # Older Pythons may not support this for pipes; fall back to
                # blocking reads (still works, only without query timeouts).
                pass

    # -- low level -------------------------------------------------------
    def _raw_write(self, data: bytes):
        if self._sock is not None:
            self._sock.sendall(data)
            return
        view = memoryview(data)
        deadline = time.monotonic() + 2.0
        while view:
            try:
                written = os.write(self._fd, view)
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise IOError("mpv IPC write timed out")
                time.sleep(0.02)
                continue
            if written <= 0:
                raise IOError("mpv IPC write failed")
            view = view[written:]

    def _raw_read(self, timeout: float) -> bytes:
        if self._sock is not None:
            self._sock.settimeout(max(0.01, timeout))
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                return b""
            except OSError:
                self._alive = False
                return b""
            if not chunk:
                self._alive = False
            return chunk
        deadline = time.monotonic() + timeout
        while True:
            try:
                chunk = os.read(self._fd, 65536)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return b""
                time.sleep(0.02)
                continue
            except OSError:
                self._alive = False
                return b""
            if not chunk:
                # A non-blocking pipe only reports b"" at end-of-file.
                self._alive = False
            return chunk

    # -- public API ------------------------------------------------------
    def write(self, data: bytes):
        try:
            self._raw_write(data)
        except Exception:
            self._alive = False
            raise

    def flush(self):
        # Unbuffered connection: nothing to flush.
        return None

    def _take_line(self) -> Optional[bytes]:
        """Pop one complete line from the buffer, or None if incomplete."""
        if b"\n" not in self._buffer:
            return None
        line, self._buffer = self._buffer.split(b"\n", 1)
        line = line.strip()
        return line or b""

    def readline(self, timeout: Optional[float] = None) -> bytes:
        """Return one complete JSON line, or b'' on timeout / EOF."""
        deadline = time.monotonic() + (timeout if timeout is not None else _REPLY_TIMEOUT)
        while True:
            line = self._take_line()
            if line is not None:
                return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""
            chunk = self._raw_read(min(remaining, 0.5))
            if not chunk:
                if not self._alive:
                    return b""
                continue
            self._buffer += chunk

    def is_alive(self) -> bool:
        return self._alive

    def close(self):
        self._alive = False
        try:
            if self._fd is not None:
                os.close(self._fd)
            elif self._sock is not None:
                self._sock.close()
        except Exception:
            pass
        self._fd = None
        self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _mpv_ipc_open(ipc_path: str, retries: int = 25, delay: float = 0.2):
    """Open a *persistent* connection to mpv's IPC endpoint.

    Unix/macOS: AF_UNIX socket. Windows: mpv exposes IPC through a named
    pipe. Reopening that pipe for every query is unreliable on Windows, so we
    open it once and keep it open for the whole playback, reusing it for every
    property query.

    Returns an ``MpvIPCConnection`` or raises IOError once mpv has had long
    enough to create the endpoint.
    """
    ipc_path = _normalize_ipc_path(ipc_path)
    last_err = None
    for _ in range(max(1, retries)):
        try:
            if sys.platform == "win32":
                fd = os.open(ipc_path, os.O_RDWR | os.O_BINARY)
                return MpvIPCConnection(fd=fd)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(ipc_path)
            return MpvIPCConnection(sock=sock)
        except Exception as e:  # mpv may not have created the endpoint yet
            last_err = e
            time.sleep(delay)
    raise IOError(f"could not connect to mpv IPC at {ipc_path}: {last_err}")


def _mpv_ipc_query(conn, name: str, timeout: float = _REPLY_TIMEOUT) -> Optional[float]:
    """Ask mpv for one property over an already-open IPC connection.

    Returns the property value, or None when it is unavailable. Unsolicited
    event frames mpv may emit are skipped so the caller always gets the
    reply to its own request.
    """
    global _mpv_ipc_seq
    _mpv_ipc_seq += 1
    request_id = _mpv_ipc_seq
    request = {"command": ["get_property", name], "request_id": request_id}
    try:
        conn.write((json.dumps(request) + "\n").encode("utf-8"))
    except Exception:
        return None

    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            line = conn.readline(timeout=remaining)
        except Exception:
            return None
        if not line:
            return None
        try:
            msg = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        # Unsolicited events (audio-reconfig, end-file, ...) carry no
        # request_id; they are never the answer to our query. Remember
        # end-of-file so the poller can release the connection (mpv keeps
        # running as long as an IPC client stays connected).
        if "request_id" not in msg:
            if msg.get("event") in ("end-file", "shutdown"):
                try:
                    conn.eof_seen = True
                except Exception:
                    pass
            continue
        if msg.get("request_id") != request_id:
            continue  # stale reply from an earlier query
        if msg.get("error") not in (None, "success"):
            return None
        return msg.get("data")


def _mpv_progress_poll(
    app_self,
    ipc_path: str,
    stop_event: "threading.Event",
    state: dict,
    item_info: Optional[dict] = None,
):
    """Background thread: poll mpv IPC for time-pos and duration until stop_event.

    Keeps ONE persistent IPC connection for the whole playback (reopening the
    Windows named pipe per query is unreliable) and reconnects if it drops.
    Saves progress to disk every 10 seconds and once more when playback ends.
    """
    conn = None
    connected_logged = False
    last_save = -1e9
    ended = False
    try:
        while not stop_event.is_set():
            if not ended and (conn is None or not conn.is_alive()):
                if conn is not None:
                    conn.close()
                    conn = None
                try:
                    conn = _mpv_ipc_open(ipc_path, retries=5, delay=0.5)
                    if not connected_logged:
                        log.info("tracking playback progress via mpv IPC")
                        connected_logged = True
                except Exception as e:
                    if not connected_logged:
                        log.debug("mpv IPC not ready yet (%s)", e)

            if conn is not None:
                pos = _mpv_ipc_query(conn, "time-pos")
                dur = _mpv_ipc_query(conn, "duration")
                log.debug(
                    "mpv poll: pos=%s dur=%s alive=%s eof=%s",
                    pos, dur, conn.is_alive(), getattr(conn, "eof_seen", False),
                )
                if pos is not None:
                    try:
                        state["position_seconds"] = float(pos)
                    except (TypeError, ValueError):
                        pass
                if dur is not None:
                    try:
                        state["duration_seconds"] = float(dur)
                    except (TypeError, ValueError):
                        pass
                if state.get("duration_seconds"):
                    state["progress_percent"] = round(
                        (state["position_seconds"] / state["duration_seconds"]) * 100.0, 1
                    )
                if getattr(conn, "eof_seen", False):
                    # Playback reached the end. mpv stays alive while an IPC
                    # client is connected, so drop the connection to let it
                    # exit, keeping the position we already recorded.
                    ended = True
                    conn.close()
                    conn = None
                elif not conn.is_alive():
                    conn.close()
                    conn = None

            # Periodic save to disk (every 10 seconds)
            now = time.monotonic()
            if item_info and now - last_save >= 10.0:
                try:
                    app_self._save_playback_progress(item_info, state)
                    last_save = now
                except Exception:
                    pass
            stop_event.wait(2.0)
    finally:
        # Final best-effort save when playback ends.
        if item_info:
            try:
                log.debug(
                    "mpv playback finished at %.2fs / %s",
                    state.get("position_seconds", 0.0),
                    state.get("duration_seconds"),
                )
                app_self._save_playback_progress(item_info, state)
            except Exception:
                pass
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
