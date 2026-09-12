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


OK = "STATUS_SUCCEEDED"


def _track(
    video_id: str | None, title: str, set_video_id: str | None = "svid", position: int | None = None,
) -> TrackInfo:
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
        position=position,
    )


def _swap() -> SwapCandidate:
    return SwapCandidate(
        original=_track("orig_vid", "Clean", set_video_id="orig_svid"),
        replacement=_track("new_vid", "Explicit", set_video_id=None),
    )


def _mock_yt():
    # Return shapes match ytmusicapi 1.12.2 on success
    yt = MagicMock()
    yt.add_playlist_items.return_value = {
        "status": OK,
        "playlistEditResults": [{"setVideoId": "new_svid"}],
    }
    yt.edit_playlist.return_value = OK
    yt.remove_playlist_items.return_value = OK
    yt.create_playlist.return_value = "PLnew"
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


class TestReplaceInPlaceExistingReplacement:
    """YouTube Music rejects adding a video the playlist already holds."""

    def test_replacement_already_present_removes_original_only(self):
        yt = _mock_yt()

        report = replace_in_place(yt, "PL123", [_swap()], existing_video_ids={"new_vid"})

        assert yt.add_playlist_items.call_count == 0
        yt.remove_playlist_items.assert_called_once_with(
            "PL123", [{"videoId": "orig_vid", "setVideoId": "orig_svid"}],
        )
        assert report.results[0].success is True

    def test_two_originals_sharing_a_replacement_add_once(self):
        yt = _mock_yt()
        swaps = [
            _swap(),
            SwapCandidate(
                original=_track("orig2", "Clean 2", set_video_id="orig2_svid"),
                replacement=_track("new_vid", "Explicit"),
            ),
        ]

        report = replace_in_place(yt, "PL123", swaps, existing_video_ids=set())

        assert yt.add_playlist_items.call_count == 1
        assert yt.remove_playlist_items.call_count == 2
        assert all(r.success for r in report.results)


class TestReplaceInPlaceRejectedEdits:
    """ytmusicapi returns a failure status instead of raising."""

    def test_rejected_add_never_removes_original(self):
        yt = _mock_yt()
        yt.add_playlist_items.return_value = {"actions": [], "status": "STATUS_FAILED"}

        report = replace_in_place(yt, "PL123", [_swap()])

        assert yt.remove_playlist_items.call_count == 0
        assert report.results[0].success is False
        assert "STATUS_FAILED" in report.results[0].error

    def test_rejected_remove_is_not_success(self):
        yt = _mock_yt()
        yt.remove_playlist_items.return_value = "STATUS_FAILED"

        report = replace_in_place(yt, "PL123", [_swap()])

        assert report.results[0].success is False
        assert report.results[0].duplicate_warning is True


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
        yt.remove_playlist_items.side_effect = [Exception("network blip"), OK]
        tracks = [
            _track("v1", "One", set_video_id="s1"),
            _track("v2", "Two", set_video_id="s2"),
        ]
        report = remove_from_playlist(yt, "PL123", tracks)

        assert report.copy_mode_fallback is False
        assert len(report.results) == 2
        assert report.results[0].success is False
        assert report.results[1].success is True

    def test_rejected_status_is_not_success(self):
        yt = _mock_yt()
        yt.remove_playlist_items.return_value = "STATUS_FAILED"

        report = remove_from_playlist(yt, "PL123", [_track("v1", "One", set_video_id="s1")])

        assert report.results[0].success is False
        assert "STATUS_FAILED" in report.results[0].error


def _unavailable_swap(position: int, new_vid: str) -> SwapCandidate:
    return SwapCandidate(
        original=_track(None, "Unavailable", set_video_id=None, position=position),
        replacement=_track(new_vid, "Explicit"),
    )


