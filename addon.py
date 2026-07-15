"""Rclone Nexus 1.3.1 - Kodi video add-on for local rclone remotes."""

import configparser
import datetime
import mimetypes
import os
import sys
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id') or 'plugin.ariostv'
ADDON_NAME = ADDON.getAddonInfo('name') or 'Rclone Nexus'
ADDON_DIR = ADDON.getAddonInfo('path')
sys.path.insert(0, os.path.join(ADDON_DIR, 'resources', 'lib'))

from ariostv.rclone_backend import (
    RcloneBackend, RcloneError, find_rclone, import_manual_rclone,
    diagnostic_text, get_last_report, detected_android_arch,
)
from ariostv import library_manager as library

HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]
ARGS = urllib.parse.parse_qs(urllib.parse.urlparse(sys.argv[2]).query)

VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.iso', '.m2ts', '.mpg',
    '.mpeg', '.wmv', '.flv', '.webm', '.vob', '.3gp', '.ogm', '.ogv', '.strm'
}
PREFERRED_REMOTE_TYPES = ('crypt', 'combine', 'union', 'alias', 'chunker', 'compress')
LOW_PRIORITY_REMOTE_TYPES = ('onedrive', 'drive', 'dropbox', 'box', 'pcloud')
_backend_cache = {}


def build_url(**kwargs):
    return BASE_URL + '?' + urllib.parse.urlencode(kwargs)


def plugin_url(**kwargs):
    return 'plugin://%s/?%s' % (ADDON_ID, urllib.parse.urlencode(kwargs))


def get_param(key, default=''):
    return ARGS.get(key, [default])[0]


def _translate(path):
    try:
        return xbmcvfs.translatePath(path)
    except Exception:
        return path


def _exists(path):
    try:
        return bool(path) and (os.path.exists(path) or xbmcvfs.exists(path))
    except Exception:
        return bool(path) and os.path.exists(path)


def _setting_bool(key, default=False):
    value = ADDON.getSetting(key)
    if value in (None, ''):
        return bool(default)
    return str(value).lower() in ('true', '1', 'yes', 'on', 'si', 'sí')


def notify(message, heading=None, icon=xbmcgui.NOTIFICATION_INFO, milliseconds=4500):
    try:
        xbmcgui.Dialog().notification(heading or ADDON_NAME, message, icon, milliseconds)
    except Exception:
        pass


def get_conf_path():
    candidates = []
    setting_path = ADDON.getSetting('conf_path')
    if setting_path:
        candidates.append(_translate(setting_path))
    candidates.extend([
        _translate('special://masterprofile/rclone.conf'),
        _translate('special://profile/rclone.conf'),
        '/sdcard/Download/rclone.conf',
        '/storage/emulated/0/Download/rclone.conf',
        '/sdcard/Downloads/rclone.conf',
        '/storage/emulated/0/Downloads/rclone.conf',
    ])
    for path in candidates:
        if _exists(path):
            return path
    return setting_path or candidates[-1]


def require_conf_path(show_dialog=True):
    conf_path = get_conf_path()
    if not _exists(conf_path):
        if show_dialog:
            xbmcgui.Dialog().ok(
                ADDON_NAME,
                'rclone.conf was not found.\n\n'
                'Copy it to /sdcard/Download/rclone.conf or set its path in Settings.'
            )
        return ''
    return conf_path


def get_backend(show_dialog=True):
    conf_path = require_conf_path(show_dialog=show_dialog)
    if not conf_path:
        return None
    try:
        key = conf_path + ':' + str(os.path.getmtime(conf_path))
    except Exception:
        key = conf_path
    if key not in _backend_cache:
        _backend_cache.clear()
        _backend_cache[key] = RcloneBackend(conf_path)
    return _backend_cache[key]


def parse_config_remotes(conf_path):
    parser = configparser.RawConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        with open(conf_path, 'r', encoding='utf-8-sig') as fh:
            parser.read_file(fh)
    except Exception:
        return []
    remotes = []
    for section in parser.sections():
        cfg = dict(parser.items(section))
        remotes.append({'name': section, 'type': (cfg.get('type') or '').lower()})
    return remotes


