# Changelog

## [Unreleased]

### Security
- Updated transitive dependencies to versions that patch open Dependabot advisories: `requests>=2.33.0`, `urllib3>=2.7.0`, `idna>=3.15`, `Pygments>=2.20.0`, and dev `pytest>=9.0.3` (supersedes PR #4, whose versions are overtaken)

### Changed
- In-place replacements no longer reorder the replacement into the original track's slot by default. This avoids flipping the YouTube Music playlist's server-side sort to Manual, preserving "Recently added" as the default sort

### Fixed
- Links for playlist entries now pin to the playlist (`watch?v=...&list=...`), the same way the YouTube Music UI links them. Bare `watch?v=` links for album-sourced entries get remapped by YouTube Music to a different video or an autoplay mix when opened standalone
- Interactive prompts now mark original tracks that are unavailable on YouTube Music. Their links cannot land anywhere (YouTube Music jumps to the nearest playable entry), which previously made correct matches look like mismatches
- Browser auth setup now strips replay-hostile headers (content-encoding, hop-by-hop headers, and the stray HTTP request line Firefox/Chrome copies into the header list) from browser.json, and ensures the SAPISIDHASH auth marker ytmusicapi needs to detect browser auth is present; existing credential files are repaired automatically on load. Previously these caused the YouTube API to answer `400 Bad Request` with an HTML page, which surfaced as a JSON decode error

### Added
- `--preserve-position` flag to restore the previous behavior of moving the replacement into the original track's playlist position (still flips the playlist to Manual sort)
- `--dedupe` standalone mode: finds duplicate songs (same title/artist within 10s duration, or identical uploads), keeps the best copy (available > unavailable, official > YouTube upload, explicit > clean, earliest position breaks ties), and removes the rest after interactive confirmation
- Up-front unowned-playlist detection: when no playlist entries expose a `setVideoId`, both the replacement flow and dedupe switch to copy mode instead of firing doomed write requests
- Dedupe copy-mode fallback: builds the deduped copy even when no confirmed duplicate can be removed in-place; copy rebuild drops entries lacking a `setVideoId` positionally by videoId
- "Duplicate Groups" report section with KEEP/REMOVED verdicts per copy, plus a NO SETVIDEOID verdict for copies in-place removal cannot touch
- Positional copy rebuild for dedupe (`copy_playlist_without`), so identical videoIds added multiple times keep correct per-entry counts in copies
- Raised minimum versions: `ytmusicapi>=1.12.2` (was 1.7.0) and Python 3.10+ (ytmusicapi itself dropped 3.9 in its 1.11.0 release, making the old floor fiction); `reporter.py` gained the `from __future__ import annotations` import it was missing either way
- API error logs now include the server's message (playlist create/add/remove failures only showed the exception class name before)

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
