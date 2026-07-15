# Rclone Nexus for Kodi Android

Rclone Nexus browses and streams media from `rclone` remotes, including `crypt`, `combine`, `union`, `alias`, and compatible cloud services. It can also create incremental `.strm` libraries without downloading the media files to the device.

## Highlights

- Folder browsing with favorites, search, and new-content tracking.
- Recursive export to `.strm` files for Kodi libraries.
- Incremental synchronization: create new entries, update changed entries, and remove stale entries.
- Optional background synchronization, disabled by default.
- Local-only rclone binary handling; the add-on never downloads rclone from the network.
- Bounded temporary storage and VFS disk caching disabled by default.

## Requirements

- Kodi 19 Matrix or newer on Android or Fire OS.
- A working `rclone.conf` file.
- A compatible rclone binary supplied by the user or bundled in the add-on ZIP.
- Access to a configured rclone remote.

## Install from a release ZIP

1. Download `plugin.ariostv-<version>.zip` from the GitHub Releases page.
2. In Kodi, enable **Settings > System > Add-ons > Unknown sources** when installing outside the official Kodi repository.
3. Open **Add-ons > Install from ZIP file** and select the downloaded ZIP.
4. Open the add-on settings and configure `rclone.conf` and the rclone binary if they are not auto-detected.

## rclone binary locations inside the ZIP

Bundle only the architecture you need to keep the package small:

- 32-bit Android ARM / many Fire TV devices: `resources/bin/android/armeabi-v7a/rclone`
- Android ARM64: `resources/bin/android/arm64-v8a/rclone`
- Android x86: `resources/bin/android/x86/rclone`
- Android x86_64: `resources/bin/android/x86_64/rclone`

The same folders also accept `rclone.gz`. At startup, Rclone Nexus copies a bundled binary to an internal executable directory.

You can alternatively configure **Direct path to the rclone binary** in the add-on settings.

## Create a Kodi STRM library

1. Open a remote and highlight a folder.
2. Open its context menu.
3. Select **Export folder to STRM library**.
4. Choose Movies, TV shows, or General videos.
5. Rclone Nexus creates the `.strm` files, registers the folder as a Kodi video source, and requests a library update.
6. The first time, open **Videos > Files**, open the context menu for the `Rclone Nexus - ...` source, and select **Set content** to choose the appropriate scraper.

Kodi stores scraper configuration in its database. The add-on does not modify that database directly.

## Detect new content

- From a folder: context menu > **Check for new content here**.
- From **STRM libraries**: sync one library or all libraries.
- Files added after the initial scan appear under **New content**.
- Automatic synchronization can be enabled in Settings; use an interval of at least 30 minutes.

## Storage policy

Default playback settings:

- `--vfs-cache-mode off`
- 8 MB in-memory buffer
- 16 MB initial read chunks, up to 128 MB

Media files are not stored on the device. The add-on also removes large VFS cache leftovers from version 1.2.0 and applies hard limits to logs, temporary files, and browsing cache.

## Build a release ZIP

```bash
python scripts/build_release.py
```

The installable file is written to `dist/plugin.ariostv-<version>.zip`.

## License and project status

Licensed under GPL-3.0-or-later. This project is independent and is not affiliated with the Kodi Foundation or the rclone project.
