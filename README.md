# 🎬 Stub TUI - Terminal Streaming Interface

> A beginner-friendly terminal user interface for streaming movies and TV shows using the TMDB Embed API.

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=flat" />
  <img src="https://img.shields.io/badge/Node.js-18%2B-brightgreen?style=flat" />
  <img src="https://img.shields.io/badge/Status-Active-success?style=flat" />
  <img src="https://img.shields.io/badge/License-MIT-blue?style=flat" />
</p>

---

## 📸 Preview

```
┌─────────────────────────────────────────────────────────────────┐
│  🎬 Stub TUI - Movie & TV Show Browser                          │
├─────────────────────────────────────────────────────────────────┤
│  [Search]  [Movies]  [TV Shows]  [History]  [Settings]  [Exit]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐           │
│   │ 🎬      │  │ 🎬      │  │ 🎬      │  │ 🎬      │           │
│   │ Incept  │  │ The     │  │ Interst │  │ The     │           │
│   │ ion     │  │ Dark    │  │ ellar   │  │ Matrix  │           │
│   │ 8.8/10  │  │ Knight  │  │ 8.6/10  │  │ 8.7/10  │           │
│   └─────────┘  └─────────┘  └─────────┘  └─────────┘           │
│                                                                 │
│   Use ↑↓←→ to navigate, Enter to select, q to quit             │
└─────────────────────────────────────────────────────────────────┘
```

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **🎬 Browse Movies & TV Shows** | Search and explore content from TMDB |
| **🔍 Smart Search** | Find anything by title, year, or genre |
| **📺 Episode Selection** | Browse seasons and episodes for TV shows |
| **▶️ Stream Playback** | Play via MPV with quality selection |
| **📜 Watch History** | Track what you've watched |
| **⚙️ Settings** | Configure API keys, quality, providers |
| **🎨 Beautiful TUI** | Clean, keyboard-driven interface |
| **🔌 Multiple Providers** | 13+ streaming sources aggregated |

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+** - For the TUI frontend
- **Node.js 18+** - For the backend API server
- **MPV Player** - For video playback
- **TMDB API Key** - Get one free at [themoviedb.org](https://www.themoviedb.org/settings/api)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/krouu-x/stub-tui.git
cd stub-tui

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install Node.js dependencies (for backend API)
cd server && npm install && cd ..

# 4. Set up your TMDB API key
cp server/.env.example server/.env
# Edit server/.env and add your TMDB_API_KEY
```

### Running the Application

You need **two terminals** - one for the backend API, one for the TUI:

**Terminal 1 - Start the Backend API:**
```bash
cd server
npm start
# API runs on http://localhost:8787
```

**Terminal 2 - Start the TUI:**
```bash
python main.py
```

---

## ⌨️ Keyboard Controls

| Key | Action |
|-----|--------|
| `↑` `↓` `←` `→` | Navigate menus and lists |
| `Enter` | Select item / Play |
| `Esc` / `q` | Go back / Quit |
| `/` | Search |
| `h` | View history |
| `s` | Open settings |
| `r` | Refresh |
| `Space` | Play/Pause (during playback) |
| `f` | Fullscreen toggle |
| `[` `]` | Seek backward/forward |
| `9` `0` | Volume down/up |

---

## 🔧 Configuration

### Backend API Settings (server/.env)

```env
# Required - Get from themoviedb.org/settings/api
TMDB_API_KEY=your_tmdb_api_key_here

# Optional - Add multiple keys for rotation (comma-separated)
# TMDB_API_KEYS=["key1","key2","key3"]

# Server settings
API_PORT=8787
BIND_HOST=0.0.0.0

# Default providers to use (comma-separated)
DEFAULT_PROVIDERS=4khdhub,vixsrc,videasy

# Quality settings
MIN_QUALITIES=720p

# Enable/disable providers
ENABLE_4KHDHUB_PROVIDER=true
ENABLE_VIXSRC_PROVIDER=true
ENABLE_VIDEASY_PROVIDER=true
ENABLE_VIDLINK_PROVIDER=true
# ... more in server/.env.example
```

### TUI Settings (settings.json - auto-generated)

```json
{
  "api_url": "http://localhost:8787",
  "default_quality": "1080p",
  "auto_play_next_episode": true,
  "show_history": true,
  "mpv_args": ["--hwdec=auto"]
}
```

---

## 📦 Project Structure

```
stub-tui/
├── main.py                 # Entry point
├── config.py               # Configuration management
├── settings.py             # User settings
├── history.py              # Watch history
├── download.py             # Download manager
├── tmdb.py                 # TMDB API client
├── metadata.py             # Metadata fetching
├── mpv_ipc.py              # MPV player control
├── js_server_manager.py    # Backend server manager
├── providers/              # Python streaming providers
│   ├── __init__.py
│   ├── stream_utils.py
│   ├── dahmermovies.py
│   ├── dramachi.py
│   ├── fourkhdhub.py
│   ├── internet_archive.py
│   ├── js_server.py
│   ├── peestream.py
│   ├── streamflix.py
│   ├── vaplayer.py
│   ├── vidlink.py
│   ├── vixsrc.py
│   └── zxcstreams.py
├── ui/                     # Terminal UI components
│   ├── __init__.py
│   ├── app.py              # Main app loop
│   ├── drawing.py          # Rendering
│   └── key_handlers.py     # Input handling
├── server/                 # Node.js Backend API
│   ├── apiServer.js        # Main server
│   ├── providers/          # JavaScript streaming providers
│   ├── utils/              # Utilities (auth, config, TMDB)
│   ├── proxy/              # Stream proxy
│   ├── public/             # Admin UI
│   └── package.json
└── requirements.txt        # Python dependencies
```

---

## 🎯 Supported Providers

The backend aggregates streams from **13+ providers**:

| Provider | Type | Notes |
|----------|------|-------|
| **4KHDHub** | Direct/MP4 | High quality |
| **VixSrc** | HLS/MP4 | Reliable |
| **Videasy** | Multiple servers | 10+ servers |
| **Vidlink** | HLS/MP4 | Good availability |
| **DahmerMovies** | Direct links | Direct file URLs |
| **StreamFlix** | Direct MP4 | Fast |
| **VaPlayer** | HLS | Via IMDb ID |
| **CastleTV** | Encrypted API | AES-128 |
| **HDGharTV** | Title-matched | IMDb verified |
| **NetMirror** | Netflix/NewTV | Direct + fallback |
| **OneTouchTV** | Encrypted API | AES-256 |
| **ZXCStreams** | Multi-server | Dynamic domains |
| **Showbox/FebBox** | JWT required | Needs cookies |

---

## 🐳 Docker Deployment

### Quick Start with Docker Compose

```bash
cd server
docker compose up -d --build
```

### Manual Docker Run

```bash
docker run -d --name stub-tui-api \
  -p 8787:8787 \
  -e TMDB_API_KEY=your_key \
  -v $(pwd)/server/utils/user-config.json:/app/utils/user-config.json \
  -v $(pwd)/server/utils/auth-users.json:/app/utils/auth-users.json \
  inside4ndroid/tmdb-embed-api:latest
```

---

## 🔐 Admin Dashboard

Access the backend admin UI at **http://localhost:8787/**

**Default Login:** `admin` / `change-me`

> ⚠️ **Change the password immediately after first login!**

Features:
- Manage TMDB API keys
- Enable/disable providers
- Configure quality filters
- View server status & metrics
- Functional provider checks

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'Add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

---

## 📝 License

MIT License - see [LICENSE](server/LICENSE.md) for details.

---

## 🙏 Acknowledgements

- [TMDB](https://www.themoviedb.org/) for the amazing movie/TV database
- [MPV](https://mpv.io/) for the best media player
- [Textual](https://textual.textualize.io/) / [Rich](https://rich.readthedocs.io/) for TUI libraries
- All streaming provider communities

---

## 💡 Need Help?

- Check the [Troubleshooting](#-troubleshooting) section below
- Open an [Issue](https://github.com/krouu-x/stub-tui/issues)
- Read the [Backend API Docs](server/README.md) for advanced configuration

---

## 🔧 Troubleshooting

### "Connection refused" / Can't connect to API
```bash
# Make sure the backend is running
cd server && npm start
# Check it's accessible
curl http://localhost:8787/api/health
```

### No streams found
- Add more TMDB API keys in the admin dashboard
- Try different providers (enable/disable in settings)
- Check if the content is available in your region

### MPV won't play / No video
```bash
# Install MPV
# Ubuntu/Debian:
sudo apt install mpv

# macOS:
brew install mpv

# Windows: Download from mpv.io
```

### Python import errors
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Permission denied on scripts
```bash
chmod +x main.py run_tmdb_tui.ps1 run_tmdb_tui.bat
```

---

## 📚 Further Reading

- [Backend API Documentation](server/README.md) - Complete API reference
- [Provider Development Guide](server/README.md#adding-a-new-provider) - Add custom providers
- [Docker Deployment Guide](server/README.md#-docker-usage) - Production deployment

---

<p align="center">
  Made with ❤️ for terminal enthusiasts<br>
  <b>Happy Streaming! 🎬</b>
</p>