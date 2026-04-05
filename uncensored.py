#!/usr/bin/env python3
"""uncensored -- Upgrade your playlist. No edits.

Scans a YouTube Music playlist and replaces clean/edited songs
with their explicit versions where available.
"""

import argparse
import logging
import sys
from datetime import datetime

from rich.console import Console
from rich.table import Table

from auth import get_client, run_browser_setup
from replacer import replace_in_place, replace_with_copy
from reporter import (
    MODE_COPY,
    MODE_COPY_FALLBACK,
    MODE_DRY_RUN,
    MODE_IN_PLACE,
    ReportContext,
    generate_report,
    open_report,
)
from scanner import LIKED_MUSIC_PLAYLIST_ID, SwapCandidate, TrackInfo, VideoSuggestion, scan_playlist

__version__ = "0.2.0"

console = Console()
logger = logging.getLogger("uncensored")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uncensored",
        description="Scan a YouTube Music playlist and replace clean songs with explicit versions.",
    )
    parser.add_argument(
        "playlist_id",
        nargs="?",
        help="YouTube Music playlist ID (e.g. PLxxxxxxxxxxxxxxxx)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan only. No changes made.")
    parser.add_argument("--yes", action="store_true", help="Auto-accept all replacements.")
    parser.add_argument("--copy", action="store_true", help="Create a new playlist instead of editing in-place.")
    parser.add_argument("--copy-name", help='Name for the new playlist. Default: "{title} [Uncensored]"')
    parser.add_argument("--output", help="Path for HTML report output.")
    parser.add_argument("--auth", default="./browser.json", help="Path to auth credentials file.")
    parser.add_argument("--setup", action="store_true", help="Run browser auth setup and exit.")
    parser.add_argument("--yt-video", action="store_true", help="Replace unavailable tracks with YouTube video versions when no YTM match exists.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    parser.add_argument("--version", action="version", version=f"uncensored {__version__}")
    return parser


def _track_table(title: str, track: TrackInfo) -> Table:
    """Build a Rich Table displaying a single track's info."""
    table = Table(title=title, show_header=False, title_style="bold", title_justify="left")
    table.add_column("", style="dim")
    table.add_column("")
    table.add_row("Title", track.title)
    table.add_row("Artist", track.artist)
    table.add_row("Link", track.ytm_link)
    return table


def prompt_confirmations(candidates: list[SwapCandidate], label: str = "Clean") -> list[SwapCandidate]:
    """Interactively prompt the user to confirm each swap."""
    confirmed = []
    total = len(candidates)

    for i, swap in enumerate(candidates):
        console.print(f"\n[bold]#{i + 1} of {total}[/bold]")

        video_tag = " [bold yellow][YT Video][/bold yellow]" if swap.replacement.is_video else ""

        console.print(_track_table("Current", swap.original))
        console.print(_track_table(f"Replacement{video_tag}", swap.replacement))

        while True:
            response = console.input("[bold][y][/bold] Yes  [bold][n][/bold] No  [bold][a][/bold] Accept All  [bold][q][/bold] Quit: ").strip().lower()
            if response == "y":
                confirmed.append(swap)
                break
            elif response == "n":
                break
            elif response == "a":
                confirmed.append(swap)
                confirmed.extend(candidates[i + 1:])
                return confirmed
            elif response == "q":
                return confirmed
            else:
                console.print("[dim]Please enter y, n, a, or q.[/dim]")

    return confirmed


def prompt_video_suggestions(suggestions: list[VideoSuggestion]) -> list[SwapCandidate]:
    """Prompt the user to pick from YouTube video suggestions for unavailable tracks."""
    confirmed = []
    total = len(suggestions)

    for i, vs in enumerate(suggestions):
        console.print(f"\n[bold]#{i + 1} of {total}[/bold]")

        console.print(_track_table("Unavailable", vs.original))

        for j, sug in enumerate(vs.suggestions):
            console.print(_track_table(
                f"Option {j + 1} [bold yellow][YT Video][/bold yellow]", sug,
            ))

        while True:
            choices = ", ".join(str(j + 1) for j in range(len(vs.suggestions)))
            response = console.input(
                f"  Pick [{choices}] or [bold]\\[s][/bold] Skip  [bold]\\[q][/bold] Quit: "
            ).strip().lower()
            if response == "s":
                break
            elif response == "q":
                return confirmed
            elif response.isdigit() and 1 <= int(response) <= len(vs.suggestions):
                picked = vs.suggestions[int(response) - 1]
                confirmed.append(SwapCandidate(original=vs.original, replacement=picked))
                break
            else:
                console.print(f"[dim]Please enter {choices}, s, or q.[/dim]")

    return confirmed


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.setup:
        success = run_browser_setup(args.auth)
        sys.exit(0 if success else 1)

    if not args.playlist_id:
        parser.print_help()
        sys.exit(1)

    start_time = datetime.now()
    use_copy = args.copy

    if args.playlist_id == LIKED_MUSIC_PLAYLIST_ID and not use_copy and not args.dry_run:
        console.print(
            "[bold yellow]Warning:[/bold yellow] The Liked Music playlist does not support "
            "track removal due to YouTube API limitations.\n"
            "Automatically switching to [bold]--copy[/bold] mode.\n"
        )
        use_copy = True

    yt = get_client(args.auth)

    console.print(f"Scanning playlist: [bold]{args.playlist_id}[/bold]\n")
    playlist_data = yt.get_playlist(args.playlist_id, limit=None)
    playlist_title = playlist_data.get("title", "Unknown Playlist")
    all_tracks = playlist_data.get("tracks") or []
    total_tracks = len(all_tracks)
    all_video_ids = [t.get("videoId", "") for t in all_tracks]

    def show_progress(current, total, track, status):
        if status == "searching":
            console.print(f"  [{current}/{total}] Searching: {track.artist} - {track.title}")
        elif status == "unavailable":
            console.print(f"  [{current}/{total}] Unavailable: {track.artist} - {track.title}", style="yellow")
        elif status == "explicit":
            console.print(f"  [{current}/{total}] Already explicit: {track.artist} - {track.title}", style="dim")
        elif status == "yt_upgrade":
            console.print(f"  [{current}/{total}] YT upgrade search: {track.artist} - {track.title}", style="cyan")

    scan = scan_playlist(
        yt, all_tracks,
        progress_callback=show_progress,
        allow_video_fallback=args.yt_video,
    )

    video_fallback_count = sum(1 for c in scan.unavailable if c.replacement.is_video)

    console.print(
        f"\nScan complete. Found [bold]{len(scan.candidates)}[/bold] explicit replacements "
        f"across [bold]{total_tracks}[/bold] songs."
    )

    if video_fallback_count:
        console.print(
            f"  {video_fallback_count} unavailable track(s) replaced with YouTube video fallbacks"
        )

    if scan.yt_upgrades:
        console.print(
            f"Found [bold]{len(scan.yt_upgrades)}[/bold] YouTube-to-YTM upgrades."
        )

    if scan.unavailable:
        console.print(
            f"Found [bold]{len(scan.unavailable)}[/bold] replacements for unavailable tracks."
        )

    if scan.unavailable_video_suggestions:
        console.print(
            f"Found [bold]{len(scan.unavailable_video_suggestions)}[/bold] unavailable track(s) "
            f"with YouTube video options (see report)."
        )

    if scan.unavailable_not_found:
        console.print(
            f"[yellow]{len(scan.unavailable_not_found)} unavailable track(s) could not be replaced.[/yellow]"
        )

    if scan.skipped_no_set_id:
        console.print(
            f"[yellow]{len(scan.skipped_no_set_id)} track(s) skipped (missing setVideoId).[/yellow]"
        )

    console.print()

    all_swap_candidates = scan.candidates + scan.unavailable + scan.yt_upgrades

    if args.dry_run:
        confirmed = all_swap_candidates
        mode = MODE_DRY_RUN
    else:
        if args.yes or not all_swap_candidates:
            confirmed = all_swap_candidates
        else:
            confirmed = []
            if scan.candidates:
                console.print("[bold]Explicit replacements:[/bold]")
                confirmed.extend(prompt_confirmations(scan.candidates, label="Clean"))
            if scan.yt_upgrades:
                console.print("\n[bold]YouTube-to-YTM upgrades:[/bold]")
                confirmed.extend(prompt_confirmations(scan.yt_upgrades, label="YT Video"))
            if scan.unavailable:
                console.print("\n[bold]Unavailable track replacements:[/bold]")
                confirmed.extend(prompt_confirmations(scan.unavailable, label="Unavailable"))

        if not args.dry_run and not args.yes and scan.unavailable_video_suggestions:
            console.print("\n[bold]YouTube video options for unavailable tracks:[/bold]")
            video_confirmed = prompt_video_suggestions(scan.unavailable_video_suggestions)
            confirmed.extend(video_confirmed)
            # Move confirmed video suggestions into unavailable list so the
            # report shows them as applied replacements, not just suggestions
            scan.unavailable.extend(video_confirmed)
            confirmed_ids = {s.original.video_id for s in video_confirmed}
            scan.unavailable_video_suggestions = [
                vs for vs in scan.unavailable_video_suggestions
                if vs.original.video_id not in confirmed_ids
            ]

        mode = MODE_COPY if use_copy else MODE_IN_PLACE

    copy_name = args.copy_name or f"{playlist_title} [Uncensored]"
    replacement_report = None

    if not args.dry_run and confirmed:
        if use_copy:
            console.print(f"Creating new playlist: [bold]{copy_name}[/bold]\n")
            replacement_report = replace_with_copy(yt, confirmed, all_video_ids, copy_name)
            if replacement_report.new_playlist_id:
                console.print(
                    f"[green]New playlist created:[/green] "
                    f"https://music.youtube.com/playlist?list={replacement_report.new_playlist_id}\n"
                )
        else:
            console.print("Applying replacements...\n")
            replacement_report = replace_in_place(yt, args.playlist_id, confirmed)

            if replacement_report.copy_mode_fallback:
                console.print(
                    "[bold yellow]You don't own this playlist. Falling back to copy mode.[/bold yellow]\n"
                )
                applied_ids = {r.candidate.original.video_id for r in replacement_report.results if r.success}
                remaining = [s for s in confirmed if s.original.video_id not in applied_ids]
                replacement_report = replace_with_copy(yt, remaining, all_video_ids, copy_name)
                mode = MODE_COPY_FALLBACK

        successful = sum(1 for r in replacement_report.results if r.success)
        console.print(f"[green]{successful}[/green] replacement(s) applied.\n")
    elif args.dry_run:
        console.print("[dim]Dry run -- no changes made.[/dim]\n")
    else:
        console.print("No replacements to apply.\n")

    end_time = datetime.now()
    report_path = generate_report(
        ReportContext(
            playlist_title=playlist_title,
            playlist_id=args.playlist_id,
            mode=mode,
            candidates=scan.candidates,
            not_found=scan.not_found,
            skipped_no_set_id=scan.skipped_no_set_id,
            unavailable=scan.unavailable,
            unavailable_not_found=scan.unavailable_not_found,
            unavailable_video_suggestions=scan.unavailable_video_suggestions,
            yt_upgrades=scan.yt_upgrades,
            already_explicit_count=scan.already_explicit_count,
            total_tracks=total_tracks,
            replacement_report=replacement_report,
            start_time=start_time,
            end_time=end_time,
        ),
        output_path=args.output,
    )

    console.print(f"Report saved to: [bold]{report_path}[/bold]")
    open_report(report_path)


if __name__ == "__main__":
    main()
