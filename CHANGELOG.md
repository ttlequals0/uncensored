# Changelog

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
