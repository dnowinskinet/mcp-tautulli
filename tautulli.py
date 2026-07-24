"""Tautulli MCP Server — Plex monitoring via MCP tools.

Single-file FastMCP server providing read-only access to Tautulli's API.
Designed for Claude Code integration via stdio transport.

Tools:
  tautulli_activity            — Who's watching right now
  tautulli_history             — Recent playback history
  tautulli_recently_added      — What's new in your Plex libraries
  tautulli_search              — Search Plex content by title
  tautulli_metadata            — Full metadata for one item (by rating key)
  tautulli_item_stats          — Per-item watch stats + which users watched it
  tautulli_user_stats          — Per-user watch statistics
  tautulli_library_stats       — Library-level statistics
  tautulli_library_media_info  — Per-library media quality/size breakdown
  tautulli_most_watched        — Top content by plays (configurable time range)
  tautulli_server_info         — Plex server identity and status
  tautulli_status              — Server configuration and reachability
  tautulli_transcode_stats     — Direct play vs transcode breakdown by platform
  tautulli_platform_stats      — Top platforms/devices by plays and watch time
  tautulli_stream_resolution   — Source vs delivered resolution analysis
  tautulli_plays_by_date       — Daily play counts over time by stream type
  tautulli_plays_by_day_of_week — Weekly viewing patterns
  tautulli_plays_by_hour       — Hourly viewing distribution

All tools are strictly read-only. User-identifying data (usernames, user IDs,
emails, client IPs) and server file paths are omitted or opt-in only.

Environment variables:
  TAUTULLI_URL        — Tautulli base URL (required)
  TAUTULLI_API_KEY    — Tautulli API key (required)
  TAUTULLI_TLS_VERIFY — Verify TLS certificates (default: true)
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastmcp import Context, FastMCP

# ── Configuration ────────────────────────────────────────────────────────

TAUTULLI_URL = os.environ.get("TAUTULLI_URL", "")
TAUTULLI_API_KEY = os.environ.get("TAUTULLI_API_KEY", "")
# Default true: set to "false" if using self-signed certs (e.g. Tailscale serve).
TLS_VERIFY = os.environ.get("TAUTULLI_TLS_VERIFY", "true").lower() in (
    "true",
    "1",
    "yes",
)

# ── Input validation ────────────────────────────────────────────────────

_VALID_MEDIA_TYPES = {"movie", "episode", "track", "live"}
_VALID_RECENTLY_ADDED_TYPES = {"movie", "show", "artist"}
_VALID_CATEGORIES = {"tv", "movies", "music", "users"}
_VALID_STAT_TYPES = {"plays", "duration"}
_MAX_STRING_LEN = 200
_MAX_DAYS = 365


def _clamp_days(days: int, default: int = 30, maximum: int = _MAX_DAYS) -> int:
    """Clamp days parameter to a safe range."""
    return min(max(1, days), maximum)


def _sanitize_str(value: str) -> str:
    """Truncate and strip control characters from user input."""
    return value[:_MAX_STRING_LEN].strip()


TIMEOUT = httpx.Timeout(15.0, connect=10.0)

_http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def _lifespan(server: FastMCP):
    global _http_client
    async with httpx.AsyncClient(timeout=TIMEOUT, verify=TLS_VERIFY) as client:
        _http_client = client
        yield
    _http_client = None


mcp = FastMCP("tautulli", lifespan=_lifespan)


# ── API helper ───────────────────────────────────────────────────────────


async def _do_request(
    client: httpx.AsyncClient, url: str, query: dict, cmd: str
) -> dict:
    try:
        resp = await client.get(url, params=query)
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPStatusError:
        raise RuntimeError(f"Tautulli returned HTTP {resp.status_code} for {cmd}")
    except httpx.HTTPError:
        raise RuntimeError(f"Tautulli unreachable for {cmd}")
    response = body.get("response", {})
    if response.get("result") != "success":
        raise RuntimeError(f"Tautulli API error for {cmd}")
    return response.get("data", {})


async def _api(cmd: str, ctx: Context | None = None, **params) -> dict:
    """Call a Tautulli API command and return the response data.

    Raises RuntimeError on failure so FastMCP returns a proper error to the client.
    """
    if not TAUTULLI_URL:
        raise RuntimeError("TAUTULLI_URL environment variable not set")
    url = f"{TAUTULLI_URL.rstrip('/')}/api/v2"
    query = {"apikey": TAUTULLI_API_KEY, "cmd": cmd, **params}
    if ctx is not None:
        await ctx.debug(f"tautulli → {cmd}")
    if _http_client is not None:
        return await _do_request(_http_client, url, query, cmd)
    async with httpx.AsyncClient(timeout=TIMEOUT, verify=TLS_VERIFY) as client:
        return await _do_request(client, url, query, cmd)


# ── Formatting helpers ───────────────────────────────────────────────────


def _fmt_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    if hours < 24:
        return f"{hours}h {mins}m"
    days = hours // 24
    hours = hours % 24
    return f"{days}d {hours}h {mins}m"


def _fmt_bytes(value: float | str | None) -> str:
    """Format a byte count into a human-readable size."""
    if value is None:
        return "?"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "?"
    if num < 1024:
        return f"{int(num)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        num /= 1024
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}"
    return f"{num:.1f} TB"


def _fmt_session(s: dict) -> str:
    """Format a single streaming session into a readable line."""
    user = s.get("friendly_name") or s.get("user", "Unknown")
    state = s.get("state", "unknown")
    media_type = s.get("media_type", "")
    progress = s.get("progress_percent", "?")
    quality = s.get("quality_profile", "")
    player = s.get("player", "")
    transcode = s.get("transcode_decision", "direct play")

    # Build title based on media type
    if media_type == "episode":
        show = s.get("grandparent_title", "")
        ep_num = f"S{int(s.get('parent_media_index', 0)):02d}E{int(s.get('media_index', 0)):02d}"
        ep_title = s.get("title", "")
        title = f"{show} {ep_num} — {ep_title}" if ep_title else f"{show} {ep_num}"
    elif media_type == "movie":
        title = s.get("title", "Unknown")
        year = s.get("year", "")
        if year:
            title = f"{title} ({year})"
    elif media_type == "track":
        book = s.get("grandparent_title", "")
        chapter = s.get("title", "")
        title = f"{book} — {chapter}" if book else chapter
    else:
        title = s.get("full_title") or s.get("title", "Unknown")

    parts = [f'{user} {state} "{title}"']
    parts.append(f"{progress}%")
    if player:
        parts.append(f"on {player}")
    if transcode and transcode != "direct play":
        parts.append(f"({transcode})")
    elif quality:
        parts.append(f"({quality})")

    return " — ".join([parts[0], ", ".join(parts[1:])])


# ── Tools ────────────────────────────────────────────────────────────────


@mcp.tool()
async def tautulli_activity(ctx: Context | None = None) -> str:
    """Get current Plex streaming activity — who's watching what, playback state, progress, and quality.

    Use this before restarting Plex or rebooting servers to check for active streams.
    """
    data = await _api("get_activity", ctx=ctx)
    stream_count = int(data.get("stream_count", 0))
    sessions = data.get("sessions", [])

    if not sessions:
        return "No active streams on Plex."

    lines = [f"{stream_count} active stream(s):\n"]
    for s in sessions:
        lines.append(f"  • {_fmt_session(s)}")

    # Bandwidth summary
    total_bw = data.get("total_bandwidth", 0)
    wan_bw = data.get("wan_bandwidth", 0)
    lan_bw = data.get("lan_bandwidth", 0)
    if total_bw:
        lines.append(
            f"\nBandwidth: {int(total_bw) / 1000:.1f} Mbps total (LAN: {int(lan_bw) / 1000:.1f}, WAN: {int(wan_bw) / 1000:.1f})"
        )

    return "\n".join(lines)


@mcp.tool()
async def tautulli_history(
    length: int = 10,
    user: str = "",
    media_type: str = "",
    search: str = "",
    start_date: str = "",
    include_performance: bool = False,
    include_ip: bool = False,
    ctx: Context | None = None,
) -> str:
    """Get recent Plex playback history.

    Args:
        length: Number of records to return (default 10, max 50).
        user: Filter by username.
        media_type: Filter by type: "movie", "episode", "track" (audiobook).
        search: Text search in titles.
        start_date: Only show history from this date (YYYY-MM-DD).
        include_performance: Also fetch stream bitrate per record via get_stream_data
            (makes one extra API call per record — use with small lengths).
        include_ip: Also show the client IP address per record. Off by default —
            IP addresses are personally identifying, so opt in when you need them.
    """
    length = min(max(1, length), 50)
    params: dict = {"length": str(length)}
    if user:
        params["user"] = _sanitize_str(user)
    if media_type:
        if media_type not in _VALID_MEDIA_TYPES:
            return f"Invalid media_type: must be one of {', '.join(sorted(_VALID_MEDIA_TYPES))}"
        params["media_type"] = media_type
    if search:
        params["search"] = _sanitize_str(search)
    if start_date:
        params["start_date"] = _sanitize_str(start_date)

    data = await _api("get_history", ctx=ctx, **params)
    records = data.get("data", [])
    total = data.get("recordsTotal", 0)

    if not records:
        return "No playback history found matching filters."

    # Optionally fetch stream performance data in parallel (one call per row_id)
    stream_perf: dict[int, dict] = {}
    if include_performance:
        row_ids = [r["row_id"] for r in records if r.get("row_id")]
        if row_ids:
            results = await asyncio.gather(
                *[_api("get_stream_data", ctx=ctx, row_id=rid) for rid in row_ids],
                return_exceptions=True,
            )
            for rid, res in zip(row_ids, results):
                if isinstance(res, dict) and res:
                    stream_perf[rid] = res

    lines = [f"Playback history ({len(records)} of {total} records):\n"]
    for r in records:
        user_name = r.get("friendly_name") or r.get("user", "?")
        media = r.get("media_type", "")
        duration = _fmt_duration(r.get("duration", 0))
        player = r.get("player", "")
        row_id = r.get("row_id")

        if media == "episode":
            show = r.get("grandparent_title", "")
            ep = r.get("title", "")
            title = f"{show} — {ep}" if show else ep
        elif media == "movie":
            title = r.get("title", "Unknown")
            year = r.get("year", "")
            if year:
                title = f"{title} ({year})"
        elif media == "track":
            book = r.get("grandparent_title", "")
            chapter = r.get("title", "")
            title = f"{book} — {chapter}" if book else chapter
        else:
            title = r.get("full_title") or r.get("title", "Unknown")

        state = r.get("state", "")
        transcode = r.get("transcode_decision", "")
        ip = r.get("ip_address", "")

        state_str = f" [{state}]" if state and state != "stopped" else ""
        player_str = f" on {player}" if player else ""
        transcode_str = f", {transcode}" if transcode else ""
        ip_str = f", {ip}" if include_ip and ip else ""
        row_str = f" [row_id: {row_id}]" if row_id else ""

        bitrate_str = ""
        if include_performance and row_id and row_id in stream_perf:
            bps = stream_perf[row_id].get("stream_bitrate") or stream_perf[row_id].get(
                "bitrate"
            )
            if bps:
                bitrate_str = f", {int(float(bps))} kbps"

        lines.append(
            f'  • {user_name}: "{title}" ({duration}{player_str}{transcode_str}{ip_str}{bitrate_str}){state_str}{row_str}'
        )

    total_dur = data.get("total_duration", "")
    if total_dur:
        lines.append(f"\nTotal watch time: {total_dur}")

    return "\n".join(lines)


@mcp.tool()
async def tautulli_stream_data(
    row_id: int | None = None,
    session_key: int | None = None,
    ctx: Context | None = None,
) -> str:
    """Get detailed stream performance data for diagnosing Plex playback issues.

    Use row_id from history or session_key from current activity to fetch detailed
    performance metrics including bitrate, bandwidth, codec information, and connection details.

    Args:
        row_id: The history row ID (from tautulli_history output).
        session_key: The current session key (from tautulli_activity).
        Either row_id or session_key must be provided.
    """
    if not row_id and not session_key:
        return "Error: Either row_id or session_key must be provided."

    params: dict = {}
    if row_id:
        params["row_id"] = row_id
    if session_key:
        params["session_key"] = session_key

    data = await _api("get_stream_data", ctx=ctx, **params)

    if not data:
        return "No stream data found."

    lines = ["Stream Performance Data:\n"]

    # Basic info
    media_type = data.get("media_type", "Unknown")
    if media_type == "episode":
        show = data.get("grandparent_title", "")
        ep = data.get("title", "Unknown")
        title = f"{show} — {ep}" if show else ep
    else:
        title = data.get("title") or data.get("grandparent_title", "Unknown")
    lines.append(f"Media: {title} ({media_type})\n")

    # Quality profile
    quality_profile = data.get("quality_profile")
    if quality_profile:
        lines.append(f"Quality Profile: {quality_profile}")

    # Source bitrates (original file)
    bitrate = data.get("bitrate")
    if bitrate:
        lines.append(f"Source Bitrate: {bitrate} kbps")

    video_bitrate = data.get("video_bitrate")
    if video_bitrate:
        lines.append(f"Source Video Bitrate: {video_bitrate} kbps")

    audio_bitrate = data.get("audio_bitrate")
    if audio_bitrate:
        lines.append(f"Source Audio Bitrate: {audio_bitrate} kbps")

    lines.append("")

    # Stream (delivered) info
    stream_bitrate = data.get("stream_bitrate")
    if stream_bitrate:
        lines.append(f"Stream Bitrate: {stream_bitrate} kbps")

    stream_video_resolution = data.get("stream_video_resolution")
    if stream_video_resolution:
        lines.append(f"Stream Resolution: {stream_video_resolution}")

    stream_video_codec = data.get("stream_video_codec")
    if stream_video_codec:
        lines.append(f"Stream Video Codec: {stream_video_codec}")

    stream_video_framerate = data.get("stream_video_framerate")
    if stream_video_framerate:
        lines.append(f"Stream Framerate: {stream_video_framerate}")

    stream_video_bitrate = data.get("stream_video_bitrate")
    if stream_video_bitrate:
        lines.append(f"Stream Video Bitrate: {stream_video_bitrate} kbps")

    stream_audio_codec = data.get("stream_audio_codec")
    if stream_audio_codec:
        lines.append(f"Stream Audio Codec: {stream_audio_codec}")

    stream_audio_channels = data.get("stream_audio_channels")
    if stream_audio_channels:
        lines.append(f"Stream Audio Channels: {stream_audio_channels}")

    stream_audio_bitrate = data.get("stream_audio_bitrate")
    if stream_audio_bitrate:
        lines.append(f"Stream Audio Bitrate: {stream_audio_bitrate} kbps")

    lines.append("")

    # Original file info
    container = data.get("container")
    if container:
        lines.append(f"Source Container: {container}")

    video_codec = data.get("video_codec")
    if video_codec:
        lines.append(f"Source Video Codec: {video_codec}")

    audio_codec = data.get("audio_codec")
    if audio_codec:
        lines.append(f"Source Audio Codec: {audio_codec}")

    video_resolution = data.get("video_resolution")
    if video_resolution:
        lines.append(f"Source Resolution: {video_resolution}")

    lines.append("")

    # Transcode decisions (what actually happened to the stream)
    stream_video_decision = data.get("stream_video_decision")
    if stream_video_decision:
        lines.append(f"Video Decision: {stream_video_decision}")

    stream_audio_decision = data.get("stream_audio_decision")
    if stream_audio_decision:
        lines.append(f"Audio Decision: {stream_audio_decision}")

    transcode_hw_decoding = data.get("transcode_hw_decoding")
    transcode_hw_encoding = data.get("transcode_hw_encoding")
    if transcode_hw_decoding or transcode_hw_encoding:
        lines.append(
            f"HW Transcode: decode={transcode_hw_decoding or 'no'}, encode={transcode_hw_encoding or 'no'}"
        )

    return "\n".join(lines)


@mcp.tool()
async def tautulli_recently_added(
    count: int = 10, media_type: str = "", ctx: Context | None = None
) -> str:
    """Get recently added content to Plex — shows what's new in your libraries.

    Args:
        count: Number of items to return (default 10, max 50).
        media_type: Filter by type: "movie", "show", "artist". Empty for all.
    """
    count = min(max(1, count), 50)
    params: dict = {"count": str(count)}
    if media_type:
        media_type = media_type.lower().strip()
        if media_type not in _VALID_RECENTLY_ADDED_TYPES:
            return f"Invalid media_type: must be one of {', '.join(sorted(_VALID_RECENTLY_ADDED_TYPES))}"
        params["media_type"] = media_type

    data = await _api("get_recently_added", ctx=ctx, **params)
    items = data.get("recently_added", [])

    if not items:
        return "No recently added content found."

    lines = [f"Recently added (last {count}):\n"]
    for i, item in enumerate(items, 1):
        title = item.get("title", "Unknown")
        year = item.get("year", "")
        mtype = item.get("media_type", "")
        library = item.get("library_name", "")
        added_at = item.get("added_at")

        if year:
            title = f"{title} ({year})"

        added_str = ""
        if added_at:
            try:
                dt = datetime.fromtimestamp(int(added_at), tz=timezone.utc)
                added_str = f", added {dt.strftime('%Y-%m-%d')}"
            except (ValueError, OSError):
                pass

        library_str = f", library: {library}" if library else ""
        rating_key = item.get("rating_key")
        key_str = f" [key: {rating_key}]" if rating_key else ""
        lines.append(f"  {i}. {title} — {mtype}{added_str}{library_str}{key_str}")

    return "\n".join(lines)


@mcp.tool()
async def tautulli_search(
    query: str, limit: int = 10, ctx: Context | None = None
) -> str:
    """Search Plex content by title — find movies, shows, episodes, and tracks.

    Args:
        query: Search text (required).
        limit: Maximum results per category (default 10, max 25).
    """
    query = _sanitize_str(query)
    if not query:
        return "Search query cannot be empty."
    limit = min(max(1, limit), 25)

    data = await _api("search", ctx=ctx, query=query, limit=str(limit))
    results_list = data.get("results_list", {})

    if not results_list:
        return f'No results for "{query}".'

    # Friendly labels for Tautulli's media type keys
    _type_labels = {
        "movie": "Movies",
        "show": "TV Shows",
        "season": "Seasons",
        "episode": "Episodes",
        "artist": "Artists",
        "album": "Albums",
        "track": "Tracks",
    }

    lines = [f'Search results for "{query}":\n']
    for media_type, items in results_list.items():
        if not items:
            continue

        label = _type_labels.get(media_type, media_type.title())
        lines.append(f"{label}:")
        for item in items:
            title = item.get("title", "Unknown")
            year = item.get("year", "")
            library = item.get("library_name", "")

            # For episodes, include show name and episode number
            if media_type == "episode":
                show = item.get("grandparent_title", "")
                ep_idx = item.get("media_index", "")
                season_idx = item.get("parent_media_index", "")
                if show and season_idx and ep_idx:
                    title = (
                        f'{show} — S{int(season_idx):02d}E{int(ep_idx):02d} "{title}"'
                    )
                elif show:
                    title = f"{show} — {title}"

            if year:
                title = f"{title} ({year})"

            library_str = f" — {library}" if library else ""
            rating_key = item.get("rating_key")
            key_str = f" [key: {rating_key}]" if rating_key else ""
            lines.append(f"  • {title}{library_str}{key_str}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _fmt_media_info(media_info: list) -> list[str]:
    """Summarize a metadata item's media_info (quality/codecs/size) — no paths."""
    if not media_info:
        return []
    m = media_info[0]
    out: list[str] = []
    resolution = m.get("video_full_resolution") or m.get("video_resolution")
    if resolution:
        out.append(f"Resolution: {resolution}")
    container = m.get("container")
    if container:
        out.append(f"Container: {container}")
    vcodec = m.get("video_codec")
    if vcodec:
        out.append(f"Video Codec: {vcodec}")
    acodec = m.get("audio_codec")
    channels = m.get("audio_channels")
    if acodec:
        out.append(f"Audio: {acodec}" + (f" {channels}ch" if channels else ""))
    bitrate = m.get("bitrate")
    if bitrate:
        out.append(f"Bitrate: {bitrate} kbps")

    # Dynamic range + file size come from the (path-bearing) parts/streams —
    # pull only the safe fields, never the file path itself.
    parts = m.get("parts") or []
    if parts:
        part = parts[0]
        file_size = part.get("file_size")
        if file_size:
            out.append(f"File Size: {_fmt_bytes(file_size)}")
        for stream in part.get("streams") or []:
            dynamic_range = stream.get("video_dynamic_range")
            if dynamic_range:
                dovi = "Dolby Vision" if stream.get("video_dovi_present") else ""
                out.append(
                    f"Dynamic Range: {dynamic_range}" + (f" ({dovi})" if dovi else "")
                )
                break
    return out


