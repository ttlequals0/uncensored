from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ytmusicapi import YTMusic

from scanner import SwapCandidate, TrackInfo

logger = logging.getLogger(__name__)

MUTATION_DELAY = 1


@dataclass
class SwapResult:
    candidate: SwapCandidate
    success: bool
    error: str | None = None
    duplicate_warning: bool = False


@dataclass
class ReplacementReport:
    results: list[SwapResult] = field(default_factory=list)
    copy_mode_fallback: bool = False
    new_playlist_id: str | None = None
    new_playlist_title: str | None = None


@dataclass
class RemovalResult:
    track: TrackInfo
    success: bool
    error: str | None = None


@dataclass
class RemovalReport:
    results: list[RemovalResult] = field(default_factory=list)
    copy_mode_fallback: bool = False
    new_playlist_id: str | None = None
    new_playlist_title: str | None = None


def _extract_set_video_id(add_response) -> str | None:
    """Extract the setVideoId from an add_playlist_items response."""
    if not isinstance(add_response, dict):
        return None
    for result in add_response.get("playlistEditResults") or []:
        if result and "setVideoId" in result:
            return result["setVideoId"]
    return None


def _move_before_original(
    yt: YTMusic,
    playlist_id: str,
    add_response,
    original_svid: str | None,
) -> None:
    """Move the just-added track to sit immediately before the original.

    Swallows errors because the replacement was already added successfully;
    a failed reorder shouldn't fail the whole swap.
    """
    if not original_svid:
        return
    new_svid = _extract_set_video_id(add_response)
    if not new_svid:
        return
    try:
        yt.edit_playlist(playlist_id, moveItem=(new_svid, original_svid))
        logger.info("Moved replacement before original")
        time.sleep(MUTATION_DELAY)
    except Exception as e:
        logger.debug("Move failed (non-fatal): %s", e)


def replace_in_place(
    yt: YTMusic,
    playlist_id: str,
    confirmed: list[SwapCandidate],
    preserve_position: bool = False,
) -> ReplacementReport:
    """Replace tracks in the original playlist.

    When preserve_position is True, replacements are moved to sit
    immediately before the original track. This flips the playlist's
    server-side "Ordering" to Manual in YouTube Music. Default False:
    replacements are appended, which preserves the playlist's
    Recently-added default sort.
    """
    report = ReplacementReport()

    for swap in confirmed:
        result = SwapResult(candidate=swap, success=False)

        try:
            add_response = yt.add_playlist_items(playlist_id, [swap.replacement.video_id])
            logger.info("Added: '%s' by %s", swap.replacement.title, swap.replacement.artist)
        except Exception as e:
            result.error = f"Failed to add replacement: {e}"
            logger.error(result.error)
            report.results.append(result)
            continue

        time.sleep(MUTATION_DELAY)

        if preserve_position:
            _move_before_original(yt, playlist_id, add_response, swap.original.set_video_id)

        if swap.original.set_video_id is None:
            logger.warning(
                "Cannot remove '%s' by %s (no setVideoId) -- replacement added but original remains",
                swap.original.title, swap.original.artist,
            )
            result.success = True
            result.duplicate_warning = True
            report.results.append(result)
            time.sleep(MUTATION_DELAY)
            continue

        try:
            yt.remove_playlist_items(
                playlist_id,
                [{"videoId": swap.original.video_id, "setVideoId": swap.original.set_video_id}],
            )
            logger.info("Removed: '%s' by %s", swap.original.title, swap.original.artist)
            result.success = True
        except Exception as e:
            error_str = str(e).lower()
            if "unauthorized" in error_str or "forbidden" in error_str or "403" in error_str:
                logger.warning("Cannot modify playlist -- you may not own it. Falling back to copy mode.")
                report.copy_mode_fallback = True
                result.error = "Playlist not owned by user"
                report.results.append(result)
                return report

            result.error = f"Failed to remove original (duplicate may exist): {e}"
            result.duplicate_warning = True
            logger.warning(result.error)

        report.results.append(result)
        time.sleep(MUTATION_DELAY)

    return report


