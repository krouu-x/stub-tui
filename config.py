# Configuration constants for TMDB TUI
import os

# TMDB API
TMDB_API_TOKEN = os.environ.get(
    "TMDB_API_TOKEN",
    "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI1YjFjM2M4ZDg3MTU1MWU2NDg0N2FlNGQ5NTRlMDgwNyIsIm5iZiI6MTc4NjU2MTM5Ni43OTMsInN1YiI6IjZhN2NjMzc0ZmI3YTFjMjkwMTQ0ZmRlYSIsInNjb3BlcyI6WyJhcGlfcmVhZCJdLCJ2ZXJzaW9uIjoxfQ.heOiSMRVHPZiCerBZK-91bqtqclEDjCbnzbYpFQo56w",
)
TMDB_BASE_URL = os.environ.get("TMDB_BASE_URL", "https://api.themoviedb.org/3")

HEADERS = {
    "Authorization": f"Bearer {TMDB_API_TOKEN}",
    "Accept": "application/json",
}

# Modes
MODE_NORMAL = "normal"
MODE_SEARCH = "search"
MODE_YEAR = "year"
MODE_ID = "id"
MODE_SEASON = "season"
MODE_EPISODE = "episode"

# Screens
SCREEN_RESULTS = "results"
SCREEN_DETAIL = "detail"
SCREEN_EPISODES = "episodes"
SCREEN_EPISODE_DETAIL = "episode_detail"
SCREEN_TRENDING = "trending"
SCREEN_HISTORY = "history"
SCREEN_DOWNLOADS = "downloads"
SCREEN_PROVIDERS = "providers"
SCREEN_NEW = "new"
SCREEN_SEASON_DOWNLOAD = "season_download"
SCREEN_HOME = "home"

# UI limits
MIN_WIDTH = 50
MIN_HEIGHT = 10

# History
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "history.json")
HISTORY_MAX = 200

# Default media player backend used for stream playback ("mpv" or "vlc")
DEFAULT_PLAYER = os.environ.get("TMDB_TUI_PLAYER", "mpv").strip().lower() or "mpv"
VALID_PLAYERS = ("mpv", "vlc")

# User settings (persisted preferences such as the default playback player)
SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "logs", "settings.json"
)

# Browser headers for scraping
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Provider tuning
PAGE_TIMEOUT = 12
MIRROR_WORKERS = 4
MAX_MIRRORS_PER_RELEASE = 6
SEARCH_TTL = 600
SEARCH_CACHE_MAX = 64

# Shared locks and caches
import threading
_SEARCH_CACHE_LOCK = threading.Lock()