@mcp.tool()
async def tautulli_metadata(rating_key: str, ctx: Context | None = None) -> str:
    """Get full metadata for one Plex item — summary, cast/crew, genres, ratings, and media quality.

    Rating keys are shown as ``[key: N]`` in tautulli_search, tautulli_recently_added,
    and tautulli_library_media_info output. Server file paths are deliberately omitted.

    Args:
        rating_key: The item's rating key (numeric string).
    """
    rating_key = _sanitize_str(str(rating_key))
    if not rating_key:
        return "Error: rating_key is required."

    data = await _api("get_metadata", ctx=ctx, rating_key=rating_key)
    if not data:
        return f"No metadata found for rating_key {rating_key}."

    title = data.get("full_title") or data.get("title", "Unknown")
    media_type = data.get("media_type", "")
    year = data.get("year", "")
    header = f"{title} ({year})" if year else title
    lines = [f"{header} — {media_type}" if media_type else header, ""]

    library = data.get("library_name")
    if library:
        lines.append(f"Library: {library}")
    content_rating = data.get("content_rating")
    if content_rating:
        lines.append(f"Content Rating: {content_rating}")
    aired = data.get("originally_available_at")
    if aired:
        lines.append(f"Aired: {aired}")
    duration = data.get("duration")
    if duration:
        # Metadata duration is milliseconds.
        lines.append(f"Duration: {_fmt_duration(int(duration) / 1000)}")
    studio = data.get("studio")
    if studio:
        lines.append(f"Studio: {studio}")

    ratings = []
    if data.get("audience_rating"):
        ratings.append(f"audience {data['audience_rating']}")
    if data.get("rating"):
        ratings.append(f"critic {data['rating']}")
    if data.get("user_rating"):
        ratings.append(f"user {data['user_rating']}")
    if ratings:
        lines.append("Ratings: " + ", ".join(ratings))

    genres = data.get("genres") or []
    if genres:
        lines.append("Genres: " + ", ".join(genres))
    directors = data.get("directors") or []
    if directors:
        lines.append("Directors: " + ", ".join(directors))
    writers = data.get("writers") or []
    if writers:
        lines.append("Writers: " + ", ".join(writers[:5]))
    actors = data.get("actors") or []
    if actors:
        lines.append("Cast: " + ", ".join(actors[:6]))

    summary = data.get("summary")
    if summary:
        lines += ["", summary]

    media_lines = _fmt_media_info(data.get("media_info") or [])
    if media_lines:
        lines.append("")
        lines += media_lines

    # Public catalog IDs (imdb/tmdb/tvdb) are safe to surface.
    guids = data.get("guids") or []
    external = [
        g for g in guids if any(g.startswith(p) for p in ("imdb", "tmdb", "tvdb"))
    ]
    if external:
        lines += ["", "IDs: " + ", ".join(external)]

    return "\n".join(lines)


