"""
JS Server Manager - Handles auto-starting and managing the TMDB-Embed-API Node.js server.
"""
import os
import sys
import time
import logging
import subprocess
import atexit
import threading
from typing import Optional

from stub_main.providers.js_server import (
    JSServerClient,
    mark_server_booting,
)

log = logging.getLogger("tmdb_tui.js_server_manager")

# Global server process reference for cleanup
_server_process: Optional[subprocess.Popen] = None
_server_started_by_us = False

# Set when the UI exits; lets a start-up worker that is still booting avoid
# leaving an orphaned node process behind.
_shutdown_requested = False
_atexit_registered = False

# State of the background start-up, read by the TUI (never blocks).
_state_lock = threading.Lock()
_server_state: dict = {
    "state": "idle",   # idle | starting | ready | failed
    "error": None,
    "started_at": 0.0,
    "ready_at": 0.0,
}


def server_state() -> dict:
    """Snapshot of the background start-up state (safe from any thread)."""
    with _state_lock:
        return dict(_server_state)


def _set_state(state: str, error: Optional[str] = None) -> None:
    with _state_lock:
        _server_state["state"] = state
        _server_state["error"] = error
        if state == "starting":
            _server_state["started_at"] = time.monotonic()
            _server_state["ready_at"] = 0.0
        elif state == "ready":
            _server_state["ready_at"] = time.monotonic()


def _register_cleanup() -> None:
    """Register the shutdown hook exactly once."""
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(stop_server)
        _atexit_registered = True


# Reused health-check client (creating a new session per probe is wasteful)
_health_client: Optional[JSServerClient] = None


def _get_health_client(base_url: str = None) -> JSServerClient:
    global _health_client
    if _health_client is None or (base_url and _health_client.base_url != base_url):
        _health_client = JSServerClient(base_url)
    return _health_client


def is_server_running(base_url: str = None) -> bool:
    """Check if the JS server is already running and healthy."""
    try:
        return _get_health_client(base_url).check_health()
    except Exception:
        return False


def start_server(server_dir: str = None, base_url: str = None, timeout: float = 30.0) -> bool:
    """
    Start the JS server as a subprocess.

    Args:
        server_dir: Path to the server directory (stub_main/server)
        base_url: Server base URL (default: http://localhost:8787)
        timeout: Maximum time to wait for server to become healthy

    Returns:
        True if server started successfully, False otherwise
    """
    global _server_process, _server_started_by_us

    if server_dir is None:
        # Default to stub_main/server relative to this file
        server_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server")

    if base_url is None:
        base_url = os.environ.get("TMDB_JS_SERVER_URL", "http://localhost:8787")

    # First check if already running
    if is_server_running(base_url):
        log.info("JS server already running at %s", base_url)
        return True

    log.info("Starting JS server in %s", server_dir)
    _register_cleanup()

    # Determine the start command
    # Use 'npm start' which runs 'nodemon --watch restart.trigger apiServer.js'
    # But for production/auto-start, we should use plain 'node apiServer.js' to avoid nodemon
    # Check if we're in a development environment
    is_dev = os.environ.get("TMDB_TUI_DEV", "").lower() in ("1", "true", "yes")

    if is_dev:
        cmd = ["npm", "start"]
        log.info("Using development command: npm start")
    else:
        cmd = ["node", "apiServer.js"]
        log.info("Using production command: node apiServer.js")

    try:
        # Start the server process
        _server_process = subprocess.Popen(
            cmd,
            cwd=server_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
        )
        _server_started_by_us = True

        # The TUI may have been quit while node was still launching.
        if _shutdown_requested:
            log.info("Shutdown requested during start-up; stopping JS server")
            stop_server()
            return False

        # Start a thread to log server output
        def log_output(stream, prefix):
            for line in iter(stream.readline, ''):
                if line:
                    log.debug("%s: %s", prefix, line.rstrip())

        if _server_process.stdout:
            threading.Thread(target=log_output, args=(_server_process.stdout, "JS Server stdout"), daemon=True).start()
        if _server_process.stderr:
            threading.Thread(target=log_output, args=(_server_process.stderr, "JS Server stderr"), daemon=True).start()

        # Wait for server to become healthy
        start_time = time.time()
        while time.time() - start_time < timeout:
            if _shutdown_requested:
                log.info("Shutdown requested during start-up; stopping JS server")
                stop_server()
                return False

            if _server_process.poll() is not None:
                # Process exited
                stdout, stderr = _server_process.communicate(timeout=1)
                log.error("JS server process exited unexpectedly. stdout: %s, stderr: %s", stdout, stderr)
                _server_started_by_us = False
                return False

            if is_server_running(base_url):
                log.info("JS server started successfully at %s", base_url)
                return True

            time.sleep(0.5)

        # Timeout
        log.error("JS server did not become healthy within %.1f seconds", timeout)
        stop_server()
        return False

    except FileNotFoundError:
        log.error("Could not start JS server: command not found. Make sure node/npm is installed.")
        return False
    except Exception as e:
        log.error("Failed to start JS server: %s", e)
        return False


def stop_server():
    """Stop the JS server if we started it."""
    global _server_process, _server_started_by_us, _shutdown_requested

    _shutdown_requested = True

    if _server_process and _server_started_by_us:
        log.info("Stopping JS server (PID: %d)", _server_process.pid)
        try:
            _server_process.terminate()
            try:
                _server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _server_process.kill()
                _server_process.wait(timeout=2)
            log.info("JS server stopped")
        except Exception as e:
            log.warning("Error stopping JS server: %s", e)
        finally:
            _server_process = None
            _server_started_by_us = False


def start_server_async(
    server_dir: str = None, base_url: str = None, timeout: float = 30.0
) -> None:
    """Start the JS server in the background and return immediately.

    The TUI is drawn right away (like the old single-file app) while the
    Node.js server boots on a worker thread. Callers can poll
    :func:`server_state` for progress, and stream fetches wait for the
    server briefly instead of failing during the cold start.
    """
    global _shutdown_requested

    with _state_lock:
        if _server_state["state"] == "starting":
            return

    # An explicit start request supersedes a previous shutdown request.
    _shutdown_requested = False
    _register_cleanup()
    _set_state("starting")
    mark_server_booting(True)

    def worker():
        try:
            ok = start_server(server_dir, base_url, timeout)
            _set_state("ready" if ok else "failed",
                       None if ok else "server did not become healthy")
        except Exception as e:  # pragma: no cover - defensive
            log.error("background JS server start failed: %s", e)
            _set_state("failed", str(e))
        finally:
            mark_server_booting(False)

    threading.Thread(target=worker, name="js-server-start", daemon=True).start()


def ensure_server_running(server_dir: str = None, base_url: str = None, timeout: float = 30.0) -> bool:
    """
    Ensure the JS server is running. Start it if necessary.

    This is the main function to call at application startup.

    Returns:
        True if server is running (either already was or we started it), False on failure
    """
    # start_server() performs the "already running?" probe itself.
    return start_server(server_dir, base_url, timeout)


# Allow running as a script for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    print("Testing JS Server Manager...")
    print("Checking if server is running...")
    if is_server_running():
        print("Server is already running!")
    else:
        print("Server not running, starting...")
        if ensure_server_running():
            print("Server started successfully!")
            # Keep running for a bit to test
            import time
            time.sleep(2)
            stop_server()
            print("Server stopped.")
        else:
            print("Failed to start server!")
            sys.exit(1)