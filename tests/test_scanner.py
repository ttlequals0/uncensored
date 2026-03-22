"""Tests for scanner module -- title normalization, artist matching, and track extraction."""

import sys
from pathlib import Path

import pytest

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner import (
    CLEAN_SUFFIXES,
    TrackInfo,
    _parse_title_for_artist,
    extract_track_info,
    normalize_artist,
    normalize_title,
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