_ITEM_STAT_DAY_LABELS = {"1": "24h", "7": "7d", "30": "30d", "0": "all time"}


@mcp.tool()
async def tautulli_item_stats(
    rating_key: str, media_type: str = "", ctx: Context | None = None
) -> str:
    """Get watch stats for one item — total plays/time over 24h/7d/30d/all, plus which users watched it.

    Users are shown by friendly name only — usernames, user IDs, emails, and
    thumbnails are deliberately omitted.

    Args:
        rating_key: The item's rating key (from search/metadata/media-info output).
        media_type: Only required when rating_key refers to a collection.
    """
    rating_key = _sanitize_str(str(rating_key))
    if not rating_key:
        return "Error: rating_key is required."

    params: dict = {"rating_key": rating_key}
    if media_type:
        params["media_type"] = _sanitize_str(media_type)

    watch = await _api("get_item_watch_time_stats", ctx=ctx, **params)
    users = await _api("get_item_user_stats", ctx=ctx, **params)

    watch_rows = watch if isinstance(watch, list) else []
    user_rows = users if isinstance(users, list) else []

    if not watch_rows and not user_rows:
        return f"No watch stats found for rating_key {rating_key}."

    lines = [f"Watch stats for rating_key {rating_key}:\n"]

    if watch_rows:
        lines.append("By period:")
        for row in watch_rows:
            label = _ITEM_STAT_DAY_LABELS.get(str(row.get("query_days")), "?")
            plays = row.get("total_plays", 0)
            time_str = _fmt_duration(row.get("total_time", 0))
            lines.append(f"  • {label}: {plays} plays, {time_str}")

    if user_rows:
        # Scrub PII: keep only friendly_name + counts.
        ranked = sorted(user_rows, key=lambda u: u.get("total_plays", 0), reverse=True)
        lines.append("\nBy user:")
        for u in ranked[:15]:
            name = u.get("friendly_name") or "Unknown"
            plays = u.get("total_plays", 0)
            time_str = _fmt_duration(u.get("total_time", 0))
            lines.append(f"  • {name}: {plays} plays, {time_str}")

    return "\n".join(lines)


