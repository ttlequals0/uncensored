from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

CLEAN_SUFFIXES = re.compile(
    r'\s*[\(\[](clean|edited|radio edit|censored|clean version|edited version)[\)\]]',
    re.IGNORECASE
)

_FEAT_SUFFIXES = re.compile(
    r'\s*[\(\[](feat\.?|ft\.?|featuring)\s+[^\)\]]+[\)\]]',
    re.IGNORECASE
)

_NON_ASCII = re.compile(r'[^\x00-\x7F]')

_LIVE_PERFORMANCE = re.compile(r'\blive\b', re.IGNORECASE)

_PUNCTUATION = re.compile(r'[^\w\s]')
_LEADING_DIGITS = re.compile(r'^\d+\s+')
_FEAT_ARTIST = re.compile(r'\s+(?:ft\.?|feat\.?|featuring)\s+.*$', re.IGNORECASE)
_PROD_TAG = re.compile(r'\s*[\[\(](?:Prod\.?|prod\.?).*?[\]\)]')

SEARCH_DELAY = 0.3
DURATION_TOLERANCE = 10
SEARCH_LIMIT = 10

LIKED_MUSIC_PLAYLIST_ID = "LM"


VIDEO_TYPE_UGC = "MUSIC_VIDEO_TYPE_UGC"


@dataclass
class TrackInfo:
    video_id: str
    set_video_id: str | None
    title: str
    artist: str
    album: str | None
    duration_seconds: int
    thumbnail_url: str | None
    ytm_link: str
    is_explicit: bool
    is_available: bool = True
    is_video: bool = False
    video_type: str | None = None


@dataclass
class SwapCandidate:
    original: TrackInfo
    replacement: TrackInfo


@dataclass
class VideoSuggestion:
    original: TrackInfo
    suggestions: list[TrackInfo]


@dataclass
class ScanResult:
    candidates: list[SwapCandidate] = field(default_factory=list)
    not_found: list[TrackInfo] = field(default_factory=list)
    skipped_no_set_id: list[TrackInfo] = field(default_factory=list)
    unavailable: list[SwapCandidate] = field(default_factory=list)
    unavailable_not_found: list[TrackInfo] = field(default_factory=list)
    unavailable_video_suggestions: list[VideoSuggestion] = field(default_factory=list)
    yt_upgrades: list[SwapCandidate] = field(default_factory=list)
    already_explicit_count: int = 0


def normalize_title(title: str) -> str:
    """Strip clean/edited suffixes, emojis, and non-ASCII characters."""
    title = CLEAN_SUFFIXES.sub('', title)
    title = _NON_ASCII.sub('', title)
    return title.strip().lower()


def normalize_title_for_comparison(title: str) -> str:
    """Strip clean, featuring, and similar suffixes for title matching."""
    return _FEAT_SUFFIXES.sub('', normalize_title(title)).strip()


def normalize_artist(name: str) -> str:
    """Lowercase and strip punctuation, emojis, and non-ASCII characters."""
    name = _NON_ASCII.sub('', name)
    return _PUNCTUATION.sub('', name).strip().lower()


def _is_live_performance(title: str) -> bool:
    return _LIVE_PERFORMANCE.search(title) is not None


def _primary_artist(name: str) -> str:
    """Extract the first/primary artist from a compound artist string.

    Works on both raw names ('Artist & Other') and normalized names
    ('artist  other') where punctuation has been stripped.
    """
    for sep in (' & ', ' and ', ' feat ', ' feat. ', ' ft ', ' ft. '):
        if sep in name:
            return name.split(sep, 1)[0].strip()
    # After normalize_artist, '&' is stripped leaving double spaces
    if '  ' in name:
        return name.split('  ', 1)[0].strip()
    return name


def extract_track_info(track: dict) -> TrackInfo:
    """Convert a raw API track dict into a TrackInfo dataclass."""
    artists = track.get("artists") or []
    artist_name = artists[0]["name"] if artists else "Unknown"

    album = track.get("album")
    album_name = album.get("name") if album else None

    thumbnails = track.get("thumbnails") or []
    thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

    video_id = track.get("videoId", "")

    return TrackInfo(
        video_id=video_id,
        set_video_id=track.get("setVideoId"),
        title=track.get("title", "Unknown"),
        artist=artist_name,
        album=album_name,
        duration_seconds=track.get("duration_seconds", 0),
        thumbnail_url=thumbnail_url,
        ytm_link=f"https://music.youtube.com/watch?v={video_id}",
        is_explicit=track.get("isExplicit", False),
        is_available=track.get("isAvailable", True),
        video_type=track.get("videoType"),
    )


