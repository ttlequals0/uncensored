from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ytmusicapi import YTMusic

from scanner import SwapCandidate

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


def _extract_set_video_id(add_response) -> str | None:
    """Extract the setVideoId from an add_playlist_items response."""
    if not isinstance(add_response, dict):
        return None
    for result in add_response.get("playlistEditResults") or []:
        if result and "setVideoId" in result:
            return result["setVideoId"]
    return None


def replace_in_place(
    yt: YTMusic,
    playlist_id: str,
    confirmed: list[SwapCandidate],
) -> ReplacementReport:
    """Replace tracks in the original playlist, maintaining position."""
    report = ReplacementReport()

    for swap in confirmed:
        result = SwapResult(candidate=swap, success=False)

        try:
            add_response = yt.add_playlist_items(playlist_id, [swap.replacement.video_id])
            logger.info("Added: '%s' by %s", swap.replacement.title, swap.replacement.artist)
        except Exception as e:
            logger.debug("Add failed detail: %s", e)
            result.error = f"Failed to add replacement ({type(e).__name__})"
            logger.error(result.error)
            report.results.append(result)
            continue

        time.sleep(MUTATION_DELAY)

        original_svid = swap.original.set_video_id
        if original_svid:
            new_svid = _extract_set_video_id(add_response)
            if new_svid:
                try:
                    yt.edit_playlist(playlist_id, moveItem=(new_svid, original_svid))
                    logger.info("Moved replacement before original")
                    time.sleep(MUTATION_DELAY)
                except Exception as e:
                    logger.debug("Move failed (non-fatal): %s", e)

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

            logger.debug("Remove failed detail: %s", e)
            result.error = f"Failed to remove original (duplicate may exist, {type(e).__name__})"
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
        logger.debug("Create playlist detail: %s", e)
        logger.error("Failed to create new playlist (%s)", type(e).__name__)
        return report

    replacement_map = {swap.original.video_id: swap.replacement.video_id for swap in confirmed}

    final_video_ids = [
        replacement_map.get(vid, vid)
        for vid in all_track_video_ids
        if vid
    ]

    failed_video_ids: set[str] = set()
    batch_size = 25
    for i in range(0, len(final_video_ids), batch_size):
        batch = final_video_ids[i:i + batch_size]
        try:
            yt.add_playlist_items(new_playlist_id, batch, duplicates=True)
            logger.info("Added batch %d-%d to new playlist", i + 1, i + len(batch))
        except Exception:
            logger.info("Batch %d-%d failed, retrying individually", i + 1, i + len(batch))
            for vid in batch:
                try:
                    yt.add_playlist_items(new_playlist_id, [vid], duplicates=True)
                except Exception as e2:
                    logger.debug("Single add failed for %s: %s", vid, e2)
                    failed_video_ids.add(vid)
                time.sleep(MUTATION_DELAY)
            continue
        time.sleep(MUTATION_DELAY)

    for swap in confirmed:
        success = swap.replacement.video_id not in failed_video_ids
        error = "Batch add failed for track" if not success else None
        report.results.append(SwapResult(candidate=swap, success=success, error=error))

    return report