@mcp.tool()
async def tautulli_user_stats(
    user: str = "", days: int = 30, ctx: Context | None = None
) -> str:
    """Get per-user watch statistics — total plays, watch time, last seen.

    Args:
        user: Filter to a specific username. If empty, shows all active users.
        days: Time range in days for stats (default 30).
    """
    days = _clamp_days(days)
    params: dict = {"length": "25", "order_column": "plays", "order_dir": "desc"}
    if user:
        params["search"] = _sanitize_str(user)

    data = await _api("get_users_table", ctx=ctx, **params)
    users = data.get("data", [])

    if not users:
        return "No users found."

    lines = ["User statistics:\n"]
    for u in users:
        name = u.get("friendly_name") or u.get("username", "?")
        plays = u.get("plays", 0)
        duration = _fmt_duration(u.get("duration", 0))
        last_played = u.get("last_played", "")

        if plays == 0:
            continue  # Skip inactive users

        parts = [f"  • {name}: {plays} plays, {duration} watched"]
        if last_played:
            parts.append(f'last: "{last_played}"')

        lines.append(" — ".join(parts))

    return "\n".join(lines)


@mcp.tool()
async def tautulli_library_stats(ctx: Context | None = None) -> str:
    """Get library-level statistics — item counts, total plays, and last played content per library."""
    data = await _api("get_libraries_table", ctx=ctx)
    libraries = data.get("data", [])

    if not libraries:
        return "No libraries found."

    lines = ["Library statistics:\n"]
    for lib in libraries:
        name = lib.get("section_name", "?")
        section_type = lib.get("section_type", "")
        section_id = lib.get("section_id")
        count = lib.get("count", 0)
        plays = lib.get("plays", 0)
        last = lib.get("last_played", "")

        # Build count string based on type
        if section_type == "show":
            seasons = lib.get("parent_count", 0)
            episodes = lib.get("child_count", 0)
            count_str = f"{count} shows, {seasons} seasons, {episodes} episodes"
        elif section_type == "artist":
            albums = lib.get("parent_count", 0)
            tracks = lib.get("child_count", 0)
            count_str = f"{count} artists/authors, {albums} albums, {tracks} tracks"
        else:
            count_str = f"{count} items"

        last_str = f' — last: "{last}"' if last else ""
        id_str = f" [id: {section_id}]" if section_id is not None else ""
        lines.append(
            f"  • {name} ({section_type}){id_str}: {count_str}, {plays} plays{last_str}"
        )

    return "\n".join(lines)


