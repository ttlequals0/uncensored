"""Smoke tests for report generation (template renders, sections appear)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from replacer import RemovalReport, RemovalResult
from reporter import MODE_DRY_RUN, MODE_IN_PLACE, ReportContext, generate_report
from scanner import DuplicateGroup, TrackInfo


def _track(video_id, title="Song", artist="Artist", set_video_id="s1", is_explicit=False):
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
    clean = _track("c", title="Song (Clean)")
    explicit = _track("e", title="Song", set_video_id="s2", is_explicit=True)
    group = DuplicateGroup(key="song|artist", tracks=[clean, explicit], winner=explicit, losers=[clean])

    html = _render(tmp_path, monkeypatch, _ctx(
        total_tracks=2,
        duplicate_groups=[group],
    ))
    assert "Duplicate Groups (1)" in html
    assert "KEEP" in html
    assert "TO REMOVE" in html


def test_dedupe_report_shows_removal_status(tmp_path, monkeypatch):
    clean = _track("c", title="Song (Clean)")
    explicit = _track("e", title="Song", set_video_id="s2", is_explicit=True)
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


def test_dedupe_unremovable_loser_marked_no_setvideoid(tmp_path, monkeypatch):
    loser = _track("c", title="Song (Clean)", set_video_id=None)
    winner = _track("e", title="Song", set_video_id="s2", is_explicit=True)
    group = DuplicateGroup(key="song|artist", tracks=[loser, winner], winner=winner, losers=[loser])

    html = _render(tmp_path, monkeypatch, _ctx(
        total_tracks=2,
        duplicate_groups=[group],
    ))
    assert "NO SETVIDEOID" in html
    assert "TO REMOVE" not in html
