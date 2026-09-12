from __future__ import annotations

import logging
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from replacer import RemovalReport, ReplacementReport, SwapResult
from scanner import DuplicateGroup, SwapCandidate, TrackInfo, VideoSuggestion

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

MODE_DRY_RUN = "dry-run"
MODE_IN_PLACE = "in-place"
MODE_COPY = "copy"
MODE_COPY_FALLBACK = "copy-fallback"


@dataclass
class ReportContext:
    playlist_title: str
    playlist_id: str
    mode: str
    total_tracks: int
    start_time: datetime
    end_time: datetime
    candidates: list[SwapCandidate] = field(default_factory=list)
    not_found: list[TrackInfo] = field(default_factory=list)
    skipped_no_set_id: list[TrackInfo] = field(default_factory=list)
    unavailable: list[SwapCandidate] = field(default_factory=list)
    unavailable_not_found: list[TrackInfo] = field(default_factory=list)
    unavailable_video_suggestions: list[VideoSuggestion] = field(default_factory=list)
    yt_upgrades: list[SwapCandidate] = field(default_factory=list)
    already_explicit_count: int = 0
    replacement_report: ReplacementReport | None = None
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    dedupe_skipped_no_set_id: list[TrackInfo] = field(default_factory=list)
    removal_report: RemovalReport | None = None


def generate_report(ctx: ReportContext, output_path: str | None = None) -> str:
    """Generate an HTML report and return the file path."""
    if output_path is None:
        timestamp = ctx.end_time.strftime("%Y%m%d_%H%M%S")
        output_path = f"uncensored_report_{timestamp}.html"

    resolved = Path(output_path).resolve()
    cwd = Path.cwd().resolve()
    if not resolved.is_relative_to(cwd):
        raise ValueError(f"Output path must be within the current directory: {output_path}")

    elapsed = ctx.end_time - ctx.start_time
    hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    elapsed_str = f"{hours}:{minutes:02d}:{seconds:02d}"

    rpt = ctx.replacement_report
    results: list[SwapResult] = rpt.results if rpt else []

    successful = sum(1 for r in results if r.success)
    errors = sum(1 for r in results if not r.success)
    duplicates = sum(1 for r in results if r.duplicate_warning)

    rem = ctx.removal_report
    removal_results = rem.results if rem else []
    removal_status = {(r.track.video_id, r.track.set_video_id): r for r in removal_results}
    duplicates_removed = sum(1 for r in removal_results if r.success)
    dedupe_loser_count = sum(len(g.losers) for g in ctx.duplicate_groups)

    def _dedupe_row(t: TrackInfo, is_winner: bool) -> dict:
        if is_winner:
            return {"track": t, "status": "keep", "error": None}
        if t.set_video_id is None:
            return {"track": t, "status": "no-setvideo-id", "error": None}
        r = removal_status.get((t.video_id, t.set_video_id))
        if r is None:
            if ctx.mode == MODE_DRY_RUN:
                return {"track": t, "status": "pending", "error": None}
            if rem and rem.new_playlist_id:
                return {"track": t, "status": "dropped-in-copy", "error": None}
            return {"track": t, "status": "not-removed", "error": None}
        if r.success:
            return {"track": t, "status": "removed", "error": None}
        return {"track": t, "status": "failed", "error": r.error}

    # Identity-safe winner marking (dataclass __eq__ would match identical
    # duplicate copies, so compare object identity when building the view).
    dedupe_view = [
        {
            "winner": g.winner,
            "rows": [_dedupe_row(t, t is g.winner) for t in g.tracks],
        }
        for g in ctx.duplicate_groups
    ]

    video_fallback_count = sum(1 for c in ctx.unavailable if c.replacement.is_video)
    yt_upgrade_count = len(ctx.yt_upgrades)

    if ctx.mode == MODE_DRY_RUN:
        replacements_label = "Replacements proposed"
        replacements_count = len(ctx.candidates) + len(ctx.unavailable) + len(ctx.yt_upgrades)
    else:
        replacements_label = "Replacements made"
        replacements_count = successful

    if ctx.duplicate_groups:
        if ctx.mode == MODE_DRY_RUN:
            replacements_label = "Removals proposed"
            replacements_count = dedupe_loser_count
        else:
            replacements_label = "Duplicate copies removed"
            replacements_count = duplicates_removed

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    html = template.render(
        playlist_title=ctx.playlist_title,
        playlist_id=ctx.playlist_id,
        playlist_url=f"https://music.youtube.com/playlist?list={ctx.playlist_id}",
        mode=ctx.mode,
        generated_at=ctx.end_time.strftime("%Y-%m-%d %H:%M:%S"),
        candidates=ctx.candidates,
        unavailable=ctx.unavailable,
        unavailable_not_found=ctx.unavailable_not_found,
        unavailable_video_suggestions=ctx.unavailable_video_suggestions,
        not_found=ctx.not_found,
        skipped_no_set_id=ctx.skipped_no_set_id,
        yt_upgrades=ctx.yt_upgrades,
        results=results,
        copy_mode_fallback=(rpt.copy_mode_fallback if rpt else False) or (rem.copy_mode_fallback if rem else False),
        new_playlist_id=(rpt.new_playlist_id if rpt else None) or (rem.new_playlist_id if rem else None),
        new_playlist_title=(rpt.new_playlist_title if rpt else None) or (rem.new_playlist_title if rem else None),
        dedupe_view=dedupe_view,
        dedupe_skipped=ctx.dedupe_skipped_no_set_id,
        duplicate_group_count=len(ctx.duplicate_groups),
        duplicates_removed=duplicates_removed,
        start_time=ctx.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        elapsed=elapsed_str,
        total_tracks=ctx.total_tracks,
        already_explicit=ctx.already_explicit_count,
        replacements_label=replacements_label,
        replacements_count=replacements_count,
        not_found_count=len(ctx.not_found),
        unavailable_count=len(ctx.unavailable),
        unavailable_not_found_count=len(ctx.unavailable_not_found),
        skipped_count=len(ctx.skipped_no_set_id) + len(ctx.dedupe_skipped_no_set_id),
        video_fallback_count=video_fallback_count,
        yt_upgrade_count=yt_upgrade_count,
        errors=errors,
        duplicates=duplicates,
    )

    Path(output_path).write_text(html, encoding="utf-8")
    logger.info("Report written to: %s", output_path)
    return output_path


def open_report(path: str) -> None:
    """Open the HTML report in the default browser."""
    try:
        webbrowser.open(f"file://{Path(path).resolve()}")
    except Exception as e:
        logger.warning("Could not open report in browser: %s", e)
        print(f"Report saved to: {path}")
