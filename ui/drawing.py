# UI drawing functions
import curses
import textwrap
import logging
from typing import Optional

from stub_main.config import (
    MIN_WIDTH, MIN_HEIGHT,
    SCREEN_HOME, SCREEN_RESULTS, SCREEN_DETAIL, SCREEN_EPISODES,
    SCREEN_EPISODE_DETAIL, SCREEN_TRENDING, SCREEN_HISTORY,
    SCREEN_DOWNLOADS, SCREEN_NEW, SCREEN_SEASON_DOWNLOAD,
    MODE_NORMAL, MODE_SEARCH, MODE_YEAR, MODE_ID, MODE_SEASON, MODE_EPISODE,
)
from stub_main.metadata import _url_label, _format_size

log = logging.getLogger("tmdb_tui.ui.drawing")


def safe_addstr(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w:
        try:
            win.addstr(y, x, text[: max(0, w - x - 1)], attr)
        except curses.error:
            pass


def draw_too_small(stdscr, h, w):
    lines = [
        "Terminal too small",
        f"Current: {w}x{h}",
        f"Need at least: {MIN_WIDTH}x{MIN_HEIGHT}",
        "Resize the window or zoom out.",
    ]
    start_row = max(0, (h - len(lines)) // 2)
    for i, line in enumerate(lines):
        row = start_row + i
        if row >= h:
            break
        col = max(0, (w - len(line)) // 2)
        safe_addstr(stdscr, row, col, line, curses.color_pair(4) | curses.A_BOLD)


def draw_home(app, stdscr, list_top, list_height, w):
    """Draw the Home dashboard with Continue Watching."""
    safe_addstr(stdscr, list_top - 1, 0, "Home — Continue Watching".ljust(w), curses.A_UNDERLINE)

    # Sort by last_watched (most recent first)
    entries = sorted(
        app.history,
        key=lambda e: e.get("last_watched") or e.get("played_at") or "",
        reverse=True,
    )

    if not entries:
        safe_addstr(stdscr, list_top, 2, "Welcome to TMDB TUI!", curses.A_BOLD | curses.color_pair(1))
        safe_addstr(stdscr, list_top + 1, 2, "No continue-watching items yet.")
        safe_addstr(stdscr, list_top + 2, 2, "Press / to search, t for trending, h for history, q to quit.")
        return

    if app.home_selected >= len(entries):
        app.home_selected = max(0, len(entries) - 1)
    app.home_offset = app.adjust_scroll(app.home_selected, app.home_offset, list_height)
    visible = entries[app.home_offset: app.home_offset + list_height]

    for row, entry in enumerate(visible):
        i = row + app.home_offset
        if list_top + row >= list_top + list_height:
            break
        title = entry.get("title") or "?"
        mtype = entry.get("media_type", "?")
        tag = "[TV]  " if mtype == "tv" else "[MOVIE]"
        progress = entry.get("progress_percent", 0)
        pos = entry.get("position_seconds", 0)
        dur = entry.get("duration_seconds")
        if dur:
            pos_str = f"{int(pos // 60)}:{int(pos % 60):02d} / {int(dur // 60)}:{int(dur % 60):02d}"
        else:
            pos_str = f"{int(pos // 60)}:{int(pos % 60):02d}"
        if mtype == "tv" and entry.get("season_number") is not None:
            s = entry.get("season_number")
            ep = entry.get("episode_number")
            ep_title = entry.get("episode_title") or f"Episode {ep}"
            line = f"{tag}S{s}E{ep} - {ep_title[:28]:<28}  {progress:4.0f}%  {pos_str}"
        else:
            line = f"{tag}{title[:42]:<42}  {progress:4.0f}%  {pos_str}"
        attr = curses.color_pair(5) if i == app.home_selected else 0
        if progress >= 100:
            attr = attr | curses.A_DIM
        safe_addstr(stdscr, list_top + row, 2, line[: max(0, w - 4)], attr)

    if len(entries) > list_height:
        safe_addstr(stdscr, list_top - 1, w - 14, f"[{app.home_selected + 1}/{len(entries)}]")


def draw_results(app, stdscr, list_top, list_height, w):
    safe_addstr(stdscr, list_top - 1, 0, "Results:".ljust(w), curses.A_UNDERLINE)
    if not app.results:
        safe_addstr(stdscr, list_top, 2, "(no results yet)")
        return
    app.results_offset = app.adjust_scroll(app.selected, app.results_offset, list_height)
    visible = app.results[app.results_offset: app.results_offset + list_height]
    for row, item in enumerate(visible):
        i = row + app.results_offset
        name = item.get("title") or item.get("name") or "?"
        date = item.get("release_date") or item.get("first_air_date") or "----"
        rating = item.get("vote_average") or 0
        mtype = item.get("media_type", "?")
        tag = "[TV]  " if mtype == "tv" else "[MOVIE]"
        line = f"{name[:42]:<42} {date[:4]:>6}  ★{rating:.1f}  {tag} id={item.get('id')}"
        attr = curses.color_pair(5) if i == app.selected else 0
        safe_addstr(stdscr, list_top + row, 2, line, attr)
    if len(app.results) > list_height:
        safe_addstr(stdscr, list_top - 1, w - 14, f"[{app.selected + 1}/{len(app.results)}]")


def draw_detail(app, stdscr, top, bottom, list_height, w):
    d = app.detail
    name = d.get("title") or d.get("name") or "?"
    date = d.get("release_date") or d.get("first_air_date") or "?"
    rating = d.get("vote_average") or 0
    genres = ", ".join(g["name"] for g in d.get("genres", []))
    overview = d.get("overview") or "(no overview)"
    mtype = d.get("media_type", "?")
    tmdb_id = d.get("id", "?")

    row = top
    if row < bottom:
        safe_addstr(
            stdscr, row, 2,
            f"{name}  ({date[:4] if date else '?'})  {'[TV]  ' if mtype == 'tv' else '[MOVIE]'} id={tmdb_id}",
            curses.A_BOLD,
        )
        row += 1
    if row < bottom:
        safe_addstr(stdscr, row, 2, f"Rating: {rating:.1f}   Genres: {genres}")
        row += 2

    if mtype == "tv":
        seasons = app.get_tv_seasons()
        if row < bottom:
            safe_addstr(stdscr, row, 2, f"Seasons ({len(seasons)}):", curses.A_UNDERLINE)
            row += 1
        seasons_top = row
        seasons_height = max(0, bottom - seasons_top)
        if not seasons:
            if seasons_top < bottom:
                safe_addstr(stdscr, seasons_top, 4, "(no season data available)")
            return
        app.season_offset = app.adjust_scroll(app.season_selected, app.season_offset, seasons_height)
        visible = seasons[app.season_offset: app.season_offset + seasons_height]
        for i, season in enumerate(visible):
            idx = i + app.season_offset
            num = season.get("season_number", "?")
            sname = season.get("name") or "?"
            ep_count = season.get("episode_count", 0)
            line = f"S{num:<3} {sname[:35]:<35} {ep_count} episode(s)"
            attr = curses.color_pair(5) if idx == app.season_selected else 0
            if seasons_top + i < bottom:
                safe_addstr(stdscr, seasons_top + i, 4, line, attr)
    else:
        if row < bottom:
            safe_addstr(stdscr, row, 2, "Overview:", curses.A_UNDERLINE)
            row += 1
        for line in textwrap.wrap(overview, max(10, w - 6)):
            if row >= bottom:
                break
            safe_addstr(stdscr, row, 4, line)
            row += 1


def draw_episode_list(app, stdscr, list_top, list_height, w):
    show_name = app.detail.get("name") if app.detail else "?"
    season_num = app.season_data.get("season_number", "?") if app.season_data else "?"
    header = f"{show_name} — Season {season_num}  ({len(app.episodes)} episodes)"
    safe_addstr(stdscr, list_top - 1, 0, header.ljust(w), curses.A_UNDERLINE)
    if not app.episodes:
        safe_addstr(stdscr, list_top, 2, "(no episodes found for this season)")
        return
    app.episode_offset = app.adjust_scroll(app.episode_selected, app.episode_offset, list_height)
    visible = app.episodes[app.episode_offset: app.episode_offset + list_height]
    for row, ep in enumerate(visible):
        i = row + app.episode_offset
        num = ep.get("episode_number", "?")
        name = ep.get("name") or "?"
        date = ep.get("air_date") or "----------"
        rating = ep.get("vote_average") or 0
        line = f"E{num:<3} {name[:42]:<42} {date}  ★{rating:.1f}"
        attr = curses.color_pair(5) if i == app.episode_selected else 0
        safe_addstr(stdscr, list_top + row, 2, line, attr)


def draw_episode_detail(app, stdscr, top, bottom, w):
    ep = app.episode_detail
    if ep is None:
        safe_addstr(stdscr, top, 2, "(no episode loaded)")
        return
    show_name = app.detail.get("name") if app.detail else "?"
    season_num = ep.get("season_number", "?")
    num = ep.get("episode_number", "?")
    name = ep.get("name") or "?"
    date = ep.get("air_date") or "?"
    rating = ep.get("vote_average") or 0
    overview = ep.get("overview") or "(no overview)"
    row = top
    if row < bottom:
        safe_addstr(stdscr, row, 2, f"{show_name} — S{season_num}E{num}: {name}", curses.A_BOLD)
        row += 1
    if row < bottom:
        safe_addstr(stdscr, row, 2, f"Air date: {date}   Rating: {rating:.1f}")
        row += 2
    if row < bottom:
        safe_addstr(stdscr, row, 2, "Overview:", curses.A_UNDERLINE)
        row += 1
    for line in textwrap.wrap(overview, max(10, w - 6)):
        if row >= bottom:
            break
        safe_addstr(stdscr, row, 4, line)
        row += 1


def draw_trending(app, stdscr, list_top, list_height, w):
    safe_addstr(stdscr, list_top - 1, 0, "Trending (this week):".ljust(w), curses.A_UNDERLINE)
    if not app.trending:
        safe_addstr(stdscr, list_top, 2, "(no trending items)")
        return
    app.trending_offset = app.adjust_scroll(
        app.trending_selected, app.trending_offset, list_height
    )
    visible = app.trending[app.trending_offset: app.trending_offset + list_height]
    for row, item in enumerate(visible):
        i = row + app.trending_offset
        name = item.get("title") or item.get("name") or "?"
        date = item.get("release_date") or item.get("first_air_date") or "----"
        rating = item.get("vote_average") or 0
        mtype = item.get("media_type", "?")
        tag = "[TV]  " if mtype == "tv" else "[MOVIE]"
        line = f"{name[:42]:<42} {date[:4]:>6}  ★{rating:.1f}  {tag} id={item.get('id')}"
        attr = curses.color_pair(5) if i == app.trending_selected else 0
        safe_addstr(stdscr, list_top + row, 2, line, attr)
    if len(app.trending) > list_height:
        safe_addstr(
            stdscr, list_top - 1, w - 14, f"[{app.trending_selected + 1}/{len(app.trending)}]"
        )


def draw_history(app, stdscr, list_top, list_height, w):
    safe_addstr(stdscr, list_top - 1, 0, "Playback history:".ljust(w), curses.A_UNDERLINE)
    if not app.history:
        safe_addstr(stdscr, list_top, 2, "(no history yet)")
        return
    app.history_offset = app.adjust_scroll(
        app.history_selected, app.history_offset, list_height
    )
    visible = app.history[app.history_offset: app.history_offset + list_height]
    for row, entry in enumerate(visible):
        i = row + app.history_offset
        title = entry.get("title") or "?"
        year = entry.get("year") or "----"
        mtype = entry.get("media_type", "?")
        tag = "[TV]  " if mtype == "tv" else "[MOVIE]"
        played = entry.get("played_at", "")
        line = f"{title[:40]:<40} {year:>4}  {tag}played {played}"
        attr = curses.color_pair(5) if i == app.history_selected else 0
        safe_addstr(stdscr, list_top + row, 2, line, attr)
    if len(app.history) > list_height:
        safe_addstr(stdscr, list_top - 1, w - 14, f"[{app.history_selected + 1}/{len(app.history)}]")


def draw_downloads(app, stdscr, list_top, list_height, w):
    source = app.detail.get("title") or app.detail.get("name") or "?" if app.detail else "?"
    loading = getattr(app, "links_partial", False)
    header = f"Stream links — {source}  ({len(app.download_links)} unique)"
    if loading:
        header += "  — still loading more…"
    safe_addstr(stdscr, list_top - 1, 0, header.ljust(w), curses.A_UNDERLINE)
    if not app.download_links:
        message = "(resolving links…)" if loading else "(no links resolved)"
        safe_addstr(stdscr, list_top, 2, message)
        return
    app.download_offset = app.adjust_scroll(
        app.download_selected, app.download_offset, list_height
    )
    visible = app.download_links[app.download_offset: app.download_offset + list_height]
    for row, (url, headers, provider) in enumerate(visible):
        i = row + app.download_offset
        # Use underlying provider from headers if available (for JS Server streams)
        actual_provider = headers.get("X-Provider", provider)
        quality = headers.get("X-Quality", "")
        label = _url_label(url)
        if quality:
            label = f"{quality}  {label}"
        size = app._link_sizes.get(url)
        if size is not None:
            label = f"{label}  [{_format_size(size)}]"
        max_len = max(10, w - 4 - len(actual_provider) - 4)
        short = label if len(label) <= max_len else label[: max_len - 1] + "…"
        line = f"[{actual_provider}] {short}"
        attr = curses.color_pair(5) if i == app.download_selected else 0
        safe_addstr(stdscr, list_top + row, 2, line, attr)
    if len(app.download_links) > list_height:
        safe_addstr(
            stdscr, list_top - 1, w - 14, f"[{app.download_selected + 1}/{len(app.download_links)}]"
        )


def draw_new_screen(app, stdscr, list_top, list_height, w):
    safe_addstr(stdscr, list_top - 1, 0, "New Screen — opened with 'd'".ljust(w), curses.A_UNDERLINE)
    safe_addstr(stdscr, list_top, 2, "This is a new curses screen drawn when you press 'd'.")
    safe_addstr(stdscr, list_top + 1, 2, "Press Esc to go back to results.")


def draw_providers(app, stdscr, list_top, list_height, w):
    """Draw the provider management screen."""
    safe_addstr(stdscr, list_top - 1, 0, "Provider Management  (j/k move, Space toggle, r refresh, Esc back)".ljust(w), curses.A_UNDERLINE)
    if not getattr(app, "provider_list", None):
        safe_addstr(stdscr, list_top, 2, "(no provider list loaded)")
        return
    providers = app.provider_list
    app.provider_offset = app.adjust_scroll(
        app.provider_selected, app.provider_offset, list_height
    )
    visible = providers[app.provider_offset: app.provider_offset + list_height]
    for row, provider in enumerate(visible):
        i = row + app.provider_offset
        name = provider.get("name", "?")
        enabled = provider.get("enabled", True)
        status = "[ON]" if enabled else "[OFF]"
        line = f"{status}  {name}"
        attr = curses.color_pair(5) if i == app.provider_selected else 0
        safe_addstr(stdscr, list_top + row, 2, line, attr)
    if len(providers) > list_height:
        safe_addstr(
            stdscr, list_top - 1, w - 14, f"[{app.provider_selected + 1}/{len(providers)}]"
        )


def draw_season_download(app, stdscr, list_top, list_height, w):
    """Draw per-episode download status for a whole season."""
    h, w = stdscr.getmaxyx()
    show_name = app.detail.get("name") or "?"
    season_num = app.season_data.get("season_number", "?") if app.season_data else "?"

    stdscr.erase()
    safe_addstr(
        stdscr, 0, 0,
        f" Download Season — {show_name} S{season_num} ".ljust(w),
        curses.A_BOLD | curses.color_pair(1) | curses.A_REVERSE,
    )
    done = sum(1 for e in app.sd_episodes if e["status"] == "done")
    total = len(app.sd_episodes)
    opct = (done / total * 100) if total else 0.0
    obar_w = max(10, w - 20)
    ofilled = int(obar_w * opct / 100.0)
    obar = "#" * ofilled + "-" * (obar_w - ofilled)
    safe_addstr(stdscr, 2, 2, f"Overall: [{obar}] {opct:5.1f}%  ({done}/{total})   q/Esc to cancel")
    safe_addstr(stdscr, 3, 2, "E#  Title                                                Status")
    safe_addstr(stdscr, 4, 2, "-" * min(w - 4, 100))
    row = 5
    cur_idx = app.sd_index if 0 <= app.sd_index < total else -1
    for ep in app.sd_episodes:
        if row >= h - 2:
            break
        ep_num = ep.get("ep")
        name = ep.get("name", "?")
        status = ep.get("status", "queued")
        status_lbl = {
            "queued": "queued",
            "resolving": "resolving...",
            "downloading": "downloading",
            "done": "DONE",
            "failed": f"FAILED ({ep.get('error','')})",
            "cancelled": "CANCELLED",
        }.get(status, status)
        line = f"E{ep_num:<3} {name[:44]:<44} {status_lbl}"
        attr = curses.color_pair(5) if cur_idx >= 0 and ep is app.sd_episodes[cur_idx] else 0
        if status == "downloading":
            attr = curses.color_pair(2) | curses.A_BOLD
            st = app._download_state
            rate_bps = st.get("rate_bps") or 0.0
            line += f"  frame={st.get('frame', 0)} time={st.get('time', '00:00:00.00')} rate={rate_bps/1e6:.2f} MB/s"
        elif status == "done":
            attr = curses.color_pair(3)
        elif status in ("failed", "cancelled"):
            attr = curses.color_pair(4)
        safe_addstr(stdscr, row, 2, line[: max(0, w - 4)], attr)
        row += 1
    safe_addstr(stdscr, h - 1, 0, "q/Esc: cancel   Enter: back".ljust(w), curses.color_pair(2) | curses.A_DIM)
    stdscr.refresh()


def draw_header_bar(app, stdscr, h, w):
    header = f" TMDB TUI — search: movies + tv   |   id-lookup type: {app.id_lookup_type.upper()} (Tab) "
    safe_addstr(stdscr, 0, 0, header.ljust(w), curses.color_pair(1) | curses.A_BOLD | curses.A_REVERSE)
    bar = f" title: {app.title_query or '-'}    year: {app.year_query or '-'}    id: {app.id_query or '-'} "
    safe_addstr(stdscr, 1, 0, bar.ljust(w), curses.color_pair(2))


def draw_mode_prompt(app, stdscr, h, w):
    if app.mode != MODE_NORMAL:
        prompt = {
            MODE_SEARCH: "Search title> ",
            MODE_YEAR: "Year> ",
            MODE_ID: "TMDB ID> ",
            MODE_SEASON: "Season number (0 = Specials)> ",
            MODE_EPISODE: "Episode number> ",
        }[app.mode]
        safe_addstr(stdscr, h - 2, 0, (prompt + app.input_buffer).ljust(w), curses.A_BOLD)
    else:
        safe_addstr(stdscr, h - 2, 0, app.help_text().ljust(w), curses.color_pair(2) | curses.A_DIM)


def draw_status_line(app, stdscr, h, w):
    status_line = app.error if app.error else app.status
    color = curses.color_pair(4) if app.error else curses.color_pair(3)
    safe_addstr(stdscr, h - 1, 0, status_line.ljust(w), color)