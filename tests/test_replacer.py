"""Tests for replacer module -- in-place replacement and moveItem gating."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from replacer import replace_in_place
from scanner import SwapCandidate, TrackInfo


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("replacer.time.sleep", lambda _: None)


def _track(video_id: str, title: str, set_video_id: str | None = "svid") -> TrackInfo:
    return TrackInfo(
        video_id=video_id,
        set_video_id=set_video_id,
        title=title,
        artist="Test Artist",
        album=None,
        duration_seconds=200,
        thumbnail_url=None,
        ytm_link=f"https://music.youtube.com/watch?v={video_id}",
        is_explicit=False,
    )


def _swap() -> SwapCandidate:
    return SwapCandidate(
        original=_track("orig_vid", "Clean", set_video_id="orig_svid"),
        replacement=_track("new_vid", "Explicit", set_video_id=None),
    )


def _mock_yt():
    yt = MagicMock()
    yt.add_playlist_items.return_value = {
        "playlistEditResults": [{"setVideoId": "new_svid"}]
    }
    yt.edit_playlist.return_value = "ok"
    yt.remove_playlist_items.return_value = "ok"
    return yt


def _move_calls(yt):
    return [c for c in yt.edit_playlist.call_args_list if "moveItem" in c.kwargs]


class TestReplaceInPlaceDefault:
    def test_default_skips_move_item(self):
        yt = _mock_yt()
        replace_in_place(yt, "PL123", [_swap()])
        assert _move_calls(yt) == []

    def test_default_still_adds_and_removes(self):
        yt = _mock_yt()
        replace_in_place(yt, "PL123", [_swap()])

        yt.add_playlist_items.assert_called_once_with("PL123", ["new_vid"])
        yt.remove_playlist_items.assert_called_once_with(
            "PL123",
            [{"videoId": "orig_vid", "setVideoId": "orig_svid"}],
        )


class TestReplaceInPlacePreservePosition:
    def test_preserve_position_calls_move_item(self):
        yt = _mock_yt()
        replace_in_place(yt, "PL123", [_swap()], preserve_position=True)

        moves = _move_calls(yt)
        assert len(moves) == 1
        assert moves[0].kwargs["moveItem"] == ("new_svid", "orig_svid")

    def test_preserve_position_skips_move_when_original_has_no_svid(self):
        """No setVideoId on original means we can't target where to move to."""
        yt = _mock_yt()
        swap = SwapCandidate(
            original=_track("orig_vid", "Clean", set_video_id=None),
            replacement=_track("new_vid", "Explicit"),
        )

        replace_in_place(yt, "PL123", [swap], preserve_position=True)

        assert _move_calls(yt) == []