def replace_with_copy(
    yt: YTMusic,
    confirmed: list[SwapCandidate],
    all_track_video_ids: list[str],
    copy_name: str,
) -> ReplacementReport:
    """Create a new playlist with replacements applied."""
    report = ReplacementReport()

    try:
        new_playlist_id = yt.create_playlist(
            copy_name,
            description="Created by uncensored",
        )
        report.new_playlist_id = new_playlist_id
        report.new_playlist_title = copy_name
        logger.info("Created new playlist: %s (%s)", copy_name, new_playlist_id)
    except Exception as e:
        logger.error("Failed to create new playlist: %s", e)
        return report

    replacement_map = {swap.original.video_id: swap.replacement.video_id for swap in confirmed}

    # Map before filtering empty ids: an unavailable original may carry no
    # videoId at all, and its replacement must survive into the copy.
    final_video_ids = [
        vid
        for vid in (replacement_map.get(orig, orig) for orig in all_track_video_ids)
        if vid
    ]

    failed_video_ids = _add_in_batches(yt, new_playlist_id, final_video_ids)

    for swap in confirmed:
        success = swap.replacement.video_id not in failed_video_ids
        error = "Batch add failed for track" if not success else None
        report.results.append(SwapResult(candidate=swap, success=success, error=error))

    return report


def _add_in_batches(yt: YTMusic, playlist_id: str, video_ids: list[str]) -> set[str]:
    """Add video ids in batches of 25, retrying failed batches individually.

    Returns the video ids that failed even after individual retry.
    """
    ids = [vid for vid in video_ids if vid]
    failed_video_ids: set[str] = set()
    batch_size = 25
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        try:
            yt.add_playlist_items(playlist_id, batch, duplicates=True)
            logger.info("Added batch %d-%d to new playlist", i + 1, i + len(batch))
        except Exception:
            logger.info("Batch %d-%d failed, retrying individually", i + 1, i + len(batch))
            for vid in batch:
                try:
                    yt.add_playlist_items(playlist_id, [vid], duplicates=True)
                except Exception as e2:
                    logger.debug("Single add failed for %s: %s", vid, e2)
                    failed_video_ids.add(vid)
                time.sleep(MUTATION_DELAY)
            continue
        time.sleep(MUTATION_DELAY)
    return failed_video_ids


def remove_from_playlist(
    yt: YTMusic,
    playlist_id: str,
    tracks: list[TrackInfo],
) -> RemovalReport:
    """Remove playlist entries by (videoId, setVideoId) pair.

    One call per track so a single failure doesn't take the batch with it.
    Caller must filter out tracks without set_video_id beforehand.
    """
    report = RemovalReport()

    for i, track in enumerate(tracks):
        if i > 0:
            time.sleep(MUTATION_DELAY)

        result = RemovalResult(track=track, success=False)
        try:
            yt.remove_playlist_items(
                playlist_id,
                [{"videoId": track.video_id, "setVideoId": track.set_video_id}],
            )
            logger.info("Removed: '%s' by %s", track.title, track.artist)
            result.success = True
        except Exception as e:
            error_str = str(e).lower()
            if "unauthorized" in error_str or "forbidden" in error_str or "403" in error_str:
                logger.warning("Cannot modify playlist -- you may not own it. Falling back to copy mode.")
                report.copy_mode_fallback = True
                result.error = "Playlist not owned by user"
                report.results.append(result)
                return report

            result.error = f"Failed to remove track: {e}"
            logger.error(result.error)

        report.results.append(result)

    return report


def copy_playlist_without(
    yt: YTMusic,
    video_ids: list[str],
    copy_name: str,
) -> RemovalReport:
    """Create a new playlist containing exactly the given video ids, in order.

    Positional by design: duplicate video ids appear as many times as they
    occur in the list, so callers can drop single entries from identical pairs.
    """
    report = RemovalReport()

    try:
        new_playlist_id = yt.create_playlist(
            copy_name,
            description="Created by uncensored",
        )
        report.new_playlist_id = new_playlist_id
        report.new_playlist_title = copy_name
        logger.info("Created new playlist: %s (%s)", copy_name, new_playlist_id)
    except Exception as e:
        logger.error("Failed to create new playlist: %s", e)
        return report

    _add_in_batches(yt, new_playlist_id, video_ids)
    return report
