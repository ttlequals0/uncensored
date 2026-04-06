import logging
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from replacer import ReplacementReport, SwapResult
from scanner import SwapCandidate, TrackInfo, VideoSuggestion

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
    candidates: list[SwapCandidate]
    not_found: list[TrackInfo]
    skipped_no_set_id: list[TrackInfo]
    unavailable: list[SwapCandidate]
    unavailable_not_found: list[TrackInfo]
    unavailable_video_suggestions: list[VideoSuggestion]
    yt_upgrades: list[SwapCandidate]
    already_explicit_count: int
    total_tracks: int
    replacement_report: ReplacementReport | None
    start_time: datetime
    end_time: datetime


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

    video_fallback_count = sum(1 for c in ctx.unavailable if c.replacement.is_video)
    yt_upgrade_count = len(ctx.yt_upgrades)

    if ctx.mode == MODE_DRY_RUN:
        replacements_label = "Replacements proposed"
        replacements_count = len(ctx.candidates) + len(ctx.unavailable) + len(ctx.yt_upgrades)
    else:
        replacements_label = "Replacements made"
        replacements_count = successful

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
        copy_mode_fallback=rpt.copy_mode_fallback if rpt else False,
        new_playlist_id=rpt.new_playlist_id if rpt else None,
        new_playlist_title=rpt.new_playlist_title if rpt else None,
        start_time=ctx.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        elapsed=elapsed_str,
        total_tracks=ctx.total_tracks,
        already_explicit=ctx.already_explicit_count,
        replacements_label=replacements_label,
        replacements_count=replacements_count,
        not_found_count=len(ctx.not_found),
        unavailable_count=len(ctx.unavailable),
        unavailable_not_found_count=len(ctx.unavailable_not_found),
        skipped_count=len(ctx.skipped_no_set_id),
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
