"""Tests for scanner module -- title normalization, artist matching, and track extraction."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner import (
    CLEAN_SUFFIXES,
    VIDEO_TYPE_UGC,
    TrackInfo,
    _find_video_match,
    _parse_title_for_artist,
    _primary_artist,
    extract_track_info,
    find_available_match,
    find_explicit_match,
    find_ytm_upgrade,
    normalize_artist,
    normalize_title,
    normalize_title_for_comparison,
)


class TestNormalizeTitle:
    def test_strips_clean_suffix_parens(self):
        assert normalize_title("Blinding Lights (Clean)") == "blinding lights"

    def test_strips_clean_suffix_brackets(self):
        assert normalize_title("Blinding Lights [Clean]") == "blinding lights"

    def test_strips_edited_suffix(self):
        assert normalize_title("HUMBLE. (Edited)") == "humble."

    def test_strips_radio_edit(self):
        assert normalize_title("Song Title (Radio Edit)") == "song title"

    def test_strips_censored_suffix(self):
        assert normalize_title("Bad Guy [Censored]") == "bad guy"

    def test_strips_clean_version(self):
        assert normalize_title("WAP (Clean Version)") == "wap"

    def test_strips_edited_version(self):
        assert normalize_title("Song [Edited Version]") == "song"

    def test_case_insensitive(self):
        assert normalize_title("Song (CLEAN)") == "song"
        assert normalize_title("Song (clean)") == "song"
        assert normalize_title("Song (Clean)") == "song"

    def test_no_suffix_unchanged(self):
        assert normalize_title("Normal Song Title") == "normal song title"

    def test_preserves_other_parens(self):
        assert normalize_title("Song (feat. Artist)") == "song (feat. artist)"

    def test_empty_string(self):
        assert normalize_title("") == ""

    def test_only_suffix(self):
        assert normalize_title("(Clean)") == ""

    def test_multiple_suffixes_strips_first(self):
        # Only the clean suffix pattern is stripped, other parens remain
        result = normalize_title("Song (feat. X) (Clean)")
        assert result == "song (feat. x)"


class TestNormalizeTitleForComparison:
    def test_strips_feat_parens(self):
        assert normalize_title_for_comparison("Play For Keeps (feat. Rondonumbanine)") == "play for keeps"

    def test_strips_ft_parens(self):
        assert normalize_title_for_comparison("Song (ft. Artist)") == "song"

    def test_strips_featuring_brackets(self):
        assert normalize_title_for_comparison("Song [featuring Artist]") == "song"

    def test_strips_feat_dot_parens(self):
        assert normalize_title_for_comparison("Song (feat Artist Name)") == "song"

    def test_strips_clean_and_feat(self):
        assert normalize_title_for_comparison("Song (feat. X) (Clean)") == "song"

    def test_preserves_non_feat_parens(self):
        assert normalize_title_for_comparison("Song (Remix)") == "song (remix)"

    def test_no_feat_unchanged(self):
        assert normalize_title_for_comparison("Normal Song") == "normal song"

    def test_empty_string(self):
        assert normalize_title_for_comparison("") == ""


class TestNormalizeArtist:
    def test_basic(self):
        assert normalize_artist("The Weeknd") == "the weeknd"

    def test_strips_punctuation(self):
        assert normalize_artist("P!nk") == "pnk"

    def test_featured_artist(self):
        normalized = normalize_artist("Post Malone ft. Swae Lee")
        assert "post malone" in normalized

    def test_empty(self):
        assert normalize_artist("") == ""


class TestExtractTrackInfo:
    def test_basic_track(self):
        raw = {
            "videoId": "abc123",
            "setVideoId": "set456",
            "title": "Test Song",
            "artists": [{"name": "Test Artist", "id": "a1"}],
            "album": {"name": "Test Album", "id": "b1"},
            "duration_seconds": 200,
            "thumbnails": [
                {"url": "http://small.jpg", "width": 60, "height": 60},
                {"url": "http://large.jpg", "width": 226, "height": 226},
            ],
            "isExplicit": True,
        }
        track = extract_track_info(raw)

        assert track.video_id == "abc123"
        assert track.set_video_id == "set456"
        assert track.title == "Test Song"
        assert track.artist == "Test Artist"
        assert track.album == "Test Album"
        assert track.duration_seconds == 200
        assert track.thumbnail_url == "http://large.jpg"
        assert track.ytm_link == "https://music.youtube.com/watch?v=abc123"
        assert track.is_explicit is True

    def test_missing_optional_fields(self):
        raw = {
            "videoId": "xyz",
            "title": "Minimal",
            "artists": [],
            "duration_seconds": 100,
            "isExplicit": False,
        }
        track = extract_track_info(raw)

        assert track.artist == "Unknown"
        assert track.album is None
        assert track.thumbnail_url is None
        assert track.set_video_id is None
        assert track.is_explicit is False

    def test_missing_explicit_defaults_false(self):
        raw = {
            "videoId": "v1",
            "title": "Song",
            "artists": [{"name": "A"}],
            "duration_seconds": 180,
        }
        track = extract_track_info(raw)
        assert track.is_explicit is False

    def test_multiple_artists_uses_first(self):
        raw = {
            "videoId": "v1",
            "title": "Collab",
            "artists": [{"name": "First"}, {"name": "Second"}],
            "duration_seconds": 200,
        }
        track = extract_track_info(raw)
        assert track.artist == "First"


class TestCleanSuffixesPattern:
    """Verify the regex pattern matches expected suffixes."""

    @pytest.mark.parametrize("suffix", [
        "(Clean)", "[Clean]", "(Edited)", "[Edited]",
        "(Radio Edit)", "[Radio Edit]",
        "(Censored)", "[Censored]",
        "(Clean Version)", "[Clean Version]",
        "(Edited Version)", "[Edited Version]",
    ])
    def test_matches_known_suffixes(self, suffix):
        assert CLEAN_SUFFIXES.search(f"Song {suffix}") is not None

    @pytest.mark.parametrize("suffix", [
        "(feat. Artist)", "(Remix)", "(Live)", "(Deluxe)",
        "(Acoustic)", "[Remastered]",
    ])
    def test_does_not_match_other_suffixes(self, suffix):
        assert CLEAN_SUFFIXES.search(f"Song {suffix}") is None


class TestParseTitleForArtist:
    def test_simple_artist_dash_song(self):
        assert _parse_title_for_artist("Drake - Ransom") == ("Drake", "Ransom")

    def test_artist_with_feat_stripped(self):
        result = _parse_title_for_artist("Meek Mill ft. Red Cafe - I'm Killin Em (Flamers 3)")
        assert result is not None
        assert result[0] == "Meek Mill"
        assert result[1] == "I'm Killin Em (Flamers 3)"

    def test_artist_with_featuring_stripped(self):
        result = _parse_title_for_artist("Drake featuring Lil Wayne - Ransom")
        assert result is not None
        assert result[0] == "Drake"

    def test_artist_with_feat_dot_stripped(self):
        result = _parse_title_for_artist("Soulja Boy feat. Lil Wayne - Turn My Swag On")
        assert result is not None
        assert result[0] == "Soulja Boy"

    def test_leading_track_number_stripped(self):
        result = _parse_title_for_artist("04 Travis Porter - Make It Rain [Prod. By Fki]")
        assert result is not None
        assert result[0] == "Travis Porter"
        assert result[1] == "Make It Rain"

    def test_prod_tag_stripped_from_song(self):
        result = _parse_title_for_artist("Tech N9ne - Promiseland (Prod by. Wyshmaster)")
        assert result is not None
        assert result[1] == "Promiseland"

    def test_no_dash_returns_none(self):
        assert _parse_title_for_artist("Just A Song Title") is None

    def test_empty_parts_returns_none(self):
        assert _parse_title_for_artist(" - Song") is None
        assert _parse_title_for_artist("Artist - ") is None

    def test_multiple_dashes_splits_on_first(self):
        result = _parse_title_for_artist("Artist - Song - Part 2")
        assert result == ("Artist", "Song - Part 2")

    def test_real_youtube_upload_title(self):
        result = _parse_title_for_artist("Bone Crusher - Never Scared (Dirty Version)")
        assert result == ("Bone Crusher", "Never Scared (Dirty Version)")


class TestTrackInfoNewFields:
    def test_is_video_defaults_false(self):
        raw = {
            "videoId": "v1",
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "duration_seconds": 200,
        }
        track = extract_track_info(raw)
        assert track.is_video is False

    def test_video_type_extracted(self):
        raw = {
            "videoId": "v1",
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "duration_seconds": 200,
            "videoType": "MUSIC_VIDEO_TYPE_UGC",
        }
        track = extract_track_info(raw)
        assert track.video_type == "MUSIC_VIDEO_TYPE_UGC"

    def test_video_type_none_when_absent(self):
        raw = {
            "videoId": "v1",
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "duration_seconds": 200,
        }
        track = extract_track_info(raw)
        assert track.video_type is None


def _make_track(title="Test Song", artist="Test Artist", duration=200,
                video_id="orig1", is_explicit=False, video_type=None):
    return TrackInfo(
        video_id=video_id,
        set_video_id="set1",
        title=title,
        artist=artist,
        album=None,
        duration_seconds=duration,
        thumbnail_url=None,
        ytm_link=f"https://music.youtube.com/watch?v={video_id}",
        is_explicit=is_explicit,
        video_type=video_type,
    )


def _make_search_result(title="Test Song", artist="Test Artist", duration=200,
                        video_id="match1", is_explicit=True):
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist}],
        "duration_seconds": duration,
        "isExplicit": is_explicit,
        "thumbnails": [],
    }


class TestFindVideoMatch:
    @patch("scanner._find_match_with_title_fallback")
    def test_returns_track_with_is_video_true(self, mock_fallback):
        song_match = _make_track(video_id="vid1")
        mock_fallback.return_value = song_match
        yt = MagicMock()
        track = _make_track()

        result = _find_video_match(yt, track)

        assert result is not None
        assert result.is_video is True
        assert result.video_id == "vid1"
        mock_fallback.assert_called_once_with(
            yt, track, require_explicit=False, search_filter="videos",
        )

    @patch("scanner._find_match_with_title_fallback")
    def test_ytm_link_points_to_youtube(self, mock_fallback):
        song_match = _make_track(video_id="vid1")
        mock_fallback.return_value = song_match
        yt = MagicMock()
        track = _make_track()

        result = _find_video_match(yt, track)

        assert result is not None
        assert result.ytm_link == "https://www.youtube.com/watch?v=vid1"

    @patch("scanner._find_match_with_title_fallback")
    def test_returns_none_when_no_results(self, mock_fallback):
        mock_fallback.return_value = None
        yt = MagicMock()
        track = _make_track()

        result = _find_video_match(yt, track)

        assert result is None


class TestFindExplicitMatch:
    @patch("scanner._find_match_with_title_fallback")
    def test_returns_match(self, mock_fallback):
        song_match = _make_track(video_id="song1", is_explicit=True)
        mock_fallback.return_value = song_match
        yt = MagicMock()
        track = _make_track()

        result = find_explicit_match(yt, track)

        assert result is song_match
        mock_fallback.assert_called_once_with(yt, track, require_explicit=True)

    @patch("scanner._find_match_with_title_fallback")
    def test_returns_none_when_no_match(self, mock_fallback):
        mock_fallback.return_value = None
        yt = MagicMock()
        track = _make_track()

        result = find_explicit_match(yt, track)

        assert result is None


class TestFindYtmUpgrade:
    @patch("scanner._find_match_with_title_fallback")
    def test_finds_ytm_song_for_ugc_track(self, mock_match):
        ytm_match = _make_track(video_id="ytm1", is_explicit=True)
        mock_match.return_value = ytm_match
        yt = MagicMock()
        track = _make_track(video_type=VIDEO_TYPE_UGC)

        result = find_ytm_upgrade(yt, track)

        assert result is ytm_match
        mock_match.assert_called_once_with(yt, track, require_explicit=False)

    @patch("scanner._find_match_with_title_fallback")
    def test_require_explicit_passed_through(self, mock_match):
        ytm_match = _make_track(video_id="ytm1", is_explicit=True)
        mock_match.return_value = ytm_match
        yt = MagicMock()
        track = _make_track(video_type=VIDEO_TYPE_UGC, is_explicit=True)

        result = find_ytm_upgrade(yt, track, require_explicit=True)

        assert result is ytm_match
        mock_match.assert_called_once_with(yt, track, require_explicit=True)

    @patch("scanner._find_match_with_title_fallback")
    def test_returns_none_when_no_ytm_version(self, mock_match):
        mock_match.return_value = None
        yt = MagicMock()
        track = _make_track(video_type=VIDEO_TYPE_UGC)

        result = find_ytm_upgrade(yt, track)

        assert result is None


class TestPrimaryArtist:
    def test_splits_on_ampersand(self):
        assert _primary_artist("Artist A & Artist B") == "Artist A"

    def test_splits_on_and(self):
        assert _primary_artist("Artist A and Artist B") == "Artist A"

    def test_splits_on_feat(self):
        assert _primary_artist("Artist A feat Artist B") == "Artist A"

    def test_splits_on_ft_dot(self):
        assert _primary_artist("Artist A ft. Artist B") == "Artist A"

    def test_x_collab_not_split_to_avoid_false_positives(self):
        # "x" as separator is too ambiguous (matches "dex", "flex", etc.)
        # These are handled by the double-space fallback after normalization
        assert _primary_artist("Artist A x Artist B") == "Artist A x Artist B"

    def test_no_separator_returns_full(self):
        assert _primary_artist("Solo Artist") == "Solo Artist"

    def test_normalized_double_space(self):
        normed = normalize_artist("Rico Recklezz & DJ Milticket")
        assert _primary_artist(normed) == "rico recklezz"

    def test_normalized_single_artist(self):
        normed = normalize_artist("Tee Grizzley")
        assert _primary_artist(normed) == "tee grizzley"


class TestFindAvailableMatchVideoFlag:
    @patch("scanner._find_video_match")
    @patch("scanner._find_match_with_title_fallback")
    def test_no_video_fallback_by_default(self, mock_song, mock_video):
        mock_song.return_value = None
        yt = MagicMock()
        track = _make_track()

        result = find_available_match(yt, track)

        assert result is None
        mock_video.assert_not_called()

    @patch("scanner._find_video_match")
    @patch("scanner._find_match_with_title_fallback")
    def test_video_fallback_when_enabled(self, mock_song, mock_video):
        mock_song.return_value = None
        video_match = _make_track(video_id="vid1")
        video_match.is_video = True
        mock_video.return_value = video_match
        yt = MagicMock()
        track = _make_track()

        result = find_available_match(yt, track, allow_video_fallback=True)

        assert result is video_match
        assert result.is_video is True
