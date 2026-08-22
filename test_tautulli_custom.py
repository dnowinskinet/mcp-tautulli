"""Tests for Daniel-specific mcp-tautulli customizations."""

from unittest.mock import AsyncMock, patch

import tautulli_custom


class TestCustomizedLibraryMediaInfo:
    async def test_includes_rating_key_in_each_media_row(self):
        with patch.object(
            tautulli_custom.upstream, "_api", new_callable=AsyncMock
        ) as mock_api:
            mock_api.return_value = {
                "data": [
                    {
                        "rating_key": "4525",
                        "title": "V for Vendetta",
                        "year": "2006",
                        "video_resolution": "4k",
                        "video_codec": "hevc",
                        "container": "mkv",
                        "file_size": 76_665_122_816,
                        "play_count": 0,
                    }
                ],
                "recordsTotal": 1,
                "total_file_size": 76_665_122_816,
            }

            result = await tautulli_custom.tautulli_library_media_info("1")

        assert "V for Vendetta (2006) [key: 4525]" in result
        assert "4k, hevc, mkv" in result
        mock_api.assert_awaited_once_with(
            "get_library_media_info",
            ctx=None,
            section_id="1",
            order_column="file_size",
            order_dir="desc",
            start="0",
            length="25",
        )

    async def test_omits_rating_key_marker_when_key_is_unavailable(self):
        with patch.object(
            tautulli_custom.upstream, "_api", new_callable=AsyncMock
        ) as mock_api:
            mock_api.return_value = {
                "data": [{"title": "Unknown Movie", "year": "2024"}],
                "recordsTotal": 1,
            }

            result = await tautulli_custom.tautulli_library_media_info("1")

        assert "Unknown Movie (2024)" in result
        assert "[key:" not in result
