# Changelog

## [Unreleased]

### Security
- Refreshed `uv.lock` to versions that patch the open Dependabot advisories: requests 2.34.2, urllib3 2.7.0, idna 3.19, Pygments 2.21.0 and pytest 9.1.1 (supersedes PR #4, whose versions are overtaken). The dev dependency floor is now `pytest>=9.0.3`

### Changed
- In-place replacements no longer reorder the replacement into the original track's slot by default. This avoids flipping the YouTube Music playlist's server-side sort to Manual, preserving "Recently added" as the default sort
- Raised minimum versions: `ytmusicapi>=1.12.2` (was 1.7.0) and Python 3.10+ (ytmusicapi itself dropped 3.9 in its 1.11.0 release, so the old 3.9 floor could not install); `reporter.py` gained the `from __future__ import annotations` import it was missing either way

### Fixed
- Links for playlist entries now pin to the playlist (`watch?v=...&list=...`), the same way the YouTube Music UI links them. Bare `watch?v=` links for album-sourced entries get remapped by YouTube Music to a different video or an autoplay mix when opened standalone
- Interactive prompts now mark original tracks that are unavailable on YouTube Music. Their links cannot land anywhere (YouTube Music jumps to the nearest playable entry), which previously made correct matches look like mismatches
- Browser auth setup now strips replay-hostile headers (content-encoding, hop-by-hop headers, and the stray HTTP request line Firefox/Chrome copies into the header list) from browser.json, and ensures the SAPISIDHASH auth marker ytmusicapi needs to detect browser auth is present; existing credential files are repaired automatically on load. Previously these caused the YouTube API to answer `400 Bad Request` with an HTML page, which surfaced as a JSON decode error
- Copy mode now matches swaps to playlist entries by position instead of videoId. Unavailable tracks can share an empty videoId, so several of them got one replacement between them (the others' replacements were never added but still reported as applied). The same videoId assumption also hid unpicked YouTube video suggestions from the report after one was picked
- Copy mode on a playlist without `setVideoId`s (every playlist you don't own) now searches clean tracks for explicit versions. They were skipped as unremovable, so the copy only got unavailable-track swaps
- Playlist edits now check the status ytmusicapi returns, which reports a rejected edit instead of raising. A rejected add no longer leads to the original being removed (when the replacement is already in the playlist, the add is skipped, since YouTube Music rejects re-adding it, and only the original is removed), a rejected removal is no longer reported as done, and a failed playlist creation is caught instead of passing the error response on as a playlist id
- Tracks that cannot be added to a new playlist, even after the one-by-one retry, are now listed in the console and the report instead of only a debug log
- When an in-place run falls back to copy mode after a permission error, the copy now carries every confirmed swap. It used to leave out swaps already applied in place, which put their clean originals back, and it never printed the new playlist link

### Added
- `--preserve-position` flag to restore the previous behavior of moving the replacement into the original track's playlist position (still flips the playlist to Manual sort)
- `--dedupe` standalone mode: finds duplicate songs (same title/artist within 10s duration, or identical uploads), keeps the best copy (available > unavailable, official > YouTube upload, explicit > clean, earliest position breaks ties), and removes the rest after interactive confirmation
- Up-front unowned-playlist detection: when no playlist entries expose a `setVideoId`, both the replacement flow and dedupe switch to copy mode instead of firing doomed write requests. Dry runs switch too (as do Liked Music dry runs), so the report previews what a real run would do
- Dedupe copy-mode fallback: builds the deduped copy even when no confirmed duplicate can be removed in-place
- Dedupe matching keeps letters in any script: titles and artists are compared NFKC-normalized and casefolded, ignoring emoji and clean/edited/featuring suffixes. The ASCII-only search normalizer would have reduced different non-Latin titles by one artist to the same empty key
- "Duplicate Groups" report section with a verdict per copy: KEEP, REMOVED, DROPPED IN COPY, TO REMOVE (dry run), NOT REMOVED (declined or run failed), NO SETVIDEOID for copies in-place removal cannot touch, and FAILED (removal rejected, with the error)
- Positional copy rebuild for dedupe (`copy_playlist_without`): entries are dropped by playlist position, so exactly the chosen copy of an identical pair goes (the winner keeps its slot), even on playlists that expose no `setVideoId`
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
