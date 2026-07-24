<!-- mcp-name: io.github.lodordev/mcp-tautulli -->

# mcp-tautulli

A single-file [MCP](https://modelcontextprotocol.io/) server for [Tautulli](https://tautulli.com/) — Plex monitoring via Claude Code (or any MCP client).

19 read-only tools. No mutations. All configuration via environment variables.

## Prerequisites

- Python 3.10+
- A running [Tautulli](https://tautulli.com/) instance with an API key
- [Claude Code](https://claude.ai/code) (or any MCP-compatible client)

## Installation

```bash
pip install mcp-tautulli
```

Or install with uv:

```bash
uv tool install mcp-tautulli
```

Or from source:

```bash
git clone https://github.com/lodordev/mcp-tautulli.git
cd mcp-tautulli
pip install .
```

## Configuration

Three environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TAUTULLI_URL` | Yes | — | Tautulli base URL with protocol (e.g. `http://localhost:8181` or `https://tautulli.example.com:8181`) |
| `TAUTULLI_API_KEY` | Yes | — | Tautulli API key (Settings → Web Interface → API Key) |
| `TAUTULLI_TLS_VERIFY` | No | `true` | Set to `false` if using self-signed certs (e.g. Tailscale serve) |

## Claude Code Setup

Add to your project's `.mcp.json`:

```jsonc
{
  "mcpServers": {
    "tautulli": {
      "command": "mcp-tautulli",
      "env": {
        // Include the protocol (http:// or https://)
        "TAUTULLI_URL": "http://your-tautulli-host:8181",
        "TAUTULLI_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

## Claude Desktop Setup

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "tautulli": {
      "command": "mcp-tautulli",
      "env": {
        "TAUTULLI_URL": "http://your-tautulli-host:8181",
        "TAUTULLI_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

## Local Development Config

To point your MCP client at local source without reinstalling after every change, use `uv run --directory` instead of the installed binary:

```json
{
  "mcpServers": {
    "tautulli-dev": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-tautulli", "mcp-tautulli"],
      "env": {
        "TAUTULLI_URL": "http://your-tautulli-host:8181",
        "TAUTULLI_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

This works in both Claude Code (`.mcp.json`) and Claude Desktop (`claude_desktop_config.json`). Restart the client after code changes — no `uv tool install` needed.

Or run standalone:

```bash
export TAUTULLI_URL="http://localhost:8181"
export TAUTULLI_API_KEY="your-api-key"
mcp-tautulli
```

## Tools

| Tool | Description |
|------|-------------|
| `tautulli_activity` | Current Plex streaming activity — who's watching what, progress, quality |
| `tautulli_history` | Recent playback history with filters (user, media type, search, date) — includes transcode decision; pass `include_ip=true` to show client IP (off by default, it's PII) and `include_performance=true` to also fetch per-record bitrate via `get_stream_data` |
| `tautulli_recently_added` | Recently added content — what's new in your Plex libraries |
| `tautulli_search` | Search Plex content by title across all libraries — output includes `[key: N]` rating keys |
| `tautulli_metadata` | Full metadata for one item by rating key — summary, cast/crew, genres, ratings, and media quality (resolution, codecs, HDR/Dolby Vision, file size). Server file paths omitted |
| `tautulli_item_stats` | Watch stats for one item — plays/time over 24h/7d/30d/all, plus which users watched it (friendly name only) |
| `tautulli_user_stats` | Per-user watch statistics — plays, watch time, last seen |
| `tautulli_library_stats` | Library item counts, total plays, last played per library — output includes `[id: N]` section ids |
| `tautulli_library_media_info` | Per-library media-quality breakdown — total size, item count, and per-item resolution/codec/container/size (find largest files) |
| `tautulli_most_watched` | Top content by plays or duration (TV, movies, music, users) |
| `tautulli_server_info` | Plex server identity — name, version, platform, connection |
| `tautulli_status` | Server config and reachability check |
| `tautulli_transcode_stats` | Direct play vs transcode breakdown by platform |
| `tautulli_platform_stats` | Top platforms/devices by plays and watch time |
| `tautulli_stream_resolution` | Source vs delivered resolution analysis |
| `tautulli_plays_by_date` | Daily play counts over time by stream type |
| `tautulli_plays_by_day_of_week` | Weekly viewing patterns — which days see the most activity |
| `tautulli_plays_by_hour` | Hourly viewing distribution — when people watch |
| `tautulli_stream_data` | Detailed stream performance data — bitrate, codec, transcode decision, bandwidth for a specific play (use `row_id` from history or `session_key` from activity) |

All tools are **read-only** — this server does not modify any Tautulli or Plex state. Each tool calls a fixed Tautulli `get_*`/`search` command; there is no passthrough that could reach a write/destructive endpoint.

**Privacy:** user-identifying data is minimized by default — usernames, user IDs, emails, and thumbnails are never emitted (users appear by friendly name only), server file paths are omitted, and client IP addresses are opt-in (`include_ip=true` on `tautulli_history`). Note that Tautulli itself has no read-only API key — the key you configure is a full-access master key, so this server's read-only guarantee lives in its fixed command set, not in the credential.

<details>
<summary><strong>Example Output</strong></summary>

**`tautulli_activity`**
```
2 active stream(s):

  • Alice playing "The Bear S02E06 — Fishes" — 45%, on Apple TV (direct play)
  • Bob playing "Oppenheimer (2023)" — 12%, on Roku (transcode)

Bandwidth: 18.5 Mbps total (LAN: 12.2, WAN: 6.3)
```

**`tautulli_plays_by_day_of_week`**
```
Plays by day of week (last 30 days):

  Monday   :  91 ██████████████████████████████  (TV:62, Movies:18, Music:11)  ← peak
  Tuesday  :  76 █████████████████████████  (TV:56, Movies:15, Music:5)
  Wednesday:  62 ████████████████████  (TV:34, Movies:20, Music:8)
  Thursday :  45 ██████████████  (TV:32, Movies:8, Music:5)
  Friday   :  59 ███████████████████  (TV:37, Movies:14, Music:8)
  Saturday :  50 ████████████████  (TV:32, Movies:10, Music:8)
  Sunday   :  86 ████████████████████████████  (TV:60, Movies:16, Music:10)

Total: 469 plays, avg 67.0/day
```

**`tautulli_stream_data`** (pass `row_id` from history output)
```
Stream Performance Data:

Media: Game of Thrones — The Red Woman (episode)

Quality Profile: Original
Source Bitrate: 10617 kbps
Source Video Bitrate: 10233 kbps
Source Audio Bitrate: 384 kbps

Stream Bitrate: 10617 kbps
Stream Resolution: 1080p
Stream Video Codec: h264
Stream Framerate: 24p
Stream Audio Codec: ac3
Stream Audio Channels: 6

Source Container: mkv
Source Video Codec: h264
Source Audio Codec: ac3
Source Resolution: 1080

Video Decision: direct play
Audio Decision: direct play
```

**`tautulli_metadata`** (pass `[key: N]` from search / recently-added)
```
Example Movie (2019) — movie

Library: Movies
Content Rating: PG-13
Aired: 2019-05-01
Duration: 2h 10m
Ratings: audience 7.9
Genres: Adventure, Sci-Fi
Resolution: 1080p
Container: mkv
Video Codec: h264
Audio: eac3 6ch
File Size: 18.5 GB
Dynamic Range: SDR

IDs: imdb://tt0000000, tmdb://00000
```

**`tautulli_library_media_info`** (pass `[id: N]` from library stats)
```
Library media info (section 1, 500 items, 4.2 TB total):

Resolutions (top 3): 1080:2, 4k:1

Items (sorted by file_size desc):
  • Example Feature (2021) — 4k, hevc, mkv, 42.0 GB
  • Another Movie (2018) — 1080, h264, mkv, 12.3 GB
  • A Third Film (2016) — 1080, h264, mp4, 9.8 GB
```

**`tautulli_search`**
```
Search results for "breaking":

Movies:
  • Breaking (2012) — Movies

TV Shows:
  • Breaking Bad (2008) — TV Shows
```

</details>

## Troubleshooting

**"TAUTULLI_URL environment variable not set"**
Both `TAUTULLI_URL` and `TAUTULLI_API_KEY` must be set. Find your API key in Tautulli → Settings → Web Interface → API Key.

**TLS/SSL errors**
If Tautulli is behind a reverse proxy with a self-signed certificate, set `TAUTULLI_TLS_VERIFY=false`.

**"Tautulli unreachable"**
Verify the URL is accessible from the machine running the MCP server. Check firewalls and that Tautulli is running.

## License

MIT
