"""Tests for duplicate detection and winner selection (pure logic, no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner import (
    VIDEO_TYPE_UGC,
    TrackInfo,
    _dedupe_winner,
    _is_duplicate_pair,
    dedupe_key,
    dedupe_playlist,
    find_duplicates,
)
from uncensored import _kept_video_ids


def make_track(
    video_id="vid1", set_video_id="sv1", title="Song", artist="Artist",
    duration=200, is_explicit=False, is_available=True, video_type=None,
):
    return TrackInfo(
        video_id=video_id,
        set_video_id=set_video_id,
        title=title,
        artist=artist,
        album=None,
        duration_seconds=duration,
        thumbnail_url=None,
        ytm_link=f"https://music.youtube.com/watch?v={video_id}",
        is_explicit=is_explicit,
        is_available=is_available,
        video_type=video_type,
    )


class TestDedupeKey:
    def test_clean_and_explicit_share_key(self):
        assert dedupe_key("Song (Clean)", "Artist") == dedupe_key("Song", "Artist")

    def test_feat_suffix_stripped(self):
        assert dedupe_key("Song (feat. Guest)", "Artist") == dedupe_key("Song", "Artist")

    def test_primary_artist_used(self):
        assert dedupe_key("Song", "A & B") == dedupe_key("Song", "A")

    def test_different_artists_differ(self):
        assert dedupe_key("Song", "Artist A") != dedupe_key("Song", "Artist B")

    def test_live_version_kept_distinct(self):
        assert dedupe_key("Song (Live)", "Artist") != dedupe_key("Song", "Artist")


class TestIsDuplicatePair:
    def test_identical_video_id_groups(self):
        a = make_track(video_id="x", duration=0)
        b = make_track(video_id="x", set_video_id="sv2", duration=0)
        assert _is_duplicate_pair(a, b)

    def test_identical_video_id_groups_despite_duration_gap(self):
        a = make_track(video_id="x", duration=100)
        b = make_track(video_id="x", set_video_id="sv2", duration=500)
        assert _is_duplicate_pair(a, b)

    def test_duration_delta_10_groups(self):
        assert _is_duplicate_pair(make_track(duration=100), make_track(duration=110))

    def test_duration_delta_11_does_not_group(self):
        a = make_track(video_id="a", duration=100)
        b = make_track(video_id="b", set_video_id="sv2", duration=111)
        assert not _is_duplicate_pair(a, b)

    def test_unknown_duration_different_ids_do_not_group(self):
        a = make_track(video_id="a", duration=0)
        b = make_track(video_id="b", set_video_id="sv2", duration=0)
        assert not _is_duplicate_pair(a, b)


class TestFindDuplicates:
    def test_singleton_no_group(self):
        assert find_duplicates([make_track()]) == []

    def test_clean_explicit_pair_groups(self):
        tracks = [
            make_track(video_id="c", set_video_id="s1", title="Song (Clean)"),
            make_track(video_id="e", set_video_id="s2", title="Song", is_explicit=True),
        ]
        groups = find_duplicates(tracks)
        assert len(groups) == 1
        assert len(groups[0].losers) == 1

    def test_anchor_clustering_pinned(self):
        # 116s is >10s from the 100s anchor: it must NOT join via the 108s member
        tracks = [
            make_track(video_id="a", set_video_id="s1", duration=100),
            make_track(video_id="b", set_video_id="s2", duration=108),
            make_track(video_id="c", set_video_id="s3", duration=116),
        ]
        groups = find_duplicates(tracks)
        assert len(groups) == 1
        assert [t.video_id for t in groups[0].tracks] == ["a", "b"]

    def test_losers_keep_playlist_order(self):
        tracks = [
            make_track(video_id="w", set_video_id="s1", title="Song", is_explicit=True),
            make_track(video_id="l1", set_video_id="s2", title="Song (Clean)"),
            make_track(video_id="l2", set_video_id="s3", title="Song (Edited)"),
        ]
        groups = find_duplicates(tracks)
        assert [t.video_id for t in groups[0].losers] == ["l1", "l2"]


class TestWinner:
    def test_explicit_beats_clean(self):
        clean = make_track(video_id="c", title="Song (Clean)")
        explicit = make_track(video_id="e", title="Song", is_explicit=True)
        assert _dedupe_winner([clean, explicit]).video_id == "e"

    def test_official_beats_ugc(self):
        ugc = make_track(video_id="u", video_type=VIDEO_TYPE_UGC)
        official = make_track(video_id="o")
        assert _dedupe_winner([ugc, official]).video_id == "o"

    def test_available_beats_unavailable(self):
        gone = make_track(video_id="g", is_available=False, is_explicit=True)
        alive = make_track(video_id="a", is_available=True)
        assert _dedupe_winner([gone, alive]).video_id == "a"

    def test_earliest_position_wins_ties(self):
        a = make_track(video_id="a", set_video_id="s1")
        b = make_track(video_id="b", set_video_id="s2")
        assert _dedupe_winner([a, b]).video_id == "a"

    def test_precedence_order(self):
        # unavailable explicit official loses to available explicit official
        gone = make_track(video_id="g", is_available=False, is_explicit=True)
        alive = make_track(video_id="a", is_available=True, is_explicit=True)
        assert _dedupe_winner([gone, alive]).video_id == "a"

    def test_official_clean_beats_ugc_explicit(self):
        # official > UGC outranks explicit > clean
        ugc_explicit = make_track(video_id="u", is_explicit=True, video_type=VIDEO_TYPE_UGC)
        official_clean = make_track(video_id="o", is_explicit=False)
        assert _dedupe_winner([ugc_explicit, official_clean]).video_id == "o"


def raw(video_id, set_video_id, title="Song", artist="Artist", duration=200, is_explicit=False):
    track = {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist}],
        "duration_seconds": duration,
        "isExplicit": is_explicit,
    }
    if set_video_id is not None:
        track["setVideoId"] = set_video_id
    return track


class TestDedupePlaylist:
    def test_no_duplicates_empty_result(self):
        result = dedupe_playlist([raw("a", "s1", title="One"), raw("b", "s2", title="Two")])
        assert result.groups == []
        assert result.total_tracks == 2

    def test_missing_set_video_id_goes_to_skipped(self):
        result = dedupe_playlist([
            raw("w", "s1", title="Song", is_explicit=True),
            raw("l", None, title="Song (Clean)"),
        ])
        assert len(result.groups) == 1
        # Unremovable losers stay in the group: copy mode can still drop them.
        assert [t.video_id for t in result.groups[0].losers] == ["l"]
        assert len(result.skipped_no_set_id) == 1

    def test_winner_choosen_from_full_group_including_unremovable(self):
        # The winner is picked before the setVideoId filter, so an
        # unremovable explicit copy still beats a removable clean copy.
        result = dedupe_playlist([
            raw("clean", "s1", title="Song (Clean)"),
            raw("explicit", None, title="Song", is_explicit=True),
        ])
        assert result.groups[0].winner.video_id == "explicit"
        assert [t.video_id for t in result.groups[0].losers] == ["clean"]


class TestKeptVideoIds:
    def test_excludes_by_set_video_id(self):
        all_tracks = [raw("v1", "s1"), raw("v2", "s2")]
        exclude = [make_track(video_id="v2", set_video_id="s2")]
        assert _kept_video_ids(all_tracks, exclude) == ["v1"]

    def test_unmarked_identical_pair_drops_one(self):
        # Unowned playlists expose no setVideoId at all: positional drop.
        all_tracks = [{"videoId": "a"}, {"videoId": "a"}, {"videoId": "b"}]
        exclude = [make_track(video_id="a", set_video_id=None)]
        assert _kept_video_ids(all_tracks, exclude) == ["a", "b"]

    def test_unmarked_triple_drops_two(self):
        all_tracks = [{"videoId": "a"}] * 3
        exclude = [
            make_track(video_id="a", set_video_id=None),
            make_track(video_id="a", set_video_id="svx", title="Other"),
        ]
        # The svid-marked loser matches nothing (no entry has setVideoId),
        # so only one positional drop happens.
        assert _kept_video_ids(all_tracks, exclude) == ["a", "a"]

    def test_marked_entry_survives_unmarked_exclusion(self):
        all_tracks = [{"videoId": "a", "setVideoId": "s9"}, {"videoId": "a"}]
        exclude = [make_track(video_id="a", set_video_id=None)]
        assert _kept_video_ids(all_tracks, exclude) == ["a"]
