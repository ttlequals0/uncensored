# Changelog

## [Unreleased]

### Changed
- In-place replacements no longer reorder the replacement into the original track's slot by default. This avoids flipping the YouTube Music playlist's server-side sort to Manual, preserving "Recently added" as the default sort

### Added
- `--preserve-position` flag to restore the previous behavior of moving the replacement into the original track's playlist position (still flips the playlist to Manual sort)

## [0.2.0] - 2026-04-05

### Added
- YouTube video fallback search when no explicit YTM song match is found
- YT Video badge indicator in terminal prompts and HTML reports for video fallback matches
- YouTube-to-YTM upgrade detection: replaces YouTube-sourced (UGC) tracks with proper YTM versions
- New "YouTube to YTM Upgrades" section in HTML reports
- Video fallback and YT upgrade counts in report stats
- `is_video` and `video_type` fields on TrackInfo for source tracking
- Strip featuring suffixes (feat., ft.) from titles during match comparison for better accuracy
- Compound artist matching: splits on & / x / and to match primary artist
- `--yt-video` flag to opt in to YouTube video fallback for unavailable tracks
- Unavailable tracks with `--yt-video` show top 2 YouTube video options when strict matching fails
- Unavailable tracks no longer skipped when missing setVideoId

## [0.1.0] - 2026-03-22

### Added
- Initial project scaffolding
- OAuth authentication flow via ytmusicapi
- Playlist scanning with explicit track detection
- Title normalization and clean-suffix stripping
- Duration-based matching (+/-10s) to filter remixes/live versions
- Lenient artist matching for featured artist variations
- Interactive confirmation prompts with rich terminal UI
- In-place playlist replacement (add explicit, remove clean)
- Copy mode for non-destructive playlist creation
- Unowned playlist detection with automatic copy-mode fallback
- Liked Music playlist detection with early warning
- Self-contained HTML report with light/dark theme
- Dry-run mode for audit-only scanning
- Auto-accept mode (--yes) for unattended runs
- Search throttling (0.3s delay) to avoid rate limits
- Verbose logging flag for API debugging
