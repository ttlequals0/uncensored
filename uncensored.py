#!/usr/bin/env python3
"""uncensored -- Upgrade your playlist. No edits.

Scans a YouTube Music playlist and replaces clean/edited songs
with their explicit versions where available.
"""

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime

from rich.console import Console
from rich.table import Table

from auth import get_client, run_browser_setup
from replacer import (
    copy_playlist_without,
    remove_from_playlist,
    replace_in_place,
    replace_with_copy,
)
from reporter import (
    MODE_COPY,
    MODE_COPY_FALLBACK,
    MODE_DRY_RUN,
    MODE_IN_PLACE,
    ReportContext,
    generate_report,
    open_report,
)
from scanner import (
    LIKED_MUSIC_PLAYLIST_ID,
    DuplicateGroup,
    SwapCandidate,
    TrackInfo,
    VideoSuggestion,
    dedupe_playlist,
    scan_playlist,
)

__version__ = "0.2.0"

console = Console()
logger = logging.getLogger("uncensored")

_YT_VIDEO_TAG = " [bold yellow][YT Video][/bold yellow]"


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
    parser.add_argument(
        "--preserve-position",
        action="store_true",
        help="Keep replacement tracks in the original track's playlist position. "
             "Flips the playlist to Manual sort in YouTube Music.",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Standalone: find duplicate songs and remove extra copies, keeping the best "
             "version (explicit > clean, official YTM > YouTube upload). Skips the explicit scan.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    parser.add_argument("--version", action="version", version=f"uncensored {__version__}")
    return parser


def _track_table(title: str, track: TrackInfo) -> Table:
    table = Table(title=title, show_header=False, title_style="bold", title_justify="left")
    table.add_column("", style="dim")
    table.add_column("")
    table.add_row("Title", track.title)
    table.add_row("Artist", track.artist)
    table.add_row("Link", track.ytm_link)
    if not track.is_available:
        table.add_row("Status", "[red]Unavailable on YouTube Music[/red]")
    return table


def _ask_ynaq(question: str) -> str:
    while True:
        response = console.input(question).strip().lower()
        if response in ("y", "n", "a", "q"):
            return response
        console.print("[dim]Please enter y, n, a, or q.[/dim]")


def prompt_confirmations(candidates: list[SwapCandidate], label: str = "Clean") -> list[SwapCandidate]:
    """Interactively prompt the user to confirm each swap."""
    confirmed = []
    total = len(candidates)

    for i, swap in enumerate(candidates):
        console.print(f"\n[bold]#{i + 1} of {total}[/bold]")

        video_tag = _YT_VIDEO_TAG if swap.replacement.is_video else ""

        console.print(_track_table("Current", swap.original))
        console.print(_track_table(f"Replacement{video_tag}", swap.replacement))

        response = _ask_ynaq("[bold][y][/bold] Yes  [bold][n][/bold] No  [bold][a][/bold] Accept All  [bold][q][/bold] Quit: ")
        if response == "y":
            confirmed.append(swap)
        elif response == "a":
            confirmed.append(swap)
            confirmed.extend(candidates[i + 1:])
            return confirmed
        elif response == "q":
            return confirmed

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
                f"Option {j + 1}{_YT_VIDEO_TAG}", sug,
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


def prompt_dedupe_groups(groups: list[DuplicateGroup]) -> list[TrackInfo]:
    """Interactively confirm which duplicate copies to remove, group by group."""
    confirmed: list[TrackInfo] = []
    total = len(groups)

    for i, group in enumerate(groups):
        if not group.losers:
            continue

        console.print(f"\n[bold]Group {i + 1} of {total}[/bold]")
        console.print(_track_table("Keep (best version)", group.winner))

        for loser in group.losers:
            console.print(_track_table("Remove", loser))

        response = _ask_ynaq(
            f"Remove {len(group.losers)} duplicate copy(ies)? "
            "[bold][y][/bold] Yes  [bold][n][/bold] No  [bold][a][/bold] Accept All  [bold][q][/bold] Quit: "
        )
        if response == "y":
            confirmed.extend(group.losers)
        elif response == "a":
            confirmed.extend(group.losers)
            confirmed.extend(t for g in groups[i + 1:] for t in g.losers)
            return confirmed
        elif response == "q":
            return confirmed

    return confirmed


def _kept_video_ids(all_tracks: list[dict], exclude: list[TrackInfo]) -> list[str]:
    """Video ids for a copy rebuild, dropping excluded entries.

    Entries that expose a setVideoId match excluded losers by it exactly.
    Entries without any setVideoId (unowned playlists serve none) match
    excluded losers of the same videoId positionally, first occurrence
    first, so identical unmarked copies still drop one-for-one.
    """
    excluded_svids = {t.set_video_id for t in exclude if t.set_video_id}
    pending_unmarked = Counter(
        t.video_id for t in exclude if not t.set_video_id and t.video_id
    )

    kept = []
    for t in all_tracks:
        svid = t.get("setVideoId")
        if svid and svid in excluded_svids:
            continue
        if not svid:
            vid = t.get("videoId")
            if vid and pending_unmarked.get(vid, 0) > 0:
                pending_unmarked[vid] -= 1
                continue
        kept.append(t.get("videoId", ""))
    return kept


def _run_dedupe(
    args,
    yt,
    playlist_id: str,
    playlist_title: str,
    all_tracks: list[dict],
    use_copy: bool,
    start_time: datetime,
) -> None:
    """Standalone --dedupe flow: find duplicate copies, confirm, remove, report."""
    result = dedupe_playlist(all_tracks, playlist_id)
    loser_count = sum(len(g.losers) for g in result.groups)

    console.print(
        f"\nFound [bold]{len(result.groups)}[/bold] duplicate group(s) across "
        f"[bold]{result.total_tracks}[/bold] songs ({loser_count} extra copies)."
    )
    if result.skipped_no_set_id:
        console.print(
            f"[yellow]{len(result.skipped_no_set_id)} duplicate copy(ies) missing setVideoId: "
            f"cannot be removed in-place, but copy mode still drops them.[/yellow]"
        )

    mode = MODE_DRY_RUN if args.dry_run else (MODE_COPY if use_copy else MODE_IN_PLACE)
    removal_report = None

    if args.dry_run:
        console.print("[dim]Dry run -- no changes made.[/dim]\n")
    elif loser_count == 0:
        console.print("No duplicates to remove.\n")
    else:
        if args.yes:
            console.print(f"Removing [bold]{loser_count}[/bold] duplicate copies (--yes).")
            confirmed = [t for g in result.groups for t in g.losers]
        else:
            confirmed = prompt_dedupe_groups(result.groups)

        if confirmed:
            copy_name = args.copy_name or f"{playlist_title} [Uncensored]"
            if not use_copy and not any(t.set_video_id for t in confirmed):
                console.print(
                    "[bold yellow]None of the confirmed copies can be removed in-place "
                    "(missing setVideoId). Falling back to copy mode.[/bold yellow]\n"
                )
                use_copy = True

            if use_copy:
                console.print(f"\nCreating new playlist: [bold]{copy_name}[/bold]\n")
                removal_report = copy_playlist_without(
                    yt, _kept_video_ids(all_tracks, confirmed), copy_name,
                )
                if mode == MODE_IN_PLACE:
                    mode = MODE_COPY_FALLBACK
            else:
                console.print(f"\nRemoving {len(confirmed)} duplicate copies...\n")
                removal_report = remove_from_playlist(
                    yt, playlist_id, [t for t in confirmed if t.set_video_id],
                )

                if removal_report.copy_mode_fallback:
                    console.print(
                        "[bold yellow]You don't own this playlist. Falling back to copy mode.[/bold yellow]\n"
                    )
                    removal_report = copy_playlist_without(
                        yt, _kept_video_ids(all_tracks, confirmed), copy_name,
                    )
                    mode = MODE_COPY_FALLBACK

            if removal_report.new_playlist_id:
                console.print(
                    f"[green]New playlist created:[/green] "
                    f"https://music.youtube.com/playlist?list={removal_report.new_playlist_id}\n"
                )
            if removal_report.results:
                successful = sum(1 for r in removal_report.results if r.success)
                console.print(f"[green]{successful}[/green] duplicate copy(ies) removed.\n")

    end_time = datetime.now()
    report_path = generate_report(
        ReportContext(
            playlist_title=playlist_title,
            playlist_id=playlist_id,
            mode=mode,
            total_tracks=result.total_tracks,
            start_time=start_time,
            end_time=end_time,
            duplicate_groups=result.groups,
            dedupe_skipped_no_set_id=result.skipped_no_set_id,
            removal_report=removal_report,
        ),
        output_path=args.output,
    )
    console.print(f"Report saved to: [bold]{report_path}[/bold]")
    open_report(report_path)


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

    if (
        all_tracks
        and not use_copy
        and not args.dry_run
        and not any(t.get("setVideoId") for t in all_tracks)
    ):
        console.print(
            "[bold yellow]Warning:[/bold yellow] No entries in this playlist expose a "
            "setVideoId, so this account cannot edit it (it is likely not owned by you).\n"
            "Automatically switching to [bold]--copy[/bold] mode.\n"
        )
        use_copy = True

    if args.dedupe:
        _run_dedupe(args, yt, args.playlist_id, playlist_title, all_tracks, use_copy, start_time)
        return

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
        playlist_id=args.playlist_id,
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

        if not args.yes and scan.unavailable_video_suggestions:
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
            replacement_report = replace_in_place(
                yt, args.playlist_id, confirmed,
                preserve_position=args.preserve_position,
            )

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
