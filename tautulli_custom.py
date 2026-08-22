"""Customization layer for Daniel's mcp-tautulli fork.

The upstream project intentionally keeps all tools in a single ``tautulli.py``
module. This module reuses that server and replaces only the library-media-info
tool so bulk results include Plex rating keys for efficient follow-up metadata
lookups.
"""

from __future__ import annotations

import tautulli as upstream
from fastmcp import Context

mcp = upstream.mcp

# Replace the upstream formatter while keeping the same public MCP tool name.
mcp.local_provider.remove_tool("tautulli_library_media_info")


@mcp.tool()
async def tautulli_library_media_info(
    section_id: str,
    order_column: str = "file_size",
    order_dir: str = "desc",
    length: int = 25,
    search: str = "",
    ctx: Context | None = None,
) -> str:
    """Get a media-quality breakdown for a Plex library.

    Each item includes its Plex rating key when Tautulli returns one, allowing
    callers to pass the key directly to ``tautulli_metadata`` or
    ``tautulli_item_stats`` without an additional title search.

    Args:
        section_id: The Plex library section id.
        order_column: Sort field — one of file_size, added_at, sort_title,
            container, bitrate, video_codec, video_resolution,
            video_framerate, audio_codec, audio_channels, last_played,
            play_count (default file_size).
        order_dir: "desc" or "asc" (default desc).
        length: Number of items to return (default 25, max 100).
        search: Filter by title text.
    """
    section_id = upstream._sanitize_str(str(section_id))
    if not section_id:
        return "Error: section_id is required."
    if order_column not in upstream._MEDIA_INFO_ORDER_COLUMNS:
        valid = ", ".join(sorted(upstream._MEDIA_INFO_ORDER_COLUMNS))
        return f"Invalid order_column: must be one of {valid}"
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
        params["search"] = upstream._sanitize_str(search)

    data = await upstream._api("get_library_media_info", ctx=ctx, **params)
    rows = data.get("data", [])
    if not rows:
        return f"No media info found for section {section_id}."

    total = data.get("recordsTotal", len(rows))
    total_size = data.get("total_file_size")
    size_str = f", {upstream._fmt_bytes(total_size)} total" if total_size else ""
    lines = [
        f"Library media info (section {section_id}, {total} items{size_str}):\n"
    ]

    res_counts: dict[str, int] = {}
    for row in rows:
        resolution = row.get("video_resolution") or "—"
        res_counts[resolution] = res_counts.get(resolution, 0) + 1
    if res_counts:
        breakdown = ", ".join(
            f"{resolution}:{count}"
            for resolution, count in sorted(
                res_counts.items(), key=lambda item: -item[1]
            )
        )
        lines.append(f"Resolutions (top {len(rows)}): {breakdown}\n")

    lines.append(f"Items (sorted by {order_column} {order_dir}):")
    for row in rows:
        title = row.get("title", "Unknown")
        year = row.get("year", "")
        name = f"{title} ({year})" if year else title
        rating_key = row.get("rating_key")
        key_part = f" [key: {rating_key}]" if rating_key else ""
        resolution = row.get("video_resolution", "")
        video_codec = row.get("video_codec", "")
        container = row.get("container", "")
        file_size = row.get("file_size")
        plays = row.get("play_count", 0)

        details = [
            detail
            for detail in (resolution, video_codec, container)
            if detail
        ]
        detail_str = f" — {', '.join(details)}" if details else ""
        size_part = (
            f", {upstream._fmt_bytes(file_size)}" if file_size else ""
        )
        plays_part = f", {plays} plays" if plays else ""
        lines.append(
            f"  • {name}{key_part}{detail_str}{size_part}{plays_part}"
        )

    return "\n".join(lines)


def main() -> None:
    """Run the customized Tautulli MCP server."""
    upstream.main()


if __name__ == "__main__":
    main()