def preferred_remotes(conf_path):
    remotes = parse_config_remotes(conf_path)
    if not remotes:
        try:
            backend = get_backend(show_dialog=False)
            names = backend.list_remotes() if backend else []
            return [{'name': name, 'type': ''} for name in names]
        except Exception:
            return []

    def score(item):
        remote_type = item.get('type', '')
        if remote_type in PREFERRED_REMOTE_TYPES:
            return (0, item['name'].lower())
        if remote_type in LOW_PRIORITY_REMOTE_TYPES:
            return (2, item['name'].lower())
        return (1, item['name'].lower())
    return sorted(remotes, key=score)


def is_video(name):
    return os.path.splitext((name or '').lower())[1] in VIDEO_EXTENSIONS


def guess_mime(name):
    mime, _ = mimetypes.guess_type(name or '')
    if mime:
        return mime
    extension = os.path.splitext((name or '').lower())[1]
    return {
        '.mkv': 'video/x-matroska',
        '.ts': 'video/mp2t',
        '.webm': 'video/webm',
        '.iso': 'application/octet-stream',
    }.get(extension, 'video/mp4')


def set_category(text):
    try:
        xbmcplugin.setPluginCategory(HANDLE, text)
    except Exception:
        pass


def add_folder(label, url, icon='DefaultFolder.png', context=None, info=None):
    item = xbmcgui.ListItem(label)
    item.setArt({'icon': icon, 'thumb': icon})
    if info:
        try:
            item.setInfo('video', info)
        except Exception:
            pass
    if context:
        item.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(HANDLE, url, item, True)


def add_action(label, action, icon='DefaultAddonProgram.png', context=None):
    item = xbmcgui.ListItem(label)
    item.setArt({'icon': icon, 'thumb': icon})
    if context:
        item.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(HANDLE, action, item, False)


def add_video_item(remote, item, prefix=''):
    name = item.get('name') or os.path.basename(item.get('path', ''))
    label = prefix + name
    li = xbmcgui.ListItem(label)
    li.setProperty('IsPlayable', 'true')
    li.setArt({'icon': 'DefaultVideo.png', 'thumb': 'DefaultVideo.png'})
    info = {'title': name}
    size = int(item.get('size', 0) or 0)
    if size:
        info['size'] = size
    try:
        li.setInfo('video', info)
        li.setMimeType(guess_mime(name))
    except Exception:
        pass
    context = [
        ('Play', 'PlayMedia(%s)' % plugin_url(action='play', remote=remote, path=item.get('path', ''), name=name)),
        ('Refresh folder', 'Container.Refresh'),
    ]
    li.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url(action='play', remote=remote, path=item.get('path', ''), name=name),
        li,
        False,
    )


def folder_context(remote, path, name):
    favorite_label = 'Remove from favorites' if library.is_favorite(remote, path) else 'Add to favorites'
    return [
        ('Export folder to STRM library', 'RunPlugin(%s)' % plugin_url(action='export_folder', remote=remote, path=path, name=name)),
        ('Check for new content here', 'RunPlugin(%s)' % plugin_url(action='scan_new', remote=remote, path=path, name=name)),
        ('Search videos here', 'Container.Update(%s)' % plugin_url(action='search', remote=remote, path=path)),
        (favorite_label, 'RunPlugin(%s)' % plugin_url(action='toggle_favorite', remote=remote, path=path, name=name)),
        ('Refresh this folder', 'RunPlugin(%s)' % plugin_url(action='refresh', remote=remote, path=path)),
    ]


