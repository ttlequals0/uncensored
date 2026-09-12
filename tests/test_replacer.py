"""Tests for replacer module -- in-place replacement and moveItem gating."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from replacer import (
    copy_playlist_without,
    remove_from_playlist,
    replace_in_place,
    replace_with_copy,
)
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


class TestRemoveFromPlaylist:
    def test_one_call_per_track_with_exact_pair(self):
        yt = _mock_yt()
        tracks = [
            _track("v1", "One", set_video_id="s1"),
            _track("v2", "Two", set_video_id="s2"),
        ]
        report = remove_from_playlist(yt, "PL123", tracks)

        assert yt.remove_playlist_items.call_args_list == [
            (("PL123", [{"videoId": "v1", "setVideoId": "s1"}]), {}),
            (("PL123", [{"videoId": "v2", "setVideoId": "s2"}]), {}),
        ]
        assert yt.add_playlist_items.call_count == 0
        assert all(r.success for r in report.results)

    def test_403_triggers_copy_fallback_and_stops(self):
        yt = _mock_yt()
        yt.remove_playlist_items.side_effect = Exception("403 Forbidden")
        tracks = [
            _track("v1", "One", set_video_id="s1"),
            _track("v2", "Two", set_video_id="s2"),
        ]
        report = remove_from_playlist(yt, "PL123", tracks)

        assert report.copy_mode_fallback is True
        assert yt.remove_playlist_items.call_count == 1
        assert len(report.results) == 1

    def test_other_error_recorded_and_continues(self):
        yt = _mock_yt()
        yt.remove_playlist_items.side_effect = [Exception("network blip"), "ok"]
        tracks = [
            _track("v1", "One", set_video_id="s1"),
            _track("v2", "Two", set_video_id="s2"),
        ]
        report = remove_from_playlist(yt, "PL123", tracks)

        assert report.copy_mode_fallback is False
        assert len(report.results) == 2
        assert report.results[0].success is False
        assert report.results[1].success is True


class TestReplaceWithCopy:
    def test_original_without_video_id_still_swapped(self):
        """Unavailable originals may have no videoId; the replacement must
        not be filtered out before the swap map is applied."""
        yt = _mock_yt()
        yt.create_playlist.return_value = "PLnew"
        swap = SwapCandidate(
            original=_track(None, "Unavailable", set_video_id=None),
            replacement=_track("new_vid", "Explicit"),
        )

        replace_with_copy(yt, [swap], [None, "a"], "Copy")

        yt.add_playlist_items.assert_called_once_with(
            "PLnew", ["new_vid", "a"], duplicates=True
        )


class TestCopyPlaylistWithout:
    def test_creates_playlist_and_batches(self):
        yt = _mock_yt()
        yt.create_playlist.return_value = "PLnew"
        ids = [f"v{i}" for i in range(30)]

        report = copy_playlist_without(yt, ids, "Copy Name")

        assert report.new_playlist_id == "PLnew"
        assert report.new_playlist_title == "Copy Name"
        calls = yt.add_playlist_items.call_args_list
        assert len(calls) == 2
        assert calls[0].args == ("PLnew", ids[:25])
        assert calls[1].args == ("PLnew", ids[25:])
        assert all(c.kwargs.get("duplicates") is True for c in calls)

    def test_duplicate_ids_kept_positionally(self):
        yt = _mock_yt()
        yt.create_playlist.return_value = "PLnew"

        copy_playlist_without(yt, ["a", "a", "b"], "Copy")

        yt.add_playlist_items.assert_called_once_with("PLnew", ["a", "a", "b"], duplicates=True)

    def test_empty_ids_skipped(self):
        yt = _mock_yt()
        yt.create_playlist.return_value = "PLnew"

        copy_playlist_without(yt, ["a", "", "b"], "Copy")

        yt.add_playlist_items.assert_called_once_with("PLnew", ["a", "b"], duplicates=True)

    def test_batch_failure_retries_individually(self):
        yt = _mock_yt()
        yt.create_playlist.return_value = "PLnew"
        yt.add_playlist_items.side_effect = [Exception("batch failed"), "ok", "ok"]

        copy_playlist_without(yt, ["a", "b"], "Copy")

        assert yt.add_playlist_items.call_count == 3
        assert yt.add_playlist_items.call_args_list[1].args == ("PLnew", ["a"])
        assert yt.add_playlist_items.call_args_list[2].args == ("PLnew", ["b"])