class TestReplaceWithCopy:
    def test_original_without_video_id_still_swapped(self):
        """Unavailable originals may have no videoId; the replacement must
        not be filtered out before the swap map is applied."""
        yt = _mock_yt()

        replace_with_copy(yt, [_unavailable_swap(0, "new_vid")], [None, "a"], "Copy")

        yt.add_playlist_items.assert_called_once_with(
            "PLnew", ["new_vid", "a"], duplicates=True
        )

    def test_swaps_matched_by_position_not_video_id(self):
        """Several unavailable originals share an empty videoId; each slot
        must get its own replacement, and an unconfirmed one stays empty."""
        yt = _mock_yt()
        swaps = [_unavailable_swap(1, "A2"), _unavailable_swap(3, "B2")]

        report = replace_with_copy(yt, swaps, ["x1", None, "x2", None, None], "Copy")

        yt.add_playlist_items.assert_called_once_with(
            "PLnew", ["x1", "A2", "x2", "B2"], duplicates=True
        )
        assert all(r.success for r in report.results)

    def test_failed_replacement_add_reported(self):
        yt = _mock_yt()
        yt.add_playlist_items.side_effect = [Exception("batch failed"), OK, Exception("gone")]

        report = replace_with_copy(yt, [_unavailable_swap(1, "new_vid")], ["a", None], "Copy")

        assert report.failed_adds == ["new_vid"]
        assert report.results[0].success is False

    def test_swap_not_failed_by_same_video_elsewhere(self):
        # The replacement is also already in the playlist at position 0; only
        # that other entry fails, so the swap at position 1 still succeeded.
        yt = _mock_yt()
        yt.add_playlist_items.side_effect = [Exception("batch failed"), Exception("gone"), OK]

        report = replace_with_copy(yt, [_unavailable_swap(1, "E")], ["E", None], "Copy")

        assert report.failed_adds == ["E"]
        assert report.results[0].success is True

    def test_create_failure_response_adds_nothing(self):
        # ytmusicapi returns the raw response dict instead of an id on failure
        yt = _mock_yt()
        yt.create_playlist.return_value = {"error": "rejected"}

        report = replace_with_copy(yt, [_unavailable_swap(0, "new_vid")], [None], "Copy")

        assert report.new_playlist_id is None
        assert yt.add_playlist_items.call_count == 0


class TestCopyPlaylistWithout:
    def test_creates_playlist_and_batches(self):
        yt = _mock_yt()
        ids = [f"v{i}" for i in range(30)]

        report = copy_playlist_without(yt, ids, [], "Copy Name")

        assert report.new_playlist_id == "PLnew"
        assert report.new_playlist_title == "Copy Name"
        calls = yt.add_playlist_items.call_args_list
        assert len(calls) == 2
        assert calls[0].args == ("PLnew", ids[:25])
        assert calls[1].args == ("PLnew", ids[25:])
        assert all(c.kwargs.get("duplicates") is True for c in calls)

    def test_drops_by_position(self):
        yt = _mock_yt()
        drop = [_track("v2", "Two", position=1)]

        copy_playlist_without(yt, ["v1", "v2", "v3"], drop, "Copy")

        yt.add_playlist_items.assert_called_once_with("PLnew", ["v1", "v3"], duplicates=True)

    def test_identical_pair_drops_exactly_the_loser_slot(self):
        # Unowned playlists expose no setVideoId: the winner (position 0)
        # must keep its slot and only the loser at position 2 goes.
        yt = _mock_yt()
        drop = [_track("a", "Song", set_video_id=None, position=2)]

        copy_playlist_without(yt, ["a", "b", "a", "c"], drop, "Copy")

        yt.add_playlist_items.assert_called_once_with("PLnew", ["a", "b", "c"], duplicates=True)

    def test_identical_triple_drops_two(self):
        yt = _mock_yt()
        drop = [
            _track("a", "Song", set_video_id=None, position=1),
            _track("a", "Song", set_video_id=None, position=2),
        ]

        copy_playlist_without(yt, ["a", "a", "a"], drop, "Copy")

        yt.add_playlist_items.assert_called_once_with("PLnew", ["a"], duplicates=True)

    def test_dropped_tracks_recorded_as_results(self):
        yt = _mock_yt()
        loser = _track("v2", "Two", position=1)

        report = copy_playlist_without(yt, ["v1", "v2"], [loser], "Copy")

        assert [(r.track, r.success) for r in report.results] == [(loser, True)]

    def test_empty_ids_skipped(self):
        yt = _mock_yt()

        copy_playlist_without(yt, ["a", "", "b"], [], "Copy")

        yt.add_playlist_items.assert_called_once_with("PLnew", ["a", "b"], duplicates=True)

    def test_batch_failure_retries_individually(self):
        yt = _mock_yt()
        yt.add_playlist_items.side_effect = [Exception("batch failed"), OK, OK]

        report = copy_playlist_without(yt, ["a", "b"], [], "Copy")

        assert yt.add_playlist_items.call_count == 3
        assert yt.add_playlist_items.call_args_list[1].args == ("PLnew", ["a"])
        assert yt.add_playlist_items.call_args_list[2].args == ("PLnew", ["b"])
        assert report.failed_adds == []

    def test_rejected_batch_status_retried_and_failures_reported(self):
        yt = _mock_yt()
        yt.add_playlist_items.side_effect = [
            {"status": "STATUS_FAILED"}, OK, {"status": "STATUS_FAILED"},
        ]

        report = copy_playlist_without(yt, ["a", "b"], [], "Copy")

        assert yt.add_playlist_items.call_count == 3
        assert report.failed_adds == ["b"]

    def test_create_failure_leaves_no_results(self):
        yt = _mock_yt()
        yt.create_playlist.side_effect = Exception("HTTP 400")

        report = copy_playlist_without(yt, ["a"], [_track("a", "A", position=0)], "Copy")

        assert report.new_playlist_id is None
        assert report.results == []