def show_root():
    xbmcplugin.setContent(HANDLE, 'files')
    set_category(ADDON_NAME)
    conf_path = require_conf_path(show_dialog=False)
    if not conf_path:
        add_action('[COLOR orange]Missing rclone.conf — open Settings[/COLOR]', build_url(action='settings'))
        add_folder('Tools and diagnostics', build_url(action='tools'), 'DefaultAddonProgram.png')
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        return

    recent_count = len(library.get_recent_items())
    export_count = len(library.get_exports())
    favorite_count = len(library.load_favorites())
    add_folder('STRM libraries (%d)' % export_count, build_url(action='libraries'), 'DefaultVideoPlaylists.png')
    add_folder('New content (%d)' % recent_count, build_url(action='new_content'), 'DefaultRecentlyAddedMovies.png')
    add_folder('Favorites (%d)' % favorite_count, build_url(action='favorites'), 'DefaultFavourites.png')

    selected = (ADDON.getSetting('remote_name') or '').strip()
    remotes = preferred_remotes(conf_path)
    if selected:
        remotes.sort(key=lambda item: 0 if item['name'] == selected else 1)
    for item in remotes:
        name = item['name']
        remote_type = item.get('type') or 'remote'
        label = '%s  [COLOR gray](%s)[/COLOR]' % (name, remote_type)
        add_folder(
            label,
            build_url(action='browse', remote=name, path=''),
            'DefaultNetwork.png',
            context=folder_context(name, '', name),
        )
    add_folder('Tools and settings', build_url(action='tools'), 'DefaultAddonProgram.png')
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def show_folder(remote, path=''):
    try:
        backend = get_backend()
        if not backend:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
            return
        items = backend.list_folder(remote, path or '')
    except Exception as exc:
        xbmc.log('[Rclone Nexus] Listing error: %s' % exc, xbmc.LOGERROR)
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            'rclone could not list this location:\n%s\n\nCheck the remote, rclone.conf, and the binary path.' % exc
        )
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
        return

    xbmcplugin.setContent(HANDLE, 'videos')
    location = '%s:/%s' % (remote, path.strip('/')) if path else '%s:/' % remote
    set_category(location)

    added = 0
    for item in items:
        name = item['name']
        item_path = item['path']
        if item['is_folder']:
            label = name
            if library.is_favorite(remote, item_path):
                label = '★ ' + label
            add_folder(
                label,
                build_url(action='browse', remote=remote, path=item_path),
                'DefaultFolder.png',
                context=folder_context(remote, item_path, name),
                info={'title': name},
            )
            added += 1
        elif is_video(name):
            add_video_item(remote, item)
            added += 1

    if added == 0:
        add_action('[No compatible folders or videos]', build_url(action='refresh', remote=remote, path=path), 'DefaultFolder.png')

    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE)
    try:
        xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_SIZE)
    except Exception:
        pass
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def play_item(remote, path, name):
    try:
        backend = get_backend()
        if not backend:
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            return
        url = backend.playback_url(remote, path)
    except Exception as exc:
        xbmc.log('[Rclone Nexus] Playback preparation error: %s' % exc, xbmc.LOGERROR)
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            'rclone could not prepare playback:\n%s\n\n'
            'Check execute permissions, binary architecture, and the remote.' % exc
        )
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    li = xbmcgui.ListItem(path=url)
    try:
        li.setPath(url)
    except Exception:
        pass
    li.setProperty('IsPlayable', 'true')
    try:
        li.setMimeType(guess_mime(name or path))
        li.setContentLookup(False)
        li.setInfo('video', {'title': name or os.path.basename(path)})
    except Exception:
        pass
    xbmc.log('[Rclone Nexus] Playing: %s' % url, xbmc.LOGINFO)
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def show_tools():
    xbmcplugin.setContent(HANDLE, 'files')
    set_category('%s · Tools' % ADDON_NAME)
    items = [
        ('Check local rclone', 'check_rclone'),
        ('Import rclone from Download', 'import_rclone'),
        ('Sync all STRM libraries', 'sync_all'),
        ('Clean temporary storage', 'cleanup_storage'),
        ('Clear browsing cache', 'clear_cache'),
        ('Stop rclone servers', 'stop_rclone'),
        ('Android/rclone diagnostics', 'diagnostic'),
        ('Settings', 'settings'),
    ]
    for label, action in items:
        add_action(label, build_url(action=action))
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def show_libraries():
    xbmcplugin.setContent(HANDLE, 'files')
    set_category('%s · STRM libraries' % ADDON_NAME)
    exports = library.get_exports()
    add_action('[Sync all]', build_url(action='sync_all'), 'DefaultAddonProgram.png')
    if not exports:
        add_action('[No folders have been exported yet. Open a folder and use its context menu.]', build_url(action='root'), 'DefaultFolder.png')
    for entry in exports:
        last_sync = entry.get('last_sync', 0)
        last_text = datetime.datetime.fromtimestamp(last_sync).strftime('%Y-%m-%d %H:%M') if last_sync else 'never'
        label = '%s  [COLOR gray](%d files · %s)[/COLOR]' % (
            entry.get('name', 'Library'), int(entry.get('file_count', 0) or 0), last_text
        )
        if entry.get('last_error'):
            label = '[COLOR red]⚠[/COLOR] ' + label
        context = [
            ('Sync now', 'RunPlugin(%s)' % plugin_url(action='sync_export', export_id=entry['id'])),
            ('Open remote folder', 'Container.Update(%s)' % plugin_url(action='browse', remote=entry['remote'], path=entry.get('path', ''))),
            ('Remove entry', 'RunPlugin(%s)' % plugin_url(action='remove_export', export_id=entry['id'])),
            ('Remove entry and STRM files', 'RunPlugin(%s)' % plugin_url(action='remove_export', export_id=entry['id'], delete='1')),
        ]
        add_folder(label, build_url(action='browse', remote=entry['remote'], path=entry.get('path', '')), 'DefaultVideoPlaylists.png', context=context)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def show_new_content():
    xbmcplugin.setContent(HANDLE, 'videos')
    set_category('%s · New content' % ADDON_NAME)
    items = library.get_recent_items()
    add_action('[Clear new-content list]', build_url(action='clear_new'), 'DefaultAddonProgram.png')
    if not items:
        add_action('[No new content is currently listed]', build_url(action='libraries'), 'DefaultRecentlyAddedMovies.png')
    for item in items:
        date_text = datetime.datetime.fromtimestamp(int(item.get('discovered_at', 0) or 0)).strftime('%Y-%m-%d')
        add_video_item(item.get('remote', ''), item, prefix='[COLOR lime]NEW[/COLOR] %s · ' % date_text)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def show_favorites():
    xbmcplugin.setContent(HANDLE, 'files')
    set_category('%s · Favorites' % ADDON_NAME)
    favorites = library.load_favorites()
    if not favorites:
        add_action('[No favorites]', build_url(action='root'), 'DefaultFavourites.png')
    for item in favorites:
        remote = item.get('remote', '')
        path = item.get('path', '')
        name = item.get('name') or os.path.basename(path.rstrip('/')) or remote
        add_folder('%s  [COLOR gray](%s)[/COLOR]' % (name, remote), build_url(action='browse', remote=remote, path=path), 'DefaultFavourites.png', context=folder_context(remote, path, name))
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def export_folder_action(remote, path, name):
    choices = ['Movies', 'TV shows', 'General videos']
    media_values = ['movies', 'tvshows', 'videos']
    selected = xbmcgui.Dialog().select('Kodi content type', choices)
    if selected < 0:
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        return
    destination_root = library.default_library_root()
    folder_name = library.sanitize_component(name or os.path.basename(path.rstrip('/')) or remote)
    destination = os.path.join(destination_root, folder_name)
    entry = library.create_export(remote, path, name or folder_name, media_values[selected], destination)
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        backend = get_backend()
        result = library.sync_export(backend, entry)
        source_added = library.register_kodi_video_source(result['entry'])
        if _setting_bool('auto_update_kodi_library', True):
            library.request_library_update(result['destination'])
    except Exception as exc:
        library.mark_export_error(entry, exc)
        xbmcgui.Dialog().ok(ADDON_NAME, 'The folder could not be exported:\n%s' % exc)
    else:
        message = (
            'STRM library created.\n\n'
            'Files: %d\nDestination: %s\n\n'
            '%s'
        ) % (
            result['total'], result['destination'],
            'The source was added to Kodi. In Videos > Files, open the source context menu and select “Set content” to choose the scraper.'
            if source_added else
            'The source already existed in Kodi. A library update was requested.'
        )
        xbmcgui.Dialog().ok(ADDON_NAME, message)
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def sync_export_action(entry_id):
    entry = library.find_export(entry_id)
    if not entry:
        notify('Library not found', icon=xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        return
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        result = library.sync_export(get_backend(), entry)
        if _setting_bool('auto_update_kodi_library', True) and (result['added'] or result['removed'] or result['updated']):
            library.request_library_update(result['destination'])
        notify('Added: %d · Removed: %d · Total: %d' % (result['added'], result['removed'], result['total']))
    except Exception as exc:
        library.mark_export_error(entry, exc)
        xbmcgui.Dialog().ok(ADDON_NAME, 'Sync error:\n%s' % exc)
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def sync_all_action():
    entries = library.get_exports()
    if not entries:
        notify('No STRM libraries are configured')
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        return
    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, 'Syncing STRM libraries...')
    totals = {'added': 0, 'removed': 0, 'errors': 0, 'files': 0}
    for index, entry in enumerate(entries):
        if progress.iscanceled():
            break
        progress.update(int(index * 100 / max(1, len(entries))), entry.get('name', 'Library'))
        try:
            result = library.sync_export(get_backend(), entry)
            totals['added'] += result['added']
            totals['removed'] += result['removed']
            totals['files'] += result['total']
        except Exception as exc:
            totals['errors'] += 1
            library.mark_export_error(entry, exc)
    progress.update(100, 'Finished')
    progress.close()
    if _setting_bool('auto_update_kodi_library', True) and (totals['added'] or totals['removed']):
        library.request_library_update()
    xbmcgui.Dialog().ok(
        ADDON_NAME,
        'Sync complete.\n\nNew: %d\nRemoved: %d\nIndexed files: %d\nErrors: %d' % (
            totals['added'], totals['removed'], totals['files'], totals['errors']
        )
    )
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def scan_new_action(remote, path, name):
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        result = library.scan_new_content(get_backend(), remote, path)
    except Exception as exc:
        xbmcgui.Dialog().ok(ADDON_NAME, 'New content could not be checked:\n%s' % exc)
    else:
        if result['first_scan']:
            text = 'An initial baseline was created with %d videos. Future checks will show only newly added files.' % result['total']
        else:
            text = 'New: %d\nRemoved from source: %d\nCurrent total: %d' % (result['added'], result['removed'], result['total'])
        xbmcgui.Dialog().ok(ADDON_NAME, text)
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def search_action(remote, path):
    query = xbmcgui.Dialog().input('Search videos in %s' % (path or remote), type=xbmcgui.INPUT_ALPHANUM)
    if not query:
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        return
    try:
        items = get_backend().search_videos(remote, path, query)
    except Exception as exc:
        xbmcgui.Dialog().ok(ADDON_NAME, 'Search error:\n%s' % exc)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
        return
    xbmcplugin.setContent(HANDLE, 'videos')
    set_category('Results: %s' % query)
    if not items:
        add_action('[No results]', build_url(action='browse', remote=remote, path=path), 'DefaultVideo.png')
    for item in items:
        add_video_item(remote, item)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def refresh_action(remote, path):
    try:
        backend = get_backend()
        backend.invalidate_list_cache(remote, path)
        notify('Folder refreshed')
    except Exception as exc:
        notify('Could not refresh: %s' % exc, icon=xbmcgui.NOTIFICATION_ERROR)
    xbmc.executebuiltin('Container.Refresh')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def toggle_favorite_action(remote, path, name):
    added = library.toggle_favorite(remote, path, name)
    notify('Added to favorites' if added else 'Removed from favorites')
    xbmc.executebuiltin('Container.Refresh')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def remove_export_action(entry_id, delete_files=False):
    entry = library.find_export(entry_id)
    if not entry:
        notify('Library not found')
    else:
        question = 'Remove “%s”?' % entry.get('name', 'Library')
        if delete_files:
            question += '\n\nIts local STRM files will also be deleted.'
        if xbmcgui.Dialog().yesno(ADDON_NAME, question):
            library.unregister_kodi_video_source(entry)
            library.remove_export(entry_id, delete_files=delete_files)
            notify('Library removed')
            xbmc.executebuiltin('Container.Refresh')
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def check_rclone_action():
    try:
        path = find_rclone(allow_download=False)
        if not path:
            raise RcloneError(
                'No executable rclone binary was found.\n\nDetected architecture: %s\n\n%s' % (
                    detected_android_arch(), get_last_report()
                )
            )
        backend = get_backend()
        version = backend.version().splitlines()[0]
        remotes = backend.list_remotes()
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            'rclone is ready.\n\nBinary: %s\nArchitecture: %s\nVersion: %s\nRemotes: %s' % (
                path, detected_android_arch(), version, ', '.join(remotes[:12]) or '(none)'
            )
        )
    except Exception as exc:
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            'rclone is not ready:\n%s\n\n'
            'Set a direct binary path or include the binary in the ZIP at resources/bin/android/<ABI>/rclone.' % exc
        )
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def import_rclone_action():
    try:
        path = import_manual_rclone()
        if not path:
            raise RcloneError('rclone, rclone.gz, or rclone.bin was not found in the Download folder.')
        xbmcgui.Dialog().ok(ADDON_NAME, 'rclone imported to:\n%s' % path)
    except Exception as exc:
        xbmcgui.Dialog().ok(ADDON_NAME, 'rclone could not be imported:\n%s\n\n%s' % (exc, get_last_report()))
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def cleanup_storage_action():
    try:
        backend = get_backend()
        before = backend.storage_usage()
        removed = backend.cleanup_storage(aggressive=True)
        after = backend.storage_usage()
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            'Cleanup complete.\n\nBefore: %s\nFreed: %s\nAfter: %s\n\nDisk caching is disabled by default for playback.' % (
                library.format_bytes(before['total']), library.format_bytes(removed), library.format_bytes(after['total'])
            )
        )
    except Exception as exc:
        xbmcgui.Dialog().ok(ADDON_NAME, 'Storage could not be cleaned:\n%s' % exc)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def clear_cache_action():
    try:
        count = get_backend().clear_list_cache()
        notify('Browsing cache cleared: %d files' % count)
    except Exception as exc:
        xbmcgui.Dialog().ok(ADDON_NAME, 'Cache could not be cleared:\n%s' % exc)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def stop_rclone_action():
    try:
        count = get_backend().stop_servers()
        notify('Servers stopped: %d' % count)
    except Exception as exc:
        xbmcgui.Dialog().ok(ADDON_NAME, 'Servers could not be stopped:\n%s' % exc)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def diagnostic_action():
    text = diagnostic_text()
    try:
        backend = get_backend(show_dialog=False)
        if backend:
            usage = backend.storage_usage()
            text += '\n\nTemporary add-on storage:\n  Total: %s\n  VFS: %s\n  Listings: %s\n  Logs: %s' % (
                library.format_bytes(usage['total']), library.format_bytes(usage['vfs_cache']),
                library.format_bytes(usage['list_cache']), library.format_bytes(usage['logs'])
            )
    except Exception:
        pass
    try:
        xbmcgui.Dialog().textviewer('%s · Diagnostics' % ADDON_NAME, text)
    except Exception:
        xbmcgui.Dialog().ok(ADDON_NAME, text[:4000])
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def main():
    action = get_param('action', 'root')
    if action == 'root':
        show_root()
    elif action == 'browse':
        show_folder(get_param('remote'), get_param('path'))
    elif action == 'play':
        play_item(get_param('remote'), get_param('path'), get_param('name'))
    elif action == 'tools':
        show_tools()
    elif action == 'libraries':
        show_libraries()
    elif action == 'new_content':
        show_new_content()
    elif action == 'favorites':
        show_favorites()
    elif action == 'export_folder':
        export_folder_action(get_param('remote'), get_param('path'), get_param('name'))
    elif action == 'sync_export':
        sync_export_action(get_param('export_id'))
    elif action == 'sync_all':
        sync_all_action()
    elif action == 'scan_new':
        scan_new_action(get_param('remote'), get_param('path'), get_param('name'))
    elif action == 'search':
        search_action(get_param('remote'), get_param('path'))
    elif action == 'refresh':
        refresh_action(get_param('remote'), get_param('path'))
    elif action == 'toggle_favorite':
        toggle_favorite_action(get_param('remote'), get_param('path'), get_param('name'))
    elif action == 'remove_export':
        remove_export_action(get_param('export_id'), get_param('delete') == '1')
    elif action == 'clear_new':
        library.clear_recent_items()
        notify('New-content list cleared')
        xbmc.executebuiltin('Container.Refresh')
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
    elif action == 'settings':
        ADDON.openSettings()
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
    elif action == 'check_rclone':
        check_rclone_action()
    elif action == 'import_rclone':
        import_rclone_action()
    elif action == 'cleanup_storage':
        cleanup_storage_action()
    elif action == 'clear_cache':
        clear_cache_action()
    elif action == 'stop_rclone':
        stop_rclone_action()
    elif action == 'diagnostic':
        diagnostic_action()
    else:
        xbmc.log('[Rclone Nexus] Unknown action: %s' % action, xbmc.LOGWARNING)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)


if __name__ == '__main__':
    main()
