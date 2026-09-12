"""Smoke tests for report generation (template renders, sections appear)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from replacer import RemovalReport, RemovalResult, ReplacementReport
from reporter import MODE_COPY, MODE_DRY_RUN, MODE_IN_PLACE, ReportContext, generate_report
from scanner import DuplicateGroup, TrackInfo


def _track(video_id, title="Song", artist="Artist", set_video_id="s1", is_explicit=False, position=None):
    return TrackInfo(
        video_id=video_id,
        set_video_id=set_video_id,
        title=title,
        artist=artist,
        album=None,
        duration_seconds=200,
        thumbnail_url=None,
        ytm_link=f"https://music.youtube.com/watch?v={video_id}",
        is_explicit=is_explicit,
        position=position,
    )


def _ctx(**overrides):
    base = dict(
        playlist_title="Test",
        playlist_id="PL123",
        mode=MODE_DRY_RUN,
        candidates=[],
        not_found=[],
        skipped_no_set_id=[],
        unavailable=[],
        unavailable_not_found=[],
        unavailable_video_suggestions=[],
        yt_upgrades=[],
        already_explicit_count=0,
        total_tracks=2,
        replacement_report=None,
        start_time=datetime(2026, 9, 11, 12, 0, 0),
        end_time=datetime(2026, 9, 11, 12, 1, 0),
    )
    base.update(overrides)
    return ReportContext(**base)


def _render(tmp_path, monkeypatch, ctx):
    monkeypatch.chdir(tmp_path)
    generate_report(ctx, output_path="report.html")
    return (tmp_path / "report.html").read_text()


def test_plain_report_renders(tmp_path, monkeypatch):
    html = _render(tmp_path, monkeypatch, _ctx())
    assert "uncensored report" in html
    assert "Duplicate Groups" not in html


def test_dedupe_report_shows_groups_with_winner(tmp_path, monkeypatch):
    clean = _track("c", title="Song (Clean)", position=0)
    explicit = _track("e", title="Song", set_video_id="s2", is_explicit=True, position=1)
    group = DuplicateGroup(key="song|artist", tracks=[clean, explicit], winner=explicit, losers=[clean])

    html = _render(tmp_path, monkeypatch, _ctx(
        total_tracks=2,
        duplicate_groups=[group],
    ))
    assert "Duplicate Groups (1)" in html
    assert "KEEP" in html
    assert "TO REMOVE" in html


def test_dedupe_report_shows_removal_status(tmp_path, monkeypatch):
    clean = _track("c", title="Song (Clean)", position=0)
    explicit = _track("e", title="Song", set_video_id="s2", is_explicit=True, position=1)
    group = DuplicateGroup(key="song|artist", tracks=[clean, explicit], winner=explicit, losers=[clean])
    report = RemovalReport(results=[RemovalResult(track=clean, success=True)])

    html = _render(tmp_path, monkeypatch, _ctx(
        mode=MODE_IN_PLACE,
        total_tracks=2,
        duplicate_groups=[group],
        removal_report=report,
    ))
    assert "REMOVED" in html
    assert "Duplicate groups" in html
    assert "Duplicate copies removed" in html


def test_dedupe_skipped_shown_in_setid_section(tmp_path, monkeypatch):
    orphan = _track("o", set_video_id=None)
    html = _render(tmp_path, monkeypatch, _ctx(dedupe_skipped_no_set_id=[orphan]))
    assert "Skipped -- Missing setVideoId (1)" in html


def _unowned_group():
    """Clean/explicit pair from a playlist that exposes no setVideoId."""
    loser = _track("c", title="Song (Clean)", set_video_id=None, position=0)
    winner = _track("e", title="Song", set_video_id=None, is_explicit=True, position=1)
    return DuplicateGroup(key="song|artist", tracks=[loser, winner], winner=winner, losers=[loser])


def test_dedupe_unremovable_loser_marked_no_setvideoid_in_place(tmp_path, monkeypatch):
    group = _unowned_group()
    html = _render(tmp_path, monkeypatch, _ctx(
        mode=MODE_IN_PLACE,
        duplicate_groups=[group],
        dedupe_skipped_no_set_id=group.losers,
        removal_report=RemovalReport(),
    ))
    assert "NO SETVIDEOID" in html
    assert "TO REMOVE" not in html


def test_dedupe_dry_run_copy_mode_shows_to_remove(tmp_path, monkeypatch):
    # Copy mode can drop setVideoId-less losers, so the CLI passes no
    # skipped list and the preview must not claim they are stuck.
    html = _render(tmp_path, monkeypatch, _ctx(duplicate_groups=[_unowned_group()]))
    assert "TO REMOVE" in html
    assert "NO SETVIDEOID" not in html
    assert "Skipped -- Missing setVideoId" not in html


def test_dedupe_copy_marks_dropped_loser_and_counts_it(tmp_path, monkeypatch):
    group = _unowned_group()
    report = RemovalReport(
        results=[RemovalResult(track=group.losers[0], success=True)],
        new_playlist_id="PLnew",
        new_playlist_title="Copy",
    )
    html = _render(tmp_path, monkeypatch, _ctx(
        mode=MODE_COPY, duplicate_groups=[group], removal_report=report,
    ))
    assert "DROPPED IN COPY" in html
    assert "NO SETVIDEOID" not in html
    assert "<dt>Duplicate copies left out of copy</dt><dd>1</dd>" in html


def test_dedupe_copy_declined_loser_not_marked_dropped(tmp_path, monkeypatch):
    # The user answered n: the loser has no result and stays in the copy
    group = _unowned_group()
    report = RemovalReport(new_playlist_id="PLnew", new_playlist_title="Copy")
    html = _render(tmp_path, monkeypatch, _ctx(
        mode=MODE_COPY, duplicate_groups=[group], removal_report=report,
    ))
    assert "DROPPED IN COPY" not in html
    assert "NOT REMOVED" in html


def test_failed_adds_listed(tmp_path, monkeypatch):
    report = ReplacementReport(new_playlist_id="PLnew", new_playlist_title="Copy", failed_adds=["gone1"])
    html = _render(tmp_path, monkeypatch, _ctx(mode=MODE_COPY, replacement_report=report))
    assert "1 track(s) could not be added to the new playlist" in html
    assert "watch?v=gone1" in html
    assert "<dt>Missing from new playlist</dt><dd>1</dd>" in html
