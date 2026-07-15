"""Optional background synchronization service for Rclone Nexus."""

import os
import sys

import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_DIR = ADDON.getAddonInfo('path')
sys.path.insert(0, os.path.join(ADDON_DIR, 'resources', 'lib'))

from ariostv.rclone_backend import RcloneBackend
from ariostv import library_manager as library


def setting_bool(key, default=False):
    value = ADDON.getSetting(key)
    if value in (None, ''):
        return bool(default)
    return str(value).lower() in ('true', '1', 'yes', 'on', 'si', 'sí')


def setting_int(key, default, minimum=1, maximum=10080):
    try:
        value = int(ADDON.getSetting(key) or default)
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def translate(path):
    try:
        return xbmcvfs.translatePath(path)
    except Exception:
        return path


def conf_path():
    configured = ADDON.getSetting('conf_path')
    candidates = []
    if configured:
        candidates.append(translate(configured))
    candidates.extend([
        translate('special://masterprofile/rclone.conf'),
        translate('special://profile/rclone.conf'),
        '/sdcard/Download/rclone.conf',
        '/storage/emulated/0/Download/rclone.conf',
    ])
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return ''


def synchronize_once():
    entries = library.get_exports()
    config = conf_path()
    if not entries or not config:
        return
    backend = RcloneBackend(config)
    changed = False
    for entry in entries:
        try:
            result = library.sync_export(backend, entry)
            if result.get('added') or result.get('removed') or result.get('updated'):
                changed = True
        except Exception as exc:
            library.mark_export_error(entry, exc)
            xbmc.log('[Rclone Nexus/service] Error syncing %s: %s' % (entry.get('name'), exc), xbmc.LOGERROR)
    if changed and setting_bool('auto_update_kodi_library', True):
        library.request_library_update()


def main():
    monitor = xbmc.Monitor()
    # Let Kodi finish startup and avoid competing for I/O on low-end TV devices.
    if monitor.waitForAbort(90):
        return
    while not monitor.abortRequested():
        if setting_bool('auto_sync_enabled', False) and not xbmc.Player().isPlayingVideo():
            try:
                synchronize_once()
            except Exception as exc:
                xbmc.log('[Rclone Nexus/service] General error: %s' % exc, xbmc.LOGERROR)
        minutes = setting_int('auto_sync_interval_minutes', 180, minimum=30)
        if monitor.waitForAbort(minutes * 60):
            break


if __name__ == '__main__':
    main()