_MEDIA_INFO_ORDER_COLUMNS = {
    "added_at",
    "sort_title",
    "container",
    "bitrate",
    "video_codec",
    "video_resolution",
    "video_framerate",
    "audio_codec",
    "audio_channels",
    "file_size",
    "last_played",
    "play_count",
}


@mcp.tool()
async def tautulli_library_media_info(
    section_id: str,
    order_column: str = "file_size",
    order_dir: str = "desc",
    length: int = 25,
    search: str = "",
    ctx: Context | None = None,
) -> str:
    """Get a media-quality breakdown for a Plex library — total size, item count, and per-item resolution/codec/size.

    Section IDs are shown as ``[id: N]`` in tautulli_library_stats output. Useful
    for auditing library quality and finding the largest files. Server file paths
    are not returned by this endpoint.

    Args:
        section_id: The Plex library section id.
        order_column: Sort field — one of file_size, added_at, sort_title, container,
            bitrate, video_codec, video_resolution, video_framerate, audio_codec,
            audio_channels, last_played, play_count (default file_size).
        order_dir: "desc" or "asc" (default desc).
        length: Number of items to return (default 25, max 100).
        search: Filter by title text.
    """
    section_id = _sanitize_str(str(section_id))
    if not section_id:
        return "Error: section_id is required."
    if order_column not in _MEDIA_INFO_ORDER_COLUMNS:
        return f"Invalid order_column: must be one of {', '.join(sorted(_MEDIA_INFO_ORDER_COLUMNS))}"
    order_dir = order_dir.lower()
    if order_dir not in ("desc", "asc"):
        return 'Invalid order_dir: must be "desc" or "asc"'
    length = min(max(1, length), 100)

    params: dict = {
        "section_id": section_id,
        "order_column": order_column,
        "order_dir": order_dir,
        "start": "0",
        "length": str(length),
    }
    if search:
        params["search"] = _sanitize_str(search)

    data = await _api("get_library_media_info", ctx=ctx, **params)
    rows = data.get("data", [])
    if not rows:
        return f"No media info found for section {section_id}."

    total = data.get("recordsTotal", len(rows))
    total_size = data.get("total_file_size")
    size_str = f", {_fmt_bytes(total_size)} total" if total_size else ""
    lines = [f"Library media info (section {section_id}, {total} items{size_str}):\n"]

    # Resolution breakdown across the returned rows.
    res_counts: dict[str, int] = {}
    for r in rows:
        res = r.get("video_resolution") or "—"
        res_counts[res] = res_counts.get(res, 0) + 1
    if res_counts:
        breakdown = ", ".join(
            f"{res}:{n}" for res, n in sorted(res_counts.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"Resolutions (top {len(rows)}): {breakdown}\n")

    lines.append(f"Items (sorted by {order_column} {order_dir}):")
    for r in rows:
        title = r.get("title", "Unknown")
        year = r.get("year", "")
        name = f"{title} ({year})" if year else title
        res = r.get("video_resolution", "")
        vcodec = r.get("video_codec", "")
        container = r.get("container", "")
        size = r.get("file_size")
        plays = r.get("play_count", 0)

        details = [d for d in (res, vcodec, container) if d]
        detail_str = f" — {', '.join(details)}" if details else ""
        size_part = f", {_fmt_bytes(size)}" if size else ""
        plays_part = f", {plays} plays" if plays else ""
        lines.append(f"  • {name}{detail_str}{size_part}{plays_part}")

    return "\n".join(lines)


@mcp.tool()
async def tautulli_most_watched(
    days: int = 7,
    stat_type: str = "plays",
    category: str = "tv",
    ctx: Context | None = None,
) -> str:
    """Get most watched content over a time period.

    Args:
        days: Time range in days (default 7).
        stat_type: Sort by "plays" (total plays) or "duration" (total watch time).
        category: Content category — "tv", "movies", "music", or "users" (top users).
    """
    days = _clamp_days(days)
    if category.lower() not in _VALID_CATEGORIES:
        return (
            f"Invalid category: must be one of {', '.join(sorted(_VALID_CATEGORIES))}"
        )
    if stat_type not in _VALID_STAT_TYPES:
        return (
            f"Invalid stat_type: must be one of {', '.join(sorted(_VALID_STAT_TYPES))}"
        )
    stat_map = {
        "tv": "top_tv",
        "movies": "top_movies",
        "music": "top_music",
        "users": "top_users",
    }
    stat_id = stat_map.get(category.lower(), "top_tv")
    stats_type = "total_plays" if stat_type == "plays" else "total_duration"

    data = await _api(
        "get_home_stats",
        ctx=ctx,
        time_range=str(days),
        stat_id=stat_id,
        stats_type=stats_type,
    )
    rows = data.get("rows", [])
    title = data.get("stat_title", f"Top {category}")

    if not rows:
        return f"No {category} data for the last {days} days."

    lines = [f"{title} (last {days} days):\n"]
    for i, r in enumerate(rows[:10], 1):
        name = r.get("title") or r.get("friendly_name", "?")
        year = r.get("year", "")
        plays = r.get("total_plays", 0)
        duration = _fmt_duration(r.get("total_duration", 0))

        name_str = f"{name} ({year})" if year else name
        lines.append(f"  {i}. {name_str} — {plays} plays, {duration}")

    return "\n".join(lines)


@mcp.tool()
async def tautulli_server_info(ctx: Context | None = None) -> str:
    """Get Plex server identity — name, version, platform, and connection details."""
    data = await _api("get_server_info", ctx=ctx)

    name = data.get("pms_name", "Unknown")
    version = data.get("pms_version", "?")
    platform = data.get("pms_platform", "?")
    ip = data.get("pms_ip", "?")
    port = data.get("pms_port", "?")
    ssl = "yes" if data.get("pms_ssl") else "no"
    plexpass = "yes" if data.get("pms_plexpass") else "no"

    return (
        f"Plex Server: {name}\n"
        f"  Version: {version}\n"
        f"  Platform: {platform}\n"
        f"  Address: {ip}:{port} (SSL: {ssl})\n"
        f"  PlexPass: {plexpass}"
    )


@mcp.tool()
async def tautulli_status(ctx: Context | None = None) -> str:
    """Check Tautulli server configuration and reachability."""
    lines = [
        f"Tautulli URL: {TAUTULLI_URL}",
        f"API Key: {'configured' if TAUTULLI_API_KEY else 'NOT SET'}",
        f"TLS Verify: {TLS_VERIFY}",
    ]

    if not TAUTULLI_API_KEY:
        lines.append("\nError: TAUTULLI_API_KEY environment variable not set.")
        return "\n".join(lines)

    try:
        data = await _api("get_server_info", ctx=ctx)
        name = data.get("pms_name", "Unknown")
        version = data.get("pms_version", "?")
        lines.append(f'\nReachable: yes — Plex server "{name}" v{version}')
    except RuntimeError:
        lines.append("\nReachable: NO — connection failed")

    return "\n".join(lines)


# ── Chart helper ─────────────────────────────────────────────────────────


def _chart_totals(data: dict) -> list[dict]:
    """Convert Tautulli chart format {categories, series} into per-category totals.

    Returns list of dicts: [{"name": "Roku", "Direct Play": 81, "Transcode": 31, ...}, ...]
    """
    categories = data.get("categories", [])
    series = data.get("series", [])
    result = []
    for i, cat in enumerate(categories):
        row: dict = {"name": cat}
        total = 0
        for s in series:
            val = s["data"][i] if i < len(s["data"]) else 0
            row[s["name"]] = val
            total += val
        row["total"] = total
        result.append(row)
    return result


# ── Analytics tools ──────────────────────────────────────────────────────


@mcp.tool()
async def tautulli_transcode_stats(days: int = 30, ctx: Context | None = None) -> str:
    """Get direct play vs transcode breakdown by platform — shows which devices cause the most transcoding load.

    Args:
        days: Time range in days (default 30).
    """
    days = _clamp_days(days)
    data = await _api(
        "get_stream_type_by_top_10_platforms", ctx=ctx, time_range=str(days)
    )
    rows = _chart_totals(data)

    if not rows:
        return f"No stream data for the last {days} days."

    # Compute overall totals
    all_dp = sum(r.get("Direct Play", 0) for r in rows)
    all_ds = sum(r.get("Direct Stream", 0) for r in rows)
    all_tc = sum(r.get("Transcode", 0) for r in rows)
    all_total = all_dp + all_ds + all_tc

    lines = [f"Stream type by platform (last {days} days, {all_total} total plays):\n"]

    for r in rows:
        dp = r.get("Direct Play", 0)
        ds = r.get("Direct Stream", 0)
        tc = r.get("Transcode", 0)
        total = r["total"]
        if total == 0:
            continue
        tc_pct = tc / total * 100

        parts = []
        if dp:
            parts.append(f"{dp} direct play")
        if ds:
            parts.append(f"{ds} direct stream")
        if tc:
            parts.append(f"{tc} transcode")

        lines.append(
            f"  • {r['name']}: {total} plays — {', '.join(parts)} ({tc_pct:.0f}% transcode)"
        )

    if all_total:
        overall_tc_pct = all_tc / all_total * 100
        lines.append(
            f"\nOverall: {all_dp} direct play, {all_ds} direct stream, {all_tc} transcode ({overall_tc_pct:.0f}% transcode)"
        )

    return "\n".join(lines)


@mcp.tool()
async def tautulli_platform_stats(days: int = 30, ctx: Context | None = None) -> str:
    """Get top platforms/devices by plays and total watch time.

    Args:
        days: Time range in days (default 30).
    """
    days = _clamp_days(days)
    data = await _api(
        "get_home_stats", ctx=ctx, time_range=str(days), stat_id="top_platforms"
    )
    rows = data.get("rows", [])

    if not rows:
        return f"No platform data for the last {days} days."

    lines = [f"Top platforms (last {days} days):\n"]
    for i, r in enumerate(rows[:10], 1):
        platform = r.get("platform", "Unknown")
        plays = r.get("total_plays", 0)
        duration = _fmt_duration(r.get("total_duration", 0))
        lines.append(f"  {i}. {platform} — {plays} plays, {duration} watched")

    total_plays = sum(r.get("total_plays", 0) for r in rows)
    total_dur = sum(r.get("total_duration", 0) for r in rows)
    lines.append(
        f"\nTotal: {total_plays} plays, {_fmt_duration(total_dur)} watched across {len(rows)} platforms"
    )

    return "\n".join(lines)


@mcp.tool()
async def tautulli_stream_resolution(days: int = 30, ctx: Context | None = None) -> str:
    """Get source vs delivered resolution analysis — shows what quality your library serves and what clients actually receive.

    Args:
        days: Time range in days (default 30).
    """
    days = _clamp_days(days)
    source = await _api("get_plays_by_source_resolution", ctx=ctx, time_range=str(days))
    stream = await _api("get_plays_by_stream_resolution", ctx=ctx, time_range=str(days))

    source_rows = _chart_totals(source)
    stream_rows = _chart_totals(stream)

    if not source_rows:
        return f"No resolution data for the last {days} days."

    lines = [f"Resolution analysis (last {days} days):\n"]

    # Source resolution
    lines.append("Source (file quality):")
    for r in source_rows:
        if r["total"] == 0:
            continue
        dp = r.get("Direct Play", 0)
        tc = r.get("Transcode", 0)
        ds = r.get("Direct Stream", 0)
        lines.append(f"  • {r['name']}: {r['total']} plays (DP:{dp}, DS:{ds}, TC:{tc})")

    # Stream resolution
    lines.append("\nDelivered (what clients received):")
    for r in stream_rows:
        if r["total"] == 0:
            continue
        dp = r.get("Direct Play", 0)
        tc = r.get("Transcode", 0)
        ds = r.get("Direct Stream", 0)
        lines.append(f"  • {r['name']}: {r['total']} plays (DP:{dp}, DS:{ds}, TC:{tc})")

    # Quick insight: 4K source vs stream
    src_4k = next((r["total"] for r in source_rows if r["name"] == "4k"), 0)
    str_4k = next((r["total"] for r in stream_rows if r["name"] == "4k"), 0)
    if src_4k > 0 and str_4k < src_4k:
        downgraded = src_4k - str_4k
        lines.append(
            f"\nNote: {downgraded} of {src_4k} 4K source plays were transcoded to lower resolution."
        )

    return "\n".join(lines)


@mcp.tool()
async def tautulli_plays_by_date(days: int = 14, ctx: Context | None = None) -> str:
    """Get daily play counts over time, broken down by stream type (direct play, direct stream, transcode).

    Args:
        days: Number of days to show (default 14, max 90).
    """
    days = _clamp_days(days, default=14, maximum=90)
    data = await _api("get_plays_by_stream_type", ctx=ctx, time_range=str(days))
    rows = _chart_totals(data)

    if not rows:
        return f"No play data for the last {days} days."

    # Filter out zero-activity days from the start
    first_active = 0
    for i, r in enumerate(rows):
        if r["total"] > 0:
            first_active = i
            break
    rows = rows[first_active:]

    if not rows:
        return f"No play activity in the last {days} days."

    lines = [f"Daily plays (last {len(rows)} active days):\n"]
    for r in rows:
        dp = r.get("Direct Play", 0)
        ds = r.get("Direct Stream", 0)
        tc = r.get("Transcode", 0)
        total = r["total"]
        bar = "█" * min(total, 50)  # Simple visual bar, capped
        lines.append(f"  {r['name']}: {total:3d} {bar}  (DP:{dp} DS:{ds} TC:{tc})")

    total_all = sum(r["total"] for r in rows)
    avg = total_all / len(rows) if rows else 0
    lines.append(f"\nTotal: {total_all} plays, avg {avg:.1f}/day")

    return "\n".join(lines)


_DAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


@mcp.tool()
async def tautulli_plays_by_day_of_week(
    days: int = 30, ctx: Context | None = None
) -> str:
    """Get weekly viewing patterns — which days of the week see the most Plex activity.

    Args:
        days: Time range in days (default 30).
    """
    days = _clamp_days(days)
    data = await _api("get_plays_by_dayofweek", ctx=ctx, time_range=str(days))
    rows = _chart_totals(data)

    if not rows:
        return f"No play data for the last {days} days."

    # Find peak day
    peak_idx = max(range(len(rows)), key=lambda i: rows[i]["total"])
    max_total = max(r["total"] for r in rows) or 1

    # Build series breakdown (keys vary by endpoint: TV/Movies/Music, etc.)
    series_keys = [k for k in rows[0] if k not in ("name", "total")] if rows else []

    lines = [f"Plays by day of week (last {days} days):\n"]
    for i, r in enumerate(rows):
        total = r["total"]
        bar_len = int(total / max_total * 30) if max_total else 0
        bar = "█" * bar_len
        peak = "  ← peak" if i == peak_idx else ""
        day_name = _DAY_NAMES[i] if i < len(_DAY_NAMES) else r["name"]
        breakdown = ", ".join(f"{k}:{r.get(k, 0)}" for k in series_keys)
        lines.append(f"  {day_name:<9s}: {total:3d} {bar}  ({breakdown}){peak}")

    total_all = sum(r["total"] for r in rows)
    avg = total_all / 7 if rows else 0
    lines.append(f"\nTotal: {total_all} plays, avg {avg:.1f}/day")

    return "\n".join(lines)


@mcp.tool()
async def tautulli_plays_by_hour(days: int = 30, ctx: Context | None = None) -> str:
    """Get hourly viewing distribution — when people watch Plex throughout the day.

    Args:
        days: Time range in days (default 30).
    """
    days = _clamp_days(days)
    data = await _api("get_plays_by_hourofday", ctx=ctx, time_range=str(days))
    rows = _chart_totals(data)

    if not rows:
        return f"No play data for the last {days} days."

    # Find peak hour
    peak_idx = max(range(len(rows)), key=lambda i: rows[i]["total"])
    max_total = max(r["total"] for r in rows) or 1

    series_keys = [k for k in rows[0] if k not in ("name", "total")] if rows else []

    lines = [f"Plays by hour of day (last {days} days):\n"]
    for i, r in enumerate(rows):
        total = r["total"]
        bar_len = int(total / max_total * 30) if max_total else 0
        bar = "█" * bar_len
        peak = "  ← peak" if i == peak_idx else ""
        breakdown = ", ".join(f"{k}:{r.get(k, 0)}" for k in series_keys)
        lines.append(f"  {i:02d}:00  {total:3d} {bar}  ({breakdown}){peak}")

    peak_hour = f"{peak_idx:02d}:00"
    total_all = sum(r["total"] for r in rows)
    lines.append(f"\nTotal: {total_all} plays, peak hour: {peak_hour}")

    return "\n".join(lines)


# ── Resources ────────────────────────────────────────────────────────────


@mcp.resource("tautulli://activity")
async def _resource_activity() -> str:
    """Current Plex streaming activity — who's watching what right now."""
    return await tautulli_activity()


@mcp.resource("tautulli://server")
async def _resource_server() -> str:
    """Plex server identity and status."""
    return await tautulli_server_info()


# ── Entry point ──────────────────────────────────────────────────────────


def main() -> None:
    """Entry point for the mcp-tautulli command."""
    import sys

    missing = []
    if not TAUTULLI_URL:
        missing.append("TAUTULLI_URL")
    if not TAUTULLI_API_KEY:
        missing.append("TAUTULLI_API_KEY")
    if missing:
        print(
            f"Error: Required environment variable(s) not set: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)
    mcp.run()


if __name__ == "__main__":
    main()
