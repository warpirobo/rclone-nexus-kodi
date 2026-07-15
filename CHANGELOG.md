# Changelog

## 1.3.1

- Translated the complete interface, settings, diagnostics, errors, documentation, and fanart to English (US).
- Kept legacy Spanish boolean and architecture aliases for backward compatibility with existing saved settings.
- Added a reproducible release builder, validation scripts, GitHub Actions workflows, and a Spanish publication guide.
- Standardized text files to UTF-8 without BOM and Unix line endings.

## 1.3.0

- Reorganized navigation into STRM libraries, New content, Favorites, remotes, and Tools.
- Added recursive STRM export without downloading media.
- Added incremental manifests, new-content detection, favorites, search, and optional background sync.
- Removed all automatic rclone downloads.
- Disabled VFS disk caching by default and added bounded cache, temporary-file, and log storage.
