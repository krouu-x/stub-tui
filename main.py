# !/usr/bin/env python3
"""
TMDB TUI - Main entry point
Interactive terminal UI for browsing TMDB (The Movie Database)
"""
import os
import sys
import logging
import locale
import curses

# Add the parent directory to path so we can import stub_main
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stub_main.ui.app import main
from stub_main.js_server_manager import start_server_async

if __name__ == "__main__":
    # Set up logging
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "tmdb_tui.log")

    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Start the JS server in the background: the TUI appears instantly and
    # stream fetches wait for the server if it is still booting.
    start_server_async()

    locale.setlocale(locale.LC_ALL, "")
    curses.wrapper(main)