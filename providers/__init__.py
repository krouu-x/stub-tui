# Provider package exports
# Primary: JS Server (TMDB-Embed-API-main) - delegates to Node.js backend
# Fallback: Inline Python providers (kept for offline/local use)
from dataclasses import dataclass
from typing import Callable, List, Tuple, Dict, Optional

from .js_server import fetch as fetch_js_server, fetch_streams as fetch_js_server_streams, get_available_providers


@dataclass
class StreamProvider:
    name: str
    key: str
    fetch_func: Callable[..., List[Tuple[str, Dict[str, str]]]]


# Default: Use JS Server as primary provider
# The JS server aggregates all providers (4khdhub, vixsrc, vaplayer, vidlink, streamflix, dahmermovies, zxcstreams, etc.)
PROVIDERS = [
    StreamProvider(name="JS Server", key="1", fetch_func=fetch_js_server),
]

# Fallback providers (uncomment to use inline Python providers instead of JS server)
# PROVIDERS = [
#     StreamProvider(name="4k", key="1", fetch_func=fetch_fourkhdhub),
#     StreamProvider(name="Fastest", key="2", fetch_func=fetch_peestream),
#     StreamProvider(name="Vix", key="3", fetch_func=fetch_vixsrc),
#     StreamProvider(name="Backup", key="4", fetch_func=fetch_dramachi),
#     # StreamProvider(name="Internet Archive", key="5", fetch_func=fetch_internet_archive),
#     StreamProvider(name="VaPlayer", key="6", fetch_func=fetch_vaplayer),
#     StreamProvider(name="VidLink", key="7", fetch_func=fetch_vidlink),
#     StreamProvider(name="StreamFlix", key="8", fetch_func=fetch_streamflix),
#     StreamProvider(name="DahmerMovies", key="9", fetch_func=fetch_dahmermovies),
#     StreamProvider(name="ZXCStreams", key="0", fetch_func=fetch_zxcstreams),
# ]

__all__ = [
    "StreamProvider",
    "PROVIDERS",
    "fetch_js_server",
    "fetch_js_server_streams",
    "get_available_providers",
    "fetch_fourkhdhub",
    "fetch_dramachi",
    "fetch_peestream",
    "fetch_vixsrc",
    "fetch_internet_archive",
    "fetch_vaplayer",
    "fetch_vidlink",
    "fetch_streamflix",
    "fetch_dahmermovies",
    "fetch_zxcstreams",
]

# The inline providers are only used when explicitly enabled above. Importing
# them eagerly pulls in heavy dependencies (BeautifulSoup, difflib, ...) and
# slows application start-up for no benefit, so load them on first access.
_FALLBACK_IMPORTS = {
    "fetch_fourkhdhub": ("fourkhdhub", "fetch"),
    "fetch_dramachi": ("dramachi", "fetch"),
    "fetch_peestream": ("peestream", "fetch"),
    "fetch_vixsrc": ("vixsrc", "fetch"),
    "fetch_internet_archive": ("internet_archive", "fetch"),
    "fetch_vaplayer": ("vaplayer", "fetch"),
    "fetch_vidlink": ("vidlink", "fetch"),
    "fetch_streamflix": ("streamflix", "fetch"),
    "fetch_dahmermovies": ("dahmermovies", "fetch"),
    "fetch_zxcstreams": ("zxcstreams", "fetch"),
}


def __getattr__(name):
    target = _FALLBACK_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    module = __import__(f"{__name__}.{module_name}", fromlist=[attr])
    value = getattr(module, attr)
    globals()[name] = value
    return value
