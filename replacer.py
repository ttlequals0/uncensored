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
    failed_adds: list[str] = field(default_factory=list)


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
    failed_adds: list[str] = field(default_factory=list)


def _edit_error(response) -> str | None:
    """None if a playlist edit succeeded, else its status. ytmusicapi reports
    a rejected edit through the returned status, not an exception."""
    status = response.get("status") if isinstance(response, dict) else response
    if isinstance(status, str) and "SUCCEEDED" in status:
        return None
    return status if isinstance(status, str) else "no status in response"


def _create_playlist(yt: YTMusic, name: str) -> str | None:
    """Create an empty playlist and return its id, or None on failure."""
    try:
        response = yt.create_playlist(name, description="Created by uncensored")
    except Exception as e:
        logger.error("Failed to create new playlist: %s", e)
        return None
    # On failure ytmusicapi returns the raw response instead of an id
    if not isinstance(response, str):
        logger.error("Failed to create new playlist: %s", response)
        return None
    logger.info("Created new playlist: %s (%s)", name, response)
    return response


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
    existing_video_ids: set[str] | None = None,
) -> ReplacementReport:
    """Replace tracks in the original playlist.

    When preserve_position is True, replacements are moved to sit
    immediately before the original track. This flips the playlist's
    server-side "Ordering" to Manual in YouTube Music. Default False:
    replacements are appended, which preserves the playlist's
    Recently-added default sort.

    existing_video_ids: videos already in the playlist. YouTube Music
    rejects adding those again, so for them only the original is removed.
    """
    report = ReplacementReport()
    present = set(existing_video_ids or ())

    for swap in confirmed:
        result = SwapResult(candidate=swap, success=False)
        new_vid = swap.replacement.video_id

        if new_vid in present:
            logger.info("'%s' is already in the playlist, removing original only", swap.replacement.title)
        else:
            try:
                add_response = yt.add_playlist_items(playlist_id, [new_vid])
                add_error = _edit_error(add_response)
            except Exception as e:
                add_error = str(e)
            # Never remove the original unless its replacement is really in
            if add_error is not None:
                result.error = f"Failed to add replacement: {add_error}"
                logger.error(result.error)
                report.results.append(result)
                continue
            present.add(new_vid)
            logger.info("Added: '%s' by %s", swap.replacement.title, swap.replacement.artist)

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
            remove_response = yt.remove_playlist_items(
                playlist_id,
                [{"videoId": swap.original.video_id, "setVideoId": swap.original.set_video_id}],
            )
            remove_error = _edit_error(remove_response)
            if remove_error is None:
                logger.info("Removed: '%s' by %s", swap.original.title, swap.original.artist)
                result.success = True
            else:
                result.error = f"Removal of original rejected (duplicate may exist): {remove_error}"
                result.duplicate_warning = True
                logger.warning(result.error)
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
    """Create a new playlist with replacements applied, matched by playlist
    position (unavailable originals can share an empty videoId)."""
    report = ReplacementReport()

    new_playlist_id = _create_playlist(yt, copy_name)
    if new_playlist_id is None:
        return report
    report.new_playlist_id = new_playlist_id
    report.new_playlist_title = copy_name

    replacement_at = {swap.original.position: swap.replacement.video_id for swap in confirmed}
    final_video_ids = [
        replacement_at.get(i, orig) for i, orig in enumerate(all_track_video_ids)
    ]

    failed = _add_in_batches(yt, new_playlist_id, final_video_ids)
    report.failed_adds = [final_video_ids[i] for i in failed]

    for swap in confirmed:
        success = swap.original.position not in failed
        error = "Could not add replacement to the new playlist" if not success else None
        report.results.append(SwapResult(candidate=swap, success=success, error=error))

    return report


def _try_add(yt: YTMusic, playlist_id: str, video_ids: list[str]) -> str | None:
    """Add video ids; return None on success, else the failure reason."""
    try:
        return _edit_error(yt.add_playlist_items(playlist_id, video_ids, duplicates=True))
    except Exception as e:
        return str(e)


def _add_in_batches(yt: YTMusic, playlist_id: str, video_ids: list[str]) -> list[int]:
    """Add video ids in batches of 25, retrying failed batches individually.

    Empty ids are skipped. Returns the indices into video_ids that failed
    even after the individual retry.
    """
    indices = [i for i, vid in enumerate(video_ids) if vid]
    failed: list[int] = []
    batch_size = 25
    for start in range(0, len(indices), batch_size):
        batch = indices[start:start + batch_size]
        error = _try_add(yt, playlist_id, [video_ids[i] for i in batch])
        if error is None:
            logger.info("Added batch %d-%d to new playlist", start + 1, start + len(batch))
            time.sleep(MUTATION_DELAY)
            continue
        logger.info("Batch %d-%d failed (%s), retrying individually", start + 1, start + len(batch), error)
        for i in batch:
            error = _try_add(yt, playlist_id, [video_ids[i]])
            if error is not None:
                logger.warning("Could not add %s to the new playlist: %s", video_ids[i], error)
                failed.append(i)
            time.sleep(MUTATION_DELAY)
    return failed


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
            response = yt.remove_playlist_items(
                playlist_id,
                [{"videoId": track.video_id, "setVideoId": track.set_video_id}],
            )
            error = _edit_error(response)
            if error is None:
                logger.info("Removed: '%s' by %s", track.title, track.artist)
                result.success = True
            else:
                result.error = f"Removal rejected by YouTube Music: {error}"
                logger.error(result.error)
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
    all_track_video_ids: list[str],
    drop: list[TrackInfo],
    copy_name: str,
) -> RemovalReport:
    """Create a new playlist with every original entry except those in drop,
    matched by playlist position so exactly the chosen copy of a pair goes."""
    report = RemovalReport()

    new_playlist_id = _create_playlist(yt, copy_name)
    if new_playlist_id is None:
        return report
    report.new_playlist_id = new_playlist_id
    report.new_playlist_title = copy_name

    drop_positions = {t.position for t in drop}
    kept = [vid for i, vid in enumerate(all_track_video_ids) if i not in drop_positions]
    report.failed_adds = [kept[i] for i in _add_in_batches(yt, new_playlist_id, kept)]
    report.results = [RemovalResult(track=t, success=True) for t in drop]
    return report