def _search_with_retry(yt: YTMusic, query: str, search_filter: str = "songs") -> list[dict] | None:
    """Search YouTube Music with one retry on failure/empty results."""
    time.sleep(SEARCH_DELAY)

    for attempt in range(2):
        try:
            results = yt.search(query, filter=search_filter, limit=SEARCH_LIMIT)
        except Exception as e:
            logger.warning("Search failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(1)
                continue
            return None

        if results:
            return results

        if attempt == 0:
            logger.debug("No results, retrying search for: %s", query)
            time.sleep(1)

    return None


def _filter_and_pick_best(
    results: list[dict],
    title_normalized: str,
    artist_normalized: str,
    duration: int,
    require_explicit: bool,
) -> TrackInfo | None:
    """Filter search results by title/artist/duration and return the best match."""
    candidates = []
    for result in results:
        if require_explicit and not result.get("isExplicit", False):
            continue

        if normalize_title_for_comparison(result.get("title", "")) != title_normalized:
            continue

        result_artists = result.get("artists") or []
        result_artist_names = " ".join(a.get("name", "") for a in result_artists)
        result_artist_normalized = normalize_artist(result_artist_names)
        if artist_normalized not in result_artist_normalized:
            if _primary_artist(artist_normalized) not in result_artist_normalized:
                continue

        if abs(result.get("duration_seconds", 0) - duration) > DURATION_TOLERANCE:
            continue

        candidates.append(result)

    if not candidates:
        return None

    if require_explicit:
        key = lambda r: abs(r.get("duration_seconds", 0) - duration)
    else:
        key = lambda r: (
            0 if r.get("isExplicit", False) else 1,
            abs(r.get("duration_seconds", 0) - duration),
        )

    return extract_track_info(min(candidates, key=key))


def _find_match(
    yt: YTMusic, track: TrackInfo, require_explicit: bool = True,
    search_filter: str = "songs",
) -> TrackInfo | None:
    """Search for a matching version of the given track."""
    query = f"{normalize_title(track.title)} {track.artist}"
    logger.debug("Searching for match: %s (explicit_only=%s, filter=%s)", query, require_explicit, search_filter)

    results = _search_with_retry(yt, query, search_filter=search_filter)
    if not results:
        return None

    return _filter_and_pick_best(
        results,
        normalize_title_for_comparison(track.title),
        normalize_artist(track.artist),
        track.duration_seconds,
        require_explicit,
    )


def _parse_title_for_artist(title: str) -> tuple[str, str] | None:
    """Try to extract artist and song from a video title like 'Artist - Song Title'.

    Handles common YouTube upload patterns:
      "Meek Mill ft. Red Cafe - I'm Killin Em (Flamers 3)"
      "Drake ft. Lil Wayne - Ransom"
      "Bone Crusher - Never Scared (Dirty Version)"
      "04 Travis Porter - Make It Rain [Prod. By Fki]"
    """
    if " - " not in title:
        return None

    raw_artist, raw_song = title.split(" - ", 1)
    raw_artist = raw_artist.strip()
    raw_song = raw_song.strip()

    if not raw_artist or not raw_song:
        return None

    raw_artist = _LEADING_DIGITS.sub('', raw_artist)
    raw_artist = _FEAT_ARTIST.sub('', raw_artist)
    raw_song = _PROD_TAG.sub('', raw_song).strip()

    return raw_artist, raw_song


def _find_match_with_title_fallback(
    yt: YTMusic, track: TrackInfo, require_explicit: bool,
    search_filter: str = "songs",
) -> TrackInfo | None:
    """Try normal match first, then fall back to parsing artist from the video title."""
    match = _find_match(yt, track, require_explicit, search_filter=search_filter)
    if match:
        return match

    parsed = _parse_title_for_artist(track.title)
    if not parsed:
        return None

    parsed_artist, parsed_song = parsed
    logger.debug(
        "Retrying with parsed title: artist='%s', song='%s'",
        parsed_artist, parsed_song,
    )

    query = f"{normalize_title(parsed_song)} {parsed_artist}"
    results = _search_with_retry(yt, query, search_filter=search_filter)
    if not results:
        return None

    return _filter_and_pick_best(
        results,
        normalize_title_for_comparison(parsed_song),
        normalize_artist(parsed_artist),
        track.duration_seconds,
        require_explicit,
    )


def _tag_as_video(track: TrackInfo) -> TrackInfo:
    track.is_video = True
    track.ytm_link = f"https://www.youtube.com/watch?v={track.video_id}"
    return track


def _find_video_match(yt: YTMusic, track: TrackInfo) -> TrackInfo | None:
    """Search for a YouTube video version of the given track.

    Used as a fallback when no YouTube Music song match is found.
    Video results lack isExplicit, so we skip that requirement.
    """
    match = _find_match_with_title_fallback(
        yt, track, require_explicit=False, search_filter="videos",
    )
    if match:
        match = _tag_as_video(match)
    return match


def find_explicit_match(yt: YTMusic, track: TrackInfo) -> TrackInfo | None:
    """Search for an explicit version of the given track."""
    return _find_match_with_title_fallback(yt, track, require_explicit=True)


def find_available_match(
    yt: YTMusic, track: TrackInfo, allow_video_fallback: bool = False,
) -> TrackInfo | None:
    """Search for any available version of an unavailable track (explicit preferred).

    When allow_video_fallback is True, falls back to YouTube video search
    if no YTM song match is found.
    """
    match = _find_match_with_title_fallback(yt, track, require_explicit=False)
    if match:
        return match
    if allow_video_fallback:
        return _find_video_match(yt, track)
    return None


def find_video_suggestions(yt: YTMusic, track: TrackInfo, limit: int = 2) -> list[TrackInfo]:
    """Search YouTube videos and return top N results without strict filtering.

    Used for unavailable tracks when strict video matching fails.
    Results are tagged with is_video=True and point to youtube.com.
    """
    query = f"{normalize_title(track.title)} {track.artist}"
    results = _search_with_retry(yt, query, search_filter="videos")
    if not results:
        return []
    suggestions = []
    for r in results:
        result_title = r.get("title", "")
        if _is_live_performance(result_title) and not _is_live_performance(track.title):
            continue
        suggestions.append(_tag_as_video(extract_track_info(r)))
        if len(suggestions) >= limit:
            break
    return suggestions


def find_ytm_upgrade(yt: YTMusic, track: TrackInfo, require_explicit: bool = False) -> TrackInfo | None:
    """Search for a YTM song to replace a YouTube video (UGC) track.

    When require_explicit is False, prefers explicit results but accepts
    non-explicit YTM songs too. When True, only returns explicit matches
    to avoid downgrading explicit UGC tracks.
    """
    match = _find_match_with_title_fallback(yt, track, require_explicit=require_explicit)
    if match and match.video_id == track.video_id:
        return None
    return match


def scan_playlist(
    yt: YTMusic,
    tracks: list[dict],
    progress_callback: Callable | None = None,
    allow_video_fallback: bool = False,
) -> ScanResult:
    """Scan pre-fetched tracks and find explicit replacements for clean ones."""
    logger.info("Scanning %d tracks", len(tracks))
    result = ScanResult()
    total = len(tracks)

    for i, raw_track in enumerate(tracks):
        track = extract_track_info(raw_track)
        logger.debug("[%d/%d] Processing: %s - %s", i + 1, total, track.artist, track.title)

        # Handle unavailable tracks -- find any working replacement
        if not track.is_available:
            if progress_callback:
                progress_callback(i + 1, total, track, "unavailable")

            match = find_available_match(yt, track, allow_video_fallback=allow_video_fallback)
            if match:
                result.unavailable.append(SwapCandidate(original=track, replacement=match))
                logger.info(
                    "Found replacement for unavailable track: '%s' by %s",
                    track.title, track.artist,
                )
                continue

            if allow_video_fallback:
                video_sug = find_video_suggestions(yt, track)
                if video_sug:
                    result.unavailable_video_suggestions.append(
                        VideoSuggestion(original=track, suggestions=video_sug)
                    )
                    logger.info(
                        "Found %d video suggestion(s) for unavailable: '%s' by %s",
                        len(video_sug), track.title, track.artist,
                    )
                    continue

            result.unavailable_not_found.append(track)
            logger.info("No replacement found for unavailable: '%s' by %s", track.title, track.artist)
            continue

        # Explicit non-UGC tracks need no changes. Explicit UGC tracks
        # fall through to the upgrade block below.
        if track.is_explicit and track.video_type != VIDEO_TYPE_UGC:
            result.already_explicit_count += 1
            if progress_callback:
                progress_callback(i + 1, total, track, "explicit")
            continue

        if track.set_video_id is None:
            logger.warning(
                "Track '%s' by %s has no setVideoId -- cannot be removed. Skipping.",
                track.title, track.artist,
            )
            result.skipped_no_set_id.append(track)
            if progress_callback:
                progress_callback(i + 1, total, track, "skipped")
            continue

        if track.video_type == VIDEO_TYPE_UGC:
            if progress_callback:
                progress_callback(i + 1, total, track, "yt_upgrade")

            upgrade = find_ytm_upgrade(yt, track, require_explicit=track.is_explicit)
            if upgrade:
                result.yt_upgrades.append(SwapCandidate(original=track, replacement=upgrade))
                logger.info(
                    "Found YTM upgrade for UGC track: '%s' by %s -> '%s' by %s",
                    track.title, track.artist, upgrade.title, upgrade.artist,
                )
                continue

            if track.is_explicit:
                result.already_explicit_count += 1
            else:
                result.not_found.append(track)
                logger.info("No YTM version found for UGC track: '%s' by %s", track.title, track.artist)
            continue

        if progress_callback:
            progress_callback(i + 1, total, track, "searching")

        match = find_explicit_match(yt, track)
        if match:
            result.candidates.append(SwapCandidate(original=track, replacement=match))
            logger.info(
                "Found explicit match: '%s' by %s -> '%s' by %s",
                track.title, track.artist, match.title, match.artist,
            )
        else:
            result.not_found.append(track)
            logger.info("No explicit version found for: '%s' by %s", track.title, track.artist)

    return result